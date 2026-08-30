"""Tests for per-engine search result extraction."""

from __future__ import annotations

from typing import Any

from webagent.browser.search_parsers import (
    detect_search_engine,
    extract_keyword_words,
    parse_bing_results,
    parse_duckduckgo_results,
    parse_google_results,
)


class FakeElement:
    """Minimal ElementHandle stand-in for parser tests."""

    def __init__(
        self,
        href: str | None = None,
        text: str | None = None,
        tag_name: str = "A",
        children: dict[str, FakeElement | None] | None = None,
        snippet_text: str | None = None,
        raise_on: str | None = None,
        title_attr: str | None = None,
    ) -> None:
        self.href = href
        self.text = text
        self.tag_name = tag_name
        self.children = children or {}
        self.snippet_text = snippet_text
        self.raise_on = raise_on
        self.title_attr = title_attr

    async def get_attribute(self, name: str) -> str | None:
        if self.raise_on == "get_attribute":
            raise RuntimeError("detached")
        if name == "href":
            return self.href
        if name == "title":
            return self.title_attr
        return None

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        child = self.children.get(selector)
        return [child] if child is not None else []

    async def text_content(self) -> str | None:
        if self.raise_on == "text_content":
            raise RuntimeError("detached")
        return self.text

    async def evaluate(self, _expr: str) -> str:
        return self.tag_name

    async def query_selector(self, selector: str) -> FakeElement | None:
        if self.raise_on == "query_selector":
            raise RuntimeError("detached")
        return self.children.get(selector)


class FakePage:
    """Minimal Page stand-in: dispatches query_selector_all by selector."""

    def __init__(self, elements_by_selector: dict[str, list[FakeElement]]) -> None:
        self.elements_by_selector = elements_by_selector
        self.url = "https://example.test"

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        return self.elements_by_selector.get(selector, [])

    async def query_selector(self, selector: str) -> FakeElement | None:
        elements = self.elements_by_selector.get(selector, [])
        return elements[0] if elements else None


def _google_page(results: list[dict[str, Any]]) -> FakePage:
    containers = []
    for r in results:
        link = FakeElement(href=r.get("href"), text=r.get("title"))
        container = FakeElement(
            children={"a": link},
            snippet_text=r.get("snippet"),
        )
        container.children["[style*='-webkit-line-clamp'], .VwiC3b, .IsZvec"] = (
            FakeElement(text=r.get("snippet")) if r.get("snippet") else None
        )
        containers.append(container)
    return FakePage({"div.g": containers})


class TestDetectSearchEngine:
    def test_google(self) -> None:
        assert detect_search_engine("https://www.google.com/search?q=x") == "google"

    def test_google_regional(self) -> None:
        assert detect_search_engine("https://www.google.co.jp/search?q=x") == "google"

    def test_bing(self) -> None:
        assert detect_search_engine("https://www.bing.com/search?q=x") == "bing"

    def test_duckduckgo(self) -> None:
        assert detect_search_engine("https://duckduckgo.com/?q=x") == "duckduckgo"

    def test_unknown(self) -> None:
        assert detect_search_engine("https://arxiv.org") is None


class TestGoogleParser:
    async def test_extracts_organic_results(self) -> None:
        page = _google_page(
            [
                {
                    "href": "https://arxiv.org/1",
                    "title": "Paper One",
                    "snippet": "About transformers",
                },
                {"href": "https://arxiv.org/2", "title": "Paper Two", "snippet": ""},
            ]
        )
        results = await parse_google_results(page, max_results=10)
        assert len(results) == 2
        assert results[0] == {
            "title": "Paper One",
            "link": "https://arxiv.org/1",
            "snippet": "About transformers",
        }

    async def test_skips_internal_and_relative_links(self) -> None:
        page = _google_page(
            [
                {"href": "https://accounts.google.com/signin", "title": "Sign in"},
                {"href": "/search?q=next", "title": "Next page"},
                {"href": "https://example.org/ok", "title": "Real result"},
            ]
        )
        results = await parse_google_results(page, max_results=10)
        assert [r["link"] for r in results] == ["https://example.org/ok"]

    async def test_skips_empty_titles(self) -> None:
        page = _google_page([{"href": "https://example.org/x", "title": "   "}])
        assert await parse_google_results(page, max_results=10) == []

    async def test_respects_max_results(self) -> None:
        page = _google_page(
            [{"href": f"https://example.org/{i}", "title": f"R{i}"} for i in range(5)]
        )
        results = await parse_google_results(page, max_results=2)
        assert len(results) == 2

    async def test_falls_back_to_hveid_containers(self) -> None:
        link = FakeElement(href="https://example.org/h", text="Fallback Hit")
        container = FakeElement(children={"a": link})
        page = FakePage({"div[data-hveid]": [container]})
        results = await parse_google_results(page, max_results=10)
        assert [r["title"] for r in results] == ["Fallback Hit"]

    async def test_container_without_link_is_skipped(self) -> None:
        container = FakeElement(children={"a": None})
        page = FakePage({"div.g": [container]})
        assert await parse_google_results(page, max_results=10) == []

    async def test_detached_element_does_not_abort(self) -> None:
        link = FakeElement(href="https://example.org/x", text="Ok", raise_on="text_content")
        container = FakeElement(children={"a": link})
        good = FakeElement(children={"a": FakeElement(href="https://example.org/y", text="Good")})
        page = FakePage({"div.g": [container, good]})
        results = await parse_google_results(page, max_results=10)
        assert [r["title"] for r in results] == ["Good"]


class TestBingParser:
    async def test_extracts_results(self) -> None:
        link = FakeElement(href="https://example.org/b1", text="Bing Result")
        snippet = FakeElement(text="A snippet")
        container = FakeElement(children={"h2 a": link, "p": snippet})
        page = FakePage({"li.b_algo": [container]})
        results = await parse_bing_results(page, max_results=10)
        assert results == [
            {"title": "Bing Result", "link": "https://example.org/b1", "snippet": "A snippet"}
        ]

    async def test_missing_link_is_skipped(self) -> None:
        container = FakeElement(children={"h2 a": None})
        page = FakePage({"li.b_algo": [container]})
        assert await parse_bing_results(page, max_results=10) == []

    async def test_falls_back_to_visible_links_inside_results_region(self) -> None:
        link = FakeElement(href="https://example.org/alternate", text="Alternate Bing Result")
        page = FakePage({"#b_results h2 a, #b_results a[href], main h2 a": [link, link]})

        results = await parse_bing_results(page, max_results=10)

        assert results == [
            {
                "title": "Alternate Bing Result",
                "link": "https://example.org/alternate",
                "snippet": "",
            }
        ]


def _ddg_link_element(href: str, title: str) -> FakeElement:
    # Container is itself an <a> tag (modern DDG article children are links).
    return FakeElement(href=href, text=title, tag_name="A")


class TestDuckDuckGoParser:
    async def test_extracts_direct_link_containers(self) -> None:
        page = FakePage(
            {
                "article.result": [
                    _ddg_link_element("https://example.org/d1", "DDG Result"),
                    _ddg_link_element("/internal/path", "Internal"),
                    _ddg_link_element("https://duckduckgo.com/x", "Engine link"),
                ]
            }
        )
        results = await parse_duckduckgo_results(page, max_results=10)
        assert [r["title"] for r in results] == ["DDG Result"]

    async def test_container_wrapping_link(self) -> None:
        link = FakeElement(href="https://example.org/d2", text="Wrapped")
        container = FakeElement(tag_name="ARTICLE", children={"a[href]": link})
        page = FakePage({"article.result": [container]})
        results = await parse_duckduckgo_results(page, max_results=10)
        assert [r["link"] for r in results] == ["https://example.org/d2"]

    async def test_legacy_web_result_fallback(self) -> None:
        link = FakeElement(href="https://example.org/d3", text="Legacy")
        container = FakeElement(tag_name="DIV", children={"a[href]": link})
        page = FakePage({"div.web-result": [container]})
        results = await parse_duckduckgo_results(page, max_results=10)
        assert [r["title"] for r in results] == ["Legacy"]

    async def test_main_content_links_fallback(self) -> None:
        link = FakeElement(href="https://example.org/d4", text="Deep")
        main = FakeElement(tag_name="MAIN", children={"a": link})
        page = FakePage({"main#content__main, #links, .results": [main]})
        results = await parse_duckduckgo_results(page, max_results=10)
        assert [r["title"] for r in results] == ["Deep"]

    async def test_title_attribute_fallback(self) -> None:
        el = FakeElement(
            href="https://example.org/d5", text=None, tag_name="A", title_attr="Attr Title"
        )
        page = FakePage({"article.result": [el]})
        results = await parse_duckduckgo_results(page, max_results=10)
        assert results[0]["title"] == "Attr Title"


class TestExtractKeywordWords:
    def test_filters_stop_words_and_short_words(self) -> None:
        words = extract_keyword_words("The Model for All Users")
        assert words == ["Model", "Users"]

    def test_returns_all_long_words(self) -> None:
        assert extract_keyword_words("transformer architecture") == [
            "transformer",
            "architecture",
        ]
