"""Enhanced system prompts and prompt builders for structured output.

Provides enhanced prompt templates that elicit structured thinking
from LLMs, including explicit memory tracking and goal setting.
"""

from __future__ import annotations

from webagent.core.models import BrowserState
from webagent.planner.models import EnhancedToolCall
from webagent.utils.images import image_to_jpeg_b64, is_blank_image

ENHANCED_SYSTEM_PROMPT = """You are a web automation planner. Your response must include
structured thinking to help you reason through tasks effectively.

Respond with a JSON object:

{
  "thinking": "Brief analysis of current situation (1-2 sentences)",
  "memory": "What you've found, visited, or accomplished so far",
  "next_goal": "Next immediate objective - what this action should achieve",
  "tool": "tool_name",
  "parameters": {...},
  "reasoning": "why this action is the right choice"
}

Rules:
- Keep thinking concise (1-2 sentences max)
- Update memory with concrete progress facts
- Set clear next_goal for each step
- Don't repeat failed actions more than 2 times
- If stuck, reassess your strategy
- **CRITICAL: MUST call "done" tool when task is complete**
- **For image analysis tasks: after getting analyze_image result, immediately call "done" with the analysis in the "summary" parameter - the summary should contain the actual answer, not just "completed"**
- The "summary" field is what the user sees - make it comprehensive and directly answer their question
- When searching for recent content (papers, reports, news), use recency="year" or recency="latest" parameter. DO NOT include years (2024, 2025, 2026) in the query itself - use the recency parameter instead

Example:
{
  "thinking": "On search results page looking for PDF links",
  "memory": "Searched for Qwen technical report, found 3 candidate links",
  "next_goal": "Check first result to see if it's the PDF",
  "tool": "click",
  "parameters": {"selector": "a.result__a"},
  "reasoning": "First result looks most relevant based on title"
}
"""


ENHANCED_SYSTEM_PROMPT_WITH_LOOP = """You are a web automation planner. Your response must include
structured thinking to help you reason through tasks effectively.

Respond with a JSON object:

{
  "thinking": "Brief analysis of current situation (1-2 sentences)",
  "memory": "What you've found, visited, or accomplished so far",
  "next_goal": "Next immediate objective - what this action should achieve",
  "tool": "tool_name",
  "parameters": {...},
  "reasoning": "why this action is the right choice"
}

Rules:
- Keep thinking concise (1-2 sentences max)
- Update memory with concrete progress facts
- Set clear next_goal for each step
- Don't repeat failed actions more than 2 times
- If stuck, reassess your strategy
- PAY ATTENTION to loop warnings - change your approach!
- **CRITICAL: MUST call "done" tool when task is complete**
- **For image analysis tasks: after getting analyze_image result, immediately call "done" with the analysis in the "summary" parameter - the summary should contain the actual answer, not just "completed"**
- The "summary" field is what the user sees - make it comprehensive and directly answer their question

Loop Detection: {loop_nudge}
"""


def build_enhanced_prompt(
    task: str,
    browser_state: BrowserState,
    history_text: str,
    available_tools: str,
    loop_nudge: str = "",
) -> tuple[str, str | None]:
    """Build enhanced prompt with structured output format.

    Args:
        task: The user's task description
        browser_state: Current browser state
        history_text: Formatted history of previous actions
        available_tools: Description of available tools
        loop_nudge: Optional nudge message if loop detected

    Returns:
        Tuple of (prompt_text, base64_screenshot)
    """
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

    # Choose system prompt based on loop state
    if loop_nudge:
        system_prompt = ENHANCED_SYSTEM_PROMPT_WITH_LOOP.format(loop_nudge=loop_nudge)
    else:
        system_prompt = ENHANCED_SYSTEM_PROMPT

    # Build enhanced prompt
    prompt = (
        f"{system_prompt}\n\n"
        f"TASK: {task.strip()}\n\n"
        f"URL: {browser_state.url}\n"
        f"TITLE: {browser_state.title}\n\n"
        f"PAGE:\n{dom_context}\n\n"
        f"TOOLS:\n{available_tools}\n\n"
        f"HISTORY:\n{history_summary}\n\n"
        f"YOUR RESPONSE (JSON ONLY):"
    )
    return prompt, screenshot_b64


def parse_enhanced_response(response: str) -> EnhancedToolCall | None:
    """Parse enhanced structured response from LLM.

    Args:
        response: Raw LLM response text (JSON string)

    Returns:
        EnhancedToolCall instance or None if parsing fails
    """
    import json

    s = (response or "").strip()

    # Extract the JSON payload, handling ```json / ``` code fences distinctly.
    json_str = s
    try:
        if "```json" in s:
            start = s.index("```json") + 7
            end = s.index("```", start)
            json_str = s[start:end].strip()
        elif "```" in s:
            start = s.index("```") + 3
            end = s.index("```", start)
            json_str = s[start:end].strip()
    except ValueError:
        # Unterminated fence — fall back to the text after the opening fence.
        json_str = s.split("```", 1)[-1].lstrip("json").strip()

    try:
        data = json.loads(json_str)
        return EnhancedToolCall.from_dict(data)
    except (json.JSONDecodeError, ValueError):
        return None
