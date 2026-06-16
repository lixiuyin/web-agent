"""Shared prompt construction and response parsing for all planners."""

from __future__ import annotations

import json

from webagent.core.models import BrowserState, ToolCall
from webagent.utils.images import image_to_jpeg_b64, is_blank_image

SYSTEM_PROMPT = """You are a web automation planner. Given the task,
current page state, and available tools, respond with a JSON object
describing your next action:

{
  "tool": "tool_name",
  "parameters": {...},
  "reasoning": "explanation"
}

Rules:
- Respond with JSON only, no additional text
- Choose actions based on the current page state and task requirements
- Use the tools available to accomplish the task
- **MUST call the "done" tool when the task is complete**
- **For image analysis tasks (analyze_image, pdf_analyze_figure): immediately after getting the analysis result, call "done" with the analysis in the "summary" parameter**
- CRITICAL: When calling "done", ALWAYS provide a comprehensive "summary" parameter
  that includes all findings, results, and answers to the task questions
- The "summary" field in "done" should contain the actual answer to the user's question,
  not just "Task completed"
- When searching for recent content (papers, reports, news), use recency="year" or recency="latest" parameter. DO NOT include years (2024, 2025, 2026) in the query itself - use the recency parameter instead"""


def build_prompt(
    task: str,
    browser_state: BrowserState,
    history_text: str,
    available_tools: str,
) -> tuple[str, str | None]:
    """Build prompt text and optional base64 screenshot."""
    screenshot_b64: str | None = None
    if browser_state.screenshot is not None and not is_blank_image(browser_state.screenshot):
        screenshot_b64 = image_to_jpeg_b64(browser_state.screenshot, quality=70)

    # Truncate DOM if too long
    dom_context = (
        browser_state.dom_summary[:6000]
        if len(browser_state.dom_summary) > 6000
        else browser_state.dom_summary
    )

    # Build history summary
    history_summary = history_text if history_text else "No previous actions."

    # Situational content only — SYSTEM_PROMPT is sent separately as the
    # system-role message by APIPlanner._call, so prepending it here would
    # duplicate ~300 tokens every step.
    prompt = (
        f"TASK: {task.strip()}\n\n"
        f"URL: {browser_state.url}\n"
        f"TITLE: {browser_state.title}\n\n"
        f"PAGE:\n{dom_context}\n\n"
        f"TOOLS:\n{available_tools}\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"YOUR RESPONSE (JSON ONLY):"
    )
    return prompt, screenshot_b64


def parse_llm_response(response: str) -> ToolCall | None:
    """Parse a JSON tool-call from raw LLM output. Handles various formats."""
    s = (response or "").strip()

    # Remove common natural-language preambles that some LLMs add before the JSON.
    # NOTE: entries must be prose prefixes only — never a fragment of the JSON
    # itself, or splitting on it would corrupt valid pretty-printed objects.
    prefixes_to_remove = [
        "Here's the JSON response:",
        "The JSON response is:",
        "Response:",
        "Here is the JSON:",
        "JSON:",
    ]
    for prefix in prefixes_to_remove:
        if prefix in s:
            parts = s.split(prefix, 1)
            if len(parts) > 1:
                s = parts[1].strip()

    json_str: str | None = None

    # Try direct JSON first
    if s.startswith("{") and s.endswith("}"):
        json_str = s
    # Try extracting from markdown code blocks
    elif "```json" in s:
        try:
            start = s.index("```json") + 7
            end = s.index("```", start)
            json_str = s[start:end].strip()
        except ValueError:
            pass
    elif "```" in s:
        try:
            start = s.index("```") + 3
            end = s.index("```", start)
            json_str = s[start:end].strip()
        except ValueError:
            pass
    # Try finding first complete JSON object
    elif "{" in s and "}" in s:
        try:
            start = s.index("{")
            depth = 0
            for i in range(start, len(s)):
                if s[i] == "{":
                    depth += 1
                elif s[i] == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = s[start : i + 1]
                        break
        except ValueError:
            pass

    if not json_str:
        return None

    # Clean up common issues
    json_str = json_str.strip()
    json_str = json_str.replace(",\n}", "\n}").replace(",\n]", "\n]")
    json_str = json_str.replace(",}", "}").replace(",]", "]")

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    tool_name = parsed.get("tool", parsed.get("action", parsed.get("function", "")))
    if isinstance(tool_name, str):
        tool_name = tool_name.strip()
    else:
        tool_name = ""

    parameters = parsed.get("parameters", parsed.get("params", parsed.get("arguments", {})))
    if not isinstance(parameters, dict):
        parameters = {}

    reasoning = parsed.get(
        "reasoning", parsed.get("thought", parsed.get("explanation", parsed.get("comment", "")))
    )
    if not isinstance(reasoning, str):
        reasoning = ""

    if not tool_name:
        return None

    return ToolCall(
        tool_name=tool_name,
        parameters=parameters,
        reasoning=reasoning,
    )
