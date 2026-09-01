# Documentation style and ownership

This page defines how project documentation is organized and maintained. It is a
contributor contract, not a user guide.

## One owner per subject

| Subject | Canonical owner |
|---|---|
| Product purpose, primary demo, five-minute start | Root `README.md` and `README.zh-CN.md` |
| Installation and first real run | `docs/guides/getting-started.md` |
| Hybrid, browser-grounded, and strict discovery | `docs/guides/discovery-modes.md` |
| Operational failures and recovery | `docs/guides/troubleshooting.md` |
| Configuration fields and environment variables | `docs/reference/configuration.md` |
| Run directories, checkpoints, and evidence | `docs/reference/run-artifacts.md` |
| Browser profiles, CAPTCHA, proxies, and risky actions | `docs/reference/browser-and-security.md` |
| Executable benchmark suites | `benchmarks/README.md` and `benchmarks/docs/` |
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

## Validation

Run the documentation check before committing:

```bash
python scripts/check_docs.py
git diff --check
```

The checker validates headings, code fences, local links, image alt text, table widths,
trailing whitespace, and selected terminology rules. Code behavior still requires the
full repository gates in `AGENTS.md`.
