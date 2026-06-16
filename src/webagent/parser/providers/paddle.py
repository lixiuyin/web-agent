"""PaddleOCR layout-parsing API client.

Primarily a single-image layout specialist.  For multi-page PDFs each page is
rendered with PyMuPDF and submitted to the API with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from .._build import add_block, write_outputs
from .._errors import FailureReason, ParserProviderError
from .._http import raise_for_status
from .._profile import IMAGE_EXTS
from .._request import ParseRequest
from ..models import PDFParseResult, TableInfo, TextBlock

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_PAGES = 4
_RENDER_ZOOM = 2.0  # ~144 DPI


class PaddleOCRAPIParser:
    """HTTP client for an aistudio-app PaddleOCR layout-parsing endpoint."""

    name = "paddle"

    async def parse(self, client: httpx.AsyncClient, req: ParseRequest) -> PDFParseResult:
        api_key = (req.config.paddleocr_api_key or "").strip()
        base_url = (req.config.paddleocr_base_url or "").rstrip("/")
        if not api_key or not base_url:
            raise ParserProviderError(
                provider="paddle",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("paddleocr_api_key / paddleocr_base_url not configured"),
            )

        suffix = req.file_path.suffix.lower()
        try:
            if suffix in IMAGE_EXTS:
                page_blocks = [
                    await self._call_api(client, base_url, api_key, await _read(req.file_path))
                ]
            elif suffix == ".pdf":
                page_blocks = await self._parse_pdf_pages(client, base_url, api_key, req)
            else:
                raise ParserProviderError(
                    provider="paddle",
                    retryable=False,
                    cause=ValueError(f"unsupported format for paddle: {suffix}"),
                )
        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="paddle", retryable=True, cause=exc) from exc

        result = PDFParseResult(
            markdown_path=None,
            json_path=None,
            images_dir=str(req.images_dir),
            output_dir=str(req.output_dir),
            method="cascade",
            backend="paddle",
        )
        markdown_parts: list[str] = []
        current_section = "root"
        for page_idx, blocks in enumerate(page_blocks):
            page_md, current_section = self._map_blocks(result, blocks, page_idx, current_section)
            if page_md:
                markdown_parts.append(page_md)
        write_outputs(result, req.output_dir, "\n\n".join(markdown_parts))
        return result

    async def _parse_pdf_pages(
        self, client, base_url, api_key, req: ParseRequest
    ) -> list[list[dict]]:
        page_images = await asyncio.to_thread(self._render_pdf, req.file_path)
        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _one(img: bytes) -> list[dict]:
            async with sem:
                return await self._call_api(client, base_url, api_key, img)

        return await asyncio.gather(*(_one(img) for img in page_images))

    @staticmethod
    def _render_pdf(file_path: Path) -> list[bytes]:
        import fitz  # type: ignore[import-untyped]

        images: list[bytes] = []
        with fitz.open(str(file_path)) as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
                images.append(pix.tobytes("png"))
        return images

    async def _call_api(
        self, client, base_url: str, api_key: str, image_bytes: bytes
    ) -> list[dict]:
        payload = {"file": base64.b64encode(image_bytes).decode("ascii")}
        headers = {"Authorization": f"token {api_key}"}
        resp = await client.post(base_url, json=payload, headers=headers)
        raise_for_status(resp, "paddle", "layout")
        return self._extract_blocks(resp.json())

    @staticmethod
    def _extract_blocks(body: Any) -> list[dict]:
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            result = body.get("result")
            if isinstance(result, dict):
                lpr = result.get("layoutParsingResults", [])
                if isinstance(lpr, list) and lpr:
                    pruned = lpr[0].get("prunedResult", {})
                    blocks = pruned.get("parsing_res_list", [])
                    if isinstance(blocks, list):
                        return blocks
            return body.get("layout", body.get("blocks", [])) or []
        return []

    def _map_blocks(
        self, result: PDFParseResult, blocks: list[dict], page_idx: int, current_section: str
    ) -> tuple[str, str]:
        md_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            label = block.get("block_label", block.get("type", block.get("category", "text")))
            content = block.get("block_content", block.get("text", block.get("content", "")))
            if not content:
                continue
            content = str(content).strip()

            if label in ("doc_title", "title", "heading"):
                current_section = add_block(
                    result,
                    TextBlock(content, page_idx, (0, 0, 0, 0), level=1, block_type="title"),
                    current_section,
                )
                md_parts.append(f"# {content}")
            elif label in ("section_title", "title2", "paragraph_title"):
                current_section = add_block(
                    result,
                    TextBlock(content, page_idx, (0, 0, 0, 0), level=2, block_type="title"),
                    current_section,
                )
                md_parts.append(f"## {content}")
            elif label == "table":
                result.tables.append(
                    TableInfo(path="", page_idx=page_idx, bbox=(0, 0, 0, 0), html_body=content)
                )
                md_parts.append(content)
            else:
                current_section = add_block(
                    result,
                    TextBlock(content, page_idx, (0, 0, 0, 0), block_type="paragraph"),
                    current_section,
                )
                md_parts.append(content)
        return "\n\n".join(md_parts), current_section


async def _read(path: Path) -> bytes:
    return await asyncio.to_thread(path.read_bytes)
