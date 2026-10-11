#!/usr/bin/env bash
# Source snapshot is precreated by campaign before fan-out.
# Multi-node filesystem locking is untested; use the documented staged workflow.
# Submit after prediction/XAI arrays finish. No concurrent benchmark processes on this node.
#SBATCH --job-name=streammeta-v5-resource
#SBATCH --exclusive
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=0
#SBATCH --time=24:00:00
#SBATCH --output=slurm-resource-%j.log
set -euo pipefail
V5_PLAN="${1:-outputs/plans/resource_full.json}"
V5_OUTPUT="${2:-outputs}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONHASHSEED=0
python -m streammeta.cli campaign --plan "$V5_PLAN" --workers 1 --output "$V5_OUTPUT"
