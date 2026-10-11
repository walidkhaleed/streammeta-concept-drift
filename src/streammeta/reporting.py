"""Tables are projections of run records; never manually entered outcomes."""
from pathlib import Path
import json
import tempfile
import shutil
from itertools import combinations
import numpy as np
import pandas as pd
from scipy import stats
from .config import DATASETS,METHODS
from .io import atomic_json,canonical,file_sha,digest
from .provenance import source_state,environment_signature,utc_now
from .manifest_contract import check_before_collection, publish_manifest_mirrors, frozen_release_exists

def flat(row):
    return {k:(canonical(v).decode() if isinstance(v,(list,dict)) else v) for k,v in row.items()}

def interval(values):
    a=np.asarray(values,dtype=float);n=len(a);mean=float(np.mean(a))
    sd=float(np.std(a,ddof=1)) if n>1 else None
    half=float(stats.t.ppf(.975,n-1)*sd/np.sqrt(n)) if n>1 else None
    return {'n_repetitions':n,'mean':mean,'median':float(np.median(a)),'std':sd,
            'IQR':float(np.quantile(a,.75)-np.quantile(a,.25)),
            'ci95_low':mean-half if half is not None else None,'ci95_high':mean+half if half is not None else None}

def manifest_frame(output, src=None, env=None, accepted_ids=None):
    output=Path(output)
    src=src or source_state()['source_sha256'];env=env or environment_signature();rows=[]
    for p in sorted((output/'runs').glob('*/provenance.json')):
        if accepted_ids is not None and p.parent.name not in accepted_ids:
            raise RuntimeError('Unexpected/rejected run in collection root')
        prov=json.loads(p.read_text());cfg=json.loads((p.parent/'config.json').read_text())
        s=json.loads((p.parent/'summary.json').read_text()) if (p.parent/'summary.json').exists() else {'run_id':prov['run_id'],'config_id':prov['config_id'],'status':prov['status'],'stage':cfg['stage'],'dataset':cfg['dataset'],'method':cfg['method'],'source_sha256':prov['source']['source_sha256']}
        s.update(run_path=str(p.parent.relative_to(output)),git_commit=prov['source']['git_commit'],git_dirty=prov['source']['git_dirty'])
        s['current_source_environment']=s.get('source_sha256')==src and s.get('environment_signature')==env
        s['artifacts_manifest_sha256']=file_sha(p.parent/'artifacts.json') if (p.parent/'artifacts.json').exists() else None
        rows.append(flat(s))
    return pd.DataFrame(rows)

def collect(output,release_policy=None):
    from .publication import publication_session,publish_tables
    from .release import load_policy,inspect_evidence
    from .provenance import require_source_archive
    output=Path(output)
    if frozen_release_exists(output.parent):raise RuntimeError('Immutable release: cannot collect')
    with publication_session(output):
        utility=source_state();utility_env=environment_signature()
        policy_path=Path(release_policy) if release_policy else output.parent/'provenance/release_policy.json'
        policy=load_policy(output.parent,policy_path) if policy_path.exists() else None
        if release_policy and policy is None:raise RuntimeError('Release policy missing')
        if policy:
            inspect_evidence(output.parent,policy)
            src=policy['scientific_source']['source_sha256'];env=policy['scientific_environment']
            accepted=set(policy['accepted_artifacts'])
        else:
            require_source_archive(output,source_state())
            src=source_state()['source_sha256'];env=environment_signature();accepted=None
        check_before_collection(output.parent,accepted)
        staging=Path(tempfile.mkdtemp(prefix='tables.',suffix='.partial',dir=output))
        marker=output/'collection_in_progress.json'
        try:
            metadata=_collect_tables(output,staging,src,env,accepted)
            if utility['source_sha256']!=source_state()['source_sha256'] or utility_env!=environment_signature():
                raise RuntimeError('Utility source/environment changed during collection')
            atomic_json(marker,{'staging':staging.name,'utility_source_sha256':source_state()['source_sha256']})
            publish_tables(staging,output/'tables')
            contract=publish_manifest_mirrors(output.parent,accepted)
            atomic_json(output/'release/collection.json',{
                'release':'V5.0.2','created_utc':utc_now(),'utility_source_sha256':source_state()['source_sha256'],
                'utility_environment_signature':environment_signature(),'policy_sha256':digest(policy) if policy else None,
                'scientific_source_sha256':src,'scientific_environment_signature':env,
                'result_manifest_contract':contract,
                'tables':{p.name:file_sha(p) for p in sorted((output/'tables').iterdir()) if p.is_file()}})
            marker.unlink()
            return metadata
        finally:
            shutil.rmtree(staging)

def _collect_tables(output,dest,src,env,accepted_ids):
    frame=manifest_frame(output,src,env,accepted_ids)
    if frame.empty:raise RuntimeError('No runs to collect')
    frame.to_csv(dest/'v5_result_manifest.csv',index=False)
    eligible=frame[(frame.status=='complete')&frame.current_source_environment].copy()
    # Immutable retries stay in the all-run manifest; one record per exact config in analysis.
    for cid,g in eligible.groupby('config_id'):
        if g.prediction_trace_semantic_sha256.nunique()!=1:raise RuntimeError(f'Nondeterministic repeated configuration {cid}')
    eligible=eligible.sort_values('run_id').drop_duplicates('config_id')
    for stage,name in [('primary','primary'),('windows','window'),('delays','delay'),('robustness','robustness')]:
        eligible[eligible.stage==stage].to_csv(dest/f'v5_{name}_results.csv',index=False)
    eligible[(eligible.stage.isin(['primary','robustness']))&(eligible.method.isin(['Meta','RecentError']))].to_csv(dest/'v5_routing_baselines.csv',index=False)
    summaries=[]
    repeated=eligible[eligible.stage.isin(['robustness','windows','delays'])]
    for keys,g in repeated.groupby(['stage','design','dataset','method','window','delay']):
        for metric in ('roc_auc_exact','roc_auc_streaming_approx','f1','cohen_kappa'):
            summaries.append(dict(zip(['stage','design','dataset','method','window','delay'],keys),metric=metric,**interval(g[metric])))
    pd.DataFrame(summaries).to_csv(dest/'v5_repeated_summary.csv',index=False)
    effects=[]
    for (design,dataset),g in eligible[eligible.stage=='robustness'].groupby(['design','dataset']):
        p=g.pivot(index='replicate',columns='method',values='roc_auc_exact')
        if 'Meta' in p and 'RecentError' in p:
            d=(p.Meta-p.RecentError).dropna()
            effects.append({'design':design,'dataset':dataset,'contrast':'Meta minus RecentError exact AUC',**interval(d),
                            'interval_assumption':'independent repetition differences; Student t, small n; no transaction resampling'})
    pd.DataFrame(effects).to_csv(dest/'v5_paired_router_effects.csv',index=False)
    primary=eligible[eligible.stage=='primary'];ranking={'status':'incomplete','nemenyi_performed':False}
    if len(primary)==len(DATASETS)*len(METHODS):
        m=primary.pivot(index='dataset',columns='method',values='roc_auc_exact').loc[list(DATASETS),list(METHODS)]
        ranks=m.rank(axis=1,ascending=False,method='average')
        ranks.to_csv(dest/'v5_primary_ranks.csv');ranks.mean().rename('mean_rank').to_csv(dest/'v5_primary_mean_ranks.csv')
        f=stats.friedmanchisquare(*(m[k].to_numpy() for k in METHODS))
        ranking={'status':'complete','n_datasets':len(DATASETS),'n_methods':len(METHODS),'metric':'roc_auc_exact',
                 'friedman_statistic':float(f.statistic),'friedman_p_asymptotic':float(f.pvalue),
                 'caveat':'six dataset blocks; asymptotic approximation; not independent transaction-level replication',
                 'nemenyi_predeclared_gate':.05,'nemenyi_performed':bool(f.pvalue<.05)}
        post=[]
        if f.pvalue<.05:
            k=len(METHODS);se=np.sqrt(k*(k+1)/(6*len(DATASETS)));av=ranks.mean()
            for a,b in combinations(METHODS,2):
                q=abs(av[a]-av[b])/se
                post.append({'method_a':a,'method_b':b,'mean_rank_difference':float(av[a]-av[b]),
                             'nemenyi_p':float(stats.studentized_range.sf(q*np.sqrt(2),k,np.inf))})
        pd.DataFrame(post,columns=['method_a','method_b','mean_rank_difference','nemenyi_p']).to_csv(dest/'v5_nemenyi.csv',index=False)
    atomic_json(dest/'v5_across_dataset_statistics.json',ranking)
    # Cross-stage controls for EVERY synthetic repetition, independent of final performance.
    controls=[]
    for i in range(10**6):
        g=eligible[(eligible.dataset=='Agrawal')&(eligible.method=='Meta')&(eligible.replicate==i)&(eligible.window==100)&(eligible.delay==0)&eligible.stage.isin(['primary','robustness','windows','delays'])]
        if g.empty:
            if i>=int(eligible.replicate.max()):break
            continue
        if i>0:g=g[g.stage!='primary']
        if g.stream_sha256.nunique()!=1 or g.prediction_trace_semantic_sha256.nunique()!=1:raise RuntimeError(f'Cross-stage control discrepancy at replicate {i}')
        controls.append({'replicate':i,'n_arms':len(g),'stages':','.join(sorted(g.stage.unique())),'stream_sha256':g.stream_sha256.iloc[0],'prediction_trace_semantic_sha256':g.prediction_trace_semantic_sha256.iloc[0],'equal':True})
    pd.DataFrame(controls).to_csv(dest/'v5_cross_stage_controls.csv',index=False)
    xr=[];events=[];resource=[]
    for _,r in eligible.iterrows():
        path=output/r.run_path
        if r.stage=='xai':
            xr.append(r.to_dict())
            for e in json.loads((path/'xai_events.json').read_text()):
                event={'run_id':r.run_id,'dataset':r.dataset,'replicate':r.replicate,'design':r.design,**{k:v for k,v in e.items() if not isinstance(v,(list,dict))}}
                if e['success']:
                    t=e['tier2'];im=pd.read_csv(path/e['event_dir']/'tier2_importance.csv')
                    corr=[v['spearman_importance'] for v in t['fold_stability'] if v['spearman_importance'] is not None]
                    event.update(roc_auc_exact_oof=t['roc_auc_exact_oof'],permutation_p_one_sided=t['permutation_p_one_sided'],
                                 top_feature=im.sort_values('mean_abs_shap_raw_margin',ascending=False).feature.iloc[0],
                                 mean_fold_importance_spearman=float(np.mean(corr)) if corr else None)
                events.append(event)
        if r.stage=='resource':
            rs=json.loads((path/'resource_summary.json').read_text())
            for phase in ('all','warmup','after_warmup'):
                for timer,values in rs[phase].items():
                    if timer=='n':continue
                    resource.append({'run_id':r.run_id,'dataset':r.dataset,'method':r.method,'arm':r.arm,'resource_repeat':r.resource_repeat,
                                     'hardware_fingerprint':r.get('hardware_fingerprint'),'phase':phase,'phase_n':rs[phase]['n'],'timer':timer,**values,
                                     'maximum_sampled_rss_MiB':rs['maximum_sampled_rss_MiB'],'rss_delta_MiB':rs['rss_delta_MiB'],
                                     'model_initialization_time_ms':rs['model_initialization_time_ms']})
    pd.DataFrame(xr).to_csv(dest/'v5_xai_runs.csv',index=False);pd.DataFrame(events).to_csv(dest/'v5_xai_events.csv',index=False)
    pd.DataFrame(resource).to_csv(dest/'v5_resource_results.csv',index=False)
    metadata={'created_utc':utc_now(),'source_sha256':src,'environment_signature':env,'all_runs':len(frame),'current_unique_completed_configs':len(eligible),
              'table_sources':'outputs/runs/*/summary.json, config, provenance, resource and XAI evidence',
              'replication':'real_learner and synthetic_data are distinct designs','primary_metric':'roc_auc_exact'}
    atomic_json(dest/'table_metadata.json',metadata)
    return metadata
