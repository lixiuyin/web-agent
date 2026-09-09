"""URL provenance, discovery criteria, and checkpoint value validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

_BROWSER_TOOLS = {
    "back",
    "click",
    "click_link",
    "close_tab",
    "dom_summary",
    "done",
    "extract_text",
    "frame_interact",
    "forward",
    "get_all_links",
    "get_attribute",
    "get_search_results",
    "get_title",
    "get_url",
    "goto",
    "hover",
    "inspect_download_links",
    "list_frames",
    "list_tabs",
    "open_tab",
    "press",
    "refresh",
    "screenshot",
    "scroll",
    "scroll_to_element",
    "shadow_dom",
    "switch_tab",
    "search",
    "select_dropdown",
    "type",
    "upload_file",
    "download_file",
    "wait",
    "wait_for_element",
}

# Tools whose successful result saves a browser-fetched artifact. ``download_pdf``
# fetches an evidence-grounded URL; ``download_file`` clicks a control that is
# visible on the current page, which is equally grounded browser evidence.
_DOWNLOAD_TOOLS = {"download_pdf", "download_file"}

_PDF_TOOLS = {
    "download_pdf",
    "pdf_analyze_figure",
    "pdf_compare_entities",
    "pdf_content_summary",
    "pdf_extract_citations",
    "pdf_extract_images",
    "pdf_extract_metrics",
    "pdf_extract_table_data",
    "pdf_extract_text",
    "pdf_extract_topics",
    "pdf_find_images",
    "pdf_find_mentions",
    "pdf_find_section",
    "pdf_find_tables",
    "pdf_get_figure_info",
    "pdf_get_hierarchy",
    "pdf_get_metadata",
    "pdf_get_section",
    "pdf_list_figures",
    "pdf_list_sections",
    "pdf_list_tables",
    "pdf_parse",
    "pdf_qa",
    "pdf_search",
    "pdf_summarize_sections",
}

_SEARCH_INDEX_TERMS = ("arxiv", "researchgate", "semantic scholar", "hugging face papers")
_SEARCH_INDEX_HOST_TERMS = ("arxiv", "researchgate", "semanticscholar", "huggingface")
_SEARCH_ENGINE_HOST_MARKERS = (
    "bing.",
    "google.",
    "duckduckgo.",
    "search.yahoo.",
    "search.seznam.",
)
_REPOSITORY_HOSTS = {"github.com", "gitlab.com", "codeberg.org"}
_RELEASE_LANDSCAPE_TERMS = ("generation", "lineup", "model", "release", "series", "version")
_HYBRID_DISCOVERY_TOOLS = {
    "arxiv_search",
    "github_search",
    "official_report_search",
    "search",
}
_REPORT_FILE_RE = re.compile(r"(?:report|white[-_]?paper|paper).*\.pdf$", flags=re.IGNORECASE)
_TASK_STOPWORDS = {
    "about",
    "and",
    "describe",
    "describing",
    "download",
    "explain",
    "figure",
    "find",
    "findings",
    "from",
    "interpret",
    "its",
    "key",
    "latest",
    "most",
    "newest",
    "pdf",
    "purpose",
    "recent",
    "report",
    "summarize",
    "technical",
    "that",
    "then",
    "the",
    "this",
    "with",
}

# Result/link entries expose the document identity next to its URL. Any of these
# keys can carry the visible text the planner saw for that URL.
_LABELED_URL_KEYS = ("href", "url", "link")
_LABEL_TEXT_KEYS = ("text", "title", "snippet", "label", "name", "aria_label")
_MAX_LABELS_PER_URL = 8
_MAX_LABEL_LENGTH = 200

_VERSION_SUFFIX_RE = re.compile(r"\d+(?:\.\d+)+")
_DISCOVERY_TASK_RE = re.compile(
    r"\b(?:find|search|discover|locate|look\s+up|latest|newest|most\s+recent)\b|"
    r"查找|搜索|寻找|最新|最近(?:的)?|最晚",
    flags=re.IGNORECASE,
)

_LATEST_EVIDENCE_GUIDANCE = (
    "For latest/newest discovery, do not assume the current generation from model memory. "
    "Before the first download or done attempt, collect the complete evidence checklist: "
    "(1) at least two distinct successful browser searches; (2) a broad current-year search "
    "that is not restricted to a paper index; (3) a subject-wide current-year release-landscape "
    "search whose results themselves show relevant version/release evidence, not merely a query "
    "stuffed with model/version/release terms; (4) a non-site official identity search; (5) an "
    "independent identity-bound scope search containing the endorsed host/owner and selected "
    "candidate name and whose results return that candidate "
    "repository or host; (6) an exact follow-up for the highest dotted subject version seen; and "
    "(7) for a document hosted outside the official host (for example on arXiv), visible link "
    "text or a page title that names the task subject, because an official page also links to "
    "unrelated papers. "
    "If the selected candidate is in a repository, the identity search must itself return that "
    "repository host and owner; an official-homepage result does not endorse an unrelated owner. "
    "Run that repository-identity search before its independent candidate scope search. "
    "An arXiv PDF is admissible only after its /abs/ link was visible (get_all_links) on an "
    "endorsed official repository or website page; searching for arXiv itself never endorses "
    "a paper index, so open the official page instead. "
    "A denial reports every still-missing item at once, so complete the whole list before retrying. "
    "A task requesting a PDF or numbered figure cannot finish until a PDF download (download_pdf "
    "on an observed URL, or download_file on a visible download control) and the required "
    "pdf_analyze_figure action have succeeded. "
)


def _is_pdf_artifact(path: Path) -> bool:
    """Return whether a saved download is a PDF by filename or magic header."""
    if path.suffix.casefold() == ".pdf":
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _distinctive_task_keywords(task: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._-]*", task.casefold())
        if len(token) >= 3 and token not in _TASK_STOPWORDS and not token.isdigit()
    }


def _decode_visible_result(value: str) -> dict[str, Any]:
    """Decode only complete planner-visible JSON; truncation fails closed."""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _version_key(value: str) -> tuple[int, ...]:
    """Return a comparable numeric key for a dotted version token."""
    match = _VERSION_SUFFIX_RE.search(value)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def _iter_values(value: Any) -> Any:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    else:
        yield value


def _iter_labeled_urls(value: Any) -> Any:
    """Yield ``(url, label)`` pairs for every object that names a URL and its text.

    Search results, link enumerations, download candidates and navigation results
    all place the visible text (title, anchor text, snippet) beside the URL in the
    same JSON object, so this is the exact text the planner saw for that URL.
    """
    if isinstance(value, list):
        for item in value:
            yield from _iter_labeled_urls(item)
        return
    if not isinstance(value, dict):
        return
    urls = [value[key] for key in _LABELED_URL_KEYS if isinstance(value.get(key), str)]
    labels = [
        stripped
        for key in _LABEL_TEXT_KEYS
        if isinstance(value.get(key), str) and (stripped := value[key].strip())
    ]
    for url in urls:
        for label in labels:
            yield url, label[:_MAX_LABEL_LENGTH]
    for item in value.values():
        if isinstance(item, (dict, list)):
            yield from _iter_labeled_urls(item)


def _checkpoint_label_map(state: dict[str, Any], key: str) -> dict[str, list[str]]:
    """Restore the optional visible-text map, failing closed on malformed values."""
    value = state.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"checkpoint field {key} must be an object")
    result: dict[str, list[str]] = {}
    for raw_url, raw_labels in value.items():
        if not isinstance(raw_url, str) or not isinstance(raw_labels, list):
            raise ValueError(f"checkpoint field {key} contains invalid labels")
        labels = [label[:_MAX_LABEL_LENGTH] for label in raw_labels if isinstance(label, str)]
        if len(labels) != len(raw_labels):
            raise ValueError(f"checkpoint field {key} contains invalid labels")
        result[raw_url] = labels[:_MAX_LABELS_PER_URL]
    return result


def _checkpoint_bool(state: dict[str, Any], key: str) -> bool:
    value = state.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"checkpoint field {key} must be boolean")
    return value


def _checkpoint_non_negative_int(state: dict[str, Any], key: str) -> int:
    value = state.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"checkpoint field {key} must be a non-negative integer")
    return value


def _checkpoint_str_set(state: dict[str, Any], key: str) -> set[str]:
    value = state.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"checkpoint field {key} must be a string list")
    return set(value)


def _checkpoint_evidence_map(state: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"checkpoint field {key} must be an object")
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_evidence in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_evidence, dict):
            raise ValueError(f"checkpoint field {key} contains invalid evidence")
        result[raw_name] = dict(raw_evidence)
    return result


def _checkpoint_policy_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "about:blank"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))


def _checkpoint_policy_evidence(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"page_url", "source_url", "url"} and isinstance(item, str):
            result[key] = _checkpoint_policy_url(item)
        elif key in {"policy_step", "date", "datetime"} or isinstance(item, (bool, int, float)):
            result[key] = item
        elif key in {"source", "source_tool"} and isinstance(item, str):
            result[key] = item if len(item) <= 100 else "[omitted]"
    return result


def _checkpoint_policy_evidence_map(
    value: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        _checkpoint_policy_url(url): _checkpoint_policy_evidence(evidence)
        for url, evidence in value.items()
    }


@dataclass(frozen=True, slots=True)
class _SiteScope:
    host: str
    path_prefix: str


def _site_scope(query: str) -> _SiteScope | None:
    match = re.search(r"(?:^|\s)site:([^\s]+)", query, flags=re.IGNORECASE)
    if match is None:
        return None
    value = match.group(1).strip().rstrip("/")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if not parsed.hostname:
        return None
    path = parsed.path.rstrip("/")
    return _SiteScope(parsed.hostname.casefold(), path.casefold())


def _url_matches_scope(url: str, scope: _SiteScope) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if host != scope.host and not host.endswith(f".{scope.host}"):
        return False
    path = parsed.path.casefold().rstrip("/")
    return (
        not scope.path_prefix
        or path == scope.path_prefix
        or path.startswith(f"{scope.path_prefix}/")
    )


def _query_precisely_targets_url(query: Any, target: str) -> bool:
    """Return whether a search query asks for the exact missing URL evidence."""
    if not isinstance(query, str) or not query.strip():
        return False
    normalized = query.casefold()
    canonical = target.casefold()
    if canonical in normalized:
        return True
    parsed = urlsplit(target)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold().rstrip("/")
    scope = _site_scope(query)
    if scope is not None:
        return scope.host == host and scope.path_prefix == path
    path_tokens = [token for token in path.split("/") if token]
    return bool(
        host and path_tokens and host in normalized and all(t in normalized for t in path_tokens)
    )


_ARXIV_PDF_RESOURCE_RE = re.compile(r"^/pdf/\d{4}\.\d{4,6}(?:v\d+)?$")
_PUBLIC_SUFFIX_LABELS = {"ac", "co", "com", "edu", "gov", "net", "org"}


def _looks_like_pdf_resource(url: str) -> bool:
    """Return whether a URL addresses a PDF document rather than an HTML page."""
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    if path.endswith(".pdf"):
        return True
    host = (parsed.hostname or "").casefold()
    return (
        host in {"arxiv.org", "export.arxiv.org"} and _ARXIV_PDF_RESOURCE_RE.match(path) is not None
    )


def _registrable_label(hostname: str) -> str | None:
    """Return the brand label of a hostname (``qwen`` for ``www.qwen.ai``).

    Two-level public suffixes such as ``co.uk`` or ``com.cn`` are skipped so the
    label reflects the registrant rather than the registry.
    """
    labels = [label for label in hostname.casefold().split(".") if label]
    if len(labels) < 2:
        return None
    labels.pop()
    if len(labels) >= 2 and labels[-1] in _PUBLIC_SUFFIX_LABELS:
        labels.pop()
    return labels[-1] if labels else None


def _repository_owner(path: str) -> str | None:
    segments = [segment for segment in path.casefold().split("/") if segment]
    if not segments:
        return None
    if segments[0] in {"orgs", "users"}:
        return segments[1] if len(segments) > 1 else None
    if segments[0] in {"about", "collections", "enterprise", "features", "login", "search"}:
        return None
    return segments[0]


def _repository_identity(url: str) -> tuple[str, str, str] | None:
    """Normalize repository and raw-file URLs to (host, owner, repository)."""
    parsed = urlsplit(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if host == "raw.githubusercontent.com":
        host = "github.com"
    if host not in _REPOSITORY_HOSTS or len(segments) < 2:
        return None
    if segments[0] in {"orgs", "users"}:
        return None
    return host, segments[0], segments[1].removesuffix(".git")


def _result_matches_scope(data: dict[str, Any], scope: _SiteScope) -> bool:
    values = data.get("results", [])
    if not isinstance(values, list):
        return False
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        if _url_matches_scope(item["url"], scope):
            return True
    return False


def _visible_text_urls(text: str) -> set[str]:
    """Extract explicit HTTP(S) URLs, never expand truncated display strings."""
    urls = set()
    for raw in re.findall(r"https?://[^\s<>\"'`]+", text, flags=re.IGNORECASE):
        if _truncated_display_url(raw):
            continue
        canonical = _canonical_url(raw.rstrip(".,;:!?)]}>`*。，；：！？）】》"))
        if canonical is not None:
            urls.add(canonical)
    return urls


def _truncated_display_url(value: str) -> bool:
    return re.search(r"(?:\.{3}|…)[.,;:!?)\]}>*。，；：！？）】》]*$", value.strip()) is not None


def _visible_result_urls(value: Any, page_url: str) -> set[str]:
    """Resolve relative URLs only in URL fields, not arbitrary prose or titles."""
    if isinstance(value, str):
        return _visible_text_urls(value)
    if isinstance(value, list):
        return {url for item in value for url in _visible_result_urls(item, page_url)}
    if not isinstance(value, dict):
        return set()
    urls = {
        url
        for key, item in value.items()
        if key != "query"
        for url in _visible_result_urls(item, page_url)
    }
    for key, item in value.items():
        if _is_url_value(key, value) and isinstance(item, str) and not _truncated_display_url(item):
            canonical = _canonical_url(item, page_url)
            if canonical is not None:
                urls.add(canonical)
    return urls


def _is_url_value(key: str, data: dict[str, Any]) -> bool:
    return key in {
        "url",
        "href",
        "link",
        "source_url",
        "page_url",
        "pdf_url",
        "file_url",
        "download_url",
        "target_url",
    } or (key == "value" and data.get("attribute") in {"href", "src", "action"})


def _canonical_url(url: str, base_url: str = "") -> str | None:
    try:
        resolved = urljoin(base_url, url.strip())
        parsed = urlsplit(resolved)
        scheme = parsed.scheme.casefold()
        netloc = parsed.netloc.casefold()
    except (TypeError, ValueError):
        return None
    if scheme not in {"http", "https"} or not netloc:
        return None
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
