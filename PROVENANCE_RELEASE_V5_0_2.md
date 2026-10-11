# V5.0.2 D01 integrity patch

V5.0.2 is a provenance/package integrity patch derived from V5.0.1. No accepted scientific artifact, numerical result, run ID, experiment configuration, or manuscript content changed.

The canonical manifest is `outputs/tables/v5_result_manifest.csv`. The exact declared root mirror set is `v5_result_manifest.csv`, `v5_0_1_result_manifest.csv`, and `v5_0_2_result_manifest.csv`. Every root `*result_manifest*.csv` entry must belong to this set. Historical copies live under `provenance/`. All three mirrors must be regular files, byte-identical to the canonical CSV. This binds schema, column order, row order, IDs, every cell and formatting without numeric tolerance. Verifiers never repair a discrepancy.

The manifest contract records the canonical path/hash, declared mirror paths/hashes, equality status, schema digest, row count, run-sequence digest and run-set digest. Collection records the full contract; validation binds it in its payload and gate; sealing and frozen verification independently recompute it and compare it with the bound contract. Inventory is a byte catalogue, not a semantic correctness certificate: its normal regeneration cannot legitimize a bad mirror.

Publication keeps the V5.0.1 local POSIX lock protocol and incomplete-generation marker across table AND root-mirror replacement. An existing complete generation must pass its manifest check before recollection. An explicit incomplete generation may be rebuilt from authenticated runs, preserving the marker until all tables and mirrors finish; unknown mirror names still fail. Atomicity is per file under cooperating-process locks. No arbitrary-reader directory snapshot or multi-node guarantee is added.

## Reproduce without scientific experiments

Extract the code ZIP and clone its `source.git.bundle` into a new checkout. Use the recorded Python/dependency environment. Set `PYTHONPATH` to that checkout's `src`, `STREAMMETA_PROJECT_ROOT` to the checkout, `PYTHONHASHSEED=0` and all five native thread variables to `1`.

```bash
python experiments/refreeze_v5_0_2.py stage --base-archive /path/v5_0_1_reproducibility_bundle.zip --destination /path/new_v5_0_2
python experiments/refreeze_v5_0_2.py tests --release-root /path/new_v5_0_2
python -m streammeta.cli collect --output /path/new_v5_0_2/outputs
python experiments/refreeze_v5_0_2.py compare --release-root /path/new_v5_0_2
python -m streammeta.cli validate-evidence --output /path/new_v5_0_2/outputs
```

The stage command refuses an existing destination and checks the exact V5.0.1 archive hash. Its old freeze, inventory, source bundle, validation/test gates and policy are retained under `provenance/v5_0_1/`. Original scientific runs keep the V5 source identity. The old V5.0.1 `experiments/refreeze_v5.py` and `PROVENANCE_RELEASE.md` are historical instructions; use the V5.0.2 entry point above.

Run the full-payload D01 probes in disposable copies and include their results plus release documentation before final inventory. Then:

```bash
python experiments/refreeze_v5_0_2.py check-manifests --release-root /path/new_v5_0_2
python -m streammeta.cli inventory --output /path/new_v5_0_2/outputs
python -m streammeta.cli freeze --output /path/new_v5_0_2/outputs
python -m streammeta.cli verify-package --output /path/new_v5_0_2/outputs
```

The test command separately runs the unchanged 33 scientific tests, all 43 V5.0.1 provenance/publication cases (only their shared fixture is extended), 49 new D01 cases, the full 125-test suite, and the combined 92-test provenance suite. Raw-evidence validation reads archived predictions; it does not fit a replacement model. Do not run `run_staged.sh`, `run`, `campaign`, `task`, or new stream preparation for this patch.

The canonical CSV and old mirrors retain the V5.0.1 content hash. V5.0.2 adds a versioned mirror path and a new release-bound manifest-contract identity; it does not manufacture a new CSV byte hash by changing scientific values or formatting. Source, commit, inventory, freeze, code ZIP and full package receive new hashes. The four inherited stream partial files remain unchanged and non-active. `science_campaigns_run = 0`; `manuscript_updated = false`.

V5.0.2 requires one final narrow independent delta audit. Approval for manuscript reconstruction is a later decision.
