# Discovery modes and evidence gates

WebAgent separates high-success source discovery from browser-only evaluation. The
selected mode is recorded in `trajectory/trace.json` so the two cannot be confused.

## Mode comparison

| Mode | Direct-source tools | Checkpoints | Browser isolation | Certificate | Intended use |
|---|---|---|---|---|---|
| Hybrid | Available | Enabled | Configurable | No | Ordinary tasks where success matters most |
| Browser-grounded | Hidden | Enabled | Configurable | No | Ordinary tasks that must use browser-visible discovery |
| Strict evaluation | Hidden | Disabled | Temporary profile | Yes | Auditable, uninterrupted browser-search evaluation |

`--search-engine-only` and `--strict-eval` enforce the same discovery restrictions.
Strict evaluation additionally owns the isolated run and emits a verification
certificate.

## Hybrid discovery

Hybrid is the ordinary default. The planner can combine browser search with
`official_report_search`, `github_search`, and `arxiv_search`. First-party candidates
returned by those tools may establish owner, file, and commit-date evidence, but a
latest-version task still requires the configured scope cross-checks before download.

Repeated `official_report_search` calls are bounded. Once the same unmet evidence state
recurs, the controller asks for a precise missing action or advances to the verified
download rather than permitting endless query rewrites.

Hybrid traces are valid execution evidence, but they are not browser-search benchmark
evidence.

## Browser-grounded discovery

Browser-grounded mode hides direct GitHub/arXiv/report discovery tools while retaining
ordinary checkpoints and resume behavior. It is useful when provenance must be visible
to the planner but an uninterrupted strict certificate is unnecessary.

It does not relax high-risk action policy or URL provenance. A guessed URL cannot be
used merely because it exists in page HTML that was never exposed to the planner.

## Strict evaluation

Strict evaluation creates a temporary browser profile, disables persistent PDF caches,
requires browser search as the first successful action, and fails closed on policy or
certificate violations.

For latest/newest report tasks, the evidence gate requires:

1. A broad current-year search that is not restricted to one paper index or candidate.
2. A current-year release, model, version, series, or lineup search whose results expose
   subject-relevant version evidence.
3. An exact follow-up for any higher dotted subject version observed in results.
4. A search for the official website or repository identity.
5. An independent current-year owner/scope query covering the selected candidate.
6. A planner-visible URL before `goto` or `download_pdf`.

For a repository-hosted candidate, the identity search must expose that repository host
and owner. A vendor homepage alone does not endorse a later GitHub owner, and bare
`site:github.com` is not sufficient scope evidence.

After each valid search, the planner receives the remaining checklist. A premature
action is denied with all missing prerequisites; a denied `done` remains a failed step.

## PDF acquisition

`download_pdf` accepts only bytes with a PDF header. If a repository preview returns
HTML, that response is deleted. The planner must open the preview and call
`inspect_download_links`, which exposes declared download targets, visible date metadata,
and file-history links before the raw file can be authorized.

This prevents a failed preview request from silently revealing a hidden retry URL.

## Search engines and challenges

Ordinary browser search can cascade through Bing, Yahoo Japan, Seznam, Yahoo, and
DuckDuckGo. Google automation is an explicit opt-in. Strict headless evaluation uses the
configured restricted engine set and records every quality failure, challenge, fallback,
and selector failure.

No mode solves or bypasses a CAPTCHA. Strict/headless execution fails closed. A headed
run can wait for explicit human clearance when configured; see the
[browser and security reference](../reference/browser-and-security.md).

## Verification boundary

Every strict run writes a SHA-256-bound `trajectory/verification.json`. The verifier
rejects incomplete runs, mixed run IDs, hidden direct-source success, unsupported trace
schemas, missing task-required stages, unresolved challenges, and invalid URL provenance.

Passing verification means the recorded run followed the anti-shortcut contract. It
does not prove that the chosen report is globally latest or that the natural-language
figure interpretation is correct.
