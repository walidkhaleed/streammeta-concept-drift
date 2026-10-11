"""Regenerate the human-readable validation report from frozen V5 evidence."""
from pathlib import Path
import argparse
import json
import pandas as pd
from streammeta.provenance import project_root,source_state
from streammeta.io import file_sha,atomic_json

def markdown(df):
    def val(v):
        if pd.isna(v):return '—'
        if isinstance(v,float):return f'{v:.6f}'
        return str(v).replace('|','\\|').replace('\n',' ')
    cols=list(df.columns)
    return '| '+' | '.join(cols)+' |\n| '+' | '.join(['---']*len(cols))+' |\n'+'\n'.join('| '+' | '.join(val(v) for v in row)+' |' for row in df.itertuples(index=False,name=None))

def build(output):
    output=Path(output).resolve();root=project_root();tables=output/'tables'
    manifest=pd.read_csv(tables/'v5_result_manifest.csv')
    primary=pd.read_csv(tables/'v5_primary_results.csv');rep=pd.read_csv(tables/'v5_repeated_summary.csv')
    effects=pd.read_csv(tables/'v5_paired_router_effects.csv');controls=pd.read_csv(tables/'v5_cross_stage_controls.csv')
    xai=pd.read_csv(tables/'v5_xai_events.csv');res=pd.read_csv(tables/'v5_resource_results.csv')
    tests=json.loads((output/'gates'/'tests.json').read_text());freeze=json.loads((output/'v5_freeze_manifest.json').read_text()) if (output/'v5_freeze_manifest.json').exists() else None
    old=pd.read_csv(root/'audit'/'historical_primary.csv');comparison=[]
    for r in primary.itertuples():
        col=r.method+'_AUC'
        if col in old:
            a=float(old[old.Dataset==r.dataset][col].iloc[0])
            comparison.append({'dataset':r.dataset,'method':r.method,'V4_approx_AUC_reported':a,'V5_exact_AUC':r.roc_auc_exact,'V5_approx_AUC_101':r.roc_auc_streaming_approx,'arithmetic_difference_V5_exact_minus_V4_approx':r.roc_auc_exact-a})
    comparison=pd.DataFrame(comparison);comparison.to_csv(tables/'v5_historical_comparison.csv',index=False)
    p=primary.pivot(index='dataset',columns='method',values='roc_auc_exact').reset_index()
    counts=manifest.groupby(['stage','status','current_source_environment']).size().reset_index(name='runs')
    real=rep[(rep.stage=='robustness')&(rep.metric=='roc_auc_exact')]
    windows=rep[(rep.stage=='windows')&(rep.metric=='roc_auc_exact')]
    delays=rep[(rep.stage=='delays')&(rep.metric=='roc_auc_exact')]
    xr=xai[(xai.design=='scheduled_xai_control')&(xai.success==True)]
    xx=xr.groupby('dataset').agg(successful_events=('run_id','size'),mean_oof_auc=('roc_auc_exact_oof','mean'),median_oof_auc=('roc_auc_exact_oof','median'),p_le_005=('permutation_p_one_sided',lambda a:int((a<=.05).sum())),top_x0=('top_feature',lambda a:int((a=='x0').sum())),mean_fold_spearman=('mean_fold_importance_spearman','mean')).reset_index()
    rr=res[(res.phase=='after_warmup')&(res.arm=='full')&(res.timer=='core_step_time_ms')]
    rsummary=rr.groupby(['hardware_fingerprint','dataset','method']).agg(repetitions=('run_id','size'),mean_core_ms=('mean','mean'),min_run_mean_ms=('mean','min'),max_run_mean_ms=('mean','max'),largest_sampled_RSS_MiB=('maximum_sampled_rss_MiB','max')).reset_index()
    rsummary.to_csv(tables/'v5_resource_repeated_summary.csv',index=False);xx.to_csv(tables/'v5_xai_control_summary.csv',index=False)
    stats=json.loads((tables/'v5_across_dataset_statistics.json').read_text())
    newest=manifest[(manifest.status=='complete')&manifest.current_source_environment]
    e=json.loads((output/str(newest.iloc[0].run_path)/'environment.json').read_text())
    state=source_state();status=freeze['status'] if freeze else 'UNFROZEN: acceptance not complete'
    lines=[
        '# V5 reconstruction and validation report',
        f'Status: **{status}**. Source SHA-256: `{state["source_sha256"]}`. Git commit: `{state["git_commit"]}`. The manuscript has not been revised.',
        '## 1. Architecture and experimental contract',
        'One importable Python implementation provides immutable named-seed configuration, canonical stream materialization, online experts and routing, delayed-label release, exact/approximate metrics, synchronous XAI, resource accounting, gated fresh-process scheduling and data-derived reporting. Every run has prediction/update/timing/memory Parquet traces and checksummed provenance. Source modules are under `src/streammeta`; portable and SLURM launchers are under `experiments` and `hpc`.',
        'Exact empirical prequential ROC AUC is primary. River AUC uses 101 explicit thresholds. The recent-error counterfactual uses exactly the same verified-history state as the learned Meta router; both keep training both experts. Router target and threshold ties select HAT. Expert hard-label ties use >=0.5. Full settings, seed definitions, repetition estimands and statistical choices were written before V5 outcomes in `audit/design_and_statistical_plan.md`.',
        '## 2. Audit reconciliation',
        (root/'audit'/'issue_reconciliation.md').read_text(),
        '## 3. Tests and acceptance gates',
        f'Tests: **{tests["counts"]["tests"]} passed, {tests["counts"]["failures"]} failed, {tests["counts"]["errors"]} errors, {tests["counts"]["skipped"]} skipped**. Full stdout, commands, source/environment identities and deprecation warnings are retained in `v5_test_report.txt`. Tests cover all 15 requested categories and additional pairing, cache, label mapping, manager-buffer and known-control checks.',
        'A deliberately label-sensitive learner and the real paired learner were tested with future hidden labels changed. Outputs remained identical through the prediction preceding the first altered label release. Historical scores/targets, no-flush queue counts and actual XGBoost tie behavior were tested. XAI tests reload the exact router/fold snapshots and compare their predictions; fake-clock cost injection verifies timer inclusion/exclusion independently of measured hardware speeds.',
        'Full Agrawal controlled runs have identical observations, every prediction field except run ID, exact/approx AUC, routes and fit counts. Cross-stage controls also reconcile for every repetition:',
        markdown(controls[['replicate','n_arms','stages','equal']]),
        'Hashes are in `outputs/validation/matched_controls.csv` and `outputs/tables/v5_cross_stage_controls.csv`. Raw evidence checks rehash every run artifact, recompute both AUCs/F1/kappa and check queue, historical-probability, event and memory/timer invariants. `outputs/validation/clean_checkout.json` records the clean-checkout test and complete experiment reproduction. Freeze requires every planned job to complete and forbids stale gates or dirty scientific source.',
        '## 4. Experiments executed',markdown(counts),
        'Smoke and controlled runs are validation evidence only. The ten-repetition design was fixed before outcomes; it is a modest repeatability study, not a post-hoc stopping rule or power guarantee. Synthetic studies vary paired data/wrapper realizations with model seeds fixed. Real streams preserve one native order and vary learner/router seeds. Three runtime repetitions do not count as predictive uncertainty replicates. Optional published learned selectors and joint nonzero-delay/XAI experiments were not run.',
        '## 5. New predictive evidence',
        'Principal full-horizon exact empirical AUC:',markdown(p),
        'Robustness across ten repetitions; uncertainty sources remain separately labeled:',
        markdown(real[['design','dataset','method','n_repetitions','mean','median','std','IQR']]),
        'Predeclared paired Meta-minus-RecentError exact-AUC effects. Intervals are two-sided Student-t intervals across repetition differences; no transaction pseudoreplication and no equivalence conclusion:',
        markdown(effects[['design','dataset','n_repetitions','mean','ci95_low','ci95_high']]),
        f'Across-dataset Friedman statistic = {stats.get("friedman_statistic")}; asymptotic p = {stats.get("friedman_p_asymptotic")}. Nemenyi performed: {stats.get("nemenyi_performed")}, under the predeclared p<0.05 global gate. Six dataset blocks make asymptotic inference limited. The fifth comparator changes the rank problem relative to the four-method V4 analysis.',
        'Matched window results:',markdown(windows[['window','n_repetitions','mean','median','std','IQR']]),
        'Matched delay results:',markdown(delays[['delay','n_repetitions','mean','median','std','IQR']]),
        'The only ablation manipulation is the indicated window/delay. No end flush occurs; delay 0/100/500 leaves 0/100/500 unverified observations and yields maximum queue 1/101/501 on these complete streams. Delay effects are empirical for these runs, not delay invariance.',
        '## 6. XAI validation',markdown(xx),
        'The control event is scheduled at index 3199 (sample 3200), with older raw indices 2800–2999 and newer 3000–3199. Scheduled controls assess the explainer at a known boundary; they do not establish detector power or detection delay. The stationary and label-rule controls have identical feature marginals by construction; covariate/mixed controls shift x0 by 1.5. The table reports the number of coarse permutation p-values <=0.05 descriptively across ten repetitions, without multiple-test confirmation. Even successful discrimination and x0 ranking do not certify causal root causes.',
        'The 99 permutations shuffle whole blocks within each fixed fold, preserving observed fold class counts. Calibration assumes an appropriate within-fold block-exchangeable null and is not guaranteed under arbitrary stream autocorrelation or event-selection bias. Cross-fitted validation is retrospective discrimination, not forecasting. Fold stability, all raw/SHAP matrices, out-of-fold predictions, permutation scores, every block assignment and snapshot binaries remain available for independent assessment. Event-triggered Agrawal runs are separately labeled and may have no eligible event; counts are not forced to match V4. Explanation invocation reduction uses successful calls, and is not a measured runtime saving. The earlier invalid null calibration and its correction are documented in audit/additional_findings.md; those earlier p-values are not accepted evidence.',
        '## 7. Resource evidence',
        f'Observed host: `{e["hostname"]}`; Python `{e["python"].split()[0]}`; logical CPUs {e["logical_cpu_count"]}; cgroup limits `{e["cgroup"]}`. Full CPU, NUMA-command availability, RAM, affinity, kernel, native-thread, GC and package records are archived per run. These are measurements on the available host, not a connected supercomputer or a dedicated laboratory.',
        'Full-horizon mean core step time after the first 1000 observations; min/max describe three fresh-process run means. Memory is the largest maximum-sampled RSS over those repetitions, in MiB:',
        markdown(rsummary.drop(columns='hardware_fingerprint')),
        'Core includes state construction, routing/selected prediction, shadow prediction, queue/control overhead and verified updates. Integrated additionally includes synchronous observer work where enabled. Acquisition, trace I/O, evaluator metrics and offline exact metrics are excluded and explicitly disclosed; model initialization and observer import/initialization are separate. The shared runner imports XGBoost for every method, so RSS describes the common benchmark process, not a minimal method-specific deployment. The utility run_wall_seconds field excludes interpreter startup and final archive hashing; it is not a total-service-cost measurement. XAI case-study timings were captured while predictive jobs could be parallel and are not controlled resource comparisons. Dedicated resource campaigns ran serially after predictive/XAI jobs. Shared-host noise and 25-step memory sampling limit interpretation. No hard real-time, total service cost, universal memory/latency advantage or microcontroller claim follows.',
        '## 8. Material differences from historical V4 evidence',
        'These arithmetic comparisons are **not like-for-like effect estimates**: V4 reported a coarser approximate AUC, omitted wrapper seeds and did not preserve the required traces. V5 changes stream controls/seeds, fixed label interpretation, explicit scoring and the runtime/code environment. Differences cannot be assigned to any one correction without an additional factorial experiment. V4 values never entered planning, training or acceptance tests.',
        markdown(comparison),
        'The three historical nominal Agrawal controls were 0.9457/0.9520/0.9546. V5 replaces them with one matched realization and exact trace equality rather than trying to retain any one old number. V5 rankings include RecentError. Resource boundaries/horizons and XAI validation semantics have also changed, so old resource or feature-importance numbers are not recycled.',
        '## 9. Remaining scientific concerns and readiness',
        'Passing engineering gates does not prove novelty, strong comparative performance or publication readiness. Remaining limits include ten repetitions, six benchmark streams, fixed real-stream order, no reproduced published learned-selection competitor, retrospective block-dependent XAI calibration, no validated causal localization, no joint nonzero-delay/XAI claim, and hardware-specific sampled resource measurements. The routing counterfactual comparison is at zero delay; nonzero-delay ablations evaluate Meta and do not establish its advantage over delayed routing baselines. Within-dataset 95% intervals are pointwise and not adjusted to provide simultaneous six-comparison coverage. Positive-class semantics and runtime changes complicate historical comparisons. Manuscript-only issues remain explicitly deferred in the reconciliation log.',
        ('V5 is frozen as a candidate new evidence source and ready for independent code/evidence audit. It is not yet independently approved as the paper\'s evidence source.' if freeze else 'V5 is not frozen; unfinished evidence must not be used for publication claims.'),
        '**The manuscript must not be updated until V5 results are frozen and independently audited.**',
        '## 10. Artifact map',
        '`README.md` documents installation, fresh-checkout reproduction and staged/HPC commands. `requirements-lock.txt` pins third-party dependencies. `audit/` holds reconciliation/design/baseline decisions and historical source hashes. `outputs/runs/` holds every prediction, released update, raw timing/RSS sample and XAI snapshot. `outputs/tables/` holds the run manifest and all derived result CSVs. `outputs/figures/` holds PDF/PNG/CSV/JSON figure sets. `outputs/validation/` and `outputs/gates/` hold test/control/evidence and clean-checkout proofs. `outputs/v5_freeze_manifest.json` binds the accepted result set. `source.git.bundle` preserves clean source history. `v5_reproducibility_bundle.zip` packages the full code and evidence.'
    ]
    report='\n\n'.join(lines)+'\n';(root/'v5_validation_report.md').write_text(report)
    return root/'v5_validation_report.md'

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs');a=p.parse_args();print(build(a.output))
