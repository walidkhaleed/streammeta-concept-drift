# Frozen runtime

Use CPython 3.12 and the exact third-party pins in `../requirements-lock.txt`, then install the local package with `pip install --no-deps -e .`. Runtime source/environment gates are mandatory. Each run records Python and every installed package, thread controls, CPU/NUMA availability, memory and cgroup limits, kernel, affinity and GC configuration. The per-run records describe the actual execution environment; this folder does not imply an independently rebuilt container or second-hardware replication.

The CPU-only XGBoost wheel is intentional. The SHAP/Numba/LLVM load issue and the PID-namespace-safe RSS reader are documented in `../audit/additional_findings.md`. Temporary work and partially written run files should reside on a local or appropriately configured shared filesystem, outside bidirectional desktop/browser synchronization. Publish closed, checksummed result archives for transfer.
