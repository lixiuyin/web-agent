# Evaluation protocol

This document owns the stable methodology for repository diagnostics and external
BrowserGym evaluation. Dated scores and observed failures belong in
[research results](results/README.md); executable commands belong in the
[benchmark guides](../../benchmarks/README.md).

## Two evidence layers

| Layer | Components | Purpose |
|---|---|---|
| Repository diagnostic | Dated open web, controlled sandbox, and forced-resume long horizon | Locate failures in discovery, reading, interaction, files, memory, recovery, and terminal verification |
| BrowserGym external | WebArena-Verified Hard and VisualWebArena | Provide standard observation/action and native-evaluator evidence |

The layers use different task populations and scoring functions. They remain separate;
`two_layer_portfolio` checks completeness and binds reports without averaging scores.

## Diagnostic task unit

One `provider/model/date` unit contains 36 tasks:

- 30 public-web tasks from 10 official-source families;
- 5 deterministic multi-origin sandbox tasks;
- 1 controlled 60-stage task with forced checkpoint recovery.

The open-web set separates discovery from reading. Ten tasks start at `about:blank` and
must find an official source through browser search; twenty start on official pages. The
sandbox covers SPA hydration, authentication, cross-origin forms, file handoff, and
no-payment checkout. The long task delays four cues, injects a transient failure, and
forces a browser-session restart.

Task definitions are versioned under `benchmarks/manifests/` and
`benchmarks/suites/controlled_web/`.

## Frozen comparison variables

A comparable longitudinal campaign binds:

- provider and exact model identifiers;
- task manifest and complete task-set hashes;
- agent and benchmark source fingerprints;
- model execution order policy;
- planner and per-suite step/token budgets;
- browser channel, viewport, headless/profile policy, and search mode;
- CAPTCHA and high-risk-action policies;
- parser/cache behavior;
- endpoint preflight policy;
- evaluator and report schema versions.

Changing a bound value creates a new campaign. It must not be appended to an earlier
condition after inspecting results.

## Independent judgment

`done` is an agent declaration, not a passing criterion. Evaluators independently check
the task's required terminal state, facts, URLs, browser history, file hashes, and
forbidden outcomes.

| Metric | Interpretation |
|---|---|
| Task success | Every required assertion passes |
| Mean score | Weighted partial assertion result; not strict success rate |
| Agent completion | Agent submitted `done` |
| False completion | Agent submitted `done`, but strict judgment failed |
| Action validity | Executed tools that returned success |
| Answer grounding | Required answer facts and references that passed |
| Mean steps/duration | Descriptive action and wall-clock cost |
| Planner failures | Observable attempts without an executable action |
| Collapse/stagnation | Observable repeated action or unchanged browser state |
| Calibration | Self-reported success probability versus judged outcome |

Failure taxonomy records observable symptoms. A tool failure, planner failure, or false
completion is not automatically labeled as a reasoning, memory, browser, or upstream
provider cause.

## Longitudinal readiness

The diagnostic portfolio becomes ready only when every requested endpoint has a complete
36-task unit on at least three common real UTC dates under unchanged fingerprints.
Same-day repetitions do not replace dates. Scripted harnesses calibrate infrastructure and
are rejected as empirical model evidence.

Coverage checks require development, held-out-task, and held-out-setting splits; adequate
task/category/origin breadth; real browser-search discovery; and explicit interaction,
file, recovery, and 50+-action evidence.

Generality readiness means the preregistered coverage floor was met. It does not prove
general web-agent maturity.

## External readiness

For every diagnostic endpoint, the external layer requires:

- all 258 WebArena-Verified Hard tasks;
- all 910 VisualWebArena tasks;
- native BrowserGym evaluators;
- matching site/backend and package fingerprints across compared models;
- official task identities and deterministic seed schedules;
- no custom calibration subset substituted for the full suite.

System errors remain separate from ordinary model failures. Backend unavailability is not
converted into a zero model score.

## Transfer and calibration boundaries

Baseline/intervention analysis verifies retained ledger rows and keeps development,
held-out-task, and held-out-setting effects separate. Missing confidence values are not
imputed. Brier score and ECE must report confidence coverage and sample size.

A single model/date comparison supports only a dated descriptive statement. Causal or
stable superiority claims require preregistered interventions, matched execution, and
adequate repetitions.

## Evidence retention

Each report binds task input, source, configuration, and retained evaluations by hash.
This makes local drift detectable but does not create an independent trusted timestamp.
Publication should retain source tags, dependency locks, campaign/study contracts,
reports, and independent checksums or a read-only archive.

Artifact namespaces and controller/evidence ownership are defined in the
[run-artifact reference](../reference/run-artifacts.md). Failure attribution rules are in
[failure-taxonomy.md](failure-taxonomy.md).
