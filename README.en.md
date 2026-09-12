# WebAgent

![CI](https://github.com/lixiuyin/web-agent/actions/workflows/ci.yml/badge.svg) ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg) ![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg) ![Lint: ruff](https://img.shields.io/badge/lint-ruff-261230.svg) ![Typed: mypy](https://img.shields.io/badge/typed-mypy-blue.svg)

**English** · [简体中文](README.md)

An autonomous vision-language web agent that turns a natural-language instruction into
real browser actions: search, navigation, PDF reading, figure interpretation, and a
grounded final report.

![Strict browser-only run from search to Figure 1 analysis](docs/assets/strict-run-demo.gif)

This animation contains all 21 viewport screenshots from the 2026-09-09 Qwen paired-R5
strict trajectory, followed by the extracted Figure 1. Browser frames last two seconds
and the final figure lasts six seconds. It is a visual preview; independent task
assertions and the anti-shortcut certificate establish the recorded pass.

## What is WebAgent?

WebAgent drives a real Chromium browser through an **Observe → Think → Act → Record**
loop. It captures a consistency-checked viewport image and rendered DOM projection, asks
an OpenAI-compatible planner for one typed tool call, executes it under runtime policy,
and retains an auditable trajectory. In the default adaptive mode, the observation keeps
the screenshot while the planner receives it only when visual evidence is needed.

The runtime is model-agnostic, supports local vLLM, and includes document intelligence
for downloading PDFs, routing across OCR/parsing providers, locating a figure by its real
caption, and analyzing the extracted image with vision.

## Highlights

| Area                | Capability                                                                                   |
| ------------------- | -------------------------------------------------------------------------------------------- |
| Agent runtime       | Protocol-based planner, tool, and hook interfaces with checkpointed execution                |
| Multimodal state    | DOM-to-Markdown plus adaptive screenshots and automatic vision probing                       |
| Structured actions  | Native function tools with bounded schema and prompt fallbacks                               |
| Browser reliability | Stability-aware observations, loop detection, search fallback, and explicit CAPTCHA handling |
| Evidence            | Versioned traces, strict anti-shortcut certificates, and independent task judgment           |
| Documents           | Caption-grounded Figure resolution and quality-gated parser cascade                          |
| Evaluation          | Internal diagnostic suites plus separate BrowserGym WebArena/VWA evidence                    |
| Engineering         | 67 registered tools, strict typing, Ruff, and an 85% combined statement/branch coverage gate  |

## Architecture

Three structural interfaces—`Planner`, `Tool`, and `AgentHook`—separate model planning,
execution capabilities, and lifecycle observation.

![WebAgent system architecture showing policy-filtered planner tools, browser execution, document parsing, checkpoints, and trace evidence](docs/assets/architecture-overview.svg)

```text
src/webagent/
├── core/        protocols, models, and configuration
├── agent/       loop, history, strategy, hooks, and checkpoints
├── browser/     Playwright controller, snapshots, CDP, and CAPTCHA detection
├── planner/     API/local planners, provider modes, and structured parsing
├── parser/      OCR providers, quality gates, and local PDF recovery
├── tools/       registry, exposure/risk policy, and built-in tools
├── evaluation/  trace verification, metrics, studies, and portfolios
├── schemas/     packaged stable wire schemas
└── utils/       path, image, PDF, logging, and runtime helpers

src/webagent/benchmarks/      executable environments, suites, studies, and manifests
docs/            guides, references, research records, and source study material
outputs/         ignored by default; selected reviewed evidence may be published
```

One step observes stable browser state, builds planner context, selects an allowed tool,
executes it within time/risk bounds, records the result, and atomically updates ordinary
recovery state.

![One WebAgent step from stable observation through CAPTCHA handling, planning, write-ahead checkpoint, tool execution, and committed evidence](docs/assets/agent-step-sequence.svg)

Figure requests are resolved by number and caption rather than by extraction order, so a
logo or cover decoration cannot silently become “Figure 1.”

![Caption-grounded PDF Figure resolution using a local fast path or a quality-gated cloud parser cascade with last-resort local fallback](docs/assets/figure-resolution-flow.svg)

Editable Graphviz sources and the reproducible renderer are documented in
[docs/diagrams/](docs/diagrams/README.md).

## Quick start

```bash
uv sync
uv run playwright install chromium
cp .env.example .env
```

Set `AGENT_MODEL_API_URL`, `AGENT_MODEL_API_KEY`, and `AGENT_MODEL_NAME` in `.env`, then:

```bash
webagent \
  --task "Find the most recent Qwen technical report and interpret Figure 1" \
  --headless
```

No credentials means `StubPlanner`: the runtime can demonstrate lifecycle behavior, but
it cannot autonomously solve an open-ended task.

Common modes:

```bash
# Browser-visible discovery without direct report/GitHub/arXiv tools
webagent --task "..." --discovery-mode browser-grounded --headless

# Isolated browser-search execution with a verification certificate
webagent --task "..." --strict-eval --headless

# Local OpenAI-compatible vLLM server
webagent --task "..." --use-vllm --headless
```

Use the [getting-started guide](docs/guides/getting-started.md) for resume, verification,
interactive mode, and output inspection. Discovery contracts are documented separately in
[discovery modes](docs/guides/discovery-modes.md).

## Recorded effect showcase

The current visual uses the Qwen endpoint from the 2026-09-09 paired R5 validation. It
starts at `about:blank`, discovers and compares candidates through browser-visible search,
opens the official `QwenLM/Qwen3.8-Flash-Next` repository and `tech_report.pdf`, downloads
the PDF, and interprets Figure 1. The Qwen and GLM endpoints both passed all 10 independent
assertions and all six certificate checks; recovered search failures remain in their
metrics instead of being removed.

| Model | Task judgment | Certificate | Browser frames | Failed actions |
|---|---:|---:|---:|---:|
| Qwen3.8-Flash | 10/10 | 6/6 | 21 | 3 |
| GLM-5.3-Flash | 10/10 | 6/6 | 16 | 2 |

The [paired validation record](docs/research/results/qwen-strict-search-2026-09-09.zh-CN.md)
documents source hashes, acceptance rules, Figure 1 findings, observation integrity, GIF
provenance, and limitations. The earlier
[2026-09-02 mode comparison](docs/research/results/qwen-report-modes-2026-09-02.md) remains
a historical analysis; its retired local output bundle is not the source of this GIF.

## Evaluation status

The latest complete repository diagnostic is the dated 2026-09-09 R7 campaign. Its one
failed sandbox trajectory, exact metrics, source bindings, and interpretation limits live
in the [campaign record](docs/research/results/generality-campaign-2026-09-09.zh-CN.md),
with the reviewed machine-readable subset in the
[frozen evidence bundle](outputs/published/2026-09-09/README.md). Keeping those volatile
details in dated records prevents this landing page from becoming a second source of truth.

| Layer                  | Scope                                                          | Current state                                                             |
| ---------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Repository diagnostics | Public web, controlled sandbox, and forced-resume long horizon | R7 completed 71/72 (GLM 36/36; Qwen 35/36); one common date plus a separate passed Qwen strict task, so longitudinal evidence remains interim |
| WebArena-Verified Hard | BrowserGym native tasks/evaluator                              | Not run; official sites and reset calibration required                    |
| VisualWebArena         | BrowserGym native tasks/evaluator                              | Not run; official sites, reset calibration, and evaluator assets required |

Scores are never averaged across these layers. Read exact dated results in the
[results index](docs/research/results/README.md), the stable methodology in the
[evaluation protocol](docs/research/evaluation-protocol.md), and executable suites in the
[benchmark guide](src/webagent/benchmarks/README.md).

## Documentation

| Goal                                            | Entry point                                                    |
| ----------------------------------------------- | -------------------------------------------------------------- |
| Install and run the agent                       | [Getting started](docs/guides/getting-started.md)              |
| Choose Hybrid, browser-grounded, or strict mode | [Discovery modes](docs/guides/discovery-modes.md)              |
| Diagnose provider/browser/runtime failures      | [Troubleshooting](docs/guides/troubleshooting.md)              |
| Configure the runtime                           | [Configuration reference](docs/reference/configuration.md)     |
| Understand outputs and resume state             | [Run artifacts](docs/reference/run-artifacts.md)               |
| Review browser and action boundaries            | [Browser and security](docs/reference/browser-and-security.md) |
| Run evaluation suites                           | [Benchmarks](src/webagent/benchmarks/README.md)                |
| Study source call chains in Chinese             | [中文源码理解手册](docs/understanding-zh/README.md)          |
| Navigate everything                             | [Documentation index](docs/README.md)                          |

## Development

```bash
ruff check src/ scripts/ tests/
ruff format --check src/ scripts/ tests/
mypy src/ scripts/
pytest tests/unit/ -v
pytest tests/integration/ -v --no-cov
uv run python scripts/check_docs.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for tools, planners, style, and pull requests, and
the [release guide](docs/operations/release.md) for reproducible packaging.

## Authors and project history

The project began as a University of Hong Kong STAT7008A team project led by
[Li Xiuyin](https://github.com/lixiuyin); the original repository is
[RanJu1122/Web-Agent](https://github.com/RanJu1122/Web-Agent). This repository is Li
Xiuyin's independently maintained and rewritten continuation after the course. Detailed
changes remain in Git history and the [changelog](CHANGELOG.md).

## Acknowledgements

Built with [Playwright](https://playwright.dev/), [PyMuPDF](https://pymupdf.readthedocs.io/),
[Pydantic](https://docs.pydantic.dev/), and Marker/MinerU/PaddleOCR-compatible document
services.

## License

[MIT](LICENSE) © WebAgent contributors
