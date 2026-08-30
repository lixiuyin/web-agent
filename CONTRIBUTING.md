# Contributing to webagent

Thank you for your interest in contributing!

## Development Setup

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
ruff check src/ benchmarks/ tests/
ruff format --check src/ benchmarks/ tests/
mypy src/ benchmarks/
```

> New to the codebase? See [README.md](README.md) for the architecture overview.

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Make your changes following the existing code style
3. Add tests for new functionality
4. Ensure all tests pass: `pytest tests/unit/ -v`
5. Ensure linting passes: `ruff check src/ benchmarks/ tests/`
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

## Release validation

Release tags use the package version with a `v` prefix (for example, `v0.2.0`). Before tagging,
move the corresponding changelog entry out of `Unreleased`, make sure the working tree is clean,
and run the same artifact checks as CI:

```bash
python -m webagent.release state --root . --tag v0.2.0 --require-clean
python -m build --outdir dist
python -m webagent.release artifacts dist
twine check dist/*
```

The release workflow rebuilds wheel and sdist twice from the tagged source epoch, compares their
SHA-256 digests, smoke-tests both distributions in isolated environments, creates a provenance
attestation, and publishes through a protected `pypi` environment using trusted publishing.
