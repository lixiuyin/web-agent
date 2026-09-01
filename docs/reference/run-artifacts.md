# Run artifacts and recovery state

WebAgent separates what the agent claimed, what the browser observed, what the controller
needs for recovery, and what an independent evaluator concluded.

## Ad-hoc run layout

Without `--output`, the CLI allocates a unique directory below
`outputs/runs/<UTC-date>/<model>/<task>-<run-id>/`. With `--output`, the supplied path is
the exact run root.

```text
<run>/
├── manifest.json
├── trajectory/
│   ├── trace.json
│   ├── verification.json          # strict evaluation only
│   └── turns/turn-NNN.json        # immutable ordinary-session snapshots
├── observations/
│   └── screenshots/
├── control/
│   └── checkpoints/
│       ├── latest.json
│       └── latest.json.bak
├── artifacts/
│   ├── downloads/
│   ├── documents/<document-id>/
│   ├── figures/
│   └── files/
├── result/
│   ├── summary.txt
│   ├── attachments/
│   └── turns/turn-NNN/
└── evaluation/
```

Optional namespaces appear on first write. An absent directory means that the run did
not produce that evidence class.

## Ownership by namespace

| Namespace | Owner | Meaning |
|---|---|---|
| `manifest.json` | Runtime | Run identity, configuration boundary, and source information |
| `trajectory/` | Runtime recorder | Observable execution evidence and strict certificate |
| `observations/` | Browser layer | Screenshots captured for individual steps |
| `control/` | Controller | Recoverable state; not research evidence by itself |
| `artifacts/` | Tools and parser | Acquired and derived task files |
| `result/` | Agent | Final claim and published attachments |
| `evaluation/` | External evaluator | Judgment independent of the agent's `done` action |

Do not treat `result/summary.txt` as task success without the corresponding evaluator or
manual evidence review.

## Interactive turns

An ordinary interactive session owns one run. Canonical trace and result files represent
the latest turn, while `trajectory/turns/` and `result/turns/` retain immutable,
monotonically numbered snapshots. Strict/search-only evaluation is single-turn by
contract.

## Checkpoints

Ordinary runs atomically update a checksummed checkpoint after each step and before a
potentially ambiguous action. The checkpoint binds:

- task SHA-256, not the task plaintext;
- behavior-affecting configuration;
- source fingerprint;
- browser coordinates and controller state;
- policy and loop state;
- hashes of referenced artifacts.

It intentionally excludes free page text, model rationale, form values, URL credentials,
absolute local paths, cookies, and local storage. An unresolved click, form, upload, or
other possibly state-changing action is not silently replayed.

Completed, blocked, strict, and search-only runs cannot be resumed. A trusted login that
must survive process restart requires an explicitly persistent browser profile; the
checkpoint itself does not preserve authentication state.

## Benchmark and campaign layouts

Benchmark execution roots use:

```text
outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/
```

They separate declared/generated `inputs/`, task `runs/`, append-only `ledger/`, retained
`evidence/`, derived `artifacts/`, aggregate `analysis/`, and the complete `results.json`.

Campaigns use:

```text
outputs/campaigns/<campaign-id>/
├── campaign.json
├── studies/
├── batches/<UTC-date>/<batch-id>/
└── analysis/
```

The immutable campaign contract binds model, provider, task manifest, source hashes,
budgets, and collection policy. Batch-local probes, logs, state, and results remain with
their collection attempt; cross-date portfolio views live under campaign analysis.

See [benchmark report contracts](../../benchmarks/docs/report-contracts.md) for scoring
and readiness semantics.

## Retention and publication

`outputs/` is ignored by default because it can contain large media and locally disclosed
content. Publish only an explicitly reviewed evidence bundle. Remove credentials,
personal data, cookies, local-storage state, absolute paths, and unneeded downloads before
tracking it.

Selected binary evidence can use Git LFS, but release wheels and source distributions
must continue to exclude every `outputs/` path. A published bundle is evidence, not the
source of truth for runtime behavior or documentation.
