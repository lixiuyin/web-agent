# Browser, authorization, and security boundaries

WebAgent controls a real browser. Network identity, authenticated profiles, uploads, and
state-changing actions therefore require explicit boundaries.

## Browser profiles

Temporary profiles are the default and provide per-process isolation. Use a persistent
profile only for a trusted interactive task whose signed-in state must survive browser
restart.

The runtime marks temporary profiles with owner metadata. Cleanup only reaps profiles
older than `browser_stale_profile_max_age_seconds` whose owner process is no longer alive.
Do not point automatic cleanup at a normal Chrome profile.

## Browser channel and headed mode

Bundled Chromium is the reproducible default. Local stable Chrome is appropriate for a
headed, trusted workflow that needs an existing compatible profile. A visible browser and
persistent profile can reduce repeated authentication, but neither guarantees that a
search engine will accept automation.

## CAPTCHA policy

The agent does not solve, forge, or bypass CAPTCHA challenges.

- Strict or headless execution fails closed.
- A headed ordinary run may wait for explicit human clearance when configured.
- Human-assisted runs must be labeled and cannot be reported as unattended benchmark
  evidence.
- Search fallback events and unresolved challenges remain in the trace.

## Proxies and network routing

`browser_proxy_server` affects the browser context only. Shell processes, provider HTTP
calls, GitHub APIs, and external evaluators may follow different operating-system or
environment proxy settings.

Do not assume that a browser's apparent region proves the route used by an LLM provider
or BYOK upstream. Record the configured route and use provider-returned metadata or a
bounded probe when routing matters to the claim.

Proxy URLs must not embed credentials. Keep secrets in local configuration and redact
commands, traces, logs, and issue reports.

## TLS

Certificate validation is enabled by default. `browser_ignore_https_errors` is a narrow,
explicit compatibility override and must not be enabled in strict evaluation or used to
claim normal production reachability.

## URL provenance

Browser-grounded policy authorizes navigation and download only from URLs exposed in the
exact planner-visible tool result or current browser observation. Hidden DOM anchors,
guessed URLs, and failed download retry locations do not become evidence automatically.

The policy protects experiment provenance; it is not a general proof that a domain or
repository is legally owned by the named organization.

## Uploads and downloads

Approved uploads are contained below `browser_upload_root`. File tools also enforce run
and artifact containment. Review the target site and file contents before allowing any
external disclosure.

Downloads remain untrusted input. PDF acquisition validates the file header before parser
use, and document parsers retain explicit failures rather than treating an HTML error page
as a PDF.

## High-risk actions

Externally consequential tools are classified separately from ordinary browser reads.
The default `high_risk_action_policy=deny` prevents execution. Terminal `prompt` requires
an explicit user decision; `allow` is a deliberate trusted-run override.

Controlled benchmark environments may enable state mutation inside their isolated local
sites. That authorization does not extend to real accounts, purchases, messages, or
external systems.

## Evidence publication

Before publishing a run, inspect manifests, traces, checkpoint metadata, URLs,
screenshots, downloads, and result attachments. The runtime avoids known secret-bearing
state, but page content and screenshots can still contain personal or proprietary data.

See [run artifacts](run-artifacts.md) for namespace ownership and retention guidance.
