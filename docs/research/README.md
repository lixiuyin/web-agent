# Research workflow

This directory separates methodology, artifact lifecycle, failure attribution, and dated
empirical results.

## Research questions

1. **What happened in one run?** Inspect the trajectory, tool results, retrieved evidence,
   controller state, and independent task judgment.
2. **Which failure pattern recurs?** Aggregate observable evidence without turning an
   automatic label into an unsupported internal cause.
3. **Does a change transfer?** Compare preregistered baseline and intervention conditions
   on development, held-out-task, and held-out-setting splits.

## Documents

| Document | Ownership |
|---|---|
| [Evaluation protocol](evaluation-protocol.md) | Stable two-layer design, metrics, controls, and readiness rules |
| [Experiment lifecycle](experiment-lifecycle.md) | Run → judgment → analysis → study → campaign artifact flow |
| [Failure taxonomy](failure-taxonomy.md) | Observable, candidate, and adjudicated failure evidence |
| [Results index](results/README.md) | Dated model results and trace case studies |
| [Current-result compatibility link](current-evaluation-results-zh.md) | Stable redirect for earlier documentation links |

Executable suites, environment preparation, and study commands belong in the
[benchmark guide](../../benchmarks/README.md). Run namespace ownership and checkpoint
contents belong in the [artifact reference](../reference/run-artifacts.md).

## Code ownership

Research mechanisms live in `src/webagent/evaluation/`:

- `models.py` defines task and result contracts.
- `failures.py` records observable and candidate evidence.
- `calibration.py` reports probability coverage, Brier score, and ECE.
- `transfer.py` keeps development and held-out effects separate.
- `generality.py` checks breadth using a fail-closed coverage floor.
- `long_horizon.py` derives collapse, stagnation, recovery, and resume diagnostics.
- `portfolio.py` assembles complete provider/model/date cells and rejects mixed source
  fingerprints or scripted baselines.
- `studies.py` defines immutable study manifests and hash-bound run rows.
- `artifacts.py` owns canonical research filesystem paths.

Executable environments and orchestration remain under `benchmarks/`. This keeps reusable
evaluation contracts independent from particular sites and command-line studies.

## Interpretation boundary

A deterministic harness pass proves that infrastructure composes. A model trajectory
provides one outcome. A dated diagnostic campaign provides local empirical evidence. A
ready longitudinal portfolio requires repeated common dates, and external comparison
requires complete native BrowserGym reports.

None of these layers alone establishes general-purpose web-agent maturity.
