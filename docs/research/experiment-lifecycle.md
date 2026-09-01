# Experiment lifecycle and artifact contract

## 1. Define before running

A benchmark task declares its task family, setting, split, target failure
modes, feedback condition, and verifiable assertions.  A study additionally
fixes the model/provider, system condition or intervention, repetition, date,
budgets, source fingerprint, and manifest hash.  Fields that are not known are
left missing; they are never reconstructed from a result after the fact.

## 2. Execute and retain one run

The output workspace and a run directory are different objects. A default CLI
run receives a unique directory at
`outputs/runs/<UTC-date>/<model>/<task>-<run-id>/`; starting another run must
not clear previous runs or studies. An explicit CLI `--output` names the exact
run root.

```text
<run>/
├── manifest.json
├── trajectory/
│   ├── trace.json
│   ├── verification.json
│   └── turns/
│       └── turn-NNN.json
├── observations/
│   └── screenshots/
├── control/
│   └── checkpoints/
│       ├── latest.json
│       └── latest.json.bak
├── artifacts/
│   ├── downloads/
│   ├── documents/
│   ├── figures/
│   └── files/
├── result/
│   ├── summary.txt
│   ├── attachments/
│   └── turns/
│       └── turn-NNN/
│           ├── summary.txt
│           └── attachments/
└── evaluation/
```

The namespaces have deliberately different meanings:

- `trajectory/` is auditable execution evidence, not a task-produced file.
- `observations/` contains what the controller observed.
- `control/` is resumable state and must not be treated as outcome evidence.
- `artifacts/` contains files acquired or derived while doing the task.
- `result/` is the agent's claim.
- `evaluation/` is an external judgment of that claim and terminal state.

Run namespaces are materialized on first write. Their absence is therefore meaningful
(`artifacts/` absent means no task artifact was produced) and archives do not accumulate
empty placeholder trees.

An ordinary interactive session retains one owned run rather than cleaning it
between follow-ups. Step and turn numbers increase monotonically. The canonical
`trajectory/trace.json` and `result/` files expose the latest turn, while the two
`turns/` namespaces are published atomically and never replaced, preserving
earlier turn evidence and attachments. Strict/search-only evaluation forbids
multi-turn runs so its certificate still represents one continuous task.

Legacy readers continue to recognize the former trace, certificate, and
checkpoint files below a run's `artifacts/` directory; new writers use only the
canonical locations shown above. These read fallbacks do not authorize new code
to write the legacy layout.

## 3. Judge independently

The benchmark evaluator checks browser/server terminal state, answer content,
tool history, artifact hashes, and strict-run certificates independently from
the agent's `done` text.  Agent-reported completion and empirical success remain
separate fields so false completion is measurable.

`success_probability` is elicited before external judging. A successful `done` may
report it directly; benchmark runs also perform a bounded terminal elicitation so
timeout, failure, blocked, and max-step outcomes are covered rather than silently
missing. It refers only to whole-task success. CAPTCHA and figure-detector confidence
are different quantities and are never reused for task calibration.

## 4. Analyze without overstating attribution

Automatic failure rules produce an `observed` fact or a `candidate`
attribution.  A failed assertion can establish that an answer was unsupported;
it does not by itself establish an internal reasoning or memory mechanism.
Those causal labels require trace evidence plus adjudication or a controlled
intervention.
Failed semantic and URL assertions are additionally exported to
`analysis/adjudication-queue.json`. Queue membership does not change the score and is
not a causal label; it identifies cases requiring trace review.

Calibration reports state confidence coverage before Brier/ECE values. Reports with
some but not all task probabilities are explicitly `partial`, not `available`. A
transfer report is unavailable when a required split is absent; it does not
silently treat development tasks as held-out data.

Generality reports list the exact missing scenario, origin, environment, and
split requirements. Long-horizon reports remain unavailable until at least one
trajectory reaches 50 observable actions. Tool repetition and low entropy are
behavioral signals only; the automatic analyzer does not convert them into a
reasoning or memory cause.

## 5. Compare in a study

```text
outputs/studies/<suite>/
├── study.json                         # when a study definition is materialized
├── inputs/
├── ledger/
│   └── runs.jsonl
├── evidence/
├── analysis/
└── executions/<UTC-date>/<model>/<condition>/<execution-id>/
    ├── inputs/
    ├── runs/<task-id>/                 # one canonical RunLayout per task
    ├── ledger/time-slices.jsonl         # execution-local dated evidence when emitted
    ├── evidence/
    ├── artifacts/
    ├── results.json                    # complete execution report
    └── analysis/
        ├── failures.json
        ├── calibration.json
        ├── transfer.json
        ├── generality.json
        └── long-horizon.json
```

Without benchmark `--output`, a new execution ID is allocated so earlier
evidence is not overwritten. An explicit benchmark `--output` names one exact
execution directory. Its `execution.json` claim binds the study ID, retained
task-manifest SHA-256, and canonical ordered task-set SHA-256 before the first
task executes. Subsets, reordered tasks, changed semantics, and budget drift
fail before collection and are checked again before ledger publication.
Only runs sharing the declared task snapshot and comparison contract should be
aggregated. Development gains, held-out-task gains, and
held-out-setting gains are reported separately. Historical outputs moved into `outputs/legacy/` keep
their original bytes and receive a migration inventory; missing research
metadata is not backfilled.

## 6. Coordinate multiple studies in a campaign

```text
outputs/campaigns/<campaign-id>/
├── campaign.json                     # immutable provider/model/source/budget contract
├── studies/                          # canonical component StudyLayout roots
│   ├── open-web/
│   ├── sandbox-interaction/
│   └── long-horizon/
├── batches/<UTC-date>/<batch-id>/
│   ├── batch.json                    # atomic running/completed/failed state
│   ├── evidence/endpoint-probes.json
│   ├── logs/
│   └── analysis/portfolio.json
└── analysis/portfolios/latest.json   # derived cross-date compatibility view
```

Changing the model set, task manifest, source fingerprint, budgets, CAPTCHA policy, or
preflight policy requires a new campaign root. This prevents a convenient folder name
from silently combining incomparable executions.

The external BrowserGym layer is retained as a separate study rather than inserted
into the diagnostic `results.json` population:

```text
outputs/studies/browsergym-external-model-matrix/
├── browsergym-matrix-state.json
├── executions/<date>/<model>/
│   ├── webarena_verified/<batch>/
│   │   ├── browsergym-execution.json
│   │   ├── browsergym-results.json
│   │   └── runs/<task-episode>/
│   └── visualwebarena/<batch>/...
├── evidence/logs/
└── analysis/matrices/<batch>.json
```

The final `two-layer-portfolio.json` references and hashes both the internal campaign
portfolio and external reports. It contains per-layer scores only; there is no pooled
"overall" score because the task populations and judges are different. Official
readiness requires WebArena-Verified Hard and full VisualWebArena for every diagnostic
endpoint, with complete task coverage, zero unscored system errors, and matching source
fingerprints. External execution contracts additionally bind BrowserGym's canonical
ordered task names and deterministic per-task seeds; changing either invalidates a
paired comparison.

Formal comparison metadata uses the packaged v1 JSON Schemas for `study.json`
and each study run record. Writers retain `$schema`, `schema_version`, immutable
model/condition/budget identity, task split, collection date, evidence paths and
hashes, plus optional whole-task `success_probability`. Unknown fields are
rejected rather than silently changing the comparison contract.

Persisted intervention analysis must use
`python -m benchmarks.studies.intervention_transfer ...`; it reloads every
`ledger/runs.jsonl` row, confines its evidence paths to the study, rehashes and
parses the retained task evaluation, checks preregistered identity and taxonomy,
then computes paired effects. Passing hand-built in-memory records directly to
the descriptive estimator is not retained study evidence.

Inventory a legacy tree before applying the move:

```bash
python -m webagent.evaluation.migration outputs --label pre-workspace-v1
python -m webagent.evaluation.migration outputs --label pre-workspace-v1 --apply
```

The first command is read-only. The second verifies size and SHA-256 for every
file, moves complete top-level legacy entries under
`outputs/legacy/pre-workspace-v1/tree/`, and writes
`migration-manifest.json`. Symlinks and inconsistent bytes fail closed.
