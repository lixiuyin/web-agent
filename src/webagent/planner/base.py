"""Shared prompt construction and response parsing for all planners."""

from __future__ import annotations

import json
from typing import Any

from webagent.core.models import BrowserState, ToolCall
from webagent.utils.images import image_to_jpeg_b64, is_blank_image

TRANSPORT_AGNOSTIC_PLANNING_RULES = """Task and evidence rules:
- Use only listed tools and call `done` when the task is complete. Its summary must contain the
  actual comprehensive answer, not merely a completion notice.
- After analyze_image or pdf_analyze_figure returns the requested analysis, call `done` next and
  carry the analysis into its summary. pdf_analyze_figure parses/caches the PDF and resolves Figure
  N itself, so call it directly after download_pdf unless separate parse/list output is requested.
- Automated Google search is disabled by default. Use Bing or DuckDuckGo unless the user explicitly
  requires Google and the runtime enables it. Express recency through the search tool parameter,
  not query operators such as after: or dt:y.
- For latest/newest tasks, run at least two differently worded searches. A missing result date is
  unknown, never proof that a candidate is older. Open plausible newer or official candidates and
  inspect page/file history. If a result exposes a higher subject version, search that exact version
  and seek a first-party source before accepting or rejecting it.
- For latest product/model tasks, include a broad current-year subject release landscape search
  (model/version/release/series/generation/lineup), not only a paper index or one known version.
- A latest official report must match the requested subject and have first-party provenance from the
  official organization/site/team. Reject papers that merely mention or build on the subject, compare
  explicit publication/file dates, and state the selected winner's explicit date in the final answer.
  If it remains unknown or conflicts, keep investigating or report the uncertainty.
- Never invent a URL, date, result, truncation claim, local path, or other fact in either the action
  rationale or final answer; use only observed evidence.
- For benchmark figures, a highlighted bar is not necessarily first place. Report actual values and
  rankings and reconcile vision with any related extracted table before completion.
"""

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
- pdf_analyze_figure parses/caches the PDF and resolves the numbered caption itself. For a task that only asks to interpret Figure N, call it directly after download_pdf; do not spend a separate step on pdf_parse or pdf_list_figures.
- CRITICAL: When calling "done", ALWAYS provide a comprehensive "summary" parameter
  that includes all findings, results, and answers to the task questions
- The "summary" field in "done" should contain the actual answer to the user's question,
  not just "Task completed"
- Automated Google search is disabled by default because it repeatedly triggers human verification. Use Bing/DuckDuckGo through search; do not request engine="google" unless the user explicitly requires Google and the runtime enables it.
- When searching for recent content (papers, reports, news), use recency="year" or recency="latest". DO NOT put search-engine operators such as after: or dt:y in the query.
- For latest/newest tasks, run at least two differently worded searches. A missing search-result date means UNKNOWN, never older: open plausible official candidates (especially newer-looking version names) and inspect their page/file history before ranking them. If even a third-party result exposes a higher subject version, search that exact version and look for a first-party source before accepting or rejecting it.
- Before calling "done", re-read the task and make the summary answer every requested field explicitly. For latest/newest tasks that ask for dates or a dated comparison, state the selected winner's explicit publication/release/file date in the summary; if that date is still unknown, keep searching or inspect the official page/file history instead of merely saying it is "newest".
- For latest/newest product/model tasks, include a broad current-year version-landscape search (subject plus model/version/release/series/lineup) rather than searching only paper indexes or one already-known version.
- When the task asks for the MOST RECENT / LATEST official report, paper, or model, do NOT trust the first result, arXiv alone, or a paper that merely mentions/uses the subject. Prefer official_report_search with a known official owner only when that tool is listed as available; otherwise use the browser search tool and follow visible result/page links. A qualifying report must have (1) title/filename about the requested subject and (2) first-party provenance such as the official project organization, project site, or subject team's authorship. Compare explicit publication/file-commit dates and download the newest. Treat third-party papers that only build on or mention the subject as non-candidates.
- Never invent details in "reasoning" that are not present in the actual tool results (e.g. a target URL, a truncated/omitted result, a missing date). Justify each action only from what the results really show.
- Apply the same evidence rule to the final summary: never convert relative dates or third-party coverage dates into an exact official publication/file date. If the task requires an exact selected date, obtain it from explicit search evidence or the official page/file history; if sources conflict, keep investigating or report the uncertainty.
- When a figure is a benchmark bar chart, the model's own bar may be highlighted but NOT necessarily the tallest. Report the actual numbers and ranking; if the parse also extracted a comparison table (pdf_analyze_figure returns "related_tables"), reconcile the vision reading against those table numbers before calling done."""

# Terser variant used when ``use_structured_output`` is enabled: it asks the
# model for a single directly-executable tool call. The prompt body built by
# ``build_prompt`` is identical for both variants — only the system prompt and
# this instruction differ.
STRUCTURED_SYSTEM_PROMPT = """You are a web automation planner.

Respond with one JSON object:

{
  "tool": "tool_name",
  "parameters": {...},
  "reasoning": "why this action is the right choice"
}

Rules:
- Don't repeat failed actions more than 2 times; if stuck, change strategy.
- You MUST call "done" when the task is complete.
- For image-analysis tasks, call "done" immediately after analyze_image and put the
  actual answer in the "summary" parameter.
- pdf_analyze_figure already parses/caches the PDF and resolves Figure N; call it directly
  after download_pdf unless the task separately needs a full parse/listing result.
- Automated Google search is disabled by default; use Bing/DuckDuckGo and do not
  request Google unless the user explicitly requires it.
- When searching for recent content, use the search tool's recency parameter instead
  of putting years or engine operators (after:, dt:y) in the query.
- For latest/newest tasks, use at least two distinct search queries. Treat missing SERP dates as
  unknown and open plausible official/newer-version candidates before ranking them. Search any
  higher subject version seen in a third-party result by exact version before dismissing it.
- Before "done", re-read the task and explicitly answer every requested field. If a latest/newest
  task asks for dates or a dated comparison, include the selected winner's explicit date in the
  summary; if unknown, keep searching or inspect its official page/file history.
- For latest product/model tasks, run a broad current-year subject + version/release/model/lineup
  search so an already-known report name does not hide a newer repository-only generation.
- For "most recent / latest official report" tasks, prefer official_report_search with a
  known official owner only when it is an available tool; otherwise use browser search and
  visible result/page links. Reject
  third-party papers that merely mention/use the subject. Compare explicit dates only
  among first-party, subject-matching reports, then choose the newest.
- Don't invent facts (URLs, truncation, missing dates) in "reasoning".
- Don't invent them in the final summary either. An exact selected date must come from explicit
  search evidence or the official page/file history, not an inferred relative date or the date of
  third-party coverage; investigate conflicts instead of choosing one silently.
- For benchmark figures, "highlighted" ≠ "first place"; cross-check with any extracted table.

Example:
{
  "tool": "click",
  "parameters": {"selector": {"type": "text", "value": "Submit"}},
  "reasoning": "Submit the completed form"
}
"""


def build_prompt(
    task: str,
    browser_state: BrowserState,
    history_text: str,
    available_tools: str,
    response_instruction: str = "YOUR RESPONSE (JSON ONLY):",
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
        f"{response_instruction}"
    )
    return prompt, screenshot_b64


def _strip_preamble(s: str) -> str:
    """Remove common natural-language preambles some LLMs add before the JSON.

    NOTE: entries must be prose prefixes only — never a fragment of the JSON
    itself, or splitting on it would corrupt valid pretty-printed objects.
    """
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
    return s


def _extract_fenced_json(s: str, fence: str) -> str | None:
    """Extract the first markdown code fence body, or None if unterminated."""
    try:
        start = s.index(fence) + len(fence)
        end = s.index("```", start)
    except ValueError:
        return None
    return s[start:end].strip()


def _extract_balanced_json(s: str) -> str | None:
    """Extract the first complete brace-balanced JSON object from *s*."""
    if "{" not in s or "}" not in s:
        return None
    start = s.index("{")
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _extract_json_str(s: str) -> str | None:
    """Pull a JSON candidate out of raw LLM output, trying formats in order."""
    if s.startswith("{") and s.endswith("}"):
        return s
    if "```json" in s:
        return _extract_fenced_json(s, "```json")
    if "```" in s:
        return _extract_fenced_json(s, "```")
    return _extract_balanced_json(s)


def _clean_json(json_str: str) -> str:
    """Strip trailing commas, the most common hand-written JSON defect."""
    json_str = json_str.strip()
    json_str = json_str.replace(",\n}", "\n}").replace(",\n]", "\n]")
    return json_str.replace(",}", "}").replace(",]", "]")


def _first_str(parsed: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    """Value of the first key that is present, as a stripped string.

    Mirrors nested ``dict.get`` chains: a present-but-non-string value yields
    ``""`` rather than falling through to later keys.
    """
    for key in keys:
        if key in parsed:
            value = parsed[key]
            return value.strip() if isinstance(value, str) else ""
    return default


def parse_llm_response(response: str) -> ToolCall | None:
    """Parse a JSON tool-call from raw LLM output. Handles various formats."""
    s = _strip_preamble((response or "").strip())

    json_str = _extract_json_str(s)
    if not json_str:
        return None

    try:
        parsed = json.loads(_clean_json(json_str))
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    tool_name = _first_str(parsed, ("tool", "tool_name", "action", "function"))
    if not tool_name:
        return None

    parameters = parsed.get("parameters", parsed.get("params", parsed.get("arguments", {})))
    if not isinstance(parameters, dict):
        parameters = {}

    return ToolCall(
        tool_name=tool_name,
        parameters=parameters,
        reasoning=_first_str(parsed, ("reasoning", "reason", "thought", "explanation", "comment")),
    )
