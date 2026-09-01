# Internal diagnostic suites

The internal layer is designed for failure localization and reproducibility. It combines
deterministic controlled sites, dated public-web tasks, a long-horizon workflow, and an
offline PDF Figure benchmark.

## General web interaction

`web-interaction-v1` runs eleven deterministic tasks against a local HTTP site through
the real Chromium controller and WebAgent loop. Scenarios cover navigation, cross-page
lookup, forms, delayed DOM controls, HTTP 503 recovery, login, filtering, booking, and
sandbox checkout.

```bash
# Infrastructure calibration
python -m benchmarks.suites.controlled_web.general \
  --mode scripted-harness-baseline --tool-set browser-only

# Configured planner
python -m benchmarks.suites.controlled_web.general \
  --mode agent --tool-set browser-only
```

Use `--tool-set all` as a tool-availability ablation and
`--disable-loop-detection` as a recovery ablation. Each run uses an isolated browser and
resets server state between tasks.

## Dated open web

The smoke manifest provides a small public-domain check:

```bash
python -m benchmarks.suites.open_web.runner \
  --manifest benchmarks/manifests/open_web_smoke.json
```

Every network task declares source URLs, a snapshot ID, and an expectation validity
window. The runner rejects stale manifests and judges answer facts, citations, and
observed URLs independently from `done`.

The general manifest contains 30 tasks across 10 public official sources. Ten begin at
`about:blank` and require real browser-search discovery; twenty begin on official pages
to measure reading separately from discovery.

```bash
python -m benchmarks.suites.open_web.parallel \
  --manifest benchmarks/manifests/open_web_general.json \
  --model z-ai/glm-5.3-flash \
  --shards 3 \
  --max-steps-per-task 8 \
  --discovery-max-steps-per-task 12
```

Shards improve throughput but are not independent dates. Public-web claims require
repeated real dates with one bound manifest, configuration, and source fingerprint.

## Stateful multi-origin sandbox

The two-origin loopback suite covers SPA hydration and routing, cookie-backed login, a
cross-origin form, download/upload SHA-256 handoff, and no-payment checkout.

```bash
python -m benchmarks.suites.controlled_web.sandbox \
  --mode scripted-harness-baseline
```

Use `--mode agent --model <model>` for a planner run. Mutation policy is enabled only
after both origins are verified as loopback sandbox sites; it does not authorize real
external state changes.

## Long-horizon recovery

The controlled long-horizon task is a 60-stage workflow with four delayed cues, an HTTP
503 event, independent terminal state, and a forced browser-session restart at step 35.

```bash
python -m benchmarks.suites.controlled_web.long_horizon \
  --mode scripted-harness-baseline --resume-at-step 35

python -m benchmarks.suites.controlled_web.long_horizon \
  --mode agent --model z-ai/glm-5.3-flash \
  --report-provider openrouter --resume-at-step 35 \
  --planner-max-tokens 1024 --planner-reasoning-effort low
```

The bounded `remember` tool stores only short, explicitly non-sensitive facts in
controller state. It rejects URLs, email addresses, credentials, and secret-like fields.
Analysis reports trajectory length, checkpoint resumes, recovery, entropy, repeated
actions, stagnation, failure streaks, and collapse candidates without assigning an
unobserved reasoning or memory cause.

## Strict report discovery

The dedicated manifest evaluates a browser-search-only report/PDF/Figure task:

```bash
python -m benchmarks.suites.open_web.runner \
  --manifest benchmarks/manifests/qwen_strict_search.json \
  --search-engine-only
```

The task starts at `about:blank`; source URLs and answer facts remain evaluator-only. The
trace must satisfy the versioned strict evidence gate and certificate described in the
[discovery-mode guide](../../docs/guides/discovery-modes.md).

## PDF Figure fast path

The offline suite generates ten deterministic PDFs covering vector/raster figures,
captions above and below, multiple figures, logos, fragmented paths, columns, landscape,
and negative documents.

```bash
python -m benchmarks.suites.document_figures.fast_path
```

`results.json` reports precision/recall, threshold coverage, fallback, false bypass,
crop coverage/purity, render success, and detection latency. A true positive requires the
correct figure number and page, at least 85% known-figure coverage, and at least 55% crop
purity. Ambiguous real documents should fall back rather than weaken these thresholds.
