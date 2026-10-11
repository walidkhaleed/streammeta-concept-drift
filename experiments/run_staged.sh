#!/usr/bin/env bash
# For future experiments only; NEVER run to reconstruct audited V5.0.1.
# Portable fresh-process campaign; interruption-safe config-based resume.
set -euo pipefail
V5_OUTPUT="${1:-outputs}"
V5_WORKERS="${V5_WORKERS:-6}"
V5_N_SEEDS="${V5_N_SEEDS:-10}"
python -m streammeta.cli test --output "$V5_OUTPUT"
for V5_PHASE in smoke controlled; do
    python -m streammeta.cli prepare --phase "$V5_PHASE" --output "$V5_OUTPUT"
    python -m streammeta.cli campaign --plan "$V5_OUTPUT/plans/${V5_PHASE}_plan.json" --workers "$V5_WORKERS" --output "$V5_OUTPUT"
done
python -m streammeta.cli validate-controls --output "$V5_OUTPUT"
for V5_PHASE in primary windows delays robustness xai; do
    python -m streammeta.cli prepare --phase "$V5_PHASE" --n-seeds "$V5_N_SEEDS" --output "$V5_OUTPUT"
    python -m streammeta.cli campaign --plan "$V5_OUTPUT/plans/${V5_PHASE}_plan.json" --workers "$V5_WORKERS" --output "$V5_OUTPUT"
done
python -m streammeta.cli prepare --phase resource --output "$V5_OUTPUT"
python -m streammeta.cli campaign --plan "$V5_OUTPUT/plans/resource_full.json" --workers 1 --output "$V5_OUTPUT"
python -m streammeta.cli collect --output "$V5_OUTPUT"
python -m streammeta.cli validate-evidence --output "$V5_OUTPUT"
python -m streammeta.cli figures --output "$V5_OUTPUT"
# Freeze only after the separately documented clean-checkout reproduction check.
