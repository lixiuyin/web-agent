# Experiment lifecycle

This document explains how a research claim moves from a declared task to retained
evidence, independent judgment, analysis, and a cross-run comparison. Filesystem layouts
and checkpoint contents are owned by the
[run-artifact reference](../reference/run-artifacts.md).

## 1. Define before running

A task declares its family, environment, split, target failure modes, feedback condition,
and verifiable assertions. A study additionally fixes:

- provider and model identity;
- baseline or intervention condition;
- repetition and actual collection date;
- action, wall-clock, and token budgets;
- task manifest and complete task-set hashes;
- agent and benchmark source fingerprints;
- evaluator and report schema versions.

Unknown fields remain missing. They are not reconstructed from outcomes after the fact.

## 2. Execute without overwriting evidence

An ad-hoc CLI invocation owns one run. A suite owns one execution containing task runs. A
study owns immutable executions and ledger rows. A campaign owns a fixed multi-suite,
multi-model, cross-date contract.

Default allocation always creates a new identity. Explicit `--output` names one exact run
or execution and refuses to overwrite retained evidence. Interactive follow-ups can share
one ordinary run through immutable turn snapshots; strict evaluation remains single-turn.

During execution, keep these roles separate:

| Evidence class | Meaning |
|---|---|
| Trajectory | Observable browser, planner, tool, and policy events |
| Controller state | Recovery mechanism; not outcome evidence |
| Artifacts | Downloaded or derived task files |
| Agent result | The model's claim and attachments |
| External evaluation | Independent terminal, fact, URL, history, or file judgment |

## 3. Judge independently

The evaluator inspects browser/server terminal state, answer content, tool history,
artifact hashes, and strict certificates independently from the agent's `done` text.
Agent completion and empirical success remain separate so false completion is measurable.

`success_probability` refers to whole-task success and is elicited before external
judgment. Missing probabilities remain missing. CAPTCHA confidence, Figure-detector
confidence, and parser quality are different quantities and are never reused as task
calibration.

## 4. Analyze observable failures

Automatic rules produce an `observed` fact or a `candidate` attribution. A failed answer
assertion establishes that the requirement was not met; it does not establish an internal
reasoning or memory cause.

Semantic and URL failures can enter an adjudication queue without changing their scores.
Calibration reports state confidence coverage before Brier/ECE. Transfer remains
unavailable when a required split is missing. Generality lists exact missing task, origin,
environment, and scenario requirements. Long-horizon analysis requires a sufficiently
long observed trajectory and treats repetition or low entropy as behavior, not cause.

The complete attribution vocabulary is in [failure taxonomy](failure-taxonomy.md).

## 5. Compare within a study

An execution binds the exact task-manifest bytes and ordered task-set identity before the
first task and verifies them again before ledger publication. A subset, reordered list,
changed assertion, or budget change is a new execution contract.

Only rows sharing the declared snapshot and comparison identity may be aggregated.
Development, held-out-task, and held-out-setting effects remain separate. A persisted
baseline/intervention analysis reloads retained evaluations, verifies their hashes and
registered identities, rejects duplicate cells or escaped paths, and only then computes
paired effects.

Study commands and resume semantics belong to
[running benchmark studies](../../benchmarks/docs/running-studies.md). Report fields and
readiness belong to [benchmark report contracts](../../benchmarks/docs/report-contracts.md).

## 6. Coordinate a campaign

A campaign fixes the provider/model set, component studies, task manifest, source
fingerprints, budgets, CAPTCHA policy, endpoint preflight, and date collection policy.
Each collection attempt has isolated atomic state, endpoint probes, logs, and a batch
portfolio. Cross-date analysis references complete batch evidence without flattening it.

Changing any bound comparison variable requires a new campaign root. A convenient folder
name must not combine incomparable dates or source revisions.

The external BrowserGym matrix remains a separate study. It retains native episode
evidence and evaluator reports rather than inserting foreign task rewards into the
diagnostic task population. The final two-layer portfolio references and hashes both
layers without a pooled score.

## 7. Publish a bounded claim

Before reporting a result, verify:

1. The task and comparison contract was declared before execution.
2. Every included row resolves to retained, hash-matching evidence.
3. Unavailable endpoints and system errors were not converted into ordinary model
   failures.
4. Scripted calibration was not presented as model performance.
5. Missing dates, splits, confidence, tasks, or external infrastructure remain explicit.
6. The wording matches the evidence level: implemented, tested, observed, external
   requirement, or proposed.

Local hashes detect drift but do not provide an independent trusted timestamp. A release
should retain the source tag, dependency lock, immutable contracts, reports, and an
independent checksum or read-only archive.

Historical output can be inventoried and migrated with
`webagent.evaluation.migration`; migration preserves bytes and records hashes but never
invents missing research metadata.
