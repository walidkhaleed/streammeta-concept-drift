"""Behavioral controls for the scientific contract; no historical target values."""
from dataclasses import replace
from itertools import islice
from pathlib import Path
import ast
import inspect
import json
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from river import metrics as river_metrics
from sklearn.metrics import roc_auc_score
from streammeta.config import RunConfig,Seeds,XAIConfig
from streammeta.engine import run_engine,META_COLS
from streammeta.models import (OnlineLearner,PredictionContext,RollingState,BoundedManager,
                               router_target,manager_class,expert_class,xgb_model)
from streammeta.streams import make_stream,fingerprint,stream_definition,materialize,read_stream,binary_label
from streammeta.metrics import recompute
from streammeta.plans import make_plan
from streammeta.provenance import check_gates,write_gate
from streammeta.io import atomic_json

def samples(c):return list(islice(make_stream(c),c.length))

@pytest.mark.parametrize('dataset',['Agrawal','SEA','STAGGER','Hyperplane'])
def test_same_seed_identical_hash(dataset):
    c=RunConfig(dataset=dataset,n=5200)
    assert fingerprint(samples(c))==fingerprint(samples(c))

def test_wrapper_seed_changes_transition_realization():
    c=RunConfig(n=4300)
    a=samples(c);b=samples(replace(c,seeds=replace(c.seeds,wrapper=999)))
    assert a[:2000]==b[:2000]
    assert fingerprint(a[3700:4300])!=fingerprint(b[3700:4300])

def test_ablation_stream_hash_and_cache_shared(tmp_path):
    configs=[RunConfig(n=4500),RunConfig(n=4500,window=50,stage='windows'),RunConfig(n=4500,delay=500,stage='delays')]
    mats=[materialize(c,tmp_path) for c in configs]
    assert len({str(p) for p,m in mats})==1
    assert len({m['stream_sha256'] for p,m in mats})==1
    assert fingerprint(read_stream(mats[0][0]))==mats[0][1]['stream_sha256']

def test_explicit_seed_domains_and_paired_plans():
    d=stream_definition(RunConfig())
    assert d['pre_kwargs']['seed']==42 and d['post_kwargs']['seed']!=42 and d['wrapper_kwargs']['seed']==142
    for phase in ('primary','windows','delays','robustness'):
        jobs=make_plan(phase,n_seeds=2)
        for dataset in {c.dataset for c in jobs}:
            for i in {c.replicate for c in jobs}:
                group=[stream_definition(c) for c in jobs if c.dataset==dataset and c.replicate==i]
                assert not group or all(d==group[0] for d in group)

class RememberingLearner:
    """An adversarially label-sensitive learner to expose premature feedback."""
    def __init__(self,clock=None):self.verified_count=0;self.fit_count=0;self.trained=False;self.last=0;self.history=[];self.clock=clock
    def predict(self,x,t):
        self.history.append(('predict',t,self.verified_count))
        p=.8 if self.last else .2
        return PredictionContext(t,dict(x),None,p,'ARF',1-p,p,None,False,0,self.verified_count),{
            'meta_state_time_ns':0,'selected_prediction_time_ns':0,'response_path_time_ns':0,'shadow_prediction_time_ns':0}
    def verify(self,ctx,y):
        self.history.append(('verify',ctx.t,y));self.last=y;self.verified_count+=1
        return router_target(y,ctx.p_hat,ctx.p_arf)

def test_future_hidden_labels_cannot_change_pre_release_outputs():
    tau=100;n=140;changed=7;c=RunConfig(n=n,delay=tau)
    original=[({'x':float(t)},0) for t in range(n)]
    altered=[(x,1 if t>=changed else y) for t,(x,y) in enumerate(original)]
    a=run_engine(c,original,'a',learner=RememberingLearner())['_test_records']['predictions']
    b=run_engine(c,altered,'b',learner=RememberingLearner())['_test_records']['predictions']
    cut=changed+tau
    for t in range(cut+1):
        for k in ('probability','route','verified_count_before','manager_fit_count','meta_state_available'):
            assert a[t][k]==b[t][k]
    assert a[cut+1]['probability']!=b[cut+1]['probability']

def test_real_experts_and_router_see_only_released_labels():
    c=RunConfig(dataset='SEA',n=180,delay=80,window=10,refit_every=10,n_models=1)
    rows=samples(c);changed=[(x,1-y if t>=30 else y) for t,(x,y) in enumerate(rows)]
    a=run_engine(c,rows,'a')['_test_records']['predictions'];b=run_engine(c,changed,'b')['_test_records']['predictions']
    keys=['probability','p_hat_hat','p_hat_arf','q_arf','verified_count_before','manager_fit_count',*META_COLS]
    pd.testing.assert_frame_equal(pd.DataFrame(a).loc[:110,keys],pd.DataFrame(b).loc[:110,keys],check_exact=True)
    assert a[80]['verified_count_before']==0 and a[80]['verified_count_after']==1
    assert not a[89]['meta_state_available'] and a[90]['meta_state_available']

@pytest.mark.parametrize('tau',[0,100,500,800])
def test_delay_queue_append_release_no_flush(tau):
    n=620;c=RunConfig(n=n,delay=tau)
    result=run_engine(c,[({'x':t},t%2) for t in range(n)],'queue',learner=RememberingLearner())
    assert result['max_pending_queue']==min(n,tau+1)
    assert result['unverified_at_end']==min(n,tau)
    assert result['verified_count']==max(0,n-tau)
    p=pd.DataFrame(result['_test_records']['predictions'])
    assert np.array_equal(p.queue_before_release,np.minimum(np.arange(n)+1,tau+1))
    assert np.array_equal(p.queue_after_release,np.minimum(np.arange(n)+1,tau))
    for row in result['_test_records']['updates']:assert row['verification_time']==row['transaction_id']+tau

def test_historical_probabilities_and_targets_survive_model_change():
    c=RunConfig(n=30,delay=4);learner=RememberingLearner()
    r=run_engine(c,[({'x':t},int(t%3==0)) for t in range(c.length)],'history',learner=learner)['_test_records']
    assert len(set(row['probability'] for row in r['predictions']))>1
    for u in r['updates']:
        p=r['predictions'][u['transaction_id']]
        assert u['historical_p_hat']==p['p_hat_hat'] and u['historical_p_arf']==p['p_hat_arf']
        assert u['router_target']==int(abs(u['y_true']-p['p_hat_arf'])<abs(u['y_true']-p['p_hat_hat']))

@pytest.mark.parametrize('y',[0,1])
def test_router_target_equal_errors_choose_hat(y):
    assert router_target(y,.7,.7)==0
    assert router_target(y,.5,.5)==0
    assert router_target(y,.1,.9)==y

def test_manager_threshold_matches_actual_xgboost_predict(monkeypatch):
    model=xgb_model(1);model.n_classes_=2
    values=np.array([.499999,.5,.500001])
    # Exercise the installed classifier's own threshold branch, isolating q.
    from xgboost.sklearn import XGBModel
    monkeypatch.setattr(XGBModel,'predict',lambda self,X,**kwargs:values)
    actual=model.predict(np.zeros((3,4)))
    assert actual.tolist()==[0,0,1]
    assert [manager_class(q) for q in values]==actual.tolist()
    assert [expert_class(q) for q in values]==[0,1,1]

def test_full_prediction_fields_equal_across_three_control_entry_configs():
    rows=samples(RunConfig(n=420,n_models=1))
    frames=[]
    for arm,stage in [('principal','primary'),('w100','windows'),('tau0','delays')]:
        c=RunConfig(n=420,n_models=1,arm=arm,stage=stage)
        r=run_engine(c,rows,arm)
        frames.append(pd.DataFrame(r['_test_records']['predictions']).drop(columns='run_id'))
    for frame in frames[1:]:pd.testing.assert_frame_equal(frames[0],frame,check_exact=True)

def test_exact_auc_from_saved_prediction_trace(tmp_path):
    c=RunConfig(dataset='SEA',n=120,n_models=1,method='HAT')
    r=run_engine(c,samples(c),'saved',tmp_path)
    saved=pd.read_parquet(tmp_path/'predictions.parquet')
    assert r['roc_auc_exact']==roc_auc_score(saved.y_true,saved.probability)
    assert np.array_equal(saved.hard_prediction,(saved.probability>=.5).astype(int))

def test_explicit_approximate_auc_recomputed():
    y=np.array([0,1,1,0,1,0]);p=np.array([.492,.497,.891,.899,.12,.42])
    actual=recompute(y,p,101);m=river_metrics.ROCAUC(n_thresholds=101)
    for a,b in zip(y,p):m.update(int(a),float(b))
    assert actual['roc_auc_streaming_approx']==m.get()
    assert actual['auc_thresholds']==101 and actual['primary_metric']=='roc_auc_exact'
    assert actual['roc_auc_exact']==roc_auc_score(y,p)

def test_recent_error_uses_same_verified_history_and_ties():
    c=RunConfig(method='RecentError',window=2,n_models=1);r=OnlineLearner(c)
    assert r.predict({'x':0.},0)[0].route=='ARF'
    r.state.update(1,.1,.8);r.state.update(0,.9,.2)
    assert r.predict({'x':0.},1)[0].route=='ARF'
    r.state.update(1,.8,.8);r.state.update(0,.2,.2)
    assert r.predict({'x':0.},2)[0].route=='HAT'
    assert r.state.features()==(.5,0.,0.,0.)

def test_manager_buffer_bound_refit_guard_and_snapshot():
    m=BoundedManager(12,5,342)
    for i in range(20):assert not m.observe((.1,.2,.3,.4),0)
    assert not m.trained and len(m.X)==12
    assert m.observe((.3,.2,.1,.4),1)
    first=m.model
    for i in range(5):m.observe((.1*i,.2,.3,.4),i%2)
    assert m.fit_count==2 and m.model is not first and len(m.X)==12

def test_xai_retrospective_windows_and_crossfit(tmp_path):
    from streammeta.xai import temporal_discrimination
    cfg=XAIConfig(raw_window=80,block_size=10,folds=2,permutations=3)
    rng=np.random.default_rng(2);raw=[(t,{'a':float(rng.normal()+2*(t>=1040)),'b':float(rng.normal())}) for t in range(1000,1080)]
    v=temporal_discrimination(raw,cfg,442,tmp_path)
    assert v['older_index_range']==[1000,1039] and v['newer_index_range']==[1040,1079]
    p=pd.read_parquet(tmp_path/'tier2_oof_predictions.parquet')
    assert p.transaction_id.max()==1079 and p.probability_newer_oof.between(0,1).all()
    assert v['roc_auc_exact_oof']==roc_auc_score(p.temporal_label,p.probability_newer_oof)
    assert v['permutation_p_one_sided'] in (.25,.5,.75,1.)
    for k in range(cfg.folds):
        train=set(p[p.fold!=k].transaction_id);test=set(p[p.fold==k].transaction_id)
        assert train.isdisjoint(test) and len(train|test)==80
        model=xgb_model(0);model.load_model(tmp_path/f'tier2_fold_{k}.ubj')
        X=pd.read_parquet(tmp_path/'raw_windows.parquet').set_index('transaction_id').loc[sorted(test),['a','b']]
        assert np.allclose(model.predict_proba(X)[:,1],p.set_index('transaction_id').loc[sorted(test),'probability_newer_oof'])
    assert v['interpretation']=='descriptive_temporal_window_attribution' and not v['localization_validated']

def test_xai_before_learning_order_and_real_snapshot(tmp_path):
    from streammeta.xai import XAIObserver
    c=RunConfig(dataset='SEA',n=140,window=5,refit_every=5,n_models=1,
                xai=XAIConfig(enabled=True,adwin_enabled=False,raw_window=80,block_size=10,folds=2,permutations=3,min_meta=10,scheduled_event=120))
    observer=XAIObserver(c,tmp_path);events=[]
    r=run_engine(c,samples(c),'xai',tmp_path,observer=observer,order_hook=lambda event,t:events.append((event,t)))
    order=[a for a,t in events if t==120]
    assert order==['predict','enqueue','release','detector_xai','learn']
    e=observer.events[0];assert e['success'] and e['event_index_zero_based']==120 and e['event_sample_number_one_based']==121
    assert e['verified_count_before_learning']==120 and e['tier2']['newer_index_range'][1]==120
    trace=pd.read_parquet(tmp_path/'predictions.parquet')
    assert e['manager_fit_count_before_learning']==trace.iloc[120].manager_fit_count
    model=xgb_model(0);model.load_model(tmp_path/e['event_dir']/'tier1_router.ubj')
    X=np.array([trace.iloc[120][list(META_COLS)].to_numpy(dtype=float)])
    assert float(model.predict_proba(X)[0,1])==trace.iloc[120].q_arf
    assert r['n_adwin_events']==0 and r['n_xai_capable_events']==1 and r['n_xai_successful']==1
    assert r['invocation_reduction_percent']==100*(1-1/140)
    plain=run_engine(replace(c,xai=replace(c.xai,enabled=False)),samples(c),'without_xai')['_test_records']['predictions']
    pd.testing.assert_frame_equal(trace.drop(columns='run_id').sort_index(axis=1),pd.DataFrame(plain).drop(columns='run_id').sort_index(axis=1),check_exact=True,check_dtype=False)

class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now
    def advance(self,n):self.now+=n

def test_timing_boundaries_with_independent_cost_injections():
    clock=Clock();c=RunConfig(n=1,window=1,n_models=1);learner=OnlineLearner(c,clock)
    class Expert:
        def __init__(self,p,pred_cost,learn_cost):self.p=p;self.pc=pred_cost;self.lc=learn_cost
        def predict_proba_one(self,x):clock.advance(self.pc);return {1:self.p}
        def learn_one(self,x,y):clock.advance(self.lc)
    class State:
        def features(self):clock.advance(3);return (.5,.1,.2,.3)
        def update(self,*a):clock.advance(2)
    class Manager:
        trained=False;fit_count=0
        def choose(self,mf):clock.advance(5);return 1,None
        def observe(self,mf,z):clock.advance(11)
    class Observer:
        def observe_input(self,ctx):clock.advance(17)
        def on_release(self,*args):clock.advance(19)
        def summary(self,n):return {}
    learner.hat=Expert(.2,7,13);learner.arf=Expert(.8,23,29);learner.state=State();learner.manager=Manager()
    def acquisition():clock.advance(31);yield {'x':0.},1
    row=run_engine(c,acquisition(),'clock',learner=learner,observer=Observer(),clock=clock)['_test_records']['timings'][0]
    assert row['acquisition_time_ns']==31
    assert row['meta_state_time_ns']==3
    assert row['selected_prediction_time_ns']==5+23
    assert row['response_path_time_ns']==3+5+23
    assert row['shadow_prediction_time_ns']==7
    assert row['update_time_ns']==11+2+13+29
    assert row['observer_time_ns']==17+19
    assert row['core_step_time_ns']==3+5+23+7+11+2+13+29
    assert row['integrated_step_time_ns']==row['core_step_time_ns']+36

def test_one_hyperplane_factory_every_path_explicit_nonzero_magnitude():
    import streammeta.streams as streams
    root=Path(streams.__file__).parent;calls=[]
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='Hyperplane':calls.append(node)
    assert len(calls)==1
    args={k.arg:k.value for k in calls[0].keywords}
    assert ast.literal_eval(args['mag_change'])==.001 and ast.literal_eval(args['n_features'])==10 and ast.literal_eval(args['n_drift_features'])==3
    for stage in ('primary','windows','delays','robustness','xai','resource'):
        c=RunConfig(dataset='Hyperplane',stage=stage)
        stream=make_stream(c);assert stream.mag_change==.001

def test_fixed_label_mapping_and_rejection():
    assert binary_label(True)==1 and binary_label(False)==0 and binary_label(np.int64(1))==1
    with pytest.raises(ValueError):binary_label('yes')

def test_self_metrics_work_in_nested_process_and_pid_namespace():
    import subprocess,sys
    code='from streammeta.system import self_rss_bytes,self_affinity; assert self_rss_bytes()>0; assert self_affinity()'
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr

def test_parquet_trace_is_published_only_after_close(tmp_path):
    import pyarrow as pa
    from streammeta.io import ParquetSink
    path=tmp_path/'trace.parquet';sink=ParquetSink(path,pa.schema([('value',pa.int64())]),batch_size=2)
    sink.append({'value':1});sink.append({'value':2})
    assert not path.exists() and sink.staging_path.exists()
    sink.close()
    assert path.exists() and not sink.staging_path.exists()
    assert pd.read_parquet(path).value.tolist()==[1,2]

def test_resource_repetitions_preserve_scientific_seeds_and_randomize_order():
    jobs=make_plan('resource',resource_repeats=3)
    assert jobs==make_plan('resource',resource_repeats=3)
    orders=[]
    for i in range(3):
        block=[c for c in jobs if c.resource_repeat==i];orders.append([(c.dataset,c.method) for c in block])
        assert len(block)==30
        assert {c.seeds.resource_run for c in block}=={542+i}
        assert {(c.seeds.data,c.seeds.wrapper,c.seeds.model,c.seeds.router,c.seeds.xai) for c in block}=={(42,142,242,342,442)}
    assert orders[0]!=orders[1] and orders[1]!=orders[2]

def test_xai_permutation_preserves_fold_balance_and_block_dependence():
    from streammeta.xai import permute_blocks_within_folds
    y=np.r_[np.zeros(200,dtype=int),np.ones(200,dtype=int)];blocks=np.arange(400)//20;fold=(blocks%10)%4
    rng=np.random.default_rng(442);draws=[]
    for i in range(40):
        yp=permute_blocks_within_folds(y,blocks,fold,rng);draws.append(yp)
        for k in range(4):
            assert np.array_equal(np.bincount(yp[fold==k]),np.bincount(y[fold==k]))
            assert yp[fold!=k].mean()==.5
        for block in range(20):assert len(np.unique(yp[blocks==block]))==1
    assert any(not np.array_equal(yp,y) for yp in draws)
    np.testing.assert_array_equal(draws[0],permute_blocks_within_folds(y,blocks,fold,np.random.default_rng(442)))

def test_stale_or_missing_gates_block_science(tmp_path):
    c=RunConfig(stage='controlled')
    with pytest.raises(RuntimeError):check_gates(c,tmp_path)
    write_gate(tmp_path,'tests',{})
    check_gates(c,tmp_path)
    p=tmp_path/'gates'/'tests.json';gate=json.loads(p.read_text());gate['source_sha256']='bad';atomic_json(p,gate)
    with pytest.raises(RuntimeError,match='Stale'):check_gates(c,tmp_path)

def test_known_control_distinctions():
    null=samples(RunConfig(dataset='NullControl',n=3250))
    label=samples(RunConfig(dataset='LabelRuleControl',n=3250))
    cov=samples(RunConfig(dataset='CovariateControl',n=3250))
    mixed=samples(RunConfig(dataset='MixedControl',n=3250))
    for t in (2999,3000,3249):
        assert null[t][0]==label[t][0]
        assert null[t][1]==cov[t][1]
        assert label[t][1]==mixed[t][1]
        if t>=3000:
            assert null[t][1]!=label[t][1]
            assert cov[t][0]['x0']==null[t][0]['x0']+1.5
