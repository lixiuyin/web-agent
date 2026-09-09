"""Evidence for recovering a Chromium navigation interrupted by an HTTP redirect."""

from ipaddress import ip_address
from urllib.parse import urldefrag

from playwright.async_api import Page, Response

from webagent.browser.url_identity import search_engine_for_url, web_url


def same_navigation_site(requested_url: str, current_url: str) -> bool:
    """Accept exact hosts/subdomains or explicitly known regional provider hosts."""
    requested_parts, current_parts = web_url(requested_url), web_url(current_url)
    if requested_parts is None or current_parts is None:
        return False
    requested = (requested_parts.hostname or "").casefold().rstrip(".")
    current = (current_parts.hostname or "").casefold().rstrip(".")
    if requested == current:
        return True
    try:
        ip_address(requested)
    except ValueError:
        pass
    else:
        return False
    if current.endswith("." + requested):
        return True
    engine = search_engine_for_url(requested_url)
    return engine is not None and engine == search_engine_for_url(current_url)


class NavigationAttempt:
    """Retain only main-frame HTTP responses rooted in this requested URL."""

    def __init__(self, page: Page, url: str) -> None:
        self.page = page
        self.url = url
        self.previous_url = page.url
        self.responses: dict[str, int] = {}
        page.on("response", self._record_response)

    def _record_response(self, response: Response) -> None:
        request = response.request
        if not request.is_navigation_request() or request.frame != self.page.main_frame:
            return
        root = request
        while root.redirected_from is not None:
            root = root.redirected_from
        if urldefrag(root.url)[0] == urldefrag(self.url)[0]:
            self.responses[urldefrag(response.url)[0]] = response.status

    def can_recover(self, current_url: str) -> bool:
        status = self.responses.get(urldefrag(current_url)[0])
        return (
            current_url != self.previous_url
            and status is not None
            and 200 <= status < 300
            and same_navigation_site(self.url, current_url)
        )

    def close(self) -> None:
        self.page.remove_listener("response", self._record_response)
