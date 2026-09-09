# AGENTS.md

Guidance for AI coding agents working in this repository. For the architecture
overview, see [README.md](README.md).

## Commands

```bash
uv sync && uv run playwright install chromium             # setup
ruff check src/ scripts/ tests/               # lint
ruff format src/ scripts/ tests/              # format
mypy src/ scripts/                            # type-check
pytest tests/unit/ -v                                     # unit tests (no browser) + coverage gate
pytest tests/integration/ -v --no-cov                    # integration (real browser)
uv run python scripts/check_docs.py                        # Markdown structure + local links
```

All code gates (ruff check, ruff format --check, mypy, pytest) and the documentation
check must stay green before committing.

Ruff enforces a maximum cyclomatic complexity of 10 (`C901`) for source, scripts and tests. Split responsibilities rather than raising the threshold or adding suppressions.

The unit suite enforces combined statement/branch coverage of at least 85%
(`branch=true` and `--cov-fail-under=85`, configured in `pyproject.toml`). This does not
require branch-only coverage to reach 85%. The integration suite exercises only a
thin slice of the code with a real browser, so run it with `--no-cov` to skip the gate.

## Conventions

- **Layout:** runtime source lives in `src/webagent/`, organized by system domain
  (`core/ agent/ browser/ planner/ parser/ tools/ utils/`); reusable research contracts and
  analyses live in `evaluation/`, while executable environments/suites/studies live in
  `src/webagent/benchmarks/`. Keep runtime mechanisms separate from external evaluation.
- **Protocols first:** major components implement `typing.Protocol`s in `core/protocols.py`
  (`Planner`, `Tool`, `AgentHook`) — no inheritance required, just matching methods.
- **Tools** are classes decorated with `@tool("name", "description")` in `tools/builtin/`;
  they are auto-discovered. Implement `validate_params` and `async execute() -> ToolResult`.
- **Config** is centralized in `core/config.py` (`pydantic-settings`, `AGENT_`-prefixed env
  vars). Don't hardcode values — add a config field.
- **Immutability & typing:** prefer returning new objects; annotate signatures; keep `mypy` clean.
- **Tests:** add tests for new behavior under `tests/unit/`; mock browser/planner/network.

## Generated and local artifacts

`outputs/`, `browser_profile/`, `uploads/`, `.env`, and other generated or locally disclosed
artifacts are gitignored by default. The only output exception is a frozen, reviewed evidence
prefix explicitly listed in `[tool.webagent.release].allowed-tracked-prefixes`; screenshots below
that prefix must use Git LFS. Never commit transient locks, OS metadata, browser profiles, uploads,
or a populated `.env`.
