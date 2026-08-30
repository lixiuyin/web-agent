# Research workflow

This directory is the entry point for research use of the repository.  It
separates three questions that are easy to conflate in an agent codebase:

1. **What happened in one run?**  Inspect the observable trajectory, tool
   results, retrieved evidence, controller state, and externally judged task
   outcome.
2. **Which failure pattern recurs?**  Aggregate evidence-backed failure
   observations without turning an automatic diagnostic into an unsupported
   causal claim about reasoning or memory.
3. **Does a change transfer?**  Compare a declared baseline and intervention
   on development tasks, held-out tasks, and held-out settings, while retaining
   missing confidence and insufficient sample sizes as explicit limitations.

The repository is organized around that lifecycle:

```text
src/webagent/                 runtime agent and reusable evaluation library
benchmarks/environments/      deterministic, controlled web environments
benchmarks/suites/            task definitions and suite runners
benchmarks/studies/           repeated, multi-model, longitudinal experiments
outputs/runs/                 isolated ad-hoc execution records
outputs/studies/              non-overwriting benchmark executions and analyses
outputs/legacy/               hash-inventoried historical outputs
```

The runtime packages (`agent`, `browser`, `planner`, `tools`, and `parser`)
remain organized by system responsibility.  Research concepts live in
`webagent.evaluation`, rather than being mixed into browser or planner code:

- `models.py` defines verifiable task/result contracts and held-out metadata.
- `failures.py` records observed or candidate failure evidence.
- `calibration.py` compares pre-judgment task-success probabilities with
  empirical outcomes and reports missing-confidence coverage.
- `transfer.py` separates development, held-out-task, and held-out-setting
  performance.
- `generality.py` checks observed task, origin, scenario, environment, and
  held-out coverage against a fail-closed breadth floor.
- `long_horizon.py` derives trajectory length, entropy, repeated-action collapse,
  same-state stagnation, replanning/strategy churn, recovery, and
  checkpoint-resume diagnostics from observable steps and events.
- `portfolio.py` combines content-hashed agent reports into complete
  provider/model/date cells, rejects scripted baselines as empirical model
  evidence, and separates provider/transport unavailability from zero model
  performance.
- `endpoints.py` performs a minimal credential-redacted availability probe so an
  expensive campaign does not turn a provider privacy-policy rejection into 30
  apparent task failures.
- `studies.py` defines immutable, versioned study manifests and hash-bound run
  records for model/condition comparisons.
- `artifacts.py` is the canonical authority for research run/study filesystem paths. Runtime
  compatibility readers and tool-level containment helpers may resolve legacy paths, but new
  research writers must obtain their namespaces from `RunLayout` or `StudyExecutionLayout`.

The CLI allocates ad-hoc runs at
`outputs/runs/<UTC-date>/<model>/<task>-<run-id>/`. Benchmark runners allocate
one exact execution at
`outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/`;
its individual tasks live in `runs/<task-id>/`. A user-supplied `--output`
always names the exact run or execution root rather than the enclosing
workspace.

Ordinary interactive follow-ups remain in one owned run. The latest canonical
trace/result stay at their top-level locations, while immutable snapshots are
added at `trajectory/turns/turn-NNN.json` and
`result/turns/turn-NNN/{summary.txt,attachments/}`. Strict/search-only runs are
single-turn by contract.

See [experiment-lifecycle.md](experiment-lifecycle.md) for the data flow and
[failure-taxonomy.md](failure-taxonomy.md) for attribution rules.

The long-horizon controlled suite deliberately separates three claims. A
scripted 60-stage pass proves only that Chromium, tools, checkpoint restoration,
the state server, and the judge compose correctly. A model run provides one
trajectory-level outcome. Generality requires the cross-suite campaign on two
or three models over at least three actual dates; until those retained cells
exist, the portfolio remains `insufficient` and the repository must not claim
empirical general-purpose maturity.
