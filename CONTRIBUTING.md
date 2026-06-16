# Contributing to webagent

Thank you for your interest in contributing!

## Development Setup

```bash
# Clone and install in editable mode with dev dependencies
git clone https://github.com/lixiuyin/web-agent.git
cd web-agent
pip install -e ".[dev]"
playwright install chromium

# Run tests (186 unit + integration tests)
pytest tests/unit/ -v
pytest tests/integration/ -v   # requires a browser

# Lint, format, and type-check
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
```

> New to the codebase? See [README.md](README.md) for the architecture overview.

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Make your changes following the existing code style
3. Add tests for new functionality
4. Ensure all tests pass: `pytest tests/unit/ -v`
5. Ensure linting passes: `ruff check src/ tests/`
6. Submit a pull request with a clear description

## Adding a New Tool

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

Then import it in `src/webagent/tools/builtin/__init__.py`.

## Adding a New Planner

Implement the `Planner` protocol from `webagent.core.protocols`:

```python
from webagent.core.protocols import Planner

class MyPlanner:
    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    async def plan_action(self, task, browser_state, history_text, available_tools) -> ToolCall | None: ...
    async def analyze_image(self, image, question) -> str: ...
```

## Code Style

- Use `ruff` for linting and formatting
- Type hints are encouraged
- Follow existing patterns for consistency
