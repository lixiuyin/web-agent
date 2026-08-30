# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Long-horizon cue scoring now uses an ordered-token assertion, so correct cue sequences remain
  valid when a model adds explanatory stage labels; reversed or missing cues still fail.
- Campaign portfolios now retain all requested endpoints, including models excluded by endpoint
  preflight, instead of making the requested set appear smaller in the standalone report.
- Empirical portfolios now fail closed when comparable model/date cells mix agent or benchmark
  source fingerprints, preventing post-fix runs from being merged into an ostensibly unchanged
  multi-date condition.
- Reserve a bounded planner-context slice for explicit checkpoint-safe durable notes, so recent
  ordinary tool traffic cannot evict long-delay memory cues after resume.
- Prioritize visible retry/reload controls, refresh, or bounded waits when the observed page itself
  declares a transient interruption, reducing navigation churn into blank/history pages.
- Correct long-horizon recall instructions to identify each cue's actual source stage instead of
  incorrectly describing every recall as a fixed 40-stage delay.
- Preflight requested study endpoints with a minimal real inference and exclude fully unavailable
  provider/transport cells from performance aggregates while retaining auditable evidence.

## [0.2.0] - 2026-08-30

### Added
- **Research-oriented run and study workspaces** — ordinary CLI runs are allocated under
  `outputs/runs/<UTC-date>/<model>/<task>-<run-id>/`, while comparable benchmark executions live
  below `outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/` and
  historical output trees can be hash-inventoried below `outputs/legacy/` without rewriting their
  bytes. Run artifacts now separate trajectory, observations, controller state, task files, agent
  results, and independent evaluation.
- **Failure-analysis research workflow** — `docs/research/` defines evidence levels, calibration
  coverage, held-out task/setting transfer boundaries, and a reproducible experiment lifecycle.
- **Typed study provenance** — immutable study manifests preregister provider/model, condition,
  budgets, task snapshot, splits, repetitions, dates, and source fingerprints. Completed task
  evaluations are hash-bound into a concurrency-safe canonical ledger without inventing missing
  experimental identity.
- **Provider-native planning and resumable control state** — the planner now exports the
  policy-filtered tool catalog as JSON Schema, prefers native function calls, and falls back through
  provider JSON Schema to prompt JSON only for explicit capability errors. Ordinary runs persist
  atomic task/config/source-bound checkpoints, browser/policy/loop coordinates, durable evidence,
  bounded strategy switches, artifact hashes, and write-ahead action markers; ambiguous interactions
  are never silently replayed, while strict-evaluation traces remain non-resumable.
- **Versioned release artifacts** — run traces now have a typed v8 envelope, a packaged JSON
  Schema, deterministic v7 migration, exact-version verifier dispatch, producer/source identity,
  continuation metadata, and a versioned verification certificate; strict runs fail closed when
  either trace or certificate persistence fails. The unique distribution name is now
  `lixiuyin-webagent` (while import and CLI stay `webagent`), and release validation binds that
  identity across project metadata, wheel/sdist filenames, METADATA, and PKG-INFO. Tagged releases
  require an exact versioned changelog section and an empty `Unreleased` section. CI exercises
  Chromium on Linux, macOS, and Windows across Python 3.13/3.14, while release jobs build and
  compare two wheel/sdist pairs, inspect archive contents, smoke-test isolated installs, attest
  provenance, and use PyPI trusted publishing.
- **Search-engine-only anti-shortcut policy** — `--search-engine-only` forces browser search as
  the first successful action, hides and rejects GitHub/arXiv report-discovery APIs, allows direct
  navigation/download only for browser-observed URLs, confines PDF analysis to policy-approved
  downloads, requires distinct current-year broad and release-landscape searches for latest-item
  tasks, forces an exact follow-up for the highest dotted subject version exposed by any SERP,
  and binds an official identity search to an independent same-owner scope search. Scope can use
  a repository owner path or a host+owner query when engines reject path-scoped operators. Only
  URLs in exact planner-visible results/current observations can authorize navigation; schema-v8
  traces receive a hash-bound certificate that rejects mixed/incomplete runs.
  Latest-evidence denials now return every missing prerequisite at once; landscape searches require
  semantic evidence in their results, and owner-scope evidence must cover the selected candidate
  repository/host rather than merely another repository owned by the same organization. Planner
  history exposes checklist progress after every successful search, declared repository downloads
  outrank preview iframe resources, repository candidates require target-owner identity evidence,
  PDF-preview navigation binds the candidate early, and a policy-denied `done` can no longer mark
  a run completed.
- **Bounded planner evidence replay** — the action history remains ten steps by default, while only
  the newest two tool payloads are replayed in full. Older SERPs become compact trace pointers,
  preventing repeated search evidence from exhausting reasoning-model output before a JSON action.
- **Content-first link extraction** — `get_all_links` deduplicates before applying its cap and ranks
  PDF, technical-report, paper, arXiv, raw, and download links ahead of site-wide navigation chrome.
  It reports both returned and total filtered counts, so content links are not hidden by large menus.
- **Browser-grounded and action-safe defaults** — ordinary runs hide direct-source discovery APIs
  and enforce user/browser URL provenance; URL-free discovery starts with browser search and
  latest/newest tasks inherit recency/official-source gates; high-risk actions default to denial, can use
  terminal confirmation, inspect opaque target-button metadata before execution, redact sensitive
  input/upload paths from traces, and confine uploads to an explicit root.
- **Modern browser primitives with task isolation** — registered tools cover tabs, iframes, open
  Shadow DOM, guarded uploads, and captured downloads. Benchmarks clear cookies, local/session
  storage, permissions, and extra tabs between tasks. Chromium no longer disables its sandbox,
  same-origin protections, phishing protection, or TLS validation by default.
  Native Playwright is now the default and strict evaluation forces stealth off;
  anti-detection behavior is an explicit compatibility opt-in.
  Ordinary runs now also use temporary profiles, disable cross-run PDF caches and randomized
  delays, and preserve native locale/timezone unless those settings are explicitly overridden.
  Temporary profiles carry owner PID/time markers; future launches safely reap only old marked
  orphans whose owner process no longer exists.
- **Repeated model and broad open-web evaluation** — a repeatable multi-model matrix aggregates
  task-level metrics across isolated runs, while a 30-task/10-domain manifest and longitudinal
  reporter track dated model-specific slices, manifest drift, variance, and minimum-date readiness.
  Reports and strict traces bind results to a SHA-256 fingerprint of the active agent source, and
  parallel merging rejects shards produced by different source states.
- **Content-validated PDF recovery** — `download_pdf` rejects and removes HTML preview pages even
  when their URL ends in `.pdf` and never extracts retry URLs from the failed response. Recovery
  is an explicit `inspect_download_links` browser step whose DOM/page-metadata evidence is shown
  to the planner first; it also exposes visible datetime metadata and file-history links for exact
  date verification. Completed Marker polling timeouts no longer resubmit duplicate remote jobs.
- **Bounded planner reasoning and semantic date checks** — compatible providers can receive a
  configurable reasoning effort; strict open-web runs use low effort with structured actions to
  reduce latency. Labeled-date assertions bind the selected date field instead of accepting an
  unrelated matching date elsewhere in the final answer.
- **Evidence-grounded general web benchmarks** — eleven deterministic real-Chromium tasks cover
  navigation, cross-page lookup, forms, login, table/location reasoning, booking, checkout,
  server-side mutation, dynamic DOM, and transient-error recovery. Page/JSON and final-answer
  assertions report false completions, answer grounding, action validity, latency, steps, and
  category success. A separate source-declared, validity-windowed open-web manifest writes
  hash-bound longitudinal summaries for repeated multi-site runs.
- **Ethical CAPTCHA handoff** — ordinary `report` logs and waits in a headed browser for manual
  clearance, then fails closed on timeout; headless and strict runs block immediately. Runtime events are auditable and
  unresolved challenges invalidate strict certificates; no mode solves or bypasses CAPTCHA.
- **Search failure telemetry** — engine cascades expose attempted engines and classify challenge/
  block pages, selector drift, empty results, navigation failures, and interaction failures rather
  than collapsing every live-search failure into an ambiguous empty result.
- **Auditable strict evaluation** — `--strict-eval` now implies the search-engine-only policy,
  forces an ephemeral Chromium profile, disables cross-run PDF-cache reuse, creates a fresh output
  by default, and writes a compact, redacted `trajectory/trace.json` plus the adjacent
  `trajectory/verification.json` certificate.
- **Generic official-report discovery** — `official_report_search` queries arXiv and GitHub
  concurrently, rejects mention-only titles, and reserves “first-party” for exact owner matches.
- **Content-addressed PDF parse cache** — successful parses can be reused across processes by
  PDF SHA-256, with per-document single-flight locking and strict-mode cache bypass.
- **Planner repair and observability** — bounded retries for empty/malformed plans plus response
  length, finish reason, token usage, error, and duration for every planner attempt; tool and
  figure-vision durations are also recorded in the run trace.
- **Bounded, leaner multimodal execution** — planning, detailed vision, and brief vision use
  independent output budgets; local PDF/image steps omit redundant browser screenshots, and
  randomized browser waits require explicit compatibility opt-in and remain disabled in strict
  evaluation.
- **Independent official-source deadlines** — a slow arXiv or GitHub request is bounded without
  discarding a successful result from the other source.
- **Evidence-specific history projection** — verbose search/PDF payloads are reduced to the
  provenance, captions, paths, and excerpts required by subsequent decisions.
- **Provider-agnostic planning** — `APIPlanner` for any OpenAI-compatible endpoint, with
  automatic vision-capability detection and adapters for OpenAI / Azure / Claude / Gemini /
  MiniMax; reasoning-model support (strips `<think>` tags, falls back to `reasoning_content`).
- **Document parser cascade** (`webagent.parser`) — cloud OCR via Marker → MinerU → PaddleOCR
  with a local PyMuPDF fallback, gated by a quality check; produces structured Markdown,
  tables, sections, and images.
- **Caption-aware figure resolution** — extracted images are associated with their real
  `Figure N` caption (from markdown alt-text or the nearest caption line); `pdf_list_figures`
  separates real captioned figures from logos, and `pdf_analyze_figure` resolves "Figure N"
  by number/caption instead of extraction order.
- **Conservative local Figure rendering** — exact numbered figures in text-native PDFs can be
  detected from caption/graphic geometry and rendered directly from vector or raster page objects.
  Duplicate, low-confidence, or unsupported layouts retain the structured-parser fallback.
- **Offline multi-document Figure benchmark** — ten generated PDFs measure detector precision/
  recall, crop coverage/purity, false bypasses, fallback rate, render success, and latency.
- **Source-caption precedence and honest vision failure** — when a parser-generated image
  caption conflicts with the PDF's later same-number caption, the source caption wins;
  exceptions/empty vision output now fail explicitly instead of returning success with null analysis.
- **Reusable PDF parses and exact cross-references** — `pdf_parse` seeds the shared parser
  cache and every structured PDF query reuses it, while Figure/Table reference lookup excludes
  captions and no longer mistakes `Figure 10` for `Figure 1`. Planner history now preserves
  complete figure captions instead of triggering redundant fallback extraction.
- **Resilient search** — `search` defaults to Bing → Yahoo → DuckDuckGo, unwraps Yahoo
  tracking redirects, keeps engine date operators out of query text, and leaves Google opt-in
  to avoid human verification;
  `github_search` returns first-party report PDFs with file commit dates, while
  title-scoped `arxiv_search` avoids third-party mention-only matches.
- **Clean browser lifecycle** — partial starts are closed, Chromium clean-exit markers are
  repaired before/after a run, and cleanup failures are logged instead of silently swallowed.
- **Output-volume isolation** — dated open-web runs preflight free space and place their temporary
  Chromium profile below the selected output volume, allowing safe execution on a large external
  volume without silently losing final traces to a full system disk.
- **Loop and budget awareness** — five signals (action-repeat, scroll-churn, page-stagnation,
  URL-oscillation, no-progress) are injected as nudges before planning, and the final two actions
  explicitly reserve capacity for a grounded `done` response.
- **Hard request timeout** (`api_hard_timeout`) — a wall-clock cap (`asyncio.wait_for`) that
  bounds trickling/hung LLM responses which evade httpx's per-read timeout.
- **Organized run artifacts** — downloads are isolated under `artifacts/downloads/`, each PDF's
  extracted content is content-addressed under `artifacts/documents/<doc-id>/`, the final analysis
  is written to `result/summary.txt`, and the selected figure is retained in `result/attachments/`.
- **Anti-detection browser** — stealth profile + CDP-based interactive-element extraction.
- Structured planning mode (`EnhancedToolCall`) with explicit reasoning fields.
- Branch coverage gated at 85%, strict `mypy` typing, and `ruff` lint/format.

### Changed
- **Purpose-oriented benchmark modules** — canonical runners now live under
  `benchmarks.suites.*`, controlled sites under `benchmarks.environments.controlled_web`, and
  repeated experiments under `benchmarks.studies.*`. Flat module names remain compatibility
  wrappers for one release cycle, and the deterministic infrastructure condition is named
  `scripted-harness-baseline` rather than implying model calibration.
- **Non-overwriting session and benchmark executions** — ordinary interactive follow-ups retain one
  owned run and publish immutable `trajectory/turns/turn-NNN.json` and
  `result/turns/turn-NNN/` snapshots while keeping canonical latest files. Strict/search-only runs
  reject multiple turns. Suite runners allocate unique model/condition executions by default, and
  the PDF Figure suite separates generated inputs from rendered artifacts.
- **Immutable, run-scoped task artifacts** — downloads, screenshots, saved images, and written files
  publish atomically into the owning run. Byte-identical retries are idempotent, conflicting names
  fail closed, and in-memory PDF caches cannot leak parsed state across run artifact roots.
- **Agent/benchmark source separation** — reports record independent fingerprints for reusable
  agent code and benchmark code, and aggregators reject executions that mix either source state.
- **License changed from Apache-2.0 to MIT.**
- Replaced the original local-only model + OCR stack (local vLLM + DotsMOCR/Chandra) with the
  provider-agnostic cloud-cascade design; PDF tools now use `webagent.parser` directly.
- Standardized the repository: `outputs/` and build artifacts are gitignored; removed stray
  root-level demo scripts and vestigial model symlinks.

## [0.1.0] - 2026-03-16

### Added
- Production-grade Python package structure (`src/webagent/`)
- Protocol-based architecture (`core/protocols.py`) for pluggable components
- Plugin-based tool registry with the `@tool` decorator and auto-discovery
- Multiple planner backends: Stub, API (OpenAI-compatible), local vLLM
- Pydantic-based configuration with env-var support
- Agent lifecycle hooks for observability and session-history management
- Comprehensive test suite (pytest)

### Changed
- Migrated from a flat file layout to a `src/` package
- Decoupled browser, planner, and tools into independent modules
