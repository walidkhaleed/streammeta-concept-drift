"""Canonical causal engine shared by primary, ablation, XAI and profiling jobs.

Core interval = integrated interval minus explicitly timed observer intervals.
The observer executes synchronously before the release's learning update.
Acquisition, evaluator metrics, evidence buffering/I/O, RSS sampling, and offline
exact metric calculation are outside both intervals and separately documented.
"""
from collections import deque
from pathlib import Path
import gc
import hashlib
import json
import time
import numpy as np
import pandas as pd
import psutil
from .io import ParquetSink,PRED_SCHEMA,UPDATE_SCHEMA,TIMING_SCHEMA,MEMORY_SCHEMA,canonical
from .metrics import Metrics
from .models import OnlineLearner, expert_class
from .streams import canonical_observation
from .system import self_rss_bytes

META_COLS=("meta_class_1_ratio","meta_hat_error","meta_arf_error","meta_disagreement")

class DelayedLabels:
    """Evaluator-owned label store; neither learner nor prediction context sees it."""
    def __init__(self):self.labels={}
    def arrive(self,t,y):self.labels[t]=int(y)
    def release(self,t):return self.labels.pop(t)

class MemorySink:
    """Test-only recorder implementing the disk writer interface."""
    def __init__(self):self.rows=[];self.count=0
    def append(self,row):self.rows.append(dict(row));self.count+=1
    def close(self):pass

def run_engine(c,rows,run_id,run_dir=None,learner=None,observer=None,clock=time.perf_counter_ns,order_hook=None):
    learner=learner or OnlineLearner(c,clock)
    if run_dir is None:
        pred,updates,timings,memory=(MemorySink() for _ in range(4))
    else:
        root=Path(run_dir)
        pred=ParquetSink(root/"predictions.parquet",PRED_SCHEMA)
        updates=ParquetSink(root/"updates.parquet",UPDATE_SCHEMA)
        timings=ParquetSink(root/"timings.parquet",TIMING_SCHEMA)
        memory=ParquetSink(root/"memory_samples.parquet",MEMORY_SCHEMA)
    initial_rss=self_rss_bytes()
    metric=Metrics(c.auc_thresholds);pending=deque();labels=DelayedLabels()
    stream_hash=hashlib.sha256();prediction_hash=hashlib.sha256()
    max_queue=0;route_counts={};n=0;rss_peak=initial_rss
    iterator=iter(rows);buffer_write_ns=0
    def note(event,t):
        if order_hook:order_hook(event,t)
    def sample(t,phase):
        nonlocal rss_peak
        rss=self_rss_bytes();rss_peak=max(rss_peak,rss)
        memory.append({"run_id":run_id,"transaction_id":t,"monotonic_ns":clock(),"phase":phase,"rss_bytes":rss,"rss_MiB":rss/1024**2})
    sample(-1,"post_model_initialization")
    try:
        for t in range(c.length):
            acq0=clock()
            try:x,y=next(iterator)
            except StopIteration:raise RuntimeError(f"Input ended at {t}, expected {c.length}")
            acquisition_ns=clock()-acq0
            stream_hash.update(canonical_observation(x,y))
            integrated0=clock();ctx,phase=learner.predict(x,t);note("predict",t)
            # Only after prediction is hidden y stored in the evaluator-owned oracle.
            labels.arrive(t,y);pending.append(ctx);note("enqueue",t)
            peak_this=len(pending);max_queue=max(max_queue,peak_this)
            observer_ns=0;update_ns=0;release_records=[]
            if observer:
                start=clock();observer.observe_input(ctx);observer_ns+=clock()-start
            while pending and pending[0].t+c.delay<=t:
                old=pending.popleft();verified=labels.release(old.t);note("release",old.t)
                if observer:
                    start=clock();note("detector_xai",old.t)
                    observer.on_release(old,verified,t,learner);observer_ns+=clock()-start
                before=learner.fit_count;start=clock();z=learner.verify(old,verified);update_ns+=clock()-start;note("learn",old.t)
                release_records.append({"run_id":run_id,"route":old.route,"transaction_id":old.t,
                    "verification_time":t,"y_true":verified,"router_target":z,"fit_count_before":before,"fit_count_after":learner.fit_count,
                    "historical_p_hat":old.p_hat,"historical_p_arf":old.p_arf,
                    **dict(zip(META_COLS,old.mf or (None,)*4))})
            integrated_ns=clock()-integrated0;core_ns=integrated_ns-observer_ns
            if core_ns<0:raise RuntimeError("Invalid clock/observer accounting")
            if len(pending)>c.delay:raise RuntimeError("Fixed-delay FIFO invariant violated")
            start=clock();metric.update(y,ctx.probability);metric_ns=clock()-start
            record={"run_id":run_id,"dataset":c.dataset,"method":c.method,"transaction_id":t,"sample_number":t+1,
                    "data_seed":c.seeds.data,"wrapper_seed":c.seeds.wrapper,"model_seed":c.seeds.model,"router_seed":c.seeds.router,
                    "window":c.window,"delay":c.delay,"y_true":int(y),"probability":ctx.probability,"p_hat":ctx.probability,
                    "hard_prediction":expert_class(ctx.probability),"y_hat":expert_class(ctx.probability),
                    "selected_expert":ctx.route,"route":ctx.route,"p_hat_hat":ctx.p_hat,"p_hat_arf":ctx.p_arf,"q_arf":ctx.q_arf,
                    "meta_state_available":ctx.mf is not None,"manager_trained":ctx.manager_trained,"manager_fit_count":ctx.fit_count,
                    "manager_trained_after_step":learner.trained,"manager_fit_count_after_step":learner.fit_count,
                    "verified_count_before":ctx.verified_count,"verified_count_after":learner.verified_count,
                    "label_release_time":t+c.delay,"label_verified_by_end":t+c.delay<c.length,
                    "queue_before_release":peak_this,"queue_after_release":len(pending),"released_this_step":len(release_records),
                    **dict(zip(META_COLS,ctx.mf or (None,)*4))}
            # Identity excludes only run ID; experiment names and wall clocks are not predictions.
            semantic={k:v for k,v in record.items() if k!='run_id'}
            prediction_hash.update(canonical(semantic)+b'\n')
            timing={"run_id":run_id,"transaction_id":t,"phase":"warmup" if t<c.warmup else "after_warmup",
                    "acquisition_time_ns":acquisition_ns,**phase,"update_time_ns":update_ns,
                    "observer_time_ns":observer_ns,"core_step_time_ns":core_ns,"integrated_step_time_ns":integrated_ns,
                    "evaluator_metric_time_ns":metric_ns}
            start=clock();pred.append(record);timings.append(timing)
            for rec in release_records:updates.append(rec)
            buffer_write_ns+=clock()-start
            if t%c.memory_every==0:sample(t,timing["phase"])
            route_counts[ctx.route]=route_counts.get(ctx.route,0)+1;n=t+1
        sample(n-1,"termination")
    finally:
        start=clock()
        for sink in (pred,updates,timings,memory):sink.close()
        buffer_write_ns+=clock()-start
    # Exact primary metric is explicitly calculated from the saved prediction trace.
    trace=pd.read_parquet(Path(run_dir)/"predictions.parquet") if run_dir else pd.DataFrame(pred.rows)
    result=metric.result(trace.y_true,trace.probability)
    result.update(n=n,stream_sha256=stream_hash.hexdigest(),prediction_trace_semantic_sha256=prediction_hash.hexdigest(),
                  max_pending_queue=max_queue,unverified_at_end=len(pending),manager_trained=learner.trained,
                  manager_fit_count=learner.fit_count,verified_count=learner.verified_count,
                  route_counts=route_counts,route_proportions={k:v/n for k,v in route_counts.items()},
                  engine_initial_rss_bytes=initial_rss,maximum_sampled_rss_bytes=rss_peak,
                  maximum_sampled_rss_MiB=rss_peak/1024**2,trace_buffer_and_write_time_ns=buffer_write_ns,
                  no_force_flush=True,warmup_n=min(n,c.warmup),after_warmup_n=max(0,n-c.warmup))
    if observer:result.update(observer.summary(n))
    if run_dir is None:result['_test_records']={"predictions":pred.rows,"updates":updates.rows,"timings":timings.rows,"memory":memory.rows}
    return result
