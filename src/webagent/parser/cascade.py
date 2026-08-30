"""Parser cascade — content-aware routing across cloud APIs with local fallback.

Flow:
  1. Profile the document locally (PyMuPDF) → DocumentProfile.
  2. Router selects an ordered list of cloud providers (primary → fallbacks).
  3. Each provider is tried in order; the first quality-passing result wins.
  4. If every cloud provider fails, fall back to local PyMuPDF text extraction.
  5. If even that fails, return a ``PDFParseResult`` with ``error`` set.

The public ``parse_pdf`` entry returns a ``PDFParseResult`` and never raises for
expected failures — downstream tools branch on ``result.error``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ._errors import MAX_RETRIES, AllParsersFailedError, FailureReason, ParserProviderError
from ._http import build_client
from ._profile import DocumentProfile, profile_document
from ._quality import assess_quality
from ._request import ParseRequest, Provider
from ._router import select_parsers
from .models import PDFParseResult
from .providers import (
    LocalPyMuPDFParser,
    MarkerAPIParser,
    MinerUAPIParser,
    PaddleOCRAPIParser,
)

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, Provider] = {
    "marker": MarkerAPIParser(),
    "mineru": MinerUAPIParser(),
    "paddle": PaddleOCRAPIParser(),
}
_LOCAL = LocalPyMuPDFParser()

IMAGES_SUBDIR = "images"

# Backoff between same-provider retries on transient (retryable) failures.
_RETRY_BASE_DELAY = 1.5  # seconds
_RETRY_MAX_DELAY = 8.0


def parse_pdf(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: AgentConfig | None = None,
) -> PDFParseResult:
    """Parse a document via the cloud cascade. Synchronous, thread-safe wrapper.

    Safe to call from a worker thread (e.g. ``asyncio.to_thread``); if invoked
    while an event loop is already running it transparently offloads to a thread.
    """

    def _run() -> PDFParseResult:
        return asyncio.run(parse_structured_async(pdf_path, output_dir, config=config))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run()

    # A loop is already running in this thread — run the cascade in a fresh one.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run).result()


async def parse_structured_async(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: AgentConfig | None = None,
) -> PDFParseResult:
    """Async cascade entry — see module docstring."""
    if config is None:
        from webagent.core.config import AgentConfig

        config = AgentConfig()

    pdf_path = Path(pdf_path)
    out_dir = Path(output_dir) if output_dir else pdf_path.parent
    images_dir = out_dir / IMAGES_SUBDIR

    if not pdf_path.exists():
        return _error_result(out_dir, images_dir, f"file not found: {pdf_path}")

    profile = profile_document(pdf_path)
    if profile.page_count and profile.page_count > config.max_parse_pages:
        return _error_result(
            out_dir,
            images_dir,
            f"document has {profile.page_count} pages, exceeding max_parse_pages={config.max_parse_pages}",
        )

    order = select_parsers(profile, user_hint=config.ocr_provider)
    logger.info(
        "Parser routing for %s: %s (pages=%d avg_chars=%.0f scanned=%s)",
        pdf_path.name,
        order,
        profile.page_count,
        profile.avg_chars_per_page,
        profile.is_likely_scanned,
    )

    timeout = float(config.parser_http_timeout_seconds)
    async with build_client(timeout, config.parser_proxy or None) as client:
        result = await _run_cascade(client, order, pdf_path, profile, out_dir, images_dir, config)
        if result is not None:
            return result
        # All cloud providers failed — last-resort local extraction.
        req = ParseRequest(pdf_path, profile, out_dir, images_dir, config)
        try:
            logger.warning(
                "All cloud parsers failed for %s — falling back to local PyMuPDF", pdf_path.name
            )
            return await _LOCAL.parse(client, req)
        except Exception as exc:
            logger.error("Local fallback failed for %s: %s", pdf_path.name, exc)
            return _error_result(out_dir, images_dir, f"all parsers failed: {exc}")


async def _run_cascade(
    client: httpx.AsyncClient,
    order: tuple[str, ...],
    pdf_path: Path,
    profile: DocumentProfile,
    out_dir: Path,
    images_dir: Path,
    config: AgentConfig,
) -> PDFParseResult | None:
    """Try cloud providers in order. Returns a result, or None if all failed."""
    deadline = time.monotonic() + config.parse_timeout_seconds
    errors: list[ParserProviderError] = []
    req = ParseRequest(pdf_path, profile, out_dir, images_dir, config)

    for name in order:
        provider = _PROVIDERS[name]
        retries = MAX_RETRIES
        while retries >= 0:
            if time.monotonic() > deadline:
                logger.warning("Parse timeout budget exhausted for %s", pdf_path.name)
                return None
            try:
                # Bound the provider to the remaining cascade budget so a single
                # hung provider (e.g. a stuck MinerU poll) can't outlive it.
                remaining = max(1.0, deadline - time.monotonic())
                result = await asyncio.wait_for(provider.parse(client, req), timeout=remaining)
                quality = assess_quality(result, profile)
                if not quality.is_satisfactory:
                    logger.warning(
                        "parser=%s file=%s quality_failed score=%.1f reasons=%s",
                        name,
                        pdf_path.name,
                        quality.score,
                        ";".join(quality.reasons),
                    )
                    errors.append(ParserProviderError(provider=name, retryable=False))
                    break
                logger.info(
                    "parser=%s file=%s parse_ok score=%.2f", name, pdf_path.name, quality.score
                )
                return result
            except TimeoutError:
                logger.warning("parser=%s file=%s timed out (cascade budget)", name, pdf_path.name)
                errors.append(
                    ParserProviderError(
                        provider=name, retryable=False, reason=FailureReason.NETWORK_TIMEOUT
                    )
                )
                break
            except ParserProviderError as ppe:
                if ppe.retryable and retries > 0:
                    retries -= 1
                    # Exponential backoff — a transient ConnectError/5xx often
                    # clears within a couple of seconds (e.g. a proxy hiccup).
                    backoff = min(
                        _RETRY_BASE_DELAY * 2 ** (MAX_RETRIES - retries - 1), _RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "parser=%s retryable failure (%s); retrying in %.1fs (%d left)",
                        name,
                        ppe,
                        backoff,
                        retries,
                    )
                    await asyncio.sleep(backoff)
                    continue
                errors.append(ppe)
                logger.warning("parser=%s file=%s failed: %s", name, pdf_path.name, ppe)
                break

    if errors:
        logger.info(
            "Cloud cascade exhausted for %s: %s", pdf_path.name, AllParsersFailedError(errors)
        )
    return None


def _error_result(out_dir: Path, images_dir: Path, message: str) -> PDFParseResult:
    return PDFParseResult(
        markdown_path=None,
        json_path=None,
        images_dir=str(images_dir),
        output_dir=str(out_dir),
        method="cascade",
        error=message,
    )
