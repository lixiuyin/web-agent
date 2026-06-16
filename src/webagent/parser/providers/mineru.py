"""MinerU API client — mineru.net v4 cloud document extraction.

Documented v4 batch-upload flow (https://mineru.net/apiManage/docs):

  1. ``POST /file-urls/batch`` — request a signed upload URL + ``batch_id``.
  2. ``PUT`` the file binary directly to that URL (no Content-Type header).
  3. Poll ``GET /extract-results/batch/{batch_id}`` until ``state == "done"``.
  4. Download ``full_zip_url`` and read ``full.md`` + ``*_content_list.json`` +
     extracted images out of the archive.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import zipfile
from pathlib import Path

import httpx

from .._build import write_outputs
from .._errors import FailureReason, ParserProviderError
from .._http import raise_for_status
from .._request import ParseRequest
from ..models import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
    extract_figure_number,
    extract_table_number,
)

logger = logging.getLogger(__name__)

_LEGACY_BASE_SUFFIXES = (
    "/extract-results/batch",
    "/file-urls/batch",
    "/extract/task/batch",
    "/extract/task",
    "/extract",
)


class MinerUAPIParser:
    """HTTP client for the mineru.net v4 batch extract API."""

    name = "mineru"

    @staticmethod
    def _api_root(base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        for suffix in _LEGACY_BASE_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base.rstrip("/")

    @staticmethod
    def _check_api_code(body: dict, context: str) -> None:
        code = body.get("code")
        if isinstance(code, int) and code != 0:
            msg = body.get("msg") or body.get("message") or "unknown"
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"MinerU API error at {context}: code={code} msg={msg}"),
            )

    async def parse(self, client: httpx.AsyncClient, req: ParseRequest) -> PDFParseResult:
        api_key = (req.config.mineru_api_key or "").strip()
        if not api_key:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("mineru_api_key is missing (set AGENT_MINERU_API_KEY)"),
            )
        api_root = self._api_root(req.config.mineru_base_url)
        if not api_root:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("mineru_base_url is not configured"),
            )

        headers = {"Authorization": f"Bearer {api_key}"}
        is_ocr = req.profile.is_likely_scanned
        try:
            batch_id, upload_url = await self._request_upload_url(
                client, api_root, headers, req, is_ocr
            )
            await self._upload_file(client, upload_url, req)
            logger.info(
                "MinerU batch %s created (file=%s ocr=%s)", batch_id, req.file_path.name, is_ocr
            )
            entry = await self._poll_batch(client, api_root, headers, batch_id, req)
            return await self._download_and_build(client, entry, req)
        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="mineru", retryable=True, cause=exc) from exc

    async def _request_upload_url(
        self, client, api_root, headers, req: ParseRequest, is_ocr: bool
    ) -> tuple[str, str]:
        endpoint = f"{api_root}/file-urls/batch"
        payload = {
            "files": [{"name": req.file_path.name, "is_ocr": is_ocr}],
            "enable_formula": True,
            "enable_table": True,
            "language": "auto",
        }
        resp = await client.post(endpoint, json=payload, headers=headers)
        raise_for_status(resp, "mineru", "file-urls/batch")
        body = resp.json()
        self._check_api_code(body, "file-urls/batch")
        data = body.get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"file-urls/batch missing batch_id/file_urls: {data}"),
            )
        return str(batch_id), str(file_urls[0])

    async def _upload_file(self, client, upload_url: str, req: ParseRequest) -> None:
        # MinerU's signed URL rejects an explicit Content-Type; suppress httpx's default.
        body = await asyncio.to_thread(req.file_path.read_bytes)
        resp = await client.put(upload_url, content=body, headers={"Content-Type": ""})
        if resp.status_code >= 400:
            raise ParserProviderError(
                provider="mineru",
                retryable=resp.status_code >= 500,
                cause=Exception(f"file upload failed (HTTP {resp.status_code}): {resp.text[:300]}"),
            )

    async def _poll_batch(self, client, api_root, headers, batch_id, req: ParseRequest) -> dict:
        poll_url = f"{api_root}/extract-results/batch/{batch_id}"
        interval = float(req.config.parser_poll_interval_seconds)
        deadline = time.monotonic() + req.config.mineru_max_wait_seconds
        file_name = req.file_path.name

        while time.monotonic() < deadline:
            resp = await client.get(poll_url, headers=headers)
            raise_for_status(resp, "mineru", "extract-results/batch")
            body = resp.json()
            self._check_api_code(body, "extract-results/batch")
            entry = self._select_entry(body, file_name)
            if entry is not None:
                state = str(entry.get("state", "")).lower()
                if state == "done":
                    return entry
                if state == "failed":
                    err = entry.get("err_msg") or "unknown error"
                    raise ParserProviderError(
                        provider="mineru",
                        retryable=False,
                        cause=Exception(f"Batch {batch_id} failed: {err}"),
                    )
            await asyncio.sleep(interval)

        raise ParserProviderError(
            provider="mineru",
            retryable=True,
            reason=FailureReason.NETWORK_TIMEOUT,
            cause=TimeoutError(
                f"Batch {batch_id} timed out after {req.config.mineru_max_wait_seconds}s"
            ),
        )

    @staticmethod
    def _select_entry(body: dict, file_name: str) -> dict | None:
        results = (body.get("data") or {}).get("extract_result") or []
        if not results:
            return None
        for item in results:
            if isinstance(item, dict) and item.get("file_name") == file_name:
                return item
        first = results[0]
        return first if isinstance(first, dict) else None

    async def _download_and_build(self, client, entry: dict, req: ParseRequest) -> PDFParseResult:
        zip_url = entry.get("full_zip_url")
        if not zip_url:
            raise ParserProviderError(
                provider="mineru",
                retryable=False,
                cause=Exception(f"batch result has no full_zip_url: {entry}"),
            )
        resp = await client.get(zip_url)
        if resp.status_code >= 400:
            raise ParserProviderError(
                provider="mineru",
                retryable=resp.status_code >= 500,
                cause=Exception(f"result download failed (HTTP {resp.status_code})"),
            )
        markdown, content_list = await asyncio.to_thread(
            self._unpack_zip, resp.content, req.images_dir
        )
        result = PDFParseResult(
            markdown_path=None,
            json_path=None,
            images_dir=str(req.images_dir),
            output_dir=str(req.output_dir),
            method="cascade",
            backend="mineru",
        )
        self._map_content_list(result, content_list, req)
        write_outputs(result, req.output_dir, markdown, content_list or None)
        return result

    @staticmethod
    def _unpack_zip(zip_bytes: bytes, images_dir: Path) -> tuple[str, list]:
        """Extract markdown, content_list.json and images from the result archive."""
        images_dir.mkdir(parents=True, exist_ok=True)
        markdown = ""
        content_list: list = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith("/"):
                    continue
                if not markdown and (lower.endswith("full.md") or lower.endswith(".md")):
                    markdown = zf.read(name).decode("utf-8", errors="replace")
                elif lower.endswith("content_list.json"):
                    try:
                        parsed = json.loads(zf.read(name).decode("utf-8", errors="replace"))
                        if isinstance(parsed, list):
                            content_list = parsed
                    except json.JSONDecodeError:
                        pass
                elif "/images/" in lower or lower.startswith("images/"):
                    (images_dir / Path(name).name).write_bytes(zf.read(name))
        return markdown, content_list

    def _map_content_list(
        self, result: PDFParseResult, content_list: list, req: ParseRequest
    ) -> None:
        """Map MinerU content_list items to structured TextBlock/Table/Image entries."""
        current_section = "root"
        for item in content_list:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "text")
            page_idx = int(item.get("page_idx") or item.get("page_no") or 0)

            if item_type in ("text", "equation"):
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                level = int(item.get("text_level", 0) or 0)
                if level > 0:
                    block = TextBlock(text, page_idx, (0, 0, 0, 0), level=level, block_type="title")
                    result.text_blocks.append(block)
                    current_section = f"{level}:{text}"
                    result.sections.setdefault(current_section, [])
                else:
                    block_type = "formula" if item_type == "equation" else "paragraph"
                    block = TextBlock(text, page_idx, (0, 0, 0, 0), block_type=block_type)
                    result.text_blocks.append(block)
                    result.sections.setdefault(current_section, []).append(block)

            elif item_type == "image":
                caption = " ".join(item.get("img_caption") or []).strip()
                result.images.append(
                    ImageInfo(
                        path=self._image_path(req, item.get("img_path")),
                        page_idx=page_idx,
                        bbox=(0, 0, 0, 0),
                        caption=caption,
                        footnote=" ".join(item.get("img_footnote") or []).strip(),
                        figure_number=extract_figure_number(caption),
                    )
                )

            elif item_type == "table":
                caption = " ".join(item.get("table_caption") or []).strip()
                result.tables.append(
                    TableInfo(
                        path=self._image_path(req, item.get("img_path")),
                        page_idx=page_idx,
                        bbox=(0, 0, 0, 0),
                        caption=caption,
                        footnote=" ".join(item.get("table_footnote") or []).strip(),
                        html_body=item.get("table_body") or "",
                        table_number=extract_table_number(caption),
                    )
                )

    @staticmethod
    def _image_path(req: ParseRequest, img_path: object) -> str:
        if not img_path:
            return ""
        return str(req.images_dir / Path(str(img_path)).name)
