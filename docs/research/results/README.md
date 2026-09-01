# Evaluation results

This directory contains dated empirical snapshots. Protocol and execution instructions
are intentionally separate so historical results remain interpretable after tooling or
documentation changes.

## Available snapshots

| Date | Campaign | Scope | Status | Document |
|---|---|---|---|---|
| 2026-09-01 UTC | `v6-final-r7` | First complete diagnostic date for GLM and Qwen | Interim; longitudinal and external layers incomplete | [Chinese report](v6-final-r7-2026-09-01.zh-CN.md) |
| 2026-09-02 | Qwen report mode case study | Four Hybrid/browser-grounded/strict trajectories | Two completed and two interrupted runs | [English analysis](qwen-report-modes-2026-09-02.md) |
| 2026-08-29 to 2026-09-01 | Engineering validation archive | Lint, typing, tests, packaging, parser probes | Historical checkout snapshots | [Chinese record](engineering-validation-2026-08-29-09-01.zh-CN.md) |

## Reading rules

- Treat each document as a dated snapshot, not the repository's live state.
- Read exact scores together with the task contract, source fingerprint, and limitations.
- Do not combine diagnostic success rates with BrowserGym rewards.
- Do not update a historical document merely because later code has more tests or a new
  benchmark adapter. Add a new verification or result snapshot instead.

The stable methodology is in [evaluation-protocol.md](../evaluation-protocol.md). Suite
commands and report formats are in the [benchmark guide](../../../benchmarks/README.md).
