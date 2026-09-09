# Troubleshooting

Start from the first observable failure and retain the run directory. Do not diagnose a
planner, browser, provider, or evaluator failure from the terminal summary alone.

## Decide whether a failed attempt invalidates the run

A nonzero failed-action count is diagnostic evidence, not an automatic task failure.
Accept a completed run only when the independent evaluator passes every required
assertion, any strict certificate and artifact-integrity checks are valid, required
files/figures exist, and each failed attempt is bounded, retained, and followed by
observable recovery.

Typical acceptable residuals are a small number of public-search quality/selector
failures, a transient engine fallback, or an explicit provider capability rejection that
successfully negotiates a supported output mode. These remain in action-validity and
planner-failure metrics.

Treat the run as failed or requiring repair when it selects the wrong document, invents
or loses date/provenance evidence, accepts HTML as PDF, omits Figure analysis, has missing
or inconsistent pre/post evidence, submits false `done`, leaves a challenge unresolved,
or repeats recovery until the step/time budget is exhausted. Never delete failed steps
or hardcode the expected answer to make a trace look clean.

## No API credentials or `StubPlanner`

**Symptom:** the CLI reports `No API credentials configured` and repeatedly says the
stub has no reasoning capability.

**Cause:** neither `AGENT_MODEL_API_URL`/`AGENT_MODEL_API_KEY` nor a local vLLM path was
configured.

**Resolution:** populate `.env`, pass `--api-url` and `--api-key`, or use `--use-vllm`.
Confirm that the shell is in the repository root and the intended virtual environment is
active.

## HTTP 429 or transient provider failures

**Symptom:** planner attempts report HTTP 429, 5xx, timeout, or provider-capacity errors.

**Checks:**

1. Inspect the attempt metadata in `trajectory/trace.json`.
2. Distinguish an HTTP response from a browser challenge or search-quality failure.
3. Confirm the endpoint, model name, account routing, concurrency, and provider dashboard.
4. Run one minimal endpoint probe before restarting a full matrix.

The planner uses bounded exponential backoff configured by `api_transient_retries`,
`api_retry_base_seconds`, and `api_retry_max_seconds`. Increasing backoff can help a
temporary upstream limit; it cannot repair invalid credentials, unsupported model
parameters, or exhausted provider capacity. OpenRouter metadata such as BYOK routing is
provider evidence and should not be inferred from local proxy mode alone.

## `tool_choice=required` is unsupported

Some thinking models reject required native tool choice. In `auto` mode, WebAgent first
retries native tools with `tool_choice=auto`; explicit capability failures can then move
to JSON Schema and prompt JSON fallbacks.

The initial capability-rejection response remains a visible failed planner attempt. If
the negotiated call produces a valid action and the task later passes independent
judgment, that one response is normally a provider compatibility event rather than an
obvious runtime defect. Repeated negotiation on every repair attempt, fallback on
authentication/429/5xx, or prose silently accepted as an action is not acceptable.

This can reduce enforcement strength because the model may return prose instead of a
tool call. The planner's repair attempts and controller failure limits remain active.
Use `--planner-output-mode native-tools` only to diagnose the provider path, not to hide
the fallback behavior in a benchmark.

## Search returns irrelevant results

**Symptom:** the tool reports `quality_failure`, ignored domain constraints, or no usable
results even though the page loaded.

This is not automatically a network error. Inspect the captured result list and query.
Quoted text and `site:` path operators are inconsistently supported across engines. The
search tool can fall back to another engine, but repeated equivalent queries eventually
trip loop controls.

For a candidate already visible in evidence, prefer the exact missing owner, scope,
history, or download action instead of rewriting the same general query.

## CAPTCHA or bot challenge

The agent never bypasses a challenge. In headless or strict mode the action fails closed.
For a trusted interactive run, use a headed persistent Chrome profile and explicitly
allow a human wait:

```bash
webagent --task "..." --headed \
  --browser-channel chrome \
  --browser-profile-mode persistent \
  --captcha-handling wait_for_human \
  --captcha-wait-timeout 180
```

Do not present a human-cleared run as unattended benchmark evidence.

## Screenshot captured before the page is ready

WebAgent waits for a bounded combination of URL, `readyState`, and DOM stability before
capturing an observation. If a page hydrates later, increase
`observation_stability_timeout_ms`, `observation_stable_ms`, or `post_action_wait_ms`
through configuration rather than adding unbounded sleeps.

Inspect consecutive screenshots and DOM snapshots. A stable loading shell may require a
task-specific `wait_for_element` or `wait` action instead of a global delay.

## Browser profile or shutdown errors

**Symptom:** Playwright reports `TargetClosedError`, connection closure, or inability to
close a context after interruption.

These messages often occur after Ctrl-C closes the browser before asynchronous cleanup
finishes. Check whether the run status is `interrupted`, whether the trace/result were
published, and whether the temporary-profile owner process is still alive.

Only marked temporary profiles older than `browser_stale_profile_max_age_seconds` and
owned by a dead process are eligible for cleanup. Do not delete an active or trusted
persistent profile.

## Resume is rejected

Resume fails closed when the task hash, source fingerprint, behavior-affecting
configuration, browser coordinates, policy state, or referenced artifacts differ. It
also rejects completed, blocked, strict, and unresolved state-changing runs.

Use the exact original task and checkpoint path. If the source or contract changed,
start a new run rather than weakening the checkpoint validation.

## BrowserGym is installed but cannot run

Package installation does not provide WebArena or VisualWebArena sites. Validate every
required `WA_*` or `VWA_*` URL, authentication state, and reset endpoint from the
BrowserGym host. Then run one custom task per suite and model before allocating a full
matrix. See the [BrowserGym guide](../../src/webagent/benchmarks/docs/browsergym.md).

## Escalation checklist

When reporting a reproducible problem, include:

- commit or source fingerprint;
- command with secrets removed;
- planner endpoint family and model name;
- run manifest and status;
- failing trace step and planner-attempt metadata;
- relevant screenshot and tool result;
- whether the run was Hybrid, browser-grounded, or strict;
- whether the browser was headed, persistent, proxied, or human-assisted.
