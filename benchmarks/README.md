# Benchmarks

The benchmark package separates repository-owned diagnostics from externally comparable
BrowserGym evaluation. The two layers use different tasks and evaluators, so their scores
remain separate.

```text
benchmarks/
├── core/                         shared execution layouts and tool contracts
├── environments/controlled_web/ deterministic local sites and judges
├── manifests/                    versioned task and expectation snapshots
├── suites/                       one-suite runners
├── studies/                      repeated, multi-model, and longitudinal studies
└── docs/                         suite, infrastructure, and report guides
```

## Choose an evaluation path

| Goal | Runner | Guide |
|---|---|---|
| Calibrate Chromium, tools, and a deterministic judge | `benchmarks.suites.controlled_web.general` | [Internal suites](docs/internal-suites.md) |
| Test dated public-web reading and discovery | `benchmarks.suites.open_web.*` | [Internal suites](docs/internal-suites.md) |
| Exercise stateful sandbox interaction | `benchmarks.suites.controlled_web.sandbox` | [Internal suites](docs/internal-suites.md) |
| Test 60-stage recovery and durable cues | `benchmarks.suites.controlled_web.long_horizon` | [Internal suites](docs/internal-suites.md) |
| Measure local PDF Figure detection | `benchmarks.suites.document_figures.fast_path` | [Internal suites](docs/internal-suites.md) |
| Run WebArena-Verified Hard or VisualWebArena | `benchmarks.studies.browsergym_matrix` | [BrowserGym](docs/browsergym.md) |
| Compare models, dates, or interventions | `benchmarks.studies.*` | [Running studies](docs/running-studies.md) |
| Interpret reports and readiness gates | report and portfolio analyzers | [Report contracts](docs/report-contracts.md) |

## Two-layer contract

1. The **diagnostic layer** contains repository-owned open-web, controlled sandbox, and
   long-horizon suites with rich trajectories and failure evidence.
2. The **external layer** contains WebArena-Verified Hard and VisualWebArena through the
   standard BrowserGym observation/action API and native evaluators.

`two_layer_portfolio` binds complete reports without creating a composite leaderboard
score. A model can be strong on one layer and weak on the other; both remain visible.

| Evidence layer | Official scope per model | Current state |
|---|---:|---|
| Repository diagnostic layer | 36 tasks per date | One complete common date; longitudinal portfolio remains insufficient |
| WebArena-Verified Hard | 258 tasks | Not run; official sites and reset calibration required |
| VisualWebArena | 910 tasks | Not run; official sites, reset calibration, and evaluator assets required |

Exact dated scores and limitations belong to the
[results index](../docs/research/results/README.md). The frozen methodology belongs to
the [evaluation protocol](../docs/research/evaluation-protocol.md).

## Fast calibration

Verify a deterministic local environment before spending model calls:

```bash
python -m benchmarks.suites.controlled_web.general \
  --mode scripted-harness-baseline \
  --tool-set browser-only
```

This proves only that the site, browser, tools, trace collector, and judge compose. It is
not a model-quality result. Use agent mode for a configured planner:

```bash
python -m benchmarks.suites.controlled_web.general \
  --mode agent \
  --tool-set browser-only
```

## Output boundary

Every execution is non-overwriting by default:

```text
outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/
```

Individual task trajectories live below `runs/<task-id>/`. An explicit `--output` names
one exact execution and refuses to replace prior evidence. Study and campaign layouts,
hash bindings, resume behavior, and report semantics are documented in
[running studies](docs/running-studies.md) and
[report contracts](docs/report-contracts.md).

## Evidence rules

- `done` is an agent claim, never automatic task success.
- Scripted harnesses calibrate infrastructure and cannot satisfy empirical model gates.
- Missing provider or backend availability remains distinct from zero model performance.
- Public-web results are dated; a successful slice is not a general success-rate claim.
- Development, held-out-task, and held-out-setting results remain separate.
- CAPTCHA is never solved or bypassed; benchmark execution fails closed.
- Changing a bound task set, source fingerprint, environment, or evaluator starts a new
  comparison.

The current evidence snapshot is deliberately separate from executable suite
documentation so benchmark guides do not become stale result reports.
