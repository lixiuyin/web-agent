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

An official owner's rendered repository index can satisfy the independent owner/scope
cross-check when it visibly exposes the candidate under that owner. Once that index
reveals a highest dotted subject version, policy recovery prioritizes the exact frontier
repository and its report file. An older candidate's PDF does not become downloadable
merely because it was observed first. A named-version report/PDF match must come from one
search-result row or the exact first-party repository; tokens combined across unrelated
rows are not evidence.

A task-matching, non-`site:` search contributes identity evidence through three signals:
results whose visible text claims to be official, results hosted on a domain whose brand
label is exactly a subject keyword (`qwen.ai` for a Qwen task, but not `qwen-mirror.*`),
and, when the query itself asks for the official presence, results on a host the query
names. Only if none of those signals is present does the whole result set stand in.
Downloads bind to a target for these owner/host checks: `download_pdf` to its URL,
`download_file` to the page hosting the clicked control, and `done` to the selected
candidate. Only PDF-like pages (a `.pdf` blob or an arXiv rendition) become the selected
candidate when `inspect_download_links` finds no URL; an ordinary HTML page does not.

After each valid search, the planner receives the remaining checklist. A premature
action is denied with all missing prerequisites; a denied `done` remains a failed step.
While the selected candidate still lacks binding evidence, the per-step
`CONTROLLER EVIDENCE RECOVERY` hint repeats the gap. An exact repeat of a denied call is
rejected before it consumes an action step until some allowed action has run, because
the outcome cannot have changed.

## PDF acquisition

`download_pdf` accepts only bytes with a PDF header. If a repository preview returns
HTML, that response is deleted. The planner must open the preview and call
`inspect_download_links`, which exposes declared download targets, visible date metadata,
and file-history links before the raw file can be authorized.

This prevents a failed preview request from silently revealing a hidden retry URL.

Some hosts render a binary-file download only as a JavaScript button without an `href`
(for example GitHub's "Download raw file" control on a PDF blob page reached by clicking
through the repository tree). `inspect_download_links` then reports that control under
`download_controls` instead of inventing a URL, and the planner clicks it with
`download_file`. A bare "Download" button on an ordinary HTML page is ignored because it
usually fetches an app or dataset; a control counts only when its label names a document
artifact (raw, file, PDF, paper, report, ...) or when the page itself is a PDF rendition. A PDF saved this way is registered as an evidence-grounded download, so
`pdf_analyze_figure` and the other `pdf_*` tools accept its path, `done` treats the PDF
deliverable as satisfied, and the trace verifier counts it for PDF tasks. For
latest/newest tasks, `download_file` is subject to the same evidence checklist as
`download_pdf`; non-PDF downloads are never registered.

## arXiv candidates

arXiv is a paper index, so identity and scope searches can never endorse `arxiv.org`.
An arXiv PDF becomes admissible only when its `/abs/<id>` link was planner-visible
(`get_all_links`) on a page that both the identity and the scope evidence endorse: a
repository under the endorsed host/owner, or the endorsed official website host. When the
selected candidate is an unendorsed arXiv page or PDF, the denial and the per-step
`CONTROLLER EVIDENCE RECOVERY` hint name the endorsed official pages already observed and
the exact route (open that page, enumerate its links, retry). Pages whose path shares a
name token with the document's observed title (for example `QwenLM/Qwen3` for "Qwen3
Technical Report") are listed first. A link seen on a third-party site outranks the same
URL exposed by the arXiv page about itself, so the order in which the pages were visited
does not matter.

## Subject binding for linked documents

An official page also links to unrelated papers (a blog post may cite a data-selection
paper its model reproduced), so link context alone does not make a document the task's
deliverable. The policy keeps the visible text the planner saw beside every URL — search
result titles, anchor text, download-candidate labels, and the `title` returned by
`goto`. For latest/newest tasks, a document hosted outside the endorsed official host (an
arXiv rendition or a third-party PDF) must have at least one observed label or file name
that names a task subject token before `download_pdf`, `download_file`, or `done` is
allowed. A badge link with empty anchor text is satisfied by opening the paper's own page
so its title becomes evidence; a PDF hosted by the official host itself is not subject to
this check. Opening an arXiv `/abs/` page selects that paper as the candidate, so the
per-step `CANDIDATE EVIDENCE INCOMPLETE` hint reports the missing subject binding before a
download step is spent. Labels are checkpointed under `observed_labels`.

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

Operational acceptance therefore also requires the manifest evaluator to persist
`evaluation/task.json` with every required assertion passing. A strict task may still
contain a small number of bounded failed search actions or planner attempts when their
raw failures are retained and later recovered. It is not acceptable if they cause an
invalid certificate, wrong report, missing PDF/Figure, false completion, unresolved
challenge, or budget-exhausting loop. See the
[evaluation protocol](../research/evaluation-protocol.md) and the
[2026-09-09 Qwen validation](../research/results/qwen-strict-search-2026-09-09.zh-CN.md).
