"""Tests for the link-clicking strategy chain."""

from __future__ import annotations

from typing import Any

from webagent.browser.link_clicking import (
    click_by_exact_text,
    click_by_fuzzy_text,
    click_by_identifier,
    click_by_keyword_match,
    click_link_by_text_strategies,
    click_pdf_link,
)


class FakeElement:
    def __init__(self, text: str = "", href: str | None = None, fail: bool = False) -> None:
        self.text = text
        self.href = href
        self.fail = fail
        self.clicked = False

    async def inner_text(self) -> str:
        if self.fail:
            raise RuntimeError("detached")
        return self.text

    async def get_attribute(self, name: str) -> str | None:
        if self.fail:
            raise RuntimeError("detached")
        return self.href if name == "href" else None

    async def click(self, timeout: int = 0) -> None:
        if self.fail:
            raise RuntimeError("cannot click")
        self.clicked = True


class FakePage:
    """Page double recording clicks; simulates selector and locator APIs."""

    def __init__(self, links: list[FakeElement] | None = None) -> None:
        self.links = links or []
        self.selector_clicks: list[str] = []
        self.fuzzy_clicks: list[str] = []

    async def click(self, selector: str, timeout: int = 0, force: bool = False) -> None:
        self.selector_clicks.append(selector)

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        return self.links if selector == "a" else []

    def get_by_text(self, text: str, exact: bool = False) -> Any:
        page = self

        class _Locator:
            @property
            def first(self) -> _Locator:
                return self

            async def click(self, timeout: int = 0) -> None:
                page.fuzzy_clicks.append(text)

        return _Locator()


class TestExactText:
    async def test_success(self) -> None:
        page = FakePage()
        result = await click_by_exact_text(page, "Sign in")
        assert result == {"success": True, "selector": 'text="Sign in"', "method": "exact"}
        assert page.selector_clicks == ['text="Sign in"']

    async def test_click_failure_returns_none(self) -> None:
        page = FakePage()
        page.click = _raise  # type: ignore[method-assign]
        assert await click_by_exact_text(page, "missing") is None


class TestFuzzyText:
    async def test_success(self) -> None:
        page = FakePage()
        result = await click_by_fuzzy_text(page, "Sign")
        assert result is not None and result["method"] == "fuzzy"
        assert page.fuzzy_clicks == ["Sign"]

    async def test_click_failure_returns_none(self) -> None:
        page = FakePage()
        result = await click_by_fuzzy_text(page, "Sign")
        assert result is not None
        # Simulate locator failure by monkeypatching after construction
        page.get_by_text = lambda text, exact=False: _FailingLocator()  # type: ignore[method-assign]
        assert await click_by_fuzzy_text(page, "Sign") is None


class _FailingLocator:
    @property
    def first(self) -> _FailingLocator:
        return self

    async def click(self, timeout: int = 0) -> None:
        raise RuntimeError("not found")


async def _raise(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("not found")


class TestKeywordMatch:
    async def test_clicks_link_matching_two_words(self) -> None:
        link = FakeElement(text="Qwen Technical Report Download")
        page = FakePage(links=[link])
        result = await click_by_keyword_match(page, "download qwen report today")
        assert result is not None and result["method"] == "keyword_match"
        assert link.clicked

    async def test_single_word_match_is_not_enough(self) -> None:
        link = FakeElement(text="Only Qwen matches here")
        page = FakePage(links=[link])
        assert await click_by_keyword_match(page, "download qwen report today") is None
        assert not link.clicked

    async def test_no_links_returns_none(self) -> None:
        assert await click_by_keyword_match(FakePage(), "anything") is None


class TestIdentifier:
    async def test_clicks_link_with_arxiv_id_in_href(self) -> None:
        link = FakeElement(href="https://arxiv.org/pdf/2505.09388")
        page = FakePage(links=[link])
        result = await click_by_identifier(page, "Read paper 2505.09388 now")
        assert result is not None and result["method"] == "url_match"
        assert link.clicked

    async def test_no_identifier_returns_none(self) -> None:
        assert await click_by_identifier(FakePage(), "no numbers here") is None


class TestPdfLink:
    async def test_clicks_first_pdf_link_for_pdf_query(self) -> None:
        html_link = FakeElement(href="https://example.org/about")
        pdf_link = FakeElement(href="https://example.org/paper.pdf")
        page = FakePage(links=[html_link, pdf_link])
        result = await click_pdf_link(page, "View the PDF version")
        assert result is not None and result["method"] == "pdf_url_fallback"
        assert pdf_link.clicked and not html_link.clicked

    async def test_non_download_query_returns_none(self) -> None:
        pdf_link = FakeElement(href="https://example.org/paper.pdf")
        page = FakePage(links=[pdf_link])
        assert await click_pdf_link(page, "About the authors") is None


class TestStrategyChain:
    async def test_exact_match_wins(self) -> None:
        page = FakePage()
        result = await click_link_by_text_strategies(page, "Sign in", fuzzy=True)
        assert result["success"] is True and result["method"] == "exact"

    async def test_falls_through_to_keyword_match(self) -> None:
        link = FakeElement(text="Qwen Technical Report Download")
        page = FakePage(links=[link])
        page.click = _raise  # type: ignore[method-assign]  # exact selector misses
        page.get_by_text = lambda text, exact=False: _FailingLocator()  # type: ignore[method-assign]
        result = await click_link_by_text_strategies(page, "download qwen report", fuzzy=True)
        assert result["method"] == "keyword_match"

    async def test_not_fuzzy_reports_plain_error(self) -> None:
        page = FakePage()
        page.click = _raise  # type: ignore[method-assign]
        result = await click_link_by_text_strategies(page, "Sign in", fuzzy=False)
        assert result == {"success": False, "error": "No link found with text: Sign in"}

    async def test_all_strategies_fail(self) -> None:
        page = FakePage()
        page.click = _raise  # type: ignore[method-assign]
        page.get_by_text = lambda text, exact=False: _FailingLocator()  # type: ignore[method-assign]
        result = await click_link_by_text_strategies(page, "Nothing matches", fuzzy=True)
        assert result["success"] is False
        assert result["tried_methods"] == ["exact", "fuzzy", "keyword_match", "url_match"]
