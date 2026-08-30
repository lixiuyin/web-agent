# AGENTS.md

Guidance for AI coding agents working in this repository. For the architecture
overview, see [README.md](README.md).

## Commands

```bash
pip install -e ".[dev]" && playwright install chromium   # setup
ruff check src/ benchmarks/ tests/                        # lint
ruff format src/ benchmarks/ tests/                       # format
mypy src/ benchmarks/                                     # type-check
pytest tests/unit/ -v                                     # unit tests (no browser) + coverage gate
pytest tests/integration/ -v --no-cov                    # integration (real browser)
```

All four gates (ruff check, ruff format --check, mypy, pytest) must stay green before committing.

The unit suite enforces a branch-coverage gate (`--cov-fail-under=85`, configured in
`pyproject.toml`); keep coverage at or above 85%. The integration suite exercises only a
thin slice of the code with a real browser, so run it with `--no-cov` to skip the gate.

## Conventions

- **Layout:** runtime source lives in `src/webagent/`, organized by system domain
  (`core/ agent/ browser/ planner/ parser/ tools/ utils/`); reusable research contracts and
  analyses live in `evaluation/`, while executable environments/suites/studies live in
  `benchmarks/`. Keep runtime mechanisms separate from external evaluation.
- **Protocols first:** major components implement `typing.Protocol`s in `core/protocols.py`
  (`Planner`, `Tool`, `AgentHook`) — no inheritance required, just matching methods.
- **Tools** are classes decorated with `@tool("name", "description")` in `tools/builtin/`;
  they are auto-discovered. Implement `validate_params` and `async execute() -> ToolResult`.
- **Config** is centralized in `core/config.py` (`pydantic-settings`, `AGENT_`-prefixed env
  vars). Don't hardcode values — add a config field.
- **Immutability & typing:** prefer returning new objects; annotate signatures; keep `mypy` clean.
- **Tests:** add tests for new behavior under `tests/unit/`; mock browser/planner/network.

## Do not commit

`outputs/`, `browser_profile/`, `uploads/`, `.env`, and other generated or locally disclosed
artifacts are gitignored — keep them out of commits. Never commit a populated `.env`.
