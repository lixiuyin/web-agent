"""webagent - Autonomous web agent using Vision-Language Models and Playwright."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("lixiuyin-webagent")
except PackageNotFoundError:  # pragma: no cover - source tree without package metadata
    # Keep ``python main.py --version`` useful in a fresh checkout without
    # duplicating the release version in source code.  Installed wheels always
    # take the metadata path above.
    try:
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        )
        __version__ = str(pyproject["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        __version__ = "0+unknown"

from webagent.core.config import AgentConfig
from webagent.core.models import (
    AgentStep,
    BrowserState,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from webagent.core.protocols import Planner, Tool
from webagent.utils.paths import get_artifacts_dir, get_output_dir, resolve_file_path

__all__ = [
    # Core
    "AgentConfig",
    "AgentStep",
    "BrowserState",
    "Planner",
    "TaskStatus",
    "Tool",
    "ToolCall",
    "ToolResult",
    # Version
    "__version__",
    # Utils
    "get_artifacts_dir",
    "get_output_dir",
    "resolve_file_path",
]
