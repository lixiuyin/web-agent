# Run artifacts and recovery state

Loop detection excludes the current observation nonce from page/action fingerprints,
while preserving actual content and nested selector differences. Parameter fingerprints
are hashes, not retained form values. Distinct form-entry actions are not treated as
page stagnation merely because visible text stays unchanged. Research-loop hints are
task-neutral and do not introduce an unrequested PDF workflow.

Historical policy hints are labelled as past state; the current candidate ledger governs
the next action. Once deliverables and evidence gates are satisfied, the planner receives
an explicit completion hint. Latest-PDF completion preflight requires an observed selected
file date in ISO form in the summary; this formatting guard does not independently grade
the answer's meaning or prove global latestness.

For latest-report tasks, an observed official PDF is inspected on its own file page
before more release searches. Search preflight redirects premature query rewrites
to that inspection; a recorded failed inspection permits recovery instead of an
endless retry. Download provenance and strict scope requirements remain enforced.
Parameter sizes such as `Model-1.8B` are not version leads, and the pending-variant
queue excludes generations older than the highest observed generation.

Incomplete runs retain an explicit handoff with the stop reason, observed candidate,
remaining evidence and challenge details. A verifier uses the last recorded policy
state even without `done`, while still failing the overall incomplete run. Historical
empty answers are reconstructed only in the derived reader and labeled as such.

Run completion refreshes both model and date indexes under the standard layout.
The reader merges action-journal and runtime events chronologically, including
CAPTCHA termination. To regenerate all nested readers/indexes without modifying
historical traces, certificates or screenshots, run:

```bash
uv run python scripts/render_run_report.py outputs/runs/2026-09-08
```

For benchmark roots containing both `shards/shard-NN/runs/` and promoted `runs/`
copies, this offline command collapses duplicate catalog rows only when the run ID
and complete trace content agree after normalizing the run-root path. Conflicting,
unpromoted, corrupt or unidentified traces remain visible. Raw files and shard-level
catalogs are retained; collapsed copies are not additional evaluated episodes.
New promotions hardlink byte-identical immutable files when the filesystem permits and
fall back to ordinary copies across filesystems. Existing campaigns can be inspected and
compacted without deleting either logical path:

```bash
uv run python scripts/compact_run_artifacts.py outputs/campaigns/<campaign>          # dry run
uv run python scripts/compact_run_artifacts.py outputs/campaigns/<campaign> --apply  # exact matches only
```

The compactor requires matching run IDs, normalized traces, file bytes, modes and mtimes.
It skips divergent or mutable-looking files and never deduplicates through symlinks.
Offline catalog rows show short run/model labels with expandable saved paths and
separate execution, independent task-assertion and compliance columns. Missing or
unreadable judgments are labeled explicitly, never inferred from `completed`.

Unverified sibling release mentions can remain unresolved after a follow-up search.
When the selected official PDF has its own date but only these sibling leads remain,
the policy permits saving partial findings after completing the PDF/figure work.
The final result is explicitly qualified, the run is `blocked` with `success=false`,
and latestness verification still fails. Search attempts never turn into proof of
absence. Higher-version leads and newer dated PDFs still prevent this bounded stop.

Vision responses use only final assistant content, never `reasoning_content`.
Token-limit truncation is a failed analysis, not a completed figure deliverable.
`AGENT_VISION_MAX_TOKENS` defaults to 8192 to leave room for reasoning models;
the requested answer word limit remains separate.

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
│   ├── events.jsonl               # append-only action/capture lifecycle
│   └── turns/turn-NNN.json        # immutable ordinary-session snapshots
├── observations/
│   ├── step_NNN/
│   │   ├── pre.json / pre.png
│   │   ├── post.json / post.png
│   │   └── pre.full.png / post.full.png  # optional supplemental captures
│   └── screenshots/step_NNN.jpg         # legacy post-action preview
├── control/
│   └── checkpoints/
│       ├── latest.json
│       └── latest.json.bak
├── blobs/<sha256>.png             # content-addressed observation images
├── evidence/tool-results/<sha256>.json  # full redacted tool results
├── report/index.html              # offline timeline reader
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
| `observations/` | Browser layer | Paired DOM projections, geometry, and screenshots |
| `control/` | Controller | Recoverable state; not research evidence by itself |
| `artifacts/` | Tools and parser | Acquired and derived task files |
| `result/` | Agent | Final claim and published attachments |
| `evaluation/` | External evaluator | Judgment independent of the agent's `done` action |

Do not treat `result/summary.txt` as task success without the corresponding evaluator or
manual evidence review.

For strict tasks, inspect `evaluation/task.json` and
`trajectory/verification.json` together. The first owns semantic assertions; the second
owns the anti-shortcut and artifact-integrity contract. A nonzero failed-action or
planner-failure count stays visible as diagnostic evidence and does not automatically
override a pass, but only bounded, recovered external failures are acceptable. Missing
deliverables, false completion, invalid provenance, unresolved challenges, or broken
observation pairs remain failures.

## Observation coverage and timing

Each step saves `pre.json` before planning and `post.json` after executing its action.
The browser-side-effect-free `done` action reuses the pre-observation for its post record.
The JSON includes the exact DOM summary, separate `viewport_context`/`document_context`,
their collected blocks before prompt budgeting, selected interactive elements,
URL, capture timestamp, viewport dimensions, document dimensions, scroll offset, and
image paths with SHA-256 hashes. `trace.json` links executed steps to these files through
`steps[].observations`. Failed planning attempts link to the pre-observation and record an
explicit `not_attempted` post placeholder, without reusing pixels or DOM as an action result.
The legacy `screenshots/step_NNN.jpg` remains a post-action
preview for executed actions; on planning failure it shows the pre-observation.
If recovery replans an interrupted step, the new pair lives in a unique
`step_NNN/capture_<id>/` directory. Earlier attempt references remain immutable; use
the paths in the trajectory rather than assuming every pair is at the step root.

The primary PNG captures the current viewport. The planner receives two explicitly named
sections: viewport content covers that same region; document supplement contains off-screen,
clipped or occluded material and is not visual evidence. Rendered text is extracted with
DOM Range geometry, including partial lines and ancestor overflow clipping. Control labels
are explicitly semantic metadata, not necessarily painted text. Open shadow roots and frames
are inspected; frame-local coordinates are mapped to the main viewport. Unsupported frame
transforms or inaccessible frames are reported instead of inventing visible content.

Controls expose frame-document `bbox`, top-level `viewport_bbox`/`visible_bbox`, frame-local
`frame_viewport_bbox`, `in_viewport`, sampled `receives_events`, and `enabled`. Hit tests
are not pixel-perfect occlusion analysis: transparent pointer-event overlays, canvas content,
closed shadow roots and complex transforms remain boundaries. The viewport projection is
not OCR. Captures compare URL, geometry, node identities and rendered-state fingerprints,
but sequential DOM/image reads are not an atomic freeze of a dynamic page.

Consequently, a complete-page DOM supplement does not make `scroll` obsolete. It can
support reading already-loaded off-screen text, but scrolling is still required to load
lazy or virtualized content, refresh an observation-bound target into the viewport, and
pass native actionability and hit-testing checks before mutation.

Controls are serialized as `[observation_id/fN:eN] tag label=...` with compact state flags.
Use `selector={"type":"ref","value":"observation_id/fN:eN"}` to address them. Full CSS
paths and the original node bindings remain internal and in the saved element records;
the model does not need to repeat them. Legacy CSS selectors with `observation_id` and `ref`
remain compatible. Ordinary click, type, press, hover, select, frame/shadow interaction,
text/attribute reads and scrolling to an element support these references.
The registry dispatches them after normal execution
and risk authorization. They resolve to the original DOM node; stale, mismatched, obscured
or off-screen interaction targets fail rather than silently resolving a replacement with
the same CSS selector. `force` cannot bypass reference checks. Use `scroll_to_element`
first for off-screen targets, then use the fresh observation. Iframe actions additionally
require their `frame_index`; `scroll` also accepts an optional frame index.

Capture consistency and action validity are separate checks. Capture still checks the
collected rendered-state fingerprint across DOM and screenshot reads. Actions validate
the original target, its ancestor identities, effective geometry/clipping/visibility,
attributes, associated labels and form destination, plus the containing iframe chain.
Unrelated text changes do not invalidate an otherwise unchanged target. Navigation,
scrolling, replacement, relevant target changes or a newer observation require fresh
references. Execution-time hit testing and native actionability checks remain mandatory;
there is no JavaScript-click fallback through an overlay.

Read-only `get_attribute`/`extract_text` may access an observed off-screen node without
scrolling; they still validate node identity and staleness, and their output is document
evidence rather than pixel evidence. Mutating interactions retain viewport/hit-test gates.
Planner validation rejects references from a previous observation before spending an
environment action; it never silently rewrites an old reference to a new target.

Legacy CSS/text selectors remain supported with their existing semantic action behavior.
Wait/upload/download workflows retain their plain selector schema and existing validations.
Observation-bound actions are not a new authorization bypass, and only inspected references
are accepted. Unknown actionability still relies on Playwright's execution-time checks.

Context budgets keep complete controls and text blocks. Long text is split at sentence
and line boundaries and wrapped into bounded chunks (including unbroken CJK text).
`AGENT_OBSERVATION_TEXT_BLOCK_CHARS` (default 400) limits each text block including its
frame prefix when the available text reservation permits it. Configure
`AGENT_OBSERVATION_VIEWPORT_CHARS` (default 5000) and
`AGENT_OBSERVATION_DOCUMENT_CHARS` (default 2500) independently. Within each scope,
`AGENT_OBSERVATION_TEXT_SHARE` (default 0.5) reserves capacity for text, with the rest
available to controls; unused capacity is reclaimed. `context_budget_usage` records
selected characters and omitted block counts separately for text and controls in the
observation metadata and planner input. Collection is bounded per
frame by `AGENT_OBSERVATION_MAX_DOM_NODES` (5000) and
`AGENT_OBSERVATION_MAX_TEXT_CHARS` (80000); omitted blocks and frame collection limits are
reported in the observation and planner input. This does not enumerate content that has
not been loaded; scroll remains necessary for lazy loading and virtualized lists.

Set `AGENT_OBSERVATION_FULL_PAGE_SCREENSHOT=true` to save supplemental full-page PNGs.
These are audit artifacts; the planner still uses the viewport image. Long-page captures
cost additional time and storage and only cover content rendered by the browser.

For API planners, `planner_attempts[].observation_input` records the actual structured DOM
length/hash, viewport/document lengths and omissions, screenshot capture/inclusion status,
omission reason, encoded-image hash, and whether the request reached the HTTP dispatch
path. `screenshot_sent=true` means an image-bearing request was dispatched; it does not
prove provider receipt or model attention. The saved lossless pre-image can reproduce
the request image using the recorded JPEG encoding. Planners without this instrumentation
leave input metadata empty rather than claiming an image was sent.

Old runs are not backfilled. Browser evidence is local run output and can contain page
text and form values; it is not copied into recovery checkpoints. The existing strict
trajectory certificate now verifies referenced image/DOM and full-result hashes, but does
not prove pixel-perfect visual/DOM alignment. Legacy missing references remain unknown,
not synthesized observations. `artifact_integrity.checked` records the coverage.

Capture status is `complete`, `partial`, `inconsistent`, or `failed`; `done` records
`reused`, and failed planning records `not_attempted`. Failed capture attempts retain
exception type, stage and timing, including when a later retry succeeds. If DOM capture
fails, a separately acquired viewport screenshot is retained with `pair_consistent=false`.
An absent screenshot is never represented as evidence of an empty/loading page.
`AGENT_OBSERVATION_CAPTURE_TIMEOUT_SECONDS` (20 seconds) bounds each complete capture
attempt, including page-stability checks. `AGENT_OBSERVATION_FALLBACK_TIMEOUT_SECONDS`
(5 seconds) bounds the independent fallback screenshot. Stability polling alone is not
a deadline for DOM extraction or image capture. A timed-out post-capture cannot silently
consume the entire task budget; its diagnostic remains in the observation.
Identical PNGs share a content-addressed blob, with compatible phase paths hardlinked when
supported. The lifecycle journal records action start before dispatch and outcome before
post-capture, so an interruption can be distinguished from a missing image.

`steps[].result_ref` points to the complete redacted tool result; `planner_visible_result`
is the bounded JSON actually used for planner history and URL authorization. Previews
remove complete records, not URL fragments. `get_all_links` supports `contains`, `offset`
and `next_offset`; `returned`, `visible_count` and `omitted_count` describe different
scopes honestly. Use the preview's `next_offset` to fetch links omitted by its budget.

Open `report/index.html` directly to compare pre/post, inspect planning failures, and see
per-attempt image delivery. Execution completion, trajectory compliance, and independent
answer correctness are separate fields. A compliance certificate is not an answer oracle.

To regenerate a reader or build a catalog of finalized runs under a model/day directory:

```bash
uv run python scripts/render_run_report.py outputs/runs/<date>/<model>
```

This only writes derived HTML readers and an `index.html` catalog. Historical trace JSON,
certificates, screenshots and missing evidence are neither overwritten nor backfilled.
For latest-PDF tasks, completion requires a persistent candidate ledger with report-specific
dates and unresolved version/variant checks. Repository listing dates do not date a report;
report-file updates and citation publication dates remain distinct. The verifier replays
visible inspection/search results instead of accepting terminal checklist booleans alone.
This establishes support among observed candidates, not exhaustive coverage of the web.
The reader includes observed-but-interrupted steps from the lifecycle journal, even when
no planner response or executed step was recorded. It displays current verification
separately from any historical stored certificate; existing traces are not rewritten.

Search quality checks exclude engine navigation/image tabs and require explicit query
constraints to co-occur within a result, rather than matching a version on one unrelated
row and a host on another. Repeated destination sets report zero new destinations and
provide a publisher-navigation recovery hint. The candidate ledger retains unresolved
release variants across history compaction and surfaces a concrete exact-variant follow-up.

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

See [benchmark report contracts](../../src/webagent/benchmarks/docs/report-contracts.md) for scoring
and readiness semantics.

## Retention and publication

`outputs/` is ignored by default because it can contain large media and locally disclosed
content. Publish only an explicitly reviewed, date-allowlisted evidence bundle. The
2026-09-09 bundle can be reproduced without overwriting its destination with:

```bash
uv run python scripts/freeze_evaluation_evidence.py \
  --campaign outputs/campaigns/generality-2026-09-09-rerun-r7 \
  --strict outputs/validation/2026-09-09-qwen-paired-r5 \
  --output outputs/published/2026-09-09
```

The freezer copies only aggregate reports, one failed trajectory, strict certificate
verification closures, and compact long-horizon recovery evidence. It rejects common
credential material and writes a per-file source, purpose, size, and SHA-256 manifest.
Review personal data, cookies, local-storage state, local path disclosure, and unneeded
downloads before tracking. Hash-bound source records may retain reviewed non-secret local
paths because redaction would invalidate their original certificate hashes.

Selected binary evidence can use Git LFS, but release wheels and source distributions
must continue to exclude every `outputs/` path. A published bundle is evidence, not the
source of truth for runtime behavior or documentation.

## Source fingerprint scopes

The offline reader separates execution status, independent task assertions (when
`evaluation/task.json` exists), and strict-search certificate applicability. Ordinary
browser-grounded runs show the strict certificate as not applicable, not failed.
Stored certificates remain unchanged when readers are rebuilt. A complete captured
observation registers its captured URL as visited evidence before planning, including
the initial page; partial or inconsistent captures do not establish that visit.
The planner and policy share the same DOM projection. Explicit URLs in noneditable
page text or approved planner-visible read-tool results are eligible navigation/citation evidence;
they are not automatically visited pages or first-party endorsement. Hidden or
budget-omitted text is excluded. Long URLs are retained whole or omitted, never
wrapped into valid-looking URL prefixes. Relative URLs are resolved from structured
URL fields, not arbitrary titles or prose.
Editable/control values, search-query echoes and write-tool echoes are not promoted
to source URLs. These are rule-based provenance boundaries, not a formal taint
analysis of arbitrary page behavior or proof of natural-language correctness.
Rendered controls also include common ARIA widget roles, disclosures, focusable
elements and custom event controls. A pointer-cursor boundary is marked as an
affordance hint, not proof of a click handler; inherited child cursors do not create
duplicate hints. Input/select values (bounded, excluding password/file inputs),
checkbox state and ARIA states are exposed separately from painted text. State
changes participate in capture/reference checks. HTML link enumeration does not
enumerate JavaScript-only menus or cards; those use observation-bound controls.

`agent_source_sha256` hashes Python paths and bytes in `webagent`, excluding the `benchmarks/` subpackage; it includes shared `evaluation/` contracts. `benchmark_source_sha256` hashes only Python sources in `webagent/benchmarks/`. Documentation and other non-Python files are outside these source fingerprints. Each fingerprint is cached for the process lifetime; restart the process after modifying source. Checkpoints produced with the former combined scope fail the source-identity check and require a new run.

Browser checkpoint restoration validates all tab and storage input before applying cookies, local storage or navigation. Local storage is written once through disposable intercepted pages; no persistent initialization script is installed. Invalid input leaves browser state untouched. A browser API failure during application can still leave partial changes; restoration is not a browser transaction.
