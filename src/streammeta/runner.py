"""One immutable experiment per run directory. Failures are preserved, not hidden."""
from pathlib import Path
import gc
import inspect
import json
import logging
import os
import sys
import time
import traceback
import uuid
import numpy as np
import pandas as pd
import psutil
from threadpoolctl import threadpool_limits
from river import tree,forest,ensemble,preprocessing
from .config import RunConfig
from .engine import run_engine
from .io import atomic_json,digest,file_sha
from .models import OnlineLearner
from .provenance import check_gates,source_state,capture_environment,require_source_archive,command,utc_now
from .streams import materialize,read_stream
from .system import self_rss_bytes

TIMER_CONTRACT={
    "meta_state_time_ns":"verified-state construction; no labels consumed",
    "selected_prediction_time_ns":"router/selector decision plus selected expert predict_proba; state construction excluded",
    "response_path_time_ns":"wall interval starting before state construction and ending after selected prediction",
    "shadow_prediction_time_ns":"nonselected expert probability under original prediction-time model state; zero for standalone learners",
    "update_time_ns":"sum of verified manager/state/expert updates, including bounded refits; excludes observer",
    "observer_time_ns":"input XAI buffer maintenance and verified-error ADWIN/explanation/snapshot work",
    "integrated_step_time_ns":"wall interval from before learner.predict through enqueue/release and all synchronous observer and learning work",
    "core_step_time_ns":"integrated interval minus measured observer intervals; a residual core-work interval, not a concurrent throughput estimate",
    "acquisition_time_ns":"next observation from already materialized local gzip stream; excludes initial materialization/download",
    "evaluator_metric_time_ns":"evaluator-only streaming AUC/F1/kappa update after learning, using stored pre-update probability",
    "excluded_from_core_and_integrated":"initialization, stream hashing/acquisition, evaluator metrics, prediction/trace hashing, trace buffer/write I/O, RSS sampling, final exact metrics and summary export",
    "memory":"process RSS, including imports and bounded evidence buffers, in bytes and MiB; maximum sampled every configured stride, not a continuous maximum",
    "warmup":"first configured observations are reported separately; no model reset after warmup; all observations retained in predictive metrics"
}

def config_id(c):return digest(c.to_dict())

def hardware_fingerprint(env):
    # CPU utilization/frequency fields from lscpu vary by run and are not hardware identity.
    stable={k:env[k] for k in ('hostname','platform','kernel','machine','logical_cpu_count','physical_cpu_count','affinity','ram_bytes','cgroup')}
    try:
        cpu=json.loads(env['lscpu']['stdout'])['lscpu']
        wanted={'Architecture:','Vendor ID:','Model name:','CPU family:','Model:','Stepping:','Thread(s) per core:','Core(s) per socket:','Socket(s):'}
        stable['cpu_identity']={r['field']:r['data'] for r in cpu if r['field'] in wanted}
    except (KeyError,ValueError,TypeError):stable['cpu_identity']='unavailable; see hardware manifest'
    return digest(stable)

def timing_summary(path):
    df=pd.read_parquet(path);out={}
    for label,part in [("all",df),("warmup",df[df.phase=="warmup"]),("after_warmup",df[df.phase=="after_warmup"])]:
        out[label]={"n":len(part)}
        for col in df:
            if col.endswith('_ns'):
                a=part[col].to_numpy()/1e6
                out[label][col.replace('_ns','_ms')]={"mean":float(np.mean(a)) if len(a) else None,
                    "median":float(np.median(a)) if len(a) else None,"p95":float(np.percentile(a,95)) if len(a) else None,
                    "p99":float(np.percentile(a,99)) if len(a) else None,"maximum_observed":float(np.max(a)) if len(a) else None}
    return out

def run_job(c:RunConfig,output):
    from .publication import worker_session
    with worker_session(output):
        require_source_archive(output,source_state())
        return _run_job(c,output)

def _run_job(c:RunConfig,output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    check_gates(c,output)
    source=source_state();cid=config_id(c)
    run_id=f"{c.stage}-{c.dataset}-{c.method}-{cid[:10]}-{uuid.uuid4().hex[:8]}"
    # A completed run is published by directory rename, after all writers close.
    # Readers never see a partially written scientific result in outputs/runs.
    run_dir=output/'staging'/run_id;run_dir.mkdir(parents=True,exist_ok=False)
    final_dir=output/'runs'/run_id;final_dir.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(run_dir/'config.json',c.to_dict())
    provenance={"run_id":run_id,"config_id":cid,"started_utc":utc_now(),"command_line":sys.argv,
                "working_directory":os.getcwd(),"source":source,"status":"running"}
    atomic_json(run_dir/'provenance.json',provenance)
    log=logging.getLogger(run_id);log.setLevel(logging.INFO)
    handler=logging.FileHandler(run_dir/'run.log');handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));log.addHandler(handler)
    start=time.perf_counter_ns()
    try:
        log.info('Run started; old numerical results are not used')
        stream_path,stream_manifest=materialize(c,output/'streams')
        atomic_json(run_dir/'stream_manifest.json',stream_manifest)
        snapshot=require_source_archive(output,source)
        provenance['source_snapshot']=str(snapshot.relative_to(output));provenance['source_snapshot_sha256']=file_sha(snapshot)
        with threadpool_limits(limits=1):
            env=capture_environment();atomic_json(run_dir/'environment.json',env)
            freeze=command([sys.executable,'-m','pip','freeze'])
            if freeze.get('returncode')!=0:raise RuntimeError('Cannot capture package freeze')
            (run_dir/'requirements-freeze.txt').write_text(freeze['stdout']+'\n')
            atomic_json(run_dir/'timer_contract.json',TIMER_CONTRACT)
            defaults={name:{"signature":str(inspect.signature(cls))} for name,cls in [('HAT',tree.HoeffdingAdaptiveTreeClassifier),('ARF',forest.ARFClassifier),('SRP',ensemble.SRPClassifier),('StandardScaler',preprocessing.StandardScaler)]}
            atomic_json(run_dir/'resolved_model_defaults.json',defaults)
            gc.collect();baseline={"phase":"pre_model_initialization","monotonic_ns":time.perf_counter_ns(),"rss_bytes":self_rss_bytes()}
            baseline['rss_MiB']=baseline['rss_bytes']/1024**2
            init0=time.perf_counter_ns();learner=OnlineLearner(c);initialization_ns=time.perf_counter_ns()-init0
            observer=None;observer_init0=time.perf_counter_ns()
            if c.xai.enabled:
                from .xai import XAIObserver
                observer=XAIObserver(c,run_dir)
            observer_initialization_ns=time.perf_counter_ns()-observer_init0 if c.xai.enabled else 0
            atomic_json(run_dir/'initialization.json',{"baseline_memory":baseline,"model_initialization_time_ns":initialization_ns,
                        "observer_import_and_initialization_time_ns":observer_initialization_ns,"gc_collect_before_baseline":True})
            result=run_engine(c,read_stream(stream_path),run_id,run_dir,learner,observer)
        if result['stream_sha256']!=stream_manifest['stream_sha256']:
            raise RuntimeError('Consumed stream fingerprint differs from materialization')
        resource=timing_summary(run_dir/'timings.parquet')
        resource['model_initialization_time_ms']=initialization_ns/1e6
        resource['observer_import_and_initialization_time_ms']=observer_initialization_ns/1e6
        resource['baseline_rss_MiB']=baseline['rss_MiB']
        resource['maximum_sampled_rss_MiB']=max(result['maximum_sampled_rss_MiB'],baseline['rss_MiB'])
        resource['rss_delta_MiB']=resource['maximum_sampled_rss_MiB']-baseline['rss_MiB']
        atomic_json(run_dir/'resource_summary.json',resource)
        summary={"run_id":run_id,"config_id":cid,"status":"complete","stage":c.stage,"dataset":c.dataset,
                 "method":c.method,"arm":c.arm,"design":c.design,"replicate":c.replicate,"resource_repeat":c.resource_repeat,
                 "window":c.window,"delay":c.delay,**{k+'_seed':v for k,v in c.to_dict()['seeds'].items()},
                 "source_sha256":source['source_sha256'],"environment_signature":env['environment_signature'],
                 "hardware_fingerprint":hardware_fingerprint(env),
                 "evidence_eligible":c.stage not in ('smoke','controlled'),"stream_cache_id":stream_manifest['cache_id'],
                 **result,"started_utc":provenance['started_utc'],"finished_utc":utc_now(),
                 "run_wall_seconds":(time.perf_counter_ns()-start)/1e9}
        atomic_json(run_dir/'summary.json',summary)
        provenance.update(status='complete',finished_utc=summary['finished_utc'])
        atomic_json(run_dir/'provenance.json',provenance)
        log.info('Complete n=%s exact_auc=%s stream=%s',summary['n'],summary['roc_auc_exact'],summary['stream_sha256'])
        handler.flush();handler.close();log.removeHandler(handler)
        artifacts={str(p.relative_to(run_dir)):{"sha256":file_sha(p),"bytes":p.stat().st_size} for p in sorted(run_dir.rglob('*')) if p.is_file()}
        atomic_json(run_dir/'artifacts.json',artifacts)
        os.replace(run_dir,final_dir)
        return summary
    except Exception as e:
        log.error('Run failed\n%s',traceback.format_exc());handler.flush();handler.close();log.removeHandler(handler)
        provenance.update(status='failed',finished_utc=utc_now(),error=f'{type(e).__name__}: {e}')
        atomic_json(run_dir/'provenance.json',provenance)
        atomic_json(run_dir/'failure.json',{"error":str(e),"traceback":traceback.format_exc()})
        if run_dir.exists() and not final_dir.exists():os.replace(run_dir,final_dir)
        raise
