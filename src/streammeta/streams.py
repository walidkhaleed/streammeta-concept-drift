"""Single canonical stream factory and immutable materializations.

Every synthetic wrapper is explicitly seeded. No executable legacy path exists.
Fingerprint = SHA-256 of newline-delimited canonical [features, binary_label].
"""
from pathlib import Path
import fcntl
import gzip
import hashlib
import inspect
import itertools
import math
import os
import tempfile
import numpy as np
import river
from river import datasets
from river.datasets import synth
from .config import RunConfig, derived_seed
from .io import atomic_json, canonical, digest, file_sha

def binary_label(y):
    # Fixed semantic mapping, never a first-observed-label map.
    if isinstance(y, (bool, np.bool_)):
        return int(y)
    if isinstance(y, (int, float, np.integer, np.floating)) and y in (0, 1):
        return int(y)
    raise ValueError(f"Unsupported binary label {y!r}; add an explicit dataset mapping")

def canonical_observation(x, y):
    clean = {}
    for k in sorted(x):
        value = x[k]
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite raw feature; specify a causal preprocessing policy")
        clean[str(k)] = value
    return canonical([clean, binary_label(y)]) + b"\n"

def stream_definition(c: RunConfig):
    seed = c.seeds.data
    if c.dataset in ("Elec2", "Phishing"):
        return {"dataset": c.dataset, "river": river.__version__, "n": c.length,
                "kind": "native_order_real", "drift_annotation": None,
                "label_mapping": "bool or numeric 0/1 -> fixed integer 0/1"}
    base = {"dataset": c.dataset, "river": river.__version__, "n": c.length,
            "data_seed": seed, "label_mapping": "fixed integer 0/1"}
    if c.dataset == "Hyperplane":
        return {**base, "kind": "incremental_label_rule", "generator": "Hyperplane",
                "kwargs": {"seed": seed, "n_features": 10, "n_drift_features": 3, "mag_change": 0.001}}
    if c.dataset.endswith("Control"):
        return {**base, "kind": {"NullControl":"stationary_null", "LabelRuleControl":"label_rule_drift", "CovariateControl":"covariate_drift", "MixedControl":"mixed_drift"}[c.dataset],
                "generator": "v5_gaussian_control", "position": 3000, "n_features": 5,
                "covariate_shift_feature": "x0", "mean_shift": 1.5,
                "label_rule": "x1+0.25*x2>0; label-rule/mixed reverse sign after center"}
    name, selector, position = {"Agrawal":("Agrawal","classification_function",4000), "SEA":("SEA","variant",5000), "STAGGER":("STAGGER","classification_function",5000)}[c.dataset]
    return {**base, "kind": "width_controlled_concept_transition", "generator": "ConceptDriftStream",
            "pre_generator": name, "post_generator": name,
            "pre_kwargs": {selector: 0, "seed": seed},
            "post_kwargs": {selector: 2, "seed": derived_seed(seed,"post_data")},
            "wrapper_kwargs": {"position": position, "width": 50, "seed": c.seeds.wrapper}}

def make_stream(c: RunConfig):
    d = stream_definition(c)
    if c.dataset == "Elec2":
        return datasets.Elec2()
    if c.dataset == "Phishing":
        return datasets.Phishing()
    if c.dataset == "Hyperplane":
        return synth.Hyperplane(seed=c.seeds.data, n_features=10, n_drift_features=3, mag_change=0.001)
    if c.dataset.endswith("Control"):
        def generate():
            rng = np.random.default_rng(c.seeds.data)
            for t in range(c.length):
                v = rng.normal(size=5)
                if t >= 3000 and c.dataset in ("CovariateControl", "MixedControl"):
                    v[0] += 1.5
                score = v[1] + .25*v[2]
                if t >= 3000 and c.dataset in ("LabelRuleControl", "MixedControl"):
                    score = -score
                yield {f"x{i}": float(a) for i, a in enumerate(v)}, int(score > 0)
        return generate()
    factory = getattr(synth, d["pre_generator"])
    return synth.ConceptDriftStream(stream=factory(**d["pre_kwargs"]),
                                   drift_stream=factory(**d["post_kwargs"]), **d["wrapper_kwargs"])

def fingerprint(rows):
    h = hashlib.sha256()
    for x, y in rows:
        h.update(canonical_observation(x, y))
    return h.hexdigest()

def materialize(c, root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    definition = stream_definition(c); key = digest(definition)
    dest = root/key; dest.mkdir(exist_ok=True)
    manifest_path = dest/"manifest.json"; data_path = dest/"observations.jsonl.gz"
    with open(dest/".lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_path.exists():
            import json
            manifest = json.loads(manifest_path.read_text())
            if manifest["definition"] != definition or manifest["file_sha256"] != file_sha(data_path):
                raise RuntimeError("Stream cache corruption or configuration collision")
            return dest, manifest
        stream = make_stream(c); h = hashlib.sha256(); count = 0; feature_schema = None
        fd, tmp = tempfile.mkstemp(dir=dest, suffix=".partial"); os.close(fd)
        try:
            with open(tmp,"wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                for x, y in itertools.islice(iter(stream), c.length):
                    line = canonical_observation(x, y); h.update(line); gz.write(line); count += 1
                    if feature_schema is None:
                        feature_schema = {str(k):type(v).__name__ for k,v in sorted(x.items())}
            if count != c.length:
                raise RuntimeError(f"Stream truncated: {count} != {c.length}")
            os.replace(tmp,data_path)
            extra = {"constructor_signature": str(inspect.signature(type(stream)))}
            if c.dataset in ("Elec2", "Phishing"):
                path = getattr(stream,"path",None)
                extra.update({"upstream_url":str(getattr(stream,"url",None)), "upstream_path":str(path),
                              "upstream_file_sha256":file_sha(path) if path and Path(path).is_file() else None})
            manifest = {"cache_id":key,"definition":definition,"n":count,"stream_sha256":h.hexdigest(),
                        "file_sha256":file_sha(data_path),"feature_schema":feature_schema,"source":extra,
                        "canonical_format":"sorted-key compact UTF-8 JSON [x,y] plus LF; NaN forbidden"}
            atomic_json(manifest_path,manifest)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return dest, manifest

def read_stream(path):
    import json
    with gzip.open(Path(path)/"observations.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            x,y = json.loads(line)
            yield x,y

