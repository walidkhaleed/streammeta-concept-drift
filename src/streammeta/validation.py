"""Acceptance gates and independent recomputation from archived raw evidence."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from .config import RunConfig
from .io import atomic_json, file_sha, digest
from .metrics import recompute
from .provenance import project_root, source_state, environment_signature, write_gate, utc_now

METRICS=('roc_auc_exact','roc_auc_streaming_approx','f1','cohen_kappa','river_f1','river_cohen_kappa')

def run_tests(output):
    root=project_root();dest=Path(output)/'validation';dest.mkdir(parents=True,exist_ok=True)
    before=source_state()['source_sha256'];env=environment_signature()
    command=[sys.executable,'-m','pytest','-ra','--junitxml',str(dest/'test_results.xml'),str(root/'tests')]
    r=subprocess.run(command,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    report='COMMAND: '+' '.join(command)+'\nSOURCE: '+before+'\nENVIRONMENT: '+env+'\n\n'+r.stdout
    (dest/'v5_test_report.txt').write_text(report);(root/'v5_test_report.txt').write_text(report)
    xml=ET.parse(dest/'test_results.xml').getroot()
    suites=[xml] if xml.tag=='testsuite' else list(xml)
    counts={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    if r.returncode or counts['tests']<15 or counts['failures'] or counts['errors'] or counts['skipped']:
        raise RuntimeError(f'Test gate failed: {counts}; see {dest}/v5_test_report.txt')
    if before!=source_state()['source_sha256'] or env!=environment_signature():raise RuntimeError('Source/environment changed during tests')
    details={'counts':counts,'report':str((dest/'v5_test_report.txt').relative_to(output)),'report_sha256':file_sha(dest/'v5_test_report.txt')}
    write_gate(output,'tests',details);return details

def current_runs(output):
    src=source_state()['source_sha256'];env=environment_signature();rows=[]
    for p in sorted((Path(output)/'runs').glob('*/summary.json')):
        row=json.loads(p.read_text())
        if row['status']=='complete' and row['source_sha256']==src and row['environment_signature']==env:
            rows.append((p.parent,row))
    return rows

def validate_controls(output):
    expected={'primary_w100_tau0','window_w100_tau0','delay_tau0_w100'}
    found={}
    for path,row in current_runs(output):
        if row['stage']=='controlled' and row['arm'] in expected:found.setdefault(row['arm'],(path,row))
    if set(found)!=expected:raise RuntimeError('Three complete current-source matched controls required')
    data=list(found.values());ref_path,ref=data[0]
    reference=pd.read_parquet(ref_path/'predictions.parquet').drop(columns='run_id')
    rc=RunConfig.from_dict(json.loads((ref_path/'config.json').read_text())).control_signature()
    for path,row in data:
        c=RunConfig.from_dict(json.loads((path/'config.json').read_text()))
        if c.control_signature()!=rc:raise RuntimeError('Control configurations differ beyond experiment labels')
        if row['n']!=10000:raise RuntimeError('Controlled validation requires full 10,000-step Agrawal')
        for key in (*METRICS,'stream_sha256','prediction_trace_semantic_sha256','route_counts','manager_fit_count'):
            if row[key]!=ref[key]:raise RuntimeError(f'Control disagreement: {key}')
        pd.testing.assert_frame_equal(reference,pd.read_parquet(path/'predictions.parquet').drop(columns='run_id'),check_exact=True)
    details={'controls':[{k:r[k] for k in ('run_id','arm','n','stream_sha256','prediction_trace_semantic_sha256',*METRICS,'manager_fit_count')} for p,r in data],
             'every_prediction_field_equal':True,'configuration_equal_except_arm_and_stage':True}
    write_gate(output,'controls',details)
    pd.DataFrame(details['controls']).to_csv(Path(output)/'validation'/'matched_controls.csv',index=False)
    return details

def validate_run(path,s):
    artifacts=json.loads((path/'artifacts.json').read_text())
    actual={p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}
    if actual!=set(artifacts):raise RuntimeError(f'Artifact set mismatch: {s["run_id"]}')
    for rel,rec in artifacts.items():
        p=path/rel
        if p.stat().st_size!=rec['bytes'] or file_sha(p)!=rec['sha256']:raise RuntimeError(f'Artifact corruption: {p}')
    pred=pd.read_parquet(path/'predictions.parquet');up=pd.read_parquet(path/'updates.parquet');tim=pd.read_parquet(path/'timings.parquet');mem=pd.read_parquet(path/'memory_samples.parquet')
    n=s['n'];tau=s['delay'];t=np.arange(n)
    if len(pred)!=n or not np.array_equal(pred.transaction_id,t):raise RuntimeError('Prediction index/length mismatch')
    values=recompute(pred.y_true,pred.probability,s['auc_thresholds'])
    for k in METRICS:
        if s[k] is None and values[k] is None:continue
        if not np.isclose(s[k],values[k],rtol=0,atol=1e-12):raise RuntimeError(f'Metric mismatch: {k}')
    checks=[np.array_equal(pred.hard_prediction,(pred.probability>=.5).astype(int)),
            np.array_equal(pred.label_release_time,t+tau),np.array_equal(pred.queue_before_release,np.minimum(t+1,tau+1)),
            np.array_equal(pred.queue_after_release,np.minimum(t+1,tau)),
            np.array_equal(pred.verified_count_before,np.maximum(0,t-tau)),
            np.array_equal(pred.verified_count_after,np.maximum(0,t-tau+1)),
            len(up)==max(0,n-tau),s['unverified_at_end']==min(n,tau),s['max_pending_queue']==min(n,tau+1),
            np.all(tim.integrated_step_time_ns==tim.core_step_time_ns+tim.observer_time_ns),
            np.allclose(mem.rss_MiB,mem.rss_bytes/1024**2,rtol=0,atol=1e-12)]
    if not all(checks):raise RuntimeError(f'Causal/timing/memory invariant failed: {s["run_id"]}')
    if len(up):
        p=pred.iloc[up.transaction_id.to_numpy()]
        if not np.array_equal(up.verification_time,up.transaction_id+tau):raise RuntimeError('Release time mismatch')
        for historic,col in [('historical_p_hat','p_hat_hat'),('historical_p_arf','p_hat_arf')]:
            if not np.allclose(up[historic],p[col],equal_nan=True,rtol=0,atol=0):raise RuntimeError('Historical prediction changed')
        if s['method'] in ('Meta','RecentError'):
            target=(abs(up.y_true-up.historical_p_arf)<abs(up.y_true-up.historical_p_hat)).astype(int)
            if not np.array_equal(up.router_target,target):raise RuntimeError('Router target mismatch')
    if (path/'xai_events.json').exists():
        events=json.loads((path/'xai_events.json').read_text())
        successes=[e for e in events if e['success']]
        if s['n_xai_successful']!=len(successes):raise RuntimeError('XAI success count mismatch')
        for e in events:
            if e['event_sample_number_one_based']!=e['event_index_zero_based']+1:raise RuntimeError('Event index mismatch')
            if e['success']:
                v=e['tier2'];now=e['xai_event_time']
                if not (v['older_index_range'][1]<v['newer_index_range'][0]<=v['newer_index_range'][1]<=now):raise RuntimeError('Future XAI window')
                if e['router_snapshot_sha256']!=file_sha(path/e['event_dir']/'tier1_router.ubj'):raise RuntimeError('Snapshot mismatch')
    return {'run_id':s['run_id'],'passed':True,'n':n,'recomputed_metrics':values}

def validate_evidence(output,release_policy=None):
    if release_policy or (Path(output).parent/'provenance/release_policy.json').exists():
        from .release import validate_release
        return validate_release(output,release_policy)
    rows=current_runs(output)
    if not rows:raise RuntimeError('No current-source runs')
    result=[]
    for i,(path,s) in enumerate(rows,1):
        result.append(validate_run(path,s))
        if i%20==0:print(json.dumps({'validated_runs':i,'total':len(rows)}),flush=True)
    details={'created_utc':utc_now(),'validated_runs':len(rows),'source_sha256':source_state()['source_sha256'],'rows':result}
    atomic_json(Path(output)/'validation'/'evidence_validation.json',details)
    write_gate(output,'evidence',{'validated_runs':len(rows),'validation_sha256':file_sha(Path(output)/'validation'/'evidence_validation.json')})
    return {'validated_runs':len(rows),'passed':True}
