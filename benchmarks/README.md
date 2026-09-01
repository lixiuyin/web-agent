# Benchmarks

The benchmark package is organized by research responsibility:

```text
benchmarks/
├── core/                         shared layout and tool-surface contracts
├── environments/controlled_web/ deterministic, independently judged HTTP sites
├── suites/
│   ├── open_web/                 public-web discovery and reading
│   ├── controlled_web/           general and sandbox interaction workflows
│   ├── document_figures/         document/figure detection and rendering
│   └── browsergym/               standard WebArena/VWA observation-action adapter
├── studies/                      repeated-model and longitudinal aggregation
└── manifests/                    versioned task and expectation snapshots
```

## Two-layer evaluation

The repository deliberately separates two kinds of evidence:

1. **Diagnostic layer:** the repository-owned open-web, controlled sandbox, and
   long-horizon suites below. These retain rich trajectories and failure diagnostics.
2. **External layer:** WebArena-Verified Hard and VisualWebArena, executed through
   BrowserGym's standard observation/action API and scored by their native evaluators.

The layers are never averaged. A model can be strong on one layer and weak on the
other, and both results remain visible. `two_layer_portfolio` is a readiness gate,
not a new composite leaderboard score.

BrowserGym 0.14.3 pins Playwright 1.44 and `greenlet` versions that require Python
3.12, while the main agent environment uses Python 3.13 and a newer Playwright.
VisualWebArena also brings a large Torch/captioning dependency. Install the external
layer into its intentionally isolated Python 3.12 environment:

```bash
scripts/setup_browsergym_env.sh
```

The installer also pins the BrowserGym packages, installs Chromium and the fixed NLTK
tokenizer resource, and validates the 258/910-task catalogs. VisualWebArena's native
image evaluator downloads `Salesforce/blip2-flan-t5-xl` on first use, so provision the
model cache and disk space before starting the full suite.

Then deploy the official WebArena and VisualWebArena sites and configure their URLs.
The isolated runner loads the repository `.env` before BrowserGym initializes.
The required WebArena variables are `WA_SHOPPING`, `WA_SHOPPING_ADMIN`, `WA_REDDIT`,
`WA_GITLAB`, `WA_WIKIPEDIA`, `WA_MAP`, and `WA_HOMEPAGE`. VisualWebArena uses
`VWA_CLASSIFIEDS`, `VWA_CLASSIFIEDS_RESET_TOKEN`, `VWA_SHOPPING`, `VWA_REDDIT`,
`VWA_WIKIPEDIA`, `VWA_HOMEPAGE`, and optionally `VWA_FULL_RESET`.

The standard matched-model external run is:

```bash
.venv/bin/python -m benchmarks.studies.browsergym_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash
```

This runs the official 258-task WebArena-Verified Hard subset and the full 910-task
VisualWebArena suite, each with BrowserGym's standard 30-step budget and deterministic
per-task seed schedule. Task name, seed, order, and source fingerprints are bound into
the execution contract. It is expensive.
For adapter/server calibration, pass explicit `--webarena-task-ids` and
`--visual-task-ids`; those reports are correctly marked `custom` and cannot satisfy
the publication-grade two-layer readiness gate. Interrupted executions retain a
partial report and can be continued with `--resume`; `--retry-errors` reruns only
episodes that recorded system errors.

BrowserGym writes its native episode evidence below the external execution's `runs/`
directory. `browsergym-results.json` records task-set identity, package versions,
an opaque backend-configuration hash, coverage, system errors, native mean reward,
binary success, a Wilson 95% interval, and task-level results. The matrix rejects
cross-model reports whose backend or package fingerprints differ, then adds exact
paired McNemar comparisons. It does not
reuse the repository's substring or terminal-state evaluator.

After the internal three-date portfolio and both external suites are complete, bind
them without pooling their scores:

```bash
.venv/bin/python -m benchmarks.studies.two_layer_portfolio \
  --diagnostic outputs/campaigns/<campaign>/analysis/portfolios/latest.json \
  --external \
    outputs/studies/browsergym-external-model-matrix/executions/date/model-a/webarena_verified/batch/browsergym-results.json \
    outputs/studies/browsergym-external-model-matrix/executions/date/model-a/visualwebarena/batch/browsergym-results.json \
    outputs/studies/browsergym-external-model-matrix/executions/date/model-b/webarena_verified/batch/browsergym-results.json \
    outputs/studies/browsergym-external-model-matrix/executions/date/model-b/visualwebarena/batch/browsergym-results.json \
  --output outputs/campaigns/<campaign>/analysis/two-layer-portfolio.json \
  --require-ready
```

The external list must contain one complete official WebArena-Verified Hard report
and one complete official VisualWebArena report for every diagnostic endpoint. Source
fingerprints must also match, so changing the agent or adapter starts a new comparison.

New code should import these canonical modules. The former flat modules, such as
`benchmarks.open_web` and `benchmarks.web_interaction`, are thin wrappers retained
for one compatibility cycle. Benchmark outputs default to
`outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/`;
individual task trajectories live below that execution's `runs/<task-id>/`, separate
from suite reports and cross-execution study records. Every default execution is new;
an explicit `--output` names one exact execution and refuses to replace existing run evidence.
Each execution separates `inputs/`, task `runs/`, execution-local
`ledger/time-slices.jsonl` when emitted, retained `evidence/`, derived `artifacts/`,
aggregate `analysis/`, and the complete `results.json` report. Matrix studies publish
an immutable `study.json`, retain its hash-addressed task input below
`inputs/task-manifests/`, and append typed task rows to the study-level
`ledger/runs.jsonl`.

## General web interaction

`web-interaction-v1` runs eleven deterministic tasks against a local HTTP site
through the real Chromium controller and `WebAgent` loop. The scenarios cover
multi-page navigation, cross-page lookup, form and dropdown submission,
server-side state mutation, delayed dynamic DOM controls, recovery from an HTTP
503 response, account login, tabular filtering, location lookup, booking, and
checkout.

First calibrate the environment with known actions:

```bash
python -m benchmarks.suites.controlled_web.general \
  --mode scripted-harness-baseline \
  --tool-set browser-only
```

The scripted harness baseline verifies the site, tools, trace collection, and
judge; it is not a model-quality result or a competitive agent baseline. To evaluate the configured planner without
domain-specific shortcuts:

```bash
python -m benchmarks.suites.controlled_web.general \
  --mode agent \
  --tool-set browser-only
```

Agent mode uses the normal `AGENT_MODEL_*` configuration (or local vLLM). Use
`--model` to name an explicit configured-provider model. Use
`--tool-set all` as a tool-availability ablation and
`--disable-loop-detection` as a recovery ablation. Each run uses a temporary
browser profile and resets server state between tasks.

`results.json` reports environment-grounded task success, weighted score,
agent-declared completion, false-completion rate, action validity, steps,
latency, planner attempts/tokens, category success, and final-answer grounding.
Page assertions and the site's independent JSON state determine interaction
success; answer assertions independently inspect required/forbidden facts and URLs.
Calling `done` never passes a task by itself.

Every report also emits `analysis/generality.json` and
`analysis/long-horizon.json`. Generality is fail-closed: a single suite cannot
claim breadth merely because it contains many tasks. The coverage floor requires
public and sandbox evidence, at least 30 tasks, eight categories, eight public
origins, development plus both held-out splits, at least five real search
discovery tasks, and explicit SPA, login, cross-origin form, file, transaction,
recovery, and discovery scenarios.
Long-horizon analysis separately reports repeated-action collapse and prolonged
same-page stagnation, plus replan and strategy-switch churn, so varied tool
thrashing is not hidden by tool entropy.

Repeat the real-model run rather than presenting a single lucky trajectory:

```bash
python -m benchmarks.studies.controlled_web_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash \
  --repetitions 3 \
  --max-steps-per-task 12 \
  --parallel-repetitions 3 \
  --minimum-success-rate 0.8 \
  --maximum-false-completion-rate 0.1
```

Immutable `analysis/matrices/<batch-id>.json` snapshots aggregate task-level counts
(not rounded run-level percentages), including success, false completion, action
validity, answer grounding, and token use. Root `matrix.json` is only an atomically
refreshed latest compatibility view/pointer, never the authoritative history.
Multiple model names are supported when the configured endpoint serves them.
Parallel repetitions use isolated browsers and sites, but may be reduced to `1`
when an API provider has a low concurrency quota.

For a preregistered baseline/intervention study, verify every canonical ledger
row against its retained `TaskEvaluation` before computing paired development
and held-out effects:

```bash
python -m benchmarks.studies.intervention_transfer \
  outputs/studies/<study-id> \
  --baseline-condition baseline \
  --intervention-condition memory-change \
  --output outputs/studies/<study-id>/analysis/intervention-transfer.json
```

This path rejects missing or escaped evidence, report hash drift, task/outcome
or registered-identity mismatches, taxonomy drift, and duplicate task-run cells.
The lower-level transfer function is a descriptive estimator; persisted study
analysis should enter through this verified command.

Each preregistered execution also retains the exact task-manifest bytes and
records both their SHA-256 and a canonical complete-task-set SHA-256 in
`execution.json`. The runner verifies those bindings before the first task and
again before ledger publication. A reordered, changed, or subset task list is
therefore a different execution contract, not a valid run of the existing
study.

## Dated open-web suites

`open-web-smoke-v1` runs a configured API/vLLM planner on several public domains:

```bash
python -m benchmarks.suites.open_web.runner \
  --manifest benchmarks/manifests/open_web_smoke.json
```

Every network task must declare source URLs, a snapshot ID, and an expectation
validity window. The runner rejects stale manifests, uses a temporary browser
profile, checks final-answer facts/citations plus observed URLs, and appends a
local append-only time slice to `ledger/time-slices.jsonl`, bound to retained `results.json`,
manifest, effective benchmark config, and task-set hashes. These checks expose local
editing or drift; they do not independently attest wall-clock truth. Use repeated dated
runs to measure volatility; one successful slice is not a general open-web claim.

The default `open-web-general-v2` manifest has 30 source-grounded tasks across
10 public domains. Ten tasks start at `about:blank` and must discover their official
source through real browser search under the strict certificate policy; twenty retain
direct starts to measure page-reading robustness independently of search:

```bash
python -m benchmarks.suites.open_web.parallel \
  --manifest benchmarks/manifests/open_web_general.json \
  --model z-ai/glm-5.3-flash \
  --shards 3 \
  --max-steps-per-task 8 \
  --discovery-max-steps-per-task 12
python -m benchmarks.studies.open_web_longitudinal \
  outputs/studies/open-web-model-matrix/ledger/time-slices.jsonl \
  --minimum-distinct-dates 3 --minimum-models 2 --expected-task-count 30
```

Collect a current-date slice for the two configured models with:

```bash
python -m benchmarks.studies.open_web_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --manifest benchmarks/manifests/open_web_general.json \
  --output outputs/studies/open-web-model-matrix --shards 3
```

There is deliberately no `--date` override. Re-run on at least three actual dates.
Serial model order rotates deterministically by UTC date to counterbalance provider
warm-up/rate-limit order effects, and same-date reruns are refused by default. Use
`--no-require-new-date` only for an explicitly documented same-day repetition.
The longitudinal gate accepts two or three models and requires dates common to every
model, exactly 30 tasks in every included repetition, and one manifest/config/task-set
hash. It reloads each retained `results.json`, recomputes its report/date/evidence
bindings, and averages same-day repetitions before comparing dates. This is fail-closed,
locally tamper-evident bookkeeping, not an external proof that the host wall clock was
truthful. Implementing the collector is not evidence that those future runs happened.
Readiness groups by the full `provider::model` endpoint, never by model text alone, and
requires one known immutable `study.json` hash. Legacy rows without provider or study
identity remain readable for inspection but cannot satisfy study-grade readiness.
The parallel runner gives every shard its own browser/profile, verifies identical
manifest/model/stealth/source-code provenance, rejects duplicate or missing task IDs,
and writes one recomputed report/history slice. Shards improve throughput; they are not dates.

## Stateful multi-origin sandbox

Public websites are never used for login mutation, uploads, bookings, or checkout.
A deterministic two-origin loopback suite safely covers a fetch-hydrated client-routed
SPA, cookie-backed authentication, a cross-origin multi-step form, browser download
followed by upload with SHA-256 verification, and a no-payment sandbox checkout:

```bash
python -m benchmarks.suites.controlled_web.sandbox \
  --mode scripted-harness-baseline
```

The scripted harness baseline calibrates Chromium, tools, policy, evaluator, and
server state; it is not a model score. Use `--mode agent --model ...` for a configured planner. Mutation
policy is enabled only after both origins have been verified as loopback sandbox origins.

## Long-horizon and cross-session recovery

The controlled long-horizon suite is a 60-stage stateful workflow. It contains
four cues that must survive 25–40-stage delays, an HTTP 503 interruption, independent
terminal JSON assertions, and an optional browser-session restart at step 35:

```bash
python -m benchmarks.suites.controlled_web.long_horizon \
  --mode scripted-harness-baseline --resume-at-step 35
python -m benchmarks.suites.controlled_web.long_horizon \
  --mode agent --model z-ai/glm-5.3-flash \
  --report-provider openrouter --resume-at-step 35 \
  --planner-max-tokens 1024 --planner-reasoning-effort low
```

The scripted run is only a harness calibration. A successful calibration must
show 65+ actions, `run_resumed`, a new temporary browser session, zero memory
errors, and the correct externally judged cue sequence. Agent runs additionally
have a bounded `remember` tool: it stores only short, explicitly non-sensitive
plain-text facts in controller state, survives checkpoints, and rejects URLs,
email addresses, credentials, and secret-like fields. This gives a model an
auditable memory mechanism without persisting cookies or arbitrary page text.

Long-horizon analysis reports short/medium/50+-action buckets, long-task success
and score, short-minus-long reliability degradation when both buckets exist,
checkpoint resumes, recovery transitions, tool entropy, repeated-action rate,
failure streaks, and behavior-collapse onset. Repetition is only flagged as a
collapse candidate when it is persistent and accompanied by an unchanged page
or tool failure; it is never labeled as a reasoning or memory cause automatically.

Collect a complete current-date slice across all three evidence surfaces:

```bash
python -m benchmarks.studies.generality_campaign \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash
```

The campaign runs the 30-task/10-origin open-web suite, five multi-origin
sandbox tasks, and the 60-stage task for each model. It content-hashes every
input report and produces `analysis/portfolios/<batch>.json`. Before allocating
browser work, one minimal real inference request per requested model is recorded
under `evidence/endpoint-probes/`. Provider/privacy/transport rejections are
excluded as unavailable endpoints rather than scored as model failures; the
requested and evaluated model sets remain explicit in both campaign and
portfolio evidence. The
preflight can be skipped only with the auditable `--skip-endpoint-preflight`
offline-harness option. Open-web execution is serial by default so shared-provider
rate limits do not become a model-quality confound; pass `--shards` explicitly only
when the endpoint has an independently controlled quota. Campaign state is written
atomically as `running`, `failed`, or `completed`, and retained report paths are
relative to the campaign root for portable release artifacts. Re-run on three
actual UTC dates; there is no date override. A portfolio becomes `ready` only
when every comparable provider/model/date cell contains all three complementary suites,
passes the coverage floor, includes a 50+-action trajectory, and the same two or
three endpoints share at least three complete dates. Comparable dates must also
share one immutable agent-source fingerprint and one immutable benchmark-source
fingerprint. After changing either source tree, start a new campaign root rather
than appending to an earlier longitudinal condition. Failed tasks remain valid
empirical outcomes when planning was actually available; scripted harness
reports are rejected as model evidence.
Direct open-web tasks retain an eight-action default while genuine search-discovery
tasks receive twelve actions. Override these independently with `--open-max-steps`
and `--open-discovery-max-steps`.

To audit an explicit set of retained reports independently:

```bash
python -m benchmarks.studies.generality_portfolio \
  outputs/studies/.../results.json \
  --output outputs/studies/.../analysis/generality-portfolio.json
```

For a browser-search-only report/PDF/Figure run, use the dedicated discovery
manifest and strict policy:

```bash
python -m benchmarks.suites.open_web.runner \
  --manifest benchmarks/manifests/qwen_strict_search.json \
  --search-engine-only
```

The task starts at `about:blank`; source URLs and answer facts remain evaluator-only.
The v8 contract requires current-year broad and release-landscape searches, an
exact follow-up for the highest dotted subject version observed in any SERP, official
identity plus same-owner scope evidence, planner-visible URL provenance, one continuous
run ID, and a SHA-256-bound certificate. The output volume must have at least 512 MiB
free, and the runner roots its temporary Chromium profile below that output directory.
Use an external output volume when the system disk is constrained.
The Qwen task also uses a labeled-date assertion: `Selected report date:` must begin
with the expected calendar date (ISO or an equivalent English rendering). A different
claimed date cannot pass merely because the expected date appears elsewhere in the answer.

CAPTCHAs are never solved or bypassed. Benchmarks fail closed. An ordinary visible
run defaults to `report`, which logs the challenge and polls only until the user
manually clears it or the configured timeout expires. Timeout and ordinary headless
runs block and close. Strict headless search is confined to the audited Bing, Yahoo
Japan, and Seznam pool; a challenged task blocks immediately, while its isolated
browser is retained only for the mandatory state reset before the next task so one
challenge cannot corrupt the remaining matrix.

## Browser and authorization boundaries

Ordinary CLI runs are isolated and browser-grounded by default: direct arXiv/GitHub/
combined discovery APIs are hidden, discovery tasks without a user URL or loaded page must begin
with browser search, and `goto`, `open_tab(url)`, and PDF downloads must use a URL
supplied by the user or observed in planner-visible browser/search evidence.
`--discovery-mode hybrid` is the explicit API-augmented ablation. Strict evaluation
adds first-search, recency, ownership, continuous-run, and certificate requirements.

The browser tool surface includes tabs, iframes, open Shadow DOM, file input, and
download capture. Upload paths are confined to `AGENT_BROWSER_UPLOAD_ROOT`.
High-risk calls are denied by default; `--high-risk-actions prompt` requests terminal
confirmation without echoing sensitive parameters, while `allow` is an explicit
trusted-run override. Synthetic checkout calibration opts into `allow` and records it.

## PDF Figure fast path

This offline benchmark generates ten deterministic PDFs rather than relying on
mutable websites or cloud parsers. It covers vector and raster figures, captions
above and below graphics, multiple figures, a nearby logo, fragmented vector
paths, a two-column layout, a landscape page, and two negative documents.

Run it from the repository root:

```bash
python -m benchmarks.suites.document_figures.fast_path
```

`results.json` reports detector precision/recall, default-threshold fast-path
coverage and fallback rate, false bypasses, crop coverage/purity, render success,
per-document details, and mean/p95 local detection latency. Generated PDFs live in
the execution's `inputs/corpus/`; rendered crops live in `artifacts/renders/`. Both
stay below the selected execution under `outputs/studies/` and are not committed.

The benchmark is deliberately geometry-grounded: a detection is a true positive
only when its figure number and page match and its crop covers at least 85% of
the known figure while keeping at least 55% crop purity. Ambiguous real PDFs must
fall back to the structured parser rather than weakening these thresholds.
