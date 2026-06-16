# AGENTS.md

Guidance for AI coding agents working in this repository. For the architecture
overview, see [README.md](README.md).

## Commands

```bash
pip install -e ".[dev]" && playwright install chromium   # setup
ruff check src/ tests/                                    # lint
ruff format src/ tests/                                   # format
mypy src/                                                 # type-check
pytest tests/unit/ -v                                     # unit tests (no browser)
pytest tests/integration/ -v                              # integration (real browser)
```

All four gates (ruff check, ruff format --check, mypy, pytest) must stay green before committing.

## Conventions

- **Layout:** source lives in `src/webagent/`, organized by domain
  (`core/ agent/ browser/ planner/ parser/ tools/ utils/`). Keep modules cohesive.
- **Protocols first:** major components implement `typing.Protocol`s in `core/protocols.py`
  (`Planner`, `Tool`, `AgentHook`) — no inheritance required, just matching methods.
- **Tools** are classes decorated with `@tool("name", "description")` in `tools/builtin/`;
  they are auto-discovered. Implement `validate_params` and `async execute() -> ToolResult`.
- **Config** is centralized in `core/config.py` (`pydantic-settings`, `AGENT_`-prefixed env
  vars). Don't hardcode values — add a config field.
- **Immutability & typing:** prefer returning new objects; annotate signatures; keep `mypy` clean.
- **Tests:** add tests for new behavior under `tests/unit/`; mock browser/planner/network.

## Do not commit

`outputs/`, `browser_profile/`, `.env`, and other generated artifacts are gitignored —
keep them out of commits. Never commit a populated `.env`.
