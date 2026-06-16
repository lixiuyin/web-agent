"""Marker API client — datalab.to cloud document parsing.

Task-based flow:
  1. ``POST`` multipart to the marker endpoint → ``request_check_url``.
  2. Poll the check URL until ``status == "complete"``.
  3. Map markdown + paginated text + images into a ``PDFParseResult``.

API docs: https://documentation.datalab.to/api-reference/marker
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import time
from pathlib import Path

import httpx

from .._build import build_from_page_texts, image_captions_from_pages, write_outputs
from .._errors import FailureReason, ParserProviderError
from .._http import raise_for_status
from .._request import ParseRequest
from ..models import ImageInfo, PDFParseResult, extract_figure_number

logger = logging.getLogger(__name__)

# Page separator: "\n\n{PAGE_NUMBER}----…----\n\n" (48 dashes per API spec).
_PAGE_SEP_RE = re.compile(r"\n\n\{\d+\}-{48}\n\n")

_MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}


class MarkerAPIParser:
    """HTTP client for the datalab.to Marker cloud API."""

    name = "marker"

    async def parse(self, client: httpx.AsyncClient, req: ParseRequest) -> PDFParseResult:
        api_key = (req.config.marker_api_key or "").strip()
        if not api_key:
            raise ParserProviderError(
                provider="marker",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("marker_api_key is missing (set AGENT_MARKER_API_KEY)"),
            )
        base_url = (req.config.marker_base_url or "").rstrip("/")
        if not base_url:
            raise ParserProviderError(
                provider="marker",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("marker_base_url is not configured"),
            )

        headers = {"X-API-Key": api_key}
        mode = (
            "accurate" if req.profile.is_likely_scanned or req.profile.image_ratio > 0.5 else "fast"
        )
        try:
            file_bytes = await asyncio.to_thread(req.file_path.read_bytes)
            files = {"file": (req.file_path.name, file_bytes, self._mime(req.file_path))}
            data = {"output_format": "markdown", "paginate": "true", "mode": mode}
            resp = await client.post(base_url, files=files, data=data, headers=headers)
            raise_for_status(resp, "marker", "submit")

            body = resp.json()
            if not body.get("success", True):
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    cause=Exception(f"submit failed: {body.get('error', 'unknown')}"),
                )
            check_url = body.get("request_check_url") or body.get("check_url")
            if check_url:
                request_id = body.get("request_id", "unknown")
                logger.info("Marker task submitted: %s", request_id)
                body = await self._poll(client, check_url, headers, req, request_id)
            return self._build(body, req)
        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="marker", retryable=True, cause=exc) from exc

    async def _poll(self, client, check_url, headers, req: ParseRequest, request_id: str) -> dict:
        interval = float(req.config.parser_poll_interval_seconds)
        deadline = time.monotonic() + req.config.marker_max_wait_seconds
        while time.monotonic() < deadline:
            resp = await client.get(check_url, headers=headers)
            # Surface auth/rate/4xx/5xx correctly instead of treating them as
            # "not done yet" and polling until the deadline.
            raise_for_status(resp, "marker", "poll")
            body = resp.json()
            status = str(body.get("status", "")).lower()
            if status in {"complete", "completed", "processed", "success", "done"}:
                return body
            if status in {"failed", "error"}:
                raise ParserProviderError(
                    provider="marker",
                    retryable=False,
                    cause=Exception(f"task {request_id} failed: {body.get('error', 'unknown')}"),
                )
            if not status and body.get("markdown") is not None:
                return body
            await asyncio.sleep(interval)
        raise ParserProviderError(
            provider="marker",
            retryable=True,
            reason=FailureReason.NETWORK_TIMEOUT,
            cause=TimeoutError(
                f"task {request_id} timed out after {req.config.marker_max_wait_seconds}s"
            ),
        )

    def _build(self, body: dict, req: ParseRequest) -> PDFParseResult:
        markdown = body.get("markdown") or ""
        metadata = body.get("metadata") or {}
        page_count = body.get("page_count") or metadata.get("page_count") or 1
        page_texts = self._split_pages(markdown, page_count)

        result = PDFParseResult(
            markdown_path=None,
            json_path=None,
            images_dir=str(req.images_dir),
            output_dir=str(req.output_dir),
            method="cascade",
            backend="marker",
        )
        build_from_page_texts(result, page_texts)
        self._save_images(result, body.get("images") or {}, req, page_texts)
        write_outputs(result, req.output_dir, markdown)
        return result

    def _save_images(
        self, result: PDFParseResult, images: dict, req: ParseRequest, page_texts: list[str]
    ) -> None:
        """Persist base64 images and attach each to its figure caption/number.

        Marker keys images by the filename used in the markdown ``![alt](key)``
        references, so the caption and page are recovered from the markdown
        rather than guessed from the (hash-like) key.
        """
        if not isinstance(images, dict):
            return
        req.images_dir.mkdir(parents=True, exist_ok=True)
        captions = image_captions_from_pages(page_texts)
        for key, payload in images.items():
            data = payload if isinstance(payload, str) else (payload or {}).get("data", "")
            if not data:
                continue
            try:
                raw = base64.b64decode(data)
            except (binascii.Error, ValueError):
                continue
            name = (
                str(key) if str(key).lower().endswith((".png", ".jpg", ".jpeg")) else f"{key}.png"
            )
            basename = Path(name).name
            out = req.images_dir / basename
            out.write_bytes(raw)
            page_idx, caption = captions.get(basename, (0, ""))
            result.images.append(
                ImageInfo(
                    path=str(out),
                    page_idx=page_idx,
                    bbox=(0, 0, 0, 0),
                    caption=caption,
                    figure_number=extract_figure_number(caption),
                )
            )

    @staticmethod
    def _mime(file_path: Path) -> str:
        return _MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")

    @staticmethod
    def _split_pages(markdown: str, page_count: int) -> list[str]:
        if not markdown:
            return [""] * max(page_count, 1)
        parts = _PAGE_SEP_RE.split(markdown)
        if parts and not parts[0].strip():
            parts = parts[1:]
        if len(parts) >= page_count:
            return parts[:page_count]
        return parts or [markdown]
