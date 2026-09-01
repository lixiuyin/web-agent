# Qwen report discovery mode comparison: 2026-09-02

This case study compares four retained executions of the same task with the same model,
browser type, temporary state, and action budget. It is a trajectory analysis, not a
general success-rate benchmark.

> Find the most recent technical report (PDF) about Qwen, then interpret Figure 1 by
> describing its purpose and key findings.

## Run matrix

| Run | Direct-source API | Terminal state | Recorded tool actions | Duration | Evidence |
|---|---|---|---:|---:|---|
| Hybrid | Yes | Completed | 5 | 75.38 s | [Trace and animation](../../../outputs/runs/qwen-report-figure1-20260902/hybrid/) |
| Browser-grounded | No | Interrupted | 11 | 390.71 s | [Trace and animation](../../../outputs/runs/qwen-report-figure1-20260902/browser-grounded/) |
| Browser-grounded retry | No | Interrupted | 21 | 760.92 s | [Trace and animation](../../../outputs/runs/qwen-report-figure1-20260902/browser-grounded-r2/) |
| Strict | No | Completed and certificate-valid | 17 | 218.96 s | [Trace and animation](../../../outputs/runs/qwen-report-figure1-20260902/strict/) |

`success=false` in the two browser-grounded manifests means the run did not reach a
successful terminal `done` result. It does not mean every recorded browser action failed.

## Hybrid

All five planner attempts produced executable actions, and all five tools succeeded.
`official_report_search` supplied the exact-owner GitHub report and commit timestamp; one
Bing search cross-checked the release landscape before direct PDF acquisition. No engine
fallback, challenge, or replan occurred.

This was the shortest path, but its first-party source lookup used a direct API and is not
browser-search benchmark evidence.

![Hybrid trajectory](../../../outputs/runs/qwen-report-figure1-20260902/hybrid/trajectory-demo.gif)

## Browser-grounded first run

Ten of eleven persisted actions were searches. The other action opened the correct
GitHub PDF at step 6. Across 15 planner attempts, four returned no executable action; the
controller recorded six strategy switches and six replans. DuckDuckGo raised a bot
challenge on the last recorded search and fell back to Bing.

The run was manually interrupted after it continued rewriting owner queries despite
already observing the correct candidate. It did not reach PDF download, Figure 1
analysis, or `done`; the retained terminal frame explains this `success=false` state.

![First browser-grounded trajectory](../../../outputs/runs/qwen-report-figure1-20260902/browser-grounded/trajectory-demo.gif)

## Browser-grounded retry

The retry persisted 21 tool actions: 15 searches, three navigations, and one each of
`get_all_links`, `screenshot`, and `analyze_image`. It required 29 planner attempts,
including eight without an executable action.

DuckDuckGo challenged at step 12 and fell back to Bing. At step 17, Bing failed the
quoted-title constraint and Yahoo Japan supplied usable results. Steps 20–23 reached and
visually inspected the exact report's commit history, but no `download_pdf`,
`pdf_analyze_figure`, or `done` occurred before the action budget was exhausted and the
stalled run was interrupted.

![Browser-grounded retry trajectory](../../../outputs/runs/qwen-report-figure1-20260902/browser-grounded-r2/trajectory-demo.gif)

## Strict evaluation

All 17 planner attempts were executable. Eight searches included two Bing quality
failures that fell back to Yahoo Japan without CAPTCHA. The first PDF attempt was denied
because independent scope evidence was incomplete. The second correctly failed because
the GitHub preview returned HTML rather than PDF bytes.

The agent then called `inspect_download_links`, observed the declared raw URL and
2026-08-26 file date, downloaded and validated the PDF, analyzed Figure 1, and completed.
The certificate validates continuity, browser-search-only discovery, visible URL
provenance, first-action search, producer binding, and schema support.

![Strict trajectory](../../../outputs/runs/qwen-report-figure1-20260902/strict/trajectory-demo.gif)

## Interpretation

No trace contains HTTP 429. The only challenge events are two DuckDuckGo bot challenges
in ordinary browser-grounded runs; both were recorded and fell back rather than being
bypassed.

The retained evidence therefore points to planner search churn as the immediate failure
mechanism in the interrupted runs, not provider rate limiting or inability to reach the
report. The strict checklist improved this case by turning an observed candidate into
explicit missing evidence actions and a verified download path.

This is one task and one model. It supports a mechanism-level case study, not a claim
that strict mode is generally more successful than ordinary browser-grounded execution.

## Figure result

The extracted Figure 1 is an architecture diagram rather than a benchmark chart. It
shows a repeating 3:1 mixture of GDN and QSA layers, gated reads and writes around an
expanded residual stream, MoE blocks in both layer types, a layer-2 n-gram embedding
backed by host-memory prefetching, and MTP modules that reuse QSA indices during
speculative decoding.

![Extracted Figure 1](../../../outputs/runs/qwen-report-figure1-20260902/strict/result/attachments/figure.png)

The full bundle contains manifests, checkpoints, traces, screenshots, downloaded PDFs,
extracted figures, result text, and animations. Binary media uses Git LFS; JSON and text
evidence remains ordinary Git content.
