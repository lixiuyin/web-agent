"""Subject identity, candidate scope and release-version evidence analysis."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from webagent.tools.policies.candidates import versions
from webagent.tools.policies.contracts import PLANNER_VISIBLE_URL_PROVENANCE_SOURCES
from webagent.tools.policies.evidence import (
    _RELEASE_LANDSCAPE_TERMS,
    _REPOSITORY_HOSTS,
    _SEARCH_INDEX_HOST_TERMS,
    _VERSION_SUFFIX_RE,
    _canonical_url,
    _looks_like_pdf_resource,
    _registrable_label,
    _repository_identity,
    _repository_owner,
    _result_matches_scope,
    _SiteScope,
    _url_matches_scope,
    _version_key,
)

_ARXIV_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
_ARXIV_PDF_PATH_RE = re.compile(r"^/pdf/(?P<identifier>\d{4}\.\d{4,6}(?:v\d+)?)$")
_ARXIV_DOCUMENT_PATH_RE = re.compile(r"^/(?:pdf|abs)/(?P<identifier>\d{4}\.\d{4,6}(?:v\d+)?)$")

if TYPE_CHECKING:
    from webagent.tools.policies.search import SearchEngineOnlyPolicy


class DiscoveryEvidence:
    """Analyze subject identity, scope and version evidence for authorization."""

    def __init__(self, policy: SearchEngineOnlyPolicy) -> None:
        self._policy = policy

    def _scope_was_endorsed(self, scope: _SiteScope) -> bool:
        if scope.host in _REPOSITORY_HOSTS:
            if not scope.path_prefix:
                return False
            target_owner = _repository_owner(scope.path_prefix)
            if target_owner is None:
                return False
            return any(
                urlsplit(url).hostname == scope.host
                and _repository_owner(urlsplit(url).path.casefold()) == target_owner
                for url in self._policy._official_identity_urls
            )
        return any(_url_matches_scope(url, scope) for url in self._policy._official_identity_urls)

    def _identity_bound_site_result(
        self, scope: _SiteScope, query: str, data: dict[str, Any]
    ) -> bool:
        """Accept path scopes or an owner-token fallback for repository search engines.

        Some engines treat ``site:github.com/Owner`` as an invalid or empty scope. The
        fallback keeps the independent search but requires all three bindings: the owner
        appeared in the prior official-identity results, the exact owner token is present
        in the new query, and the new result URL is under that same repository owner.
        """
        if scope.host not in _REPOSITORY_HOSTS or scope.path_prefix:
            return _result_matches_scope(data, scope) and self._scope_was_endorsed(scope)

        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        endorsed_owners = {
            owner
            for url in self._policy._official_identity_urls
            if urlsplit(url).hostname == scope.host
            if (owner := _repository_owner(urlsplit(url).path)) is not None
        }
        query_owners = endorsed_owners & query_tokens
        if not query_owners:
            return False
        return any(
            urlsplit(url).hostname == scope.host
            and _repository_owner(urlsplit(url).path) in query_owners
            for url in self._policy._result_urls(data)
        )

    def _identity_bound_scope_result(
        self, site_scope: _SiteScope | None, query: str, data: dict[str, Any]
    ) -> bool:
        """Validate a site, owner-qualified, or identity-then-version corroboration."""
        if site_scope is not None:
            if any(term in site_scope.host for term in _SEARCH_INDEX_HOST_TERMS):
                return False
            return self._identity_bound_site_result(site_scope, query, data)

        result_urls = self._policy._result_urls(data)
        if self._version_result_is_under_endorsed_identity(query, result_urls):
            return True
        return self._qualified_identity_result(query, result_urls)

    def _qualified_identity_result(self, query: str, result_urls: set[str]) -> bool:
        """Require an endorsed host/owner in both a plain query and its results."""
        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        for identity_url in self._policy._official_identity_urls:
            parsed = urlsplit(identity_url)
            host = parsed.hostname.casefold() if parsed.hostname else ""
            if not host or any(term in host for term in _SEARCH_INDEX_HOST_TERMS):
                continue
            if host in _REPOSITORY_HOSTS:
                owner = _repository_owner(parsed.path)
                host_token = host.split(".", 1)[0]
                if owner not in query_tokens or not ({host, host_token} & query_tokens):
                    continue
                if any(
                    urlsplit(url).hostname == host
                    and _repository_owner(urlsplit(url).path) == owner
                    for url in result_urls
                ):
                    return True
                continue

            visible_host = host.removeprefix("www.")
            if visible_host not in query and host not in query:
                continue
            scope = _SiteScope(host=host, path_prefix="")
            if any(_url_matches_scope(url, scope) for url in result_urls):
                return True
        return False

    def _version_result_is_under_endorsed_identity(self, query: str, result_urls: set[str]) -> bool:
        """Bind a focused version search to an identity endorsed by an earlier search."""
        query_names = versions(query, self._policy._task_keywords)
        if not query_names:
            return False
        for result_url in result_urls:
            result_names = versions(result_url, self._policy._task_keywords)
            if not any(
                result_name == query_name or result_name.startswith(query_name + "-")
                for query_name in query_names
                for result_name in result_names
            ):
                continue
            if self._result_is_under_endorsed_identity(result_url):
                return True
        return False

    def _result_is_under_endorsed_identity(self, result_url: str) -> bool:
        result_identity = _repository_identity(result_url)
        result_host = (urlsplit(result_url).hostname or "").casefold().removeprefix("www.")
        for identity_url in self._policy._official_identity_urls:
            identity = _repository_identity(identity_url)
            if result_identity is not None and identity is not None:
                if result_identity[:2] == identity[:2]:
                    return True
                continue
            identity_host = (urlsplit(identity_url).hostname or "").casefold().removeprefix("www.")
            if result_identity is None and result_host == identity_host:
                return True
        return False

    def _query_matches_task(self, query: str) -> bool:
        if not self._policy._task_keywords:
            return True
        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        return any(
            task_token == query_token
            or (len(task_token) >= 4 and query_token.startswith(task_token))
            for task_token in self._policy._task_keywords
            for query_token in query_tokens
        )

    def _query_has_subject_version(self, query: str) -> bool:
        return any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(keyword)}[-_ ]*{_VERSION_SUFFIX_RE.pattern}",
                query,
                flags=re.IGNORECASE,
            )
            is not None
            for keyword in self._policy._task_keywords
        )

    def _official_scope_query_is_broad(self, query: str) -> bool:
        return not self._policy._latest_task or self._query_matches_task(query)

    def _explicit_official_identity_urls(self, data: dict[str, Any]) -> set[str]:
        """Return result URLs whose visible text explicitly claims official status."""
        identity_urls: set[str] = set()
        results = data.get("results", [])
        if not isinstance(results, list):
            return identity_urls
        for item in results:
            if not isinstance(item, dict):
                continue
            searchable = " ".join(
                str(item.get(field, "")).casefold() for field in ("title", "snippet")
            )
            if re.search(r"\bofficial\b", searchable) is None:
                continue
            value = item.get("url") or item.get("link")
            canonical = _canonical_url(value) if isinstance(value, str) else None
            if canonical is not None:
                identity_urls.add(canonical)
        return identity_urls

    def _subject_branded_result_urls(self, data: dict[str, Any]) -> set[str]:
        """Return results hosted on a domain whose brand label is a task subject token.

        ``qwen.ai`` for a Qwen task is first-party evidence in the same way a
        result text that says "official" is; a look-alike such as
        ``qwen-mirror.example`` does not match because the label must be exact.
        """
        keywords = {
            keyword
            for keyword in self._policy._task_keywords
            if _VERSION_SUFFIX_RE.search(keyword) is None
        }
        branded: set[str] = set()
        if not keywords:
            return branded
        for url in self._policy._result_urls(data):
            host = urlsplit(url).hostname or ""
            if any(term in host for term in _SEARCH_INDEX_HOST_TERMS):
                continue
            label = _registrable_label(host)
            if label is not None and label in keywords:
                branded.add(url)
        return branded

    def _query_named_host_result_urls(self, query: str, data: dict[str, Any]) -> set[str]:
        """Return results whose host the identity query itself named (e.g. ``qwen.ai``)."""
        tokens = set(re.findall(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", query.casefold()))
        if not tokens:
            return set()
        named: set[str] = set()
        for url in self._policy._result_urls(data):
            host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            if host in tokens or any(host.endswith(f".{token}") for token in tokens):
                named.add(url)
        return named

    def _identity_result_urls(self, query: str, data: dict[str, Any]) -> set[str]:
        """Select which results of an identity-bearing search endorse the subject.

        Explicit "official" claims and subject-branded hosts always count. When the
        planner explicitly asked for the official presence, hosts named in the query
        count too. Only when none of those signals exist does the whole result set
        stand in, matching the pre-existing fallback.
        """
        explicit = self._explicit_official_identity_urls(data)
        branded = self._subject_branded_result_urls(data)
        if re.search(r"\bofficial\b", query) is None:
            return explicit | branded
        selected = explicit | branded | self._query_named_host_result_urls(query, data)
        return selected or self._policy._result_urls(data)

    def _release_landscape_result_evidence(self, data: dict[str, Any]) -> set[str]:
        """Require release/version semantics in results, not only in the query text."""
        evidence: set[str] = set()
        results = data.get("results", [])
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            searchable = " ".join(
                str(item.get(field, "")).casefold() for field in ("title", "url", "snippet")
            )
            if not self._query_matches_task(searchable):
                continue
            if not (
                any(term in searchable for term in _RELEASE_LANDSCAPE_TERMS)
                or self._result_version_leads({"results": [item]})
            ):
                continue
            canonical = _canonical_url(item["url"])
            if canonical is not None:
                evidence.add(canonical)
        return evidence

    def _scope_covers_target_url(self, target_url: str) -> bool:
        target = _canonical_url(target_url)
        if target is None:
            return False
        if self._is_arxiv_document(target):
            return self._arxiv_link_is_endorsed(target)
        # The evidence can arrive in either order. If an earlier successful
        # SERP exposed the exact candidate, and a later independent current-year
        # scope search established the endorsed owner/host, requiring the later
        # SERP to rediscover the identical repository adds no provenance value
        # and can create an endless loop on stale search indexes.
        target_evidence = self._policy._observed_urls.get(target, {})
        if target_evidence.get("source") in {
            "search_planner_visible",
            "get_search_results_planner_visible",
        } and self._identity_endorses_target(target):
            return True
        target_repository = _repository_identity(target)
        if target_repository is not None:
            return any(
                _repository_identity(result_url) == target_repository
                for result_url in self._policy._official_scope_result_urls
            )
        target_host = urlsplit(target).hostname
        return any(
            urlsplit(result_url).hostname == target_host
            for result_url in self._policy._official_scope_result_urls
        )

    def _identity_endorses_target(self, target_url: str) -> bool:
        target = _canonical_url(target_url)
        if target is None:
            return False
        if self._is_arxiv_document(target):
            return self._arxiv_link_is_endorsed(target)
        return self._identity_endorses_host(target)

    def _identity_endorses_host(self, target: str) -> bool:
        """Return whether the target's own host/owner is the endorsed official identity."""
        target_repository = _repository_identity(target)
        if target_repository is not None:
            target_host, target_owner, _ = target_repository
            return any(
                (identity := _repository_identity(identity_url)) is not None
                and identity[:2] == (target_host, target_owner)
                for identity_url in self._policy._official_identity_urls
            )
        target_hostname = urlsplit(target).hostname
        return any(
            urlsplit(identity_url).hostname == target_hostname
            for identity_url in self._policy._official_identity_urls
        )

    @staticmethod
    def _is_arxiv_pdf(url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.hostname in _ARXIV_HOSTS
            and _ARXIV_PDF_PATH_RE.fullmatch(parsed.path.casefold()) is not None
        )

    @staticmethod
    def _is_arxiv_document(url: str) -> bool:
        """Return whether a URL is an arXiv paper page or its PDF rendition."""
        parsed = urlsplit(url)
        return (
            parsed.hostname in _ARXIV_HOSTS
            and _ARXIV_DOCUMENT_PATH_RE.fullmatch(parsed.path.casefold()) is not None
        )

    @staticmethod
    def _arxiv_abs_url(url: str) -> str | None:
        parsed = urlsplit(url)
        match = _ARXIV_DOCUMENT_PATH_RE.fullmatch(parsed.path.casefold())
        if parsed.hostname not in _ARXIV_HOSTS or match is None:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/abs/{match.group('identifier')}"

    @staticmethod
    def _repository_owner_pairs(urls: set[str]) -> set[tuple[str, str]]:
        return {identity[:2] for url in urls if (identity := _repository_identity(url)) is not None}

    @staticmethod
    def _official_site_hosts(urls: set[str]) -> set[str]:
        """Return non-repository, non-paper-index hosts from evidence URLs."""
        hosts: set[str] = set()
        for url in urls:
            host = (urlsplit(url).hostname or "").casefold()
            if not host or host in _REPOSITORY_HOSTS:
                continue
            if any(term in host for term in _SEARCH_INDEX_HOST_TERMS):
                continue
            hosts.add(host.removeprefix("www."))
        return hosts

    def _endorsed_official_pages(self) -> list[str]:
        """Return observed official pages backed by both identity and scope evidence.

        Repository pages must share host and owner across both evidence sets; plain
        official websites must share the host. Paper indexes never qualify.
        """
        identity_urls = self._policy._official_identity_urls
        scope_urls = self._policy._official_scope_result_urls
        common_pairs = self._repository_owner_pairs(identity_urls) & self._repository_owner_pairs(
            scope_urls
        )
        common_hosts = self._official_site_hosts(identity_urls) & self._official_site_hosts(
            scope_urls
        )
        pages: set[str] = set()
        for url in identity_urls | scope_urls:
            identity = _repository_identity(url)
            if identity is not None:
                if identity[:2] in common_pairs:
                    pages.add(url)
                continue
            host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            if host in common_hosts:
                pages.add(url)
        return sorted(pages)

    def _page_is_endorsed_official(self, page_url: Any) -> bool:
        if not isinstance(page_url, str):
            return False
        page = _canonical_url(page_url)
        if page is None:
            return False
        identity_urls = self._policy._official_identity_urls
        scope_urls = self._policy._official_scope_result_urls
        page_identity = _repository_identity(page)
        if page_identity is not None:
            common_pairs = self._repository_owner_pairs(
                identity_urls
            ) & self._repository_owner_pairs(scope_urls)
            return page_identity[:2] in common_pairs
        host = (urlsplit(page).hostname or "").casefold().removeprefix("www.")
        if not host or any(term in host for term in _SEARCH_INDEX_HOST_TERMS):
            return False
        return host in (
            self._official_site_hosts(identity_urls) & self._official_site_hosts(scope_urls)
        )

    def _arxiv_link_is_endorsed(self, target_url: str) -> bool:
        """Bind an arXiv paper page or PDF to a visible official page link.

        arXiv is a paper host rather than the official subject identity for this
        policy. It is therefore accepted only when the matching ``/abs/<id>`` URL
        was planner-visible on a page belonging to an official repository owner or
        official website host that is present in both the official-identity and
        identity-bound-scope evidence.
        """
        target = _canonical_url(target_url)
        if target is None:
            return False
        abs_url = self._arxiv_abs_url(target)
        if abs_url is None:
            return False
        evidence = self._policy._observed_urls.get(abs_url)
        if evidence is None or evidence.get("source") not in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES:
            return False
        return self._page_is_endorsed_official(evidence.get("page_url"))

    def _is_arxiv_target(self, target_url: str) -> bool:
        target = _canonical_url(target_url)
        return target is not None and urlsplit(target).hostname in _ARXIV_HOSTS

    def _arxiv_link_guidance(self, target_url: str) -> str | None:
        """Explain how an unendorsed arXiv candidate can still become admissible.

        Identity and scope searches can never endorse a paper index, so repeating
        them is wasted budget. The admissible route is a visible link from an
        already-endorsed official page.
        """
        target = _canonical_url(target_url)
        if target is None or not self._is_arxiv_target(target):
            return None
        if self._arxiv_link_is_endorsed(target):
            return None
        abs_url = self._arxiv_abs_url(target) or target
        pages = self._rank_pages_for_document(self._endorsed_official_pages(), target)
        page_hint = (
            "one of the endorsed official pages already observed (" + ", ".join(pages[:3]) + ")"
            if pages
            else "an official repository or website page that identity and scope searches "
            "have already endorsed"
        )
        return (
            "arXiv is a paper index and cannot be endorsed by identity or scope searches, so "
            "do not search for arXiv again. Instead goto "
            f"{page_hint}, call get_all_links so its link to {abs_url} becomes visible page "
            "evidence, then retry download_pdf; or download the PDF that the official page "
            "itself hosts"
        )

    def _rank_pages_for_document(self, pages: list[str], target_url: str) -> list[str]:
        """Put the official pages that share a name token with the document first.

        The document's visible title (``Qwen3 Technical Report``) usually names the
        release whose repository or page links to it, so an overlapping path token
        is the best available predictor of where the link will be found.
        """
        label_tokens: set[str] = set()
        for label in self._document_labels(target_url):
            label_tokens.update(re.findall(r"[a-z0-9][a-z0-9._-]*", label.casefold()))
        label_tokens = {token.rstrip(".") for token in label_tokens}

        def overlap(page: str) -> int:
            path_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", urlsplit(page).path.casefold()))
            return len(path_tokens & label_tokens)

        return sorted(pages, key=lambda page: (-overlap(page), page))

    def _document_needs_subject_binding(self, target_url: str) -> bool:
        """Return whether a candidate is a document endorsed only through link context.

        An official page links to many papers besides the subject's own report, so
        a document hosted elsewhere (arXiv, a third-party PDF host) inherits the
        page's endorsement but not its topic. Documents hosted by the endorsed
        official host itself are already bound by that host.
        """
        target = _canonical_url(target_url)
        if target is None:
            return False
        if not (self._is_arxiv_document(target) or _looks_like_pdf_resource(target)):
            return False
        return not self._identity_endorses_host(target)

    def _document_labels(self, target_url: str) -> list[str]:
        """Return the visible text observed for a document and its arXiv renditions."""
        target = _canonical_url(target_url)
        if target is None:
            return []
        abs_url = self._arxiv_abs_url(target)
        labels: list[str] = []
        for url, seen in self._policy._observed_labels.items():
            if url == target or (abs_url is not None and self._arxiv_abs_url(url) == abs_url):
                labels.extend(label for label in seen if label not in labels)
        return labels

    def _document_names_subject(self, target_url: str) -> bool:
        """Accept a document whose visible title, link text or file name names the subject."""
        target = _canonical_url(target_url)
        if target is None:
            return False
        path_text = urlsplit(target).path.replace("/", " ")
        return any(
            self._query_matches_task(text) for text in (*self._document_labels(target), path_text)
        )

    def _document_subject_guidance(self, target_url: str) -> str | None:
        """Explain why an endorsed but off-subject document cannot be the deliverable."""
        if not self._document_needs_subject_binding(target_url):
            return None
        if self._document_names_subject(target_url):
            return None
        labels = self._document_labels(target_url)
        seen = (
            "; ".join(repr(label[:80]) for label in labels[:2])
            if labels
            else "no title or link text has been observed for it"
        )
        subject = ", ".join(sorted(self._policy._task_keywords)) or "the task subject"
        return (
            f"the candidate document {_canonical_url(target_url)} is not visibly about the task "
            f"subject ({subject}): its observed title/link text ({seen}) never names the subject, "
            "and an official page also links to unrelated papers. Choose a document whose visible "
            "title or link text names the subject, or goto its page so the title becomes visible "
            "evidence, before downloading"
        )

    def _update_version_frontier(self, query: str, data: dict[str, Any]) -> None:
        """Require explicit follow-up for the highest subject-version lead on a SERP.

        Search results often expose a newer release first through a third-party page. Such
        a page is not first-party proof, but dismissing it without an exact-version search
        is also unsound. Only tokens joined to a distinctive task keyword are considered,
        which avoids treating dates and arXiv identifiers as product versions.
        """
        leads = self._result_version_leads(data)
        if leads:
            lead = max(leads, key=self._version_lead_rank)
            lead_key = _version_key(lead)
            current = self._policy._version_frontier or ""
            if self._version_lead_rank(lead) > self._version_lead_rank(current):
                self._policy._version_frontier = lead
                self._policy._version_frontier_key = lead_key
                self._policy._version_frontier_resolved = False

    @staticmethod
    def _version_lead_rank(value: str) -> tuple[tuple[int, ...], int, int, str]:
        """Prefer a named release variant over its bare numeric family.

        This does not prove that the variant is newer. It only makes the more
        precise visible lead the mandatory corroboration target. Additional
        suffix segments break same-version ties deterministically.
        """
        normalized = value.casefold().replace("_", "-").replace(" ", "-")
        match = _VERSION_SUFFIX_RE.search(normalized)
        suffix = normalized[match.end() :] if match is not None else ""
        segments = len([part for part in suffix.split("-") if part])
        return _version_key(normalized), segments, len(suffix), normalized

    def _result_version_leads(self, data: dict[str, Any]) -> set[str]:
        results = data.get("results", [])
        if not isinstance(results, list) or not self._policy._task_keywords:
            return set()
        leads: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            searchable = " ".join(str(item.get(field, "")) for field in ("title", "url", "snippet"))
            leads.update(versions(searchable, self._policy._task_keywords))
        return leads
