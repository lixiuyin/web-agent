"""Search engine tools for web automation."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

# Search engine configurations
_SEARCH_ENGINES = {
    "google": {
        "url": "https://www.google.com",
        "query_url": "https://www.google.com/search",
        "query_param": "q",
        "input_selector": 'textarea[name="q"]',  # Google updated to textarea in 2024
        "wait_selector": 'div[id="search"]',
    },
    "bing": {
        "url": "https://www.bing.com",
        "query_url": "https://www.bing.com/search",
        "query_param": "q",
        "input_selector": 'input[name="q"]',
        "wait_selector": 'div[id="b_content"]',
    },
    "yahoo": {
        "url": "https://search.yahoo.com",
        "input_selector": 'input[name="p"]',
        "wait_selector": 'div[id="web"]',
    },
    "yahoo_japan": {
        "url": "https://search.yahoo.co.jp",
        "query_url": "https://search.yahoo.co.jp/search",
        "query_param": "p",
        "input_selector": 'input[name="p"]',
        "wait_selector": "a.sw-Card__titleInner",
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com",
        "input_selector": 'textarea[name="q"], input[name="q"]',
        "wait_selector": 'article[data-testid="result"], div[id="links"]',
    },
    "seznam": {
        "url": "https://search.seznam.cz/",
        "query_url": "https://search.seznam.cz/",
        "query_param": "q",
        "input_selector": 'input[name="q"]',
        "wait_selector": 'a[data-e-a="heading"]',
    },
}

# Lowercased text that marks a search-engine error / zero-results / bot-block page.
# Detecting these stops the agent from looping on a dead results page.
_SEARCH_ERROR_MARKERS = (
    "unexpected error",
    "no results found",
    "did not match any documents",
    "detected unusual traffic",
    "our systems have detected",
    "to continue, please type the characters",
    "before you continue",
    "unusual traffic from your computer network",
    "sorry, but your computer or network may be sending automated queries",
    "unfortunately, bots use duckduckgo too",
    "not a robot",
)
_SEARCH_CHALLENGE_URL_MARKERS = ("/sorry/", "recaptcha")
_GOOGLE_CUSTOM_SEARCH_API_URL = "https://customsearch.googleapis.com/customsearch/v1"

# Ordered engines tried automatically after the primary engine fails. A single
# engine is often bot-blocked, so cascading through all three maximizes the
# chance of getting real results before the agent has to work around it.
_FALLBACK_ORDER = ("bing", "yahoo_japan", "seznam", "yahoo", "duckduckgo", "google")

# Strict headless evaluation cannot hand a challenge to a person. Keep its
# engine pool to providers repeatedly verified in the same bundled-Chromium
# environment; broader interactive runs retain the full catalog.
_STRICT_HEADLESS_ENGINES = frozenset({"bing", "yahoo_japan", "seznam"})

_SITE_QUERY_DOMAIN_RE = re.compile(
    r"\bsite:\s*(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)+)",
    re.IGNORECASE,
)
_QUOTED_QUERY_RE = re.compile(r'["“](.+?)["”]')
_DOCUMENT_FILE_QUERY_RE = re.compile(r"(?:\bpdf\b|tech[-_ ]?report\.pdf)", re.IGNORECASE)
_DOCUMENT_RESULT_RE = re.compile(
    r"(?:\btechnical\s+report\b|\btech[-_ ]?report\b|\.pdf(?:\b|$))", re.IGNORECASE
)
_PHRASE_STOP_WORDS = frozenset(
    {"a", "an", "and", "for", "in", "is", "of", "official", "page", "the", "to"}
)
_TOPICAL_QUERY_STOP_WORDS = frozenset(
    {
        "about",
        "documentation",
        "docs",
        "encyclopedia",
        "find",
        "framework",
        "github",
        "repository",
        "repositories",
        "releases",
        "release",
        "model",
        "models",
        "guide",
        "home",
        "homepage",
        "installation",
        "introduction",
        "latest",
        "neutral",
        "newest",
        "official",
        "overview",
        "page",
        "recent",
        "report",
        "search",
        "site",
        "technical",
        "topic",
        "tutorial",
        "website",
    }
)


def _result_quality_issue(query: str, results: list[dict[str, str]]) -> str | None:
    issue = (
        _version_coverage_issue(query, results)
        or _document_file_coverage_issue(query, results)
        or _result_constraints_issue(query, results)
    )
    if issue:
        return issue
    if results and not any(
        _version_coverage_issue(query, [item]) is None
        and _result_constraints_issue(query, [item]) is None
        for item in results
    ):
        return "results only satisfy different query constraints in unrelated rows"
    return None


def _document_file_coverage_issue(query: str, results: list[dict[str, str]]) -> str | None:
    """Keep a named-version PDF request within one organic result row."""
    if not _DOCUMENT_FILE_QUERY_RE.search(query):
        return None
    names = _version_names(query)
    if not names:
        return None
    for result in results:
        text = " ".join(str(value) for value in result.values()).casefold()
        if any(name in text for name in names) and _DOCUMENT_RESULT_RE.search(text):
            return None
    return "results do not expose a requested-version report/PDF in one result; try another engine"


def _result_constraints_issue(query: str, results: list[dict[str, str]]) -> str | None:
    """Reject a populated SERP when it clearly ignores a host/title constraint.

    Some regional search pages return dictionary results for one common word
    while silently discarding ``site:`` and quoted-title constraints.  Treating
    that page as success prevents the fallback cascade from reaching a useful
    engine.  This guard is deliberately narrow: unconstrained topical searches
    are not second-guessed.
    """
    # Only an explicit ``site:`` operator is a domain constraint. Version names
    # (``qwen3.8``) and file names (``tech_report.pdf``) are ordinary query
    # terms; treating every dotted token as a host rejects useful result pages.
    hosts = {
        match.group("host").casefold().rstrip(".")
        for match in _SITE_QUERY_DOMAIN_RE.finditer(query)
    }
    if hosts:
        observed_hosts: set[str] = set()
        for result in results:
            value = str(result.get("url") or result.get("link") or "")
            try:
                if hostname := urlsplit(value).hostname:
                    observed_hosts.add(hostname.casefold().rstrip("."))
            except ValueError:
                continue
        if not any(
            observed == expected or observed.endswith(f".{expected}")
            for observed in observed_hosts
            for expected in hosts
        ):
            return "results ignored the requested domain constraint"

    for phrase in _QUOTED_QUERY_RE.findall(query):
        terms = {
            term.casefold()
            for term in re.findall(r"[A-Za-z0-9]+", phrase)
            if len(term) >= 3 and term.casefold() not in _PHRASE_STOP_WORDS
        }
        if len(terms) < 2:
            continue
        if not any(
            terms.issubset(
                set(
                    re.findall(
                        r"[a-z0-9]+",
                        " ".join(str(value) for value in result.values()).casefold(),
                    )
                )
            )
            for result in results
        ):
            return "results ignored the quoted title constraint"

    # Reject only gross topical mismatches. Search engines occasionally return a
    # fully populated but stale/corrupted SERP (for example, a FastAPI query whose
    # ten results are all about poetry or sports). Requiring one distinctive query
    # token anywhere in title/URL/snippet is conservative enough to preserve
    # synonym-heavy results while preventing such pages from counting as success.
    topical_terms = {
        term.casefold()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", query)
        if term.casefold() not in _TOPICAL_QUERY_STOP_WORDS
        and not term.casefold().startswith(("http", "www"))
    }
    if topical_terms and not any(
        any(
            term in " ".join(str(value) for value in result.values()).casefold()
            for term in topical_terms
        )
        for result in results
    ):
        return "results are unrelated to the distinctive query terms"
    return None


def _classify_search_failure(reason: str) -> str:
    lowered = reason.casefold()
    if re.search(r"\bhttp\s+429\b", lowered):
        return "rate_limited"
    if re.search(r"\bhttp\s+5\d\d\b", lowered):
        return "upstream_http_5xx"
    if any(
        marker in lowered
        for marker in ("bot challenge", "captcha", "challenge", "verification", "blocked")
    ):
        return "challenge_or_block"
    if "irrelevant results" in lowered or "ignored the" in lowered:
        return "quality_failure"
    if "selector" in lowered or "structured results" in lowered:
        return "selector_drift"
    if "no results" in lowered or "empty" in lowered:
        return "empty_results"
    if "navigate" in lowered:
        return "navigation_failure"
    if "submit" in lowered or "type query" in lowered:
        return "interaction_failure"
    return "unknown"


def _failure_data(query: str, engines: list[str], reason: str) -> dict[str, Any]:
    return {
        "query": query,
        "attempted_engines": engines,
        "failure_category": _classify_search_failure(reason),
        "search_attempts": [{"engine": engine, "outcome": "failed"} for engine in engines],
    }


def _unwrap_search_redirect(url: str) -> str:
    """Expose a search result destination instead of its engine click tracker."""
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.endswith("search.yahoo.com"):
        marker = "/RU="
        if marker in parsed.path:
            encoded = parsed.path.split(marker, 1)[1].split("/", 1)[0]
            destination = unquote(encoded)
            if urlsplit(destination).scheme in {"http", "https"}:
                return destination
    if parsed.hostname == "bing.com" or (parsed.hostname or "").endswith(".bing.com"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            payload = encoded[2:]
            try:
                padding = "=" * (-len(payload) % 4)
                destination = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                destination = ""
            if urlsplit(destination).scheme in {"http", "https"}:
                return destination
    return url


def _bing_compat_query(query: str) -> str:
    """Rewrite Bing's unreliable ``site:`` syntax while retaining its postcondition.

    Some Bing regions render no organic result cards for a valid ``site:host``
    query, while the equivalent ``terms host`` query works. The result-quality
    guard still checks the original query, so this compatibility form cannot
    turn off the requested domain or quoted-title constraint.
    """
    domains: list[str] = []

    def replace_site(match: re.Match[str]) -> str:
        domains.append(match.group("target"))
        return " "

    terms = re.sub(
        r"(?i)(?:^|\s)site:(?P<target>[a-z0-9.-]+(?:/[^\s]+)?)",
        replace_site,
        query,
    )
    if not domains:
        return query
    return " ".join([*terms.split(), *domains])


def _version_coverage_issue(query: str, results: list[dict[str, str]]) -> str | None:
    names = _version_names(query)
    if not names:
        return None
    corpus = " ".join(str(value).casefold() for result in results for value in result.values())
    if not any(name in corpus for name in names):
        return "results do not cover the requested named version; try another engine or an official release index"
    return None


def _version_names(query: str) -> list[str]:
    return re.findall(
        r"\b[a-z][a-z_-]*\d+(?:\.\d+)+(?:-[a-z][a-z0-9-]*)?|\b[a-z][a-z_-]*\d+-[a-z][a-z0-9-]*",
        query.casefold(),
    )


def _fallback_chain(primary: str, *, google_available: bool) -> list[str]:
    """Remaining fallback engines to try, in order, after ``primary`` failed."""
    return [
        engine
        for engine in _FALLBACK_ORDER
        if engine != primary and (engine != "google" or google_available)
    ]
