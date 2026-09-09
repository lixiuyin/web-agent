# Evaluation results

This directory contains dated empirical snapshots. Protocol and execution instructions
are intentionally separate so historical results remain interpretable after tooling or
documentation changes.

## Available snapshots

| Date | Campaign | Scope | Status | Document |
|---|---|---|---|---|
| 2026-09-09 | Current checkout engineering validation | Ruff, formatting, typing, documentation, unit/integration tests, GIF inspection | Passed locally: 1,450 unit and 37 integration tests; combined coverage 86.80% | [Chinese record](engineering-validation-2026-09-09.zh-CN.md) |
| 2026-09-09 | Generality campaign R7 | GLM and Qwen across 30 open-web, 5 sandbox, and 1 forced-resume task each | Completed 71/72; one retained Qwen sandbox false completion; longitudinal portfolio has only 1/3 required common dates | [Chinese record](generality-campaign-2026-09-09.zh-CN.md) |
| 2026-09-09 | Qwen strict-search paired validation R5 | Qwen and GLM browser-search-only discovery, official PDF acquisition, Figure 1 interpretation, observation integrity | Both endpoints passed 10/10 assertions and 6/6 certificate checks; bounded search failures recovered | [Chinese record](qwen-strict-search-2026-09-09.zh-CN.md) |
| 2026-09-07 | Previous checkout engineering validation | Ruff, formatting, typing, documentation, unit/integration tests, structure refactor | Historical local pass; superseded by the 2026-09-09 checkout record | [Chinese record](engineering-validation-2026-09-07.zh-CN.md) |
| 2026-09-01 UTC | `v6-final-r7` | First complete diagnostic date for GLM and Qwen | Interim; longitudinal and external layers incomplete | [Chinese report](v6-final-r7-2026-09-01.zh-CN.md) |
| 2026-09-02 | Qwen report mode case study | Four Hybrid/browser-grounded/strict trajectories | Historical analysis retained; associated local output bundle was removed during 2026-09-09 cleanup | [English analysis](qwen-report-modes-2026-09-02.md) |
| 2026-08-29 to 2026-09-01 | Engineering validation archive | Lint, typing, tests, packaging, parser probes | Historical checkout snapshots | [Chinese record](engineering-validation-2026-08-29-09-01.zh-CN.md) |

## Reading rules

- Treat each document as a dated snapshot, not the repository's live state.
- Read exact scores together with the task contract, source fingerprint, and limitations.
- Do not combine diagnostic success rates with BrowserGym rewards.
- Do not update a historical document merely because later code has more tests or a new
  benchmark adapter. Add a new verification or result snapshot instead.

The stable methodology is in [evaluation-protocol.md](../evaluation-protocol.md). Suite
commands and report formats are in the [benchmark guide](../../../src/webagent/benchmarks/README.md).
