# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Provider-agnostic planning** — `APIPlanner` for any OpenAI-compatible endpoint, with
  automatic vision-capability detection and adapters for OpenAI / Azure / Claude / Gemini /
  MiniMax; reasoning-model support (strips `<think>` tags, falls back to `reasoning_content`).
- **Document parser cascade** (`webagent.parser`) — cloud OCR via Marker → MinerU → PaddleOCR
  with a local PyMuPDF fallback, gated by a quality check; produces structured Markdown,
  tables, sections, and images.
- **Caption-aware figure resolution** — extracted images are associated with their real
  `Figure N` caption (from markdown alt-text or the nearest caption line); `pdf_list_figures`
  separates real captioned figures from logos, and `pdf_analyze_figure` resolves "Figure N"
  by number/caption instead of extraction order.
- **Resilient search** — `search` tool cascades Google → Bing → DuckDuckGo, detects
  bot-block/zero-result pages, and falls back to direct arXiv candidates; `arxiv_search` tool.
- **Loop detection** — four signals (action-repeat, page-stagnation, URL-oscillation,
  no-progress) injected as a nudge before each planning step.
- **Hard request timeout** (`api_hard_timeout`) — a wall-clock cap (`asyncio.wait_for`) that
  bounds trickling/hung LLM responses which evade httpx's per-read timeout.
- **Organized run artifacts** — API-extracted content under `artifacts/pdf/`, the final
  analysis written to `artifacts/output.txt`, and the analyzed figure saved as `artifacts/figure.<ext>`.
- **Anti-detection browser** — stealth profile + CDP-based interactive-element extraction.
- Structured planning mode (`EnhancedToolCall`) with explicit reasoning fields.
- 186 tests (unit + integration), full `mypy` typing, `ruff` lint/format.

### Changed
- **License changed from Apache-2.0 to MIT.**
- Replaced the original local-only model + OCR stack (local vLLM + DotsMOCR/Chandra) with the
  provider-agnostic cloud-cascade design; `webagent.utils.chandra_pdf` is now a thin
  compatibility shim over `webagent.parser`.
- Standardized the repository: `outputs/` and build artifacts are gitignored; removed stray
  root-level demo scripts and vestigial model symlinks.

## [0.1.0] - 2026-03-16

### Added
- Production-grade Python package structure (`src/webagent/`)
- Protocol-based architecture (`core/protocols.py`) for pluggable components
- Plugin-based tool registry with the `@tool` decorator and auto-discovery
- Multiple planner backends: Stub, API (OpenAI-compatible), local vLLM
- Pydantic-based configuration with env-var support
- Agent lifecycle hooks for observability and session-history management
- Comprehensive test suite (pytest)

### Changed
- Migrated from a flat file layout to a `src/` package
- Decoupled browser, planner, and tools into independent modules
