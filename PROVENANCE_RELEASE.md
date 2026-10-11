# V5.0.1 patch reproduction

V5.0.1 is a provenance/package correction derived from the audited V5 scientific evidence. No accepted scientific result was changed.

This release corrects A01–A03 only. It does not authorize a scientific campaign or manuscript edit. The model, stream, seed, routing, metric, XAI and experiment-plan implementations and the original 33 scientific tests are unchanged. New tests mutate temporary synthetic provenance fixtures only. Existing-trace validation recomputes metrics and invariants without fitting a model or replacing a prediction.

## Identity and trust

The original accepted archive SHA-256 is `44af327689945447b2306cb310e016f887ed6c762444e4f847b40fbcfa27fab6`. The scientific source-member identity remains `bb0d92b167db28a3ca1f2adc2d95af767ae71db809dc86c135d001198b51dc40`, commit `27cf0b3fe83caeaf531862afb7e85e0aa7ce464c`. Per-run records are not relabeled. The utility source and test gate have their own V5.0.1 identity.

`provenance/release_policy.json` is derived from the original freeze and inventory, whose fixed hashes are checked by `release.py`. It cannot be changed to admit a new run. It accepts exactly the original 532 run IDs and explicitly excludes all 610 rejected IDs. The one developer clean-checkout control is explicitly designated as an audited supplemental config; its exact config hash is anchored to the original record. All eight campaign plan files retain original byte/content hashes. The rejected companion archive is preserved under `provenance/rejected/`, outside the accepted run root. Collection never searches development history or companion archives.

Source-member SHA-256 is a digest of the sorted member/hash map. ZIP-container SHA-256 hashes ZIP bytes, including metadata. Original source ZIPs remain distinct and unchanged. New source ZIPs use fixed member timestamps/modes, sorted members, content checks, a source-specific publication lock and random temporary names. Existing ZIPs are verified before reuse and never silently replaced on mismatch.

The inventory covers all package payload files. Its two named exclusions are the inventory itself and the final freeze. The freeze binds the inventory hash, exact run/row/artifact/config/plan identities, result-manifest hash and validation payload. External `v5_0_1_identity.json` binds the final freeze and both ZIP-container hashes. This directed chain avoids circular self-hashes. Files excluded as runtime state (`.git`, caches, `.locks`) are not ZIP members. The freeze is never overwritten.

The legacy permissive freeze entry point is retired. This patch profile only freezes evidence descended from the specific audited V5 anchors. Future scientific releases require an explicitly reviewed new profile, not changes to this patch's accepted list.

## Supported publication model

Tested scope: one Linux host, a local POSIX filesystem, cooperating processes using `flock` and same-filesystem rename. Tests exercise process lock contention, shared worker versus exclusive collector exclusion, random staging names, archive verification, interrupted publication and unique campaign log directories. SLURM execution, distributed filesystem locking and general multi-node safety are not tested or claimed.

1. Prepare the source snapshot before fan-out. Local `campaign` does this before launching any worker. `prepare` also creates it. For array submission, run `python -m streammeta.cli snapshot --output outputs` before submitting jobs. Workers refuse missing snapshots.
2. Workers hold shared activity locks. A local campaign controller holds a shared lock for its entire lifetime, including gaps between jobs. A collector/validator/freezer takes a nonblocking exclusive collector lock and exclusive activity lock; any active writer causes refusal. Array scheduling requires an explicit scheduler `afterok` barrier because queued jobs are not running lock holders.
3. Run exactly one collector after all workers finish. The collector generates tables in a random staging directory, then atomically replaces individual files while holding both locks. A transaction marker remains until all replacements and the generation record succeed. Other supported readers are excluded during publication. Arbitrary readers outside the protocol do not have a whole-directory atomic snapshot guarantee.
4. A crash can leave an incomplete generation. Validation and freeze refuse its marker. Rerun the collector to complete a new generation, then validate again; do not delete the marker to bypass validation.
5. Validate the complete accepted evidence, then create inventory, then freeze. Freeze rechecks every artifact and current validation binding. No worker/collector mutation is authorized after release freeze.

Campaign logs use a random invocation directory with exclusive creation. Existing logs are never reused, even when the same plan is resumed. Scientific seeds never derive from these invocation identifiers.

## Clean checkout and commands

Use CPython 3.12.14 and the exact recorded requirements. The delivered verification used the available pinned V5 environment, not a fresh dependency reinstall. The dependency environment signature is recorded separately from the imported source identity.

Extract `v5_0_1_codebase.zip`, then clone its `source.git.bundle` to a NEW checkout. Set these explicitly, substituting your paths:

```bash
git clone /path/to/v5_0_1_source/source.git.bundle /path/to/clean
export PYTHONPATH=/path/to/clean/src
export STREAMMETA_PROJECT_ROOT=/path/to/clean
export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
```

For a fresh patch staging tree from the original supplied archives:

```bash
python /path/to/clean/experiments/refreeze_v5.py stage \
  --accepted /path/to/v5_reproducibility_bundle.zip \
  --code /path/to/v5_codebase.zip \
  --rejected /path/to/v5_rejected_development_campaign.zip \
  --audit-report /path/to/independent_v5_audit_report.md \
  --destination /path/to/new_release
python /path/to/clean/experiments/refreeze_v5.py tests --release-root /path/to/new_release
python -m streammeta.cli collect --output /path/to/new_release/outputs
python /path/to/clean/experiments/refreeze_v5.py compare --release-root /path/to/new_release
python -m streammeta.cli validate-evidence --output /path/to/new_release/outputs
```

The tests command executes the full suite and a dedicated provenance/publication pass, records interpreter/import path/commands, and binds both reports. Copy the release notes, reconciliation and other documentation into the staging tree BEFORE inventory creation. These documents do not supply scientific inputs. Package the committed source bundle. Then:

```bash
python -m streammeta.cli inventory --output /path/to/new_release/outputs
python -m streammeta.cli freeze --output /path/to/new_release/outputs
python -m streammeta.cli verify-package --output /path/to/new_release/outputs
```

For the delivered immutable bundle, extract it to a new directory and run only `verify-package`, from the clean source checkout. Verify its archive and freeze hashes against the external identity record. Do not collect or validate into the delivered frozen tree. For a new reproduction, use `stage` and new timestamps/identities.

The comparison requires precisely the A01 cell change. Scientific files are hashed against the anchored original inventory; all summary CSV cells/JSON values are independently compared exactly. Any unexpected metadata or scientific change must be investigated before sealing. The all-run manifest keeps its historical schema, including `current_source_environment=True` meaning the allowed scientific source/environment; the collector's own source/environment are recorded separately in `outputs/release/collection.json`.

No model rerun, new scientific result or manuscript edit occurs in this workflow. The manuscript can only be reconsidered after an independent delta audit.
