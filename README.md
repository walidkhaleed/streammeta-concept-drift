# V5.0.2: D01 root-manifest integrity patch

Read `PROVENANCE_RELEASE_V5_0_2.md` for the current offline patch workflow. V5 and V5.0.1 remain immutable historical releases. No scientific experiment or manuscript change is part of this patch.

# V5.0.1 provenance/package release

V5.0.1 is a provenance/package correction derived from the audited V5 scientific evidence. No accepted scientific result was changed.

Start with [PROVENANCE_RELEASE.md](PROVENANCE_RELEASE.md) for patch reproduction and verification. The audited V5 runs retain their original source identity. The changed code is release utility code; these runs were not executed by V5.0.1. Do not run the historical campaign recipe to reproduce this patch. All original scientific traces, configurations, streams, figures and results are retained.

The material below describes the original scientific implementation and future experiment commands. In the full patch bundle, the original `outputs/v5_freeze_manifest.json`, `outputs/gates/`, `outputs/validation/`, `v5_test_report.txt` and `v5_validation_report.md` are historical V5 records. The current patch freeze is `v5_0_1_freeze_manifest.json`; its gates are `outputs/release/`. Only that freeze represents V5.0.1.

---

# StreamMeta V5

Canonical code and new evidence for the reconstruction requested on 14 September 2026. The historical manuscript is unchanged. Read `audit/issue_reconciliation.md` and the pre-outcome `audit/design_and_statistical_plan.md` first. This framework does not import V4 notebooks or target their numbers.

## Install and test

Use CPython 3.12 on Linux. Dependencies are fully pinned in `requirements-lock.txt`; XGBoost uses the CPU wheel. No GPU is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
python -m streammeta.cli test --output outputs
```

The CLI restarts itself when necessary to set `PYTHONHASHSEED=0` and native thread counts to one before numerical imports. Scientific jobs additionally use `threadpoolctl`. Per-run package freezes and hardware/NUMA/cgroup manifests are authoritative for the archived execution. The test suite checks causality, full-field matched controls, exact and approximate metrics, XAI snapshots and timing boundaries. Tests write a source/environment-bound gate; subsequent executable changes invalidate it.

## Complete staged experiment

```bash
V5_WORKERS=6 V5_N_SEEDS=10 bash experiments/run_staged.sh outputs
```

This executes tests; five smoke runs (ineligible as paper evidence); three full Agrawal controls; 30 full principal runs; 30 window and 30 delay runs; 300 paired robustness runs; 43 XAI validation runs; and 90 serial full-horizon resource runs. It then derives tables, validates raw evidence and draws figures. The default 10 repetitions and three resource repetitions were selected before outcomes. A campaign interrupted midway resumes by exact configuration, source and environment, preserving all previous run folders. Failures are explicit and remain in the all-run manifest. Do not combine results from a changed scientific source.

Phases can also be executed individually:

```bash
python -m streammeta.cli prepare --phase controlled --output outputs
python -m streammeta.cli campaign --plan outputs/plans/controlled_plan.json --workers 3 --output outputs
python -m streammeta.cli validate-controls --output outputs
python -m streammeta.cli prepare --phase primary --output outputs
python -m streammeta.cli campaign --plan outputs/plans/primary_plan.json --workers 6 --output outputs
python -m streammeta.cli collect --output outputs
```

`prepare` materializes and fingerprints streams without fitting models. Real data use River's native order: Elec2 45,312 and Phishing 1,250. Synthetic principal runs have 10,000 observations. Materializations are reused across methods and arms and are supplied in the evidence archive, so archived experiments can rerun without downloading data. Fresh reproduction may download Elec2 from the upstream URL recorded in its stream manifest. Phishing ships with River. Never substitute a shuffled or truncated real stream.

For custom jobs, write a JSON configuration and invoke `run --config PATH --output outputs`. Omitted fields receive the explicit defaults in `config.py`. `config/principal.json` is a complete example. Stage `smoke` allows debugging before gates; all other stages enforce the acceptance order. `prepare --phase resource --horizon short` produces a separately named 2,000-observation diagnostic plan. Its horizon is not evidence of sustained adaptation through a later transition. Joint nonzero delay and XAI is supported via `delay` and `xai.enabled`; it is not part of this predeclared campaign.

## Scientific contract

- Independent names: DATA_SEED, DRIFT_WRAPPER_SEED, MODEL_SEED, ROUTER_SEED, XAI_SEED, RESOURCE_RUN_SEED correspond to JSON `seeds.data/wrapper/model/router/xai/resource_run`. Pre-data seed equals DATA_SEED; post-data seed is domain-separated SHA-256 and explicitly saved. Every wrapper is seeded. Hyperplane has one literal factory: 10 features, 3 drifting features, magnitude 0.001.
- Exact empirical ROC AUC is primary. River's 101-threshold AUC is a separately named diagnostic. F1 and kappa use expert hard labels `p>=0.5`. Class 1 has a fixed semantic mapping, never first-observed label order.
- Predict receives only `(x,t)`. Then enqueue; release if due; ADWIN and any explanation run on the pre-learning router snapshot; manager, verified state and experts learn from the released context. Stored probabilities/meta-state are the original prediction-time values. No end-of-stream queue flush. Maximum queue is `min(N,delay+1)`.
- Manager target is `int(abs(y-p_arf)<abs(y-p_hat))`, ties HAT. ARF route requires manager `q>0.5`, matching the tested installed XGBoost classifier. RecentError sees the same verified four-feature state and chooses lower recent hard error, ties HAT. Both use ARF until their required state/manager is available and both train HAT and ARF on each released label.
- Synthetic repetitions vary data and wrapper seeds with learner seeds fixed; real fixed-order repetitions vary learner/router seeds. These are different uncertainty estimands. Paired comparisons share the same canonical observations. Statistical intervals use repetition-level differences, never individual transactions as independent replicates.

## XAI

Tier 1 attributes the current router across recent historical meta-states; it does not establish why a particular decision changed. Tier 2 compares older/newer **retrospective** windows ending at the event. Blocked cross-fitting produces out-of-fold discrimination and SHAP values; 99 whole-block permutations within each fixed fold preserve its observed class counts and provide a coarse conditional null calibration. Every permuted block assignment, fold attribution stability and fitted observed fold model is saved. These outputs remain descriptive temporal-window attribution. The folds estimate separability within retrospective windows, not forecasting performance or future generalization. Permutation calibration assumes appropriate within-fold block exchangeability; it is a diagnostic, not a distribution-free drift test for arbitrary dependent streams.

Four matched Gaussian controls isolate stationary, label-rule, covariate and mixed changes. Their scheduled explanation time is a validation fixture, not an ADWIN detection. Agrawal case studies use actual ADWIN events. Both event index conventions, delayed trigger-error time and success/capable/event counts are saved. Invocation reduction is a count relative to hypothetical per-sample explanations; it is not a measured percentage runtime saving.

## Resource interpretation

`runner.TIMER_CONTRACT` is archived verbatim per run. Meta-state construction, router plus selected-expert prediction, shadow prediction, verified updates, core and integrated step intervals are distinct. Core equals integrated minus measured synchronous observer work. Acquisition from materialized gzip, stream hashing, evaluator metrics, evidence buffering/I/O, RSS sampling and offline metric computation are excluded and separately recorded or disclosed. No reported interval is total service cost. Observer import/initialization and model initialization are separate.

First 1,000 observations are labeled warmup without resetting models; later observations are `after_warmup`, not a guarantee of equilibrium. Full profiles cross the synthetic transition centers. Raw per-step times and RSS samples are retained; memory is maximum sampled process RSS in bytes/MiB, with sampling every 25 observations. RSS includes Python imports and bounded evidence buffers. CPU quota, affinity, load, GC, RAM, NUMA availability and package versions are saved. Resource runs are fresh processes executed serially; this local host is not a dedicated, power-controlled hardware laboratory. Resource analysis separates hardware fingerprints and does not pool incompatible hosts.

## HPC

Install the same pinned environment on each worker; materialize the plan on shared storage; run tests and matched controls in that environment. `hpc/slurm_prediction_array.sh` accepts a plan and executes exactly one indexed job. Determine the array upper bound as `len(plan['jobs'])-1`. Resource profiles use `hpc/slurm_resource_exclusive.sh` to run serially on one exclusive node. Do not submit the resource plan as a colocated ordinary array. Scheduler partitions/time limits may be adjusted to your cluster before freezing a new source snapshot. A software/environment change requires fresh gates and a distinct campaign.

## Evidence and clean checkout

`outputs/runs/RUN_ID/` contains configuration, commit/dirty status, source-snapshot pointer, command, timestamps, environment, requirements freeze, canonical stream manifest, predictions, released updates, timings, RSS samples, final metrics, logs and per-file hashes. XAI events add router/fold snapshots, raw windows, out-of-fold predictions/SHAP and permutation null samples. One row per run, including unsuccessful or superseded runs, is in `outputs/tables/v5_result_manifest.csv`. Table subsets explicitly exclude smoke/controlled stages. Figures have PDF, PNG, source CSV and metadata with axis limits. Paths are relative to the chosen output folder.

The archive includes `source.git.bundle`. A clean source checkout can be made with `git clone source.git.bundle ../v5-clean`. Install as above, use a new output directory, copy the archived `outputs/streams` cache if offline, run tests, and rerun the complete three-control plan. Compare every trace except run ID and all metrics with the archive; timings are expected to vary. The delivered `outputs/validation/clean_checkout.json` records the actual check and command/environment, not merely this recipe.

After all plans, raw-evidence checks and the clean-checkout check succeed:

```bash
python -m streammeta.cli freeze --output outputs
```

Freeze is refused for missing experiments, stale gates, a dirty source tree, unequal controls or an absent clean-checkout check. The status is `frozen_for_independent_audit`. Scientific validity, novelty, external applicability and manuscript readiness still require independent review. No TinyML, microcontroller, universal superiority, real-time, equivalence, causal-SHAP or delay-invariance claim is restored by these gates.
