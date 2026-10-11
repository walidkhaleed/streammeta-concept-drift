"""Canonical hashing, atomic records, and bounded Parquet evidence writers."""
from pathlib import Path
import hashlib
import json
import math
import os
import tempfile
import pyarrow as pa
import pyarrow.parquet as pq

def jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "item"):
        return jsonable(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj

def canonical(obj):
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()

def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()

def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def atomic_json(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(jsonable(obj), f, indent=2, sort_keys=True, allow_nan=False)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

class ParquetSink:
    def __init__(self, path, schema, batch_size=1024):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=self.path.name+'.',suffix='.partial',dir=self.path.parent);os.close(fd)
        self.staging_path=Path(name)
        self.schema = schema; self.rows = []; self.batch_size = batch_size
        self.writer = pq.ParquetWriter(self.staging_path, schema, compression="zstd")
        self.count = 0

    def append(self, row):
        self.rows.append(row); self.count += 1
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self):
        if self.rows:
            self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
            self.rows.clear()

    def close(self):
        self.flush(); self.writer.close()
        with open(self.staging_path,'rb') as f:os.fsync(f.fileno())
        os.replace(self.staging_path,self.path)

PRED_SCHEMA = pa.schema(
    [(x, pa.string()) for x in ("run_id", "dataset", "method", "route", "selected_expert")]
    + [(x, pa.int64()) for x in ("transaction_id", "sample_number", "data_seed", "wrapper_seed", "model_seed", "router_seed", "window", "delay", "y_true", "hard_prediction", "y_hat", "manager_fit_count", "manager_fit_count_after_step", "verified_count_before", "verified_count_after", "label_release_time", "queue_before_release", "queue_after_release", "released_this_step")]
    + [(x, pa.float64()) for x in ("probability", "p_hat", "p_hat_hat", "p_hat_arf", "q_arf", "meta_class_1_ratio", "meta_hat_error", "meta_arf_error", "meta_disagreement")]
    + [(x, pa.bool_()) for x in ("meta_state_available", "manager_trained", "manager_trained_after_step", "label_verified_by_end")]
)
UPDATE_SCHEMA = pa.schema(
    [("run_id", pa.string()), ("route", pa.string())]
    + [(x, pa.int64()) for x in ("transaction_id", "verification_time", "y_true", "router_target", "fit_count_before", "fit_count_after")]
    + [(x, pa.float64()) for x in ("historical_p_hat", "historical_p_arf", "meta_class_1_ratio", "meta_hat_error", "meta_arf_error", "meta_disagreement")]
)
TIMING_FIELDS = ("acquisition_time_ns", "meta_state_time_ns", "selected_prediction_time_ns", "response_path_time_ns", "shadow_prediction_time_ns", "update_time_ns", "observer_time_ns", "core_step_time_ns", "integrated_step_time_ns", "evaluator_metric_time_ns")
TIMING_SCHEMA = pa.schema([("run_id", pa.string()), ("transaction_id", pa.int64()), ("phase", pa.string())] + [(x, pa.int64()) for x in TIMING_FIELDS])
MEMORY_SCHEMA = pa.schema([("run_id", pa.string()), ("transaction_id", pa.int64()), ("monotonic_ns", pa.int64()), ("phase", pa.string()), ("rss_bytes", pa.int64()), ("rss_MiB", pa.float64())])
