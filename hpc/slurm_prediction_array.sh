#!/usr/bin/env bash
# Before submitting, run: python -m streammeta.cli snapshot --output outputs
# Wait for all array jobs (afterok) before the one collector. Shared filesystem
# flock semantics and multi-node execution are NOT tested by V5.0.1.
# Submit from V5.0.1 root: sbatch --array=0-29 hpc/slurm_prediction_array.sh outputs/plans/primary_plan.json
#SBATCH --job-name=streammeta-v5
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH --output=slurm-%A_%a.log
set -euo pipefail
: "${SLURM_ARRAY_TASK_ID:?Submit an array with bounds matching the immutable plan}"
V5_PLAN="${1:?Plan path required}"
V5_OUTPUT="${2:-outputs}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONHASHSEED=0
python -m streammeta.cli task --plan "$V5_PLAN" --index "$SLURM_ARRAY_TASK_ID" --output "$V5_OUTPUT"
