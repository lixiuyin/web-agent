# Contributing to webagent

Thank you for your interest in contributing!

## Development setup

```bash
# Clone and install in editable mode with dev dependencies
git clone https://github.com/lixiuyin/web-agent.git
cd web-agent
pip install -e ".[dev]"
playwright install chromium

# Run unit and real-browser integration tests
pytest tests/unit/ -v
pytest tests/integration/ -v --no-cov   # requires a browser

# Lint, format, and type-check
ruff check src/ benchmarks/ scripts/ tests/
ruff format --check src/ benchmarks/ scripts/ tests/
mypy src/ benchmarks/ scripts/
```

> New to the codebase? See [README.md](README.md) for the architecture overview.

## Pull request process

1. Fork the repository and create a feature branch from `main`
2. Make your changes following the existing code style
3. Add tests for new functionality
4. Ensure all tests pass: `pytest tests/unit/ -v`
5. Update the canonical owner listed in
   [`docs/documentation-style.md`](docs/documentation-style.md) when user behavior,
   configuration, outputs, evaluation, or packaging changes. Keep overview pages concise
   and link to that owner.
6. Ensure linting passes: `ruff check src/ benchmarks/ scripts/ tests/`
7. Submit a pull request with a clear description

## Adding a new tool

Tools use the `@tool` decorator for automatic registration:

```python
from webagent.tools.registry import tool
from webagent.core.models import ToolResult

@tool("my_tool", "Description of my tool. params: param1 (string)")
class MyTool:
    def __init__(self, **kw):
        pass

    def validate_params(self, params: dict) -> None:
        if "param1" not in params:
            raise ValueError("'param1' required")

    async def execute(self, params: dict) -> ToolResult:
        return ToolResult(success=True, tool_name="my_tool", data={"result": params["param1"]})
```

Place it under `src/webagent/tools/builtin/`. Built-in modules are auto-discovered; do not
add a manual import solely to register the tool. Add registry/schema tests for its name,
parameters, validation, and policy exposure.

## Adding a new planner

Implement the `Planner` protocol from `webagent.core.protocols`:

```python
from webagent.core.protocols import Planner

class MyPlanner:
    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    async def plan_action(self, task, browser_state, history_text, available_tools) -> ToolCall | None: ...
    async def analyze_image(self, image, question) -> str: ...
```

## Code style

- Use `ruff` for linting and formatting.
- Annotate public and internal signatures and keep strict Mypy green.
- Prefer the existing Protocol contracts and immutable return values.
- Follow the [documentation style](docs/documentation-style.md) for prose and diagrams.

## Release validation

Packaging, reproducible builds, sparse checkout, distribution smoke tests, tags, and
trusted publishing are maintained in the
[release procedure](docs/operations/release.md). Do not duplicate release commands here.
