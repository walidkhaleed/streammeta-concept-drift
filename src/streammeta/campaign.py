"""Fresh subprocess scheduling; immutable run directories and resumable plans."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import uuid
from .config import RunConfig
from .io import atomic_json,digest
from .provenance import source_state,environment_signature,THREAD_VARS,archive_source

def completed_configs(output,source_hash,env):
    done={}
    for p in sorted((Path(output)/'runs').glob('*/summary.json')):
        s=json.loads(p.read_text())
        if s.get('status')=='complete' and s['source_sha256']==source_hash and s['environment_signature']==env:
            done.setdefault(s['config_id'],[]).append(s)
    for cid,rows in done.items():
        if len({r['prediction_trace_semantic_sha256'] for r in rows})>1:
            raise RuntimeError(f'Repeated identical config produced different predictions: {cid}')
    return done

def run_campaign(plan_path,output,workers=1,resume=True):
    from .publication import worker_session
    with worker_session(output):
        archive_source(output,source_state())
        return _run_campaign(plan_path,output,workers,resume)

def _run_campaign(plan_path,output,workers=1,resume=True):
    output=Path(output).resolve();plan=json.loads(Path(plan_path).read_text())
    if digest(plan['jobs'])!=plan['plan_sha256']:raise RuntimeError('Plan content hash mismatch')
    if plan['phase']=='resource' and workers!=1:
        raise ValueError('Resource experiments must be serial on one measured host; use independent exclusive nodes for HPC parallelism')
    source=source_state()['source_sha256'];env_sig=environment_signature()
    done=completed_configs(output,source,env_sig) if resume else {}
    jobs=[(i,c) for i,c in enumerate(plan['jobs']) if digest(c) not in done]
    stage_dir=output/'campaign_logs'/Path(plan_path).stem/uuid.uuid4().hex;stage_dir.mkdir(parents=True,exist_ok=False)
    print(json.dumps({'phase':plan['phase'],'planned':len(plan['jobs']),'to_run':len(jobs),'workers':workers}),flush=True)
    stop=threading.Event()
    def one(item):
        i,config=item;config_path=stage_dir/f'job_{i:05d}.json';atomic_json(config_path,config)
        if stop.is_set():return i,config,None
        env=dict(os.environ,PYTHONHASHSEED='0',**{k:'1' for k in THREAD_VARS})
        with open(stage_dir/f'job_{i:05d}.log','w') as log:
            r=subprocess.run([sys.executable,'-m','streammeta.cli','run','--config',str(config_path),'--output',str(output)],env=env,stdout=log,stderr=subprocess.STDOUT)
        if r.returncode:stop.set()
        return i,config,r.returncode
    failures=[];not_started=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(one,item):item for item in jobs}
        for j,f in enumerate(as_completed(futures),1):
            i,c,code=f.result()
            if code is None:not_started.append(i)
            elif code:failures.append(i)
            print(json.dumps({'finished':j,'of':len(jobs),'job':i,'dataset':c['dataset'],'method':c['method'],'exit_code':code}),flush=True)
    result={'phase':plan['phase'],'planned':len(plan['jobs']),'started':len(jobs)-len(not_started),'failed_jobs':failures,'not_started_after_failure':not_started,'source_sha256':source,'environment_signature':env_sig,'plan_sha256':plan['plan_sha256'],'invocation':stage_dir.name}
    atomic_json(stage_dir/'campaign_status.json',result)
    if failures:raise RuntimeError(f'{len(failures)} jobs failed; inspect {stage_dir}')
    return result
