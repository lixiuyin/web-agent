# Benchmark report contracts

Reports distinguish agent claims, independent judgments, observed failures, and readiness
for broader comparisons.

## Execution layout

```text
<execution>/
├── execution.json
├── inputs/
├── runs/<task-id>/
├── ledger/
├── evidence/
├── artifacts/
├── analysis/
└── results.json
```

Task-manifest bytes and canonical complete-task-set hashes are retained and verified
before task execution and ledger publication. A reordered, changed, or subset task list
is a different contract.

## Core metrics

| Metric | Meaning |
|---|---|
| Task success | All required terminal, fact, URL, history, or file assertions pass |
| Mean score | Weighted partial assertion score; not strict success rate |
| Agent completion | The agent called `done` |
| False completion | The agent called `done`, but independent judgment failed |
| Action validity | Executed tool calls that succeeded |
| Answer grounding | Required final-answer facts and references that passed |
| Planner failures | Observable attempts that produced no executable action |
| Collapse/stagnation | Observable repeated-action or unchanged-state diagnostics |
| Calibration | Self-reported pre-judgment success probability versus outcome |

Automatic failure labels describe observable symptoms. They do not assign an internal
reasoning, memory, provider, or browser cause without controlled evidence or adjudication.

## Diagnostic readiness

Generality is fail-closed. Coverage requires public and sandbox evidence, adequate task
and origin diversity, development and held-out splits, real search discovery, and
explicit SPA, login, cross-origin form, file, transaction, recovery, and long-horizon
scenarios.

A single suite or date cannot satisfy longitudinal readiness. Scripted harnesses are
rejected as empirical model evidence.

## External reports

`browsergym-results.json` records native task coverage, package and backend identity,
system errors, reward, binary success, confidence interval, and task-level rows. A custom
task subset is useful for calibration but cannot satisfy publication-grade readiness.

## Two-layer readiness

`two_layer_portfolio.json` requires:

- a ready diagnostic portfolio for every declared endpoint;
- one complete official WebArena-Verified Hard report per endpoint;
- one complete official VisualWebArena report per endpoint;
- matching source and comparable backend fingerprints;
- no custom or partial report substituted for an official task set.

The portfolio presents the two layers side by side and never averages unlike metrics.

## Evidence integrity

Local hashes and retained manifests make accidental drift detectable. They do not provide
an independent trusted timestamp or permanent archive. Publication should retain the
source tag, dependency lock, campaign/study contracts, reports, and an independent
checksum or read-only archive.

Current empirical claims belong to the
[dated results documents](../../docs/research/results/README.md), not this stable schema
reference.
