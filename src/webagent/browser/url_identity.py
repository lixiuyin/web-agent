"""Explicit search-provider identities; URL parameters never establish ownership."""

from urllib.parse import SplitResult, urlsplit

# Supported provider domains, not a heuristic for arbitrary registrable domains.
_ENGINE_DOMAINS = {
    "google": (
        "google.com",
        "google.co.uk",
        "google.co.jp",
        "google.com.hk",
        "google.com.tw",
        "google.com.au",
        "google.co.in",
        "google.de",
        "google.fr",
        "google.ca",
        "google.cn",
    ),
    "bing": ("bing.com",),
    "duckduckgo": ("duckduckgo.com",),
    "yahoo_japan": ("search.yahoo.co.jp",),
    "seznam": ("search.seznam.cz",),
}


def web_url(value: str) -> SplitResult | None:
    """Parse an ordinary HTTP(S) URL, rejecting malformed authorities."""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if any(character.isspace() for character in value):
            return None
        return parsed
    except ValueError:
        return None


def search_engine_for_url(value: str) -> str | None:
    parsed = web_url(value)
    if parsed is None:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    return next(
        (
            engine
            for engine, domains in _ENGINE_DOMAINS.items()
            if any(host == domain or host.endswith("." + domain) for domain in domains)
        ),
        None,
    )
