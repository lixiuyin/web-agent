"""Stable URL identities shared by runtime policy and independent evaluation."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


def document_key(url: str) -> str:
    """Identify one hosted document across mutable, pinned and raw URL forms."""
    parsed = urlsplit(url)
    path = re.sub(r"/(?:blob|raw)/(?:refs/heads/)?[^/]+/", "/", parsed.path)
    host = parsed.netloc.lower()
    if host == "raw.githubusercontent.com":
        host = "github.com"
        parts = path.split("/")
        path = "/".join(parts[:3] + parts[4:])
    if host in {"arxiv.org", "export.arxiv.org"}:
        host = "arxiv.org"
        path = re.sub(r"^/(?:pdf|html)/", "/abs/", path)
        path = re.sub(r"\.pdf$", "", path)
    return urlunsplit(("https", host, path, "", ""))
