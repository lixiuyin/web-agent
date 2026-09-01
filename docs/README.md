# Documentation

The documentation is organized by user goal and information ownership. Root READMEs are
landing pages; detailed commands, stable references, methods, and dated results live in
separate maintained documents.

## Start by goal

| Goal | Entry point |
|---|---|
| Install and complete a first run | [Getting started](guides/getting-started.md) |
| Choose Hybrid, browser-grounded, or strict discovery | [Discovery modes](guides/discovery-modes.md) |
| Diagnose planner, provider, browser, or evaluation failures | [Troubleshooting](guides/troubleshooting.md) |
| Look up configuration | [Configuration reference](reference/configuration.md) |
| Understand run directories and checkpoints | [Run artifacts](reference/run-artifacts.md) |
| Review profile, proxy, CAPTCHA, upload, and action boundaries | [Browser and security](reference/browser-and-security.md) |
| Run internal or BrowserGym evaluation | [Benchmark guide](../benchmarks/README.md) |
| Read the stable evaluation methodology | [Evaluation protocol](research/evaluation-protocol.md) |
| Inspect dated empirical claims | [Results index](research/results/README.md) |
| Study implementation call chains in Chinese | [中文源码理解手册](understanding-zh/README.md) |
| Build and publish a release | [Release procedure](operations/release.md) |
| Contribute a change | [Contributing guide](../CONTRIBUTING.md) |

## Directory ownership

```text
docs/
├── guides/            task-oriented user workflows and troubleshooting
├── reference/         stable configuration, artifact, and security contracts
├── operations/        maintainer procedures such as releases
├── research/          methodology, evidence rules, and dated results
└── understanding-zh/  source-grounded Chinese implementation study

benchmarks/docs/       executable suite, study, infrastructure, and report guides
```

Runtime behavior belongs in `src/webagent/`; executable environments and suites belong
in `benchmarks/`; reusable evaluation contracts belong in `src/webagent/evaluation/`.
Generated output is evidence, not documentation source of truth.

## Current evidence boundary

The repository has one complete common diagnostic date. The longitudinal portfolio is
therefore interim, and the official WebArena-Verified Hard and VisualWebArena layers have
not been run. Exact scores and limitations belong only to the
[dated results](research/results/README.md).

The diagnostic and BrowserGym layers use different tasks and evaluators. Their scores are
never averaged; a two-layer portfolio binds complete reports side by side.

## Maintenance rules

- Update the root READMEs only for product-level behavior, the primary demo, or navigation.
- Update one canonical owner when configuration, output contracts, discovery policy,
  benchmarks, or result evidence changes; other pages should link to it.
- Keep mutable test counts and model scores in dated result/verification snapshots rather
  than conceptual source guides.
- Publish only explicitly reviewed output bundles, and link them from a maintained result
  document.
- Follow the terminology, evidence labels, and formatting rules in
  [documentation style](documentation-style.md).

Validate documentation before merging:

```bash
python scripts/check_docs.py
git diff --check
```

Then run the complete repository gates required by `AGENTS.md`.
