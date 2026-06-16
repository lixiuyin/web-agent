"""webagent - Autonomous web agent using Vision-Language Models and Playwright."""

__version__ = "0.1.0"

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
