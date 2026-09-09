"""Browser checkpoint serialization and validated tab/storage restoration."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Route
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _checkpoint_url_allowed(value: str) -> bool:
    """Confine checkpoint navigation/storage to ordinary web pages or a blank tab."""
    if value == "about:blank":
        return True
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and bool(parsed.hostname)
        and not any(character.isspace() for character in value)
        and parsed.username is None
        and parsed.password is None
    )


class _Cookie(BaseModel):
    """Validate the storage-state cookie contract before calling Playwright."""

    model_config = ConfigDict(
        strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )
    name: str
    value: str
    domain: str = Field(min_length=1)
    path: str = Field(pattern=r"^/")
    expires: float = -1
    httpOnly: bool = False
    secure: bool = False
    sameSite: Literal["Strict", "Lax", "None"] = "Lax"
    partitionKey: str | None = None


class _StorageEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    name: str
    value: str


class _StorageOrigin(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    origin: str
    localStorage: list[_StorageEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_origin(self) -> _StorageOrigin:
        parsed = urlparse(self.origin)
        if (
            not _checkpoint_url_allowed(self.origin)
            or parsed.scheme not in {"http", "https"}
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("browser checkpoint contains an unsafe storage origin")
        # Accessing port also validates its syntax and range.
        _ = parsed.port
        return self


class _StorageState(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    cookies: list[_Cookie] = Field(default_factory=list)
    origins: list[_StorageOrigin] = Field(default_factory=list)


async def export_checkpoint_state(
    context: BrowserContext, active_page: Page, *, include_storage: bool = False
) -> dict[str, Any]:
    """Capture resumable tab state and, when explicitly enabled, cookies/local storage.

    Storage state can contain authenticated session material. Callers must persist
    it as a private file and must not place it in traces or logs.
    """
    pages = list(context.pages)
    state: dict[str, Any] = {
        "schema_version": 1,
        "tabs": [page.url for page in pages],
        "active_index": pages.index(active_page),
    }
    if include_storage:
        state["storage_state"] = await context.storage_state()
    return state


async def restore_checkpoint_tabs(
    context: BrowserContext, state: dict[str, Any]
) -> tuple[Page, int, int]:
    """Restore a state produced by :meth:`export_checkpoint_state`.

    Only HTTP(S) and ``about:blank`` tabs are accepted. Cookie and local-storage
    restoration occurs only when the checkpoint contains the explicit optional
    ``storage_state`` payload.
    """
    if type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        raise ValueError("browser checkpoint schema mismatch")
    raw_tabs = state.get("tabs")
    active_index = state.get("active_index")
    if (
        not isinstance(raw_tabs, list)
        or not raw_tabs
        or not all(isinstance(url, str) and _checkpoint_url_allowed(url) for url in raw_tabs)
        or not isinstance(active_index, int)
        or isinstance(active_index, bool)
        or not 0 <= active_index < len(raw_tabs)
    ):
        raise ValueError("browser checkpoint tab state is invalid")

    storage = state.get("storage_state")
    if storage is not None:
        await restore_storage_state(context, storage)

    pages = list(context.pages)
    for page in pages[1:]:
        await page.close()
    pages = [pages[0]] if pages else [await context.new_page()]
    while len(pages) < len(raw_tabs):
        pages.append(await context.new_page())
    for page, url in zip(pages, raw_tabs, strict=True):
        await page.goto(url, wait_until="domcontentloaded")
    return pages[active_index], len(pages), active_index


async def restore_storage_state(context: BrowserContext, value: Any) -> None:
    """Validate the complete input before modifying browser storage.

    Browser API failures during application are not transactionally rolled back.
    """
    storage = _StorageState.model_validate(value)
    cookies = storage.model_dump(exclude_unset=True).get("cookies", [])
    local_by_origin = {
        item.origin: {entry.name: entry.value for entry in item.localStorage}
        for item in storage.origins
    }
    if cookies:
        await context.add_cookies(cookies)
    if local_by_origin:
        await _restore_local_storage(context, local_by_origin)


async def _restore_local_storage(
    context: BrowserContext, origins: dict[str, dict[str, str]]
) -> None:
    page = await context.new_page()
    try:
        # Chromium is the controller's browser. Bypass service workers so that
        # neither the real origin nor its application code handles these visits.
        session = await context.new_cdp_session(page)
        try:
            await session.send("Network.setBypassServiceWorker", {"bypass": True})
            await page.route("**/*", _blank_storage_document)
            for origin, entries in origins.items():
                await page.goto(origin, wait_until="domcontentloaded")
                await page.evaluate(
                    """entries => {
                        localStorage.clear();
                        for (const [name, value] of Object.entries(entries)) {
                            localStorage.setItem(name, value);
                        }
                    }""",
                    entries,
                )
        finally:
            await session.detach()
    finally:
        await page.close()


async def _blank_storage_document(route: Route) -> None:
    await route.fulfill(status=200, content_type="text/html", body="<!doctype html><html></html>")
