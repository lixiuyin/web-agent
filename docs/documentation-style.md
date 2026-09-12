# Documentation style and ownership

This page defines how project documentation is organized and maintained. It is a
contributor contract, not a user guide.

## One owner per subject

| Subject | Canonical owner |
|---|---|
| Product purpose, primary demo, five-minute start | Root `README.md` and `README.en.md` |
| Installation and first real run | `docs/guides/getting-started.md` |
| Hybrid, browser-grounded, and strict discovery | `docs/guides/discovery-modes.md` |
| Operational failures and recovery | `docs/guides/troubleshooting.md` |
| Configuration fields and environment variables | `docs/reference/configuration.md` |
| Run directories, checkpoints, and evidence | `docs/reference/run-artifacts.md` |
| Browser profiles, CAPTCHA, proxies, and risky actions | `docs/reference/browser-and-security.md` |
| Executable benchmark suites | `src/webagent/benchmarks/README.md` and `src/webagent/benchmarks/docs/` |
| Evaluation methodology | `docs/research/evaluation-protocol.md` |
| Dated empirical results | `docs/research/results/` |
| Source-level explanations in Chinese | `docs/understanding-zh/` |
| Release procedure | `docs/operations/release.md` |

An overview may summarize another document in one paragraph or one status row. It must
link to the canonical owner instead of copying detailed commands, tables, or mutable
counts.

## Language and terminology

- Use **WebAgent** for the product, `webagent` for the Python package and CLI, and
  **web-agent** for the repository name.
- Write **CAPTCHA** in prose. Preserve exact identifiers such as
  `captcha_handling` in code formatting.
- Use sentence case for English headings. Use full-width punctuation in Chinese prose,
  while leaving code, paths, URLs, and identifiers unchanged.
- Prefer the repository's exact terms: **planner**, **tool**, **checkpoint**,
  **browser-grounded**, **strict evaluation**, and **fallback**. Define a translated
  term on first use if ambiguity is possible.
- Use backticks for commands, configuration fields, filenames, module names, and literal
  values. Do not use emphasis as a substitute for a semantic label.

The root English and Chinese READMEs are audience-equivalent landing pages rather than
literal line-by-line translations. Keep their section order and factual claims aligned:
product scope, runtime defaults, demo evidence, evaluation boundary, navigation, authorship,
and limitations must change together. Language-specific source-study material may remain
Chinese-only when the documentation index labels that scope explicitly.

`scripts/check_docs.py` enforces the paired root sections, corresponding table shapes, and
factual link targets. It deliberately does not require literal translations or duplicate the
Chinese-only source-study material.

## Evidence language

Use these labels consistently:

- **Implemented:** the active source path contains the behavior.
- **Tested:** a named automated or bounded live test exercised it.
- **Observed:** a retained trace or report records it.
- **External requirement:** completion depends on infrastructure or credentials outside
  the repository.
- **Proposed:** research or engineering work that is not implemented.

Dated results must identify the source fingerprint or commit, task contract, date, and
evidence path. Do not describe an installed package, a passing harness, or an agent's
`done` claim as benchmark success.

## Formatting

- Keep one H1 per document and do not skip heading levels.
- Add a blank line around headings, lists, tables, and fenced blocks.
- Give every image meaningful alt text and every local link a repository-relative target.
- Prefer tables for exact mappings and short lists for procedures. Avoid tables whose
  cells contain long prose.
- Keep root README sections short enough to scan. Detailed policy and reference material
  belongs in the owner documents above.
- Avoid raw HTML when standard GitHub-flavored Markdown is sufficient.

## Source explanations

Link to the source file and name the symbol when explaining implementation. Describe
call order, inputs, outputs, and failure boundaries rather than maintaining a second
copy of a complete function. Label short excerpts as excerpts, and review nearby prose
when a referenced contract changes. File line numbers are not stable identifiers.

Coverage claims must distinguish statement-only, branch-only, and combined coverage.
The configured 85% threshold applies to combined statement/branch coverage; enabling
branch collection does not create a separate branch-only threshold. Preserve historical
measurements and dates when correcting their metric labels.

## Validation

Run the documentation check before committing:

```bash
uv run python scripts/check_docs.py
git diff --check
```

The checker validates headings, code fences, local links, image alt text, table widths,
trailing whitespace, and selected terminology rules. Code behavior still requires the
full repository gates in `AGENTS.md`.

## Complexity gate

The standard Ruff check includes `C901` with maximum complexity 10 for runtime code, benchmarks, scripts and tests. Refactors must preserve behavior and evidence validation; lowering measured complexity does not by itself establish correctness.
