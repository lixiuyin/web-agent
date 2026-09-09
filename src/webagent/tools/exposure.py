"""Tool-exposure profiles for ordinary Hybrid and restricted browser-grounded runs."""

from __future__ import annotations

from collections.abc import Collection

DIRECT_SOURCE_DISCOVERY_TOOLS = frozenset(
    {
        "arxiv_search",
        "github_search",
        "official_report_search",
    }
)


def allowed_tools_for_discovery_mode(
    tool_names: Collection[str], discovery_mode: str
) -> frozenset[str] | None:
    """Return the planner-visible tools for one discovery mode.

    ``None`` preserves the executor convention meaning "all registered tools".
    Browser-grounded mode removes direct-source discovery tools from both the
    planner prompt and runtime dispatch; the ordinary Hybrid mode exposes them.
    """
    if discovery_mode == "hybrid":
        return None
    if discovery_mode != "browser-grounded":
        raise ValueError(f"Unsupported discovery mode: {discovery_mode!r}")
    return frozenset(
        name for name in tool_names if name.casefold() not in DIRECT_SOURCE_DISCOVERY_TOOLS
    )


__all__ = ["DIRECT_SOURCE_DISCOVERY_TOOLS", "allowed_tools_for_discovery_mode"]
