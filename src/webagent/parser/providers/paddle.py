"""PaddleOCR asynchronous cloud Jobs API client.

Current official flow:

1. ``POST /api/v2/ocr/jobs`` with a multipart file and model name.
2. Poll ``GET /api/v2/ocr/jobs/{job_id}`` until the job is done.
3. Download the JSONL result from ``data.resultUrl.jsonUrl``.
4. Map each page's ``layoutParsingResults`` into ``PDFParseResult``.

API docs: https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from .._build import add_block, build_from_page_texts, write_outputs
from .._errors import FailureReason, ParserProviderError
from .._http import raise_for_status
from .._request import ParseRequest
from ..models import PDFParseResult, TableInfo, TextBlock

logger = logging.getLogger(__name__)


class PaddleOCRAPIParser:
    """HTTP client for PaddleOCR's asynchronous cloud document parser."""

    name = "paddle"

    async def parse(self, client: httpx.AsyncClient, req: ParseRequest) -> PDFParseResult:
        api_key = (req.config.paddleocr_api_key or "").strip()
        jobs_url = (req.config.paddleocr_base_url or "").rstrip("/")
        if not api_key or not jobs_url:
            raise ParserProviderError(
                provider="paddle",
                retryable=False,
                reason=FailureReason.NOT_CONFIGURED,
                cause=Exception("paddleocr_api_key / paddleocr_base_url not configured"),
            )

        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            job_id = await self._submit_job(client, jobs_url, headers, req)
            logger.info("PaddleOCR job %s submitted (file=%s)", job_id, req.file_path.name)
            result_url = await self._poll_job(client, jobs_url, headers, job_id, req)
            pages = await self._download_pages(client, result_url)
            return self._build(pages, req)
        except ParserProviderError:
            raise
        except Exception as exc:
            raise ParserProviderError(provider="paddle", retryable=True, cause=exc) from exc

    async def _submit_job(
        self,
        client: httpx.AsyncClient,
        jobs_url: str,
        headers: dict[str, str],
        req: ParseRequest,
    ) -> str:
        file_bytes = await asyncio.to_thread(req.file_path.read_bytes)
        files = {"file": (req.file_path.name, file_bytes, "application/octet-stream")}
        options = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": True,
        }
        data = {
            "model": req.config.paddleocr_model,
            "optionalPayload": json.dumps(options),
        }
        response = await client.post(jobs_url, headers=headers, data=data, files=files)
        raise_for_status(response, "paddle", "submit")
        body = self._response_object(response, "submit")
        self._check_api_code(body, "submit")
        payload = body.get("data")
        job_id = payload.get("jobId") if isinstance(payload, dict) else None
        if not job_id:
            raise ParserProviderError(
                provider="paddle",
                retryable=False,
                cause=Exception(f"submit response missing data.jobId: {payload}"),
            )
        return str(job_id)

    async def _poll_job(
        self,
        client: httpx.AsyncClient,
        jobs_url: str,
        headers: dict[str, str],
        job_id: str,
        req: ParseRequest,
    ) -> str:
        poll_url = f"{jobs_url}/{job_id}"
        interval = float(req.config.parser_poll_interval_seconds)
        deadline = time.monotonic() + req.config.paddleocr_max_wait_seconds
        while time.monotonic() < deadline:
            response = await client.get(poll_url, headers=headers)
            raise_for_status(response, "paddle", "poll")
            body = self._response_object(response, "poll")
            self._check_api_code(body, "poll")
            payload = body.get("data")
            data = payload if isinstance(payload, dict) else {}
            state = str(data.get("state", "")).lower()
            if state == "done":
                result_urls = data.get("resultUrl")
                result_url = result_urls.get("jsonUrl") if isinstance(result_urls, dict) else None
                if not result_url:
                    raise ParserProviderError(
                        provider="paddle",
                        retryable=False,
                        cause=Exception("completed job has no data.resultUrl.jsonUrl"),
                    )
                return str(result_url)
            if state == "failed":
                error = data.get("errorMsg") or "unknown error"
                raise ParserProviderError(
                    provider="paddle",
                    retryable=False,
                    cause=Exception(f"job {job_id} failed: {error}"),
                )
            await asyncio.sleep(interval)

        raise ParserProviderError(
            provider="paddle",
            retryable=True,
            reason=FailureReason.NETWORK_TIMEOUT,
            cause=TimeoutError(
                f"job {job_id} timed out after {req.config.paddleocr_max_wait_seconds}s"
            ),
        )

    async def _download_pages(
        self, client: httpx.AsyncClient, result_url: str
    ) -> list[dict[str, Any]]:
        response = await client.get(result_url)
        raise_for_status(response, "paddle", "result download")
        pages: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(response.text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParserProviderError(
                    provider="paddle",
                    retryable=False,
                    cause=ValueError(f"invalid JSONL at line {line_number}"),
                ) from exc
            if not isinstance(document, dict):
                continue
            result = document.get("result")
            layout_pages = result.get("layoutParsingResults") if isinstance(result, dict) else None
            if isinstance(layout_pages, list):
                pages.extend(page for page in layout_pages if isinstance(page, dict))
        if not pages:
            raise ParserProviderError(
                provider="paddle",
                retryable=False,
                cause=Exception("JSONL result contains no layoutParsingResults"),
            )
        return pages

    def _build(self, pages: list[dict[str, Any]], req: ParseRequest) -> PDFParseResult:
        result = PDFParseResult(
            markdown_path=None,
            json_path=None,
            images_dir=str(req.images_dir),
            output_dir=str(req.output_dir),
            method="cascade",
            backend="paddle",
        )
        markdown_pages: list[str] = []
        current_section = "root"
        for page_idx, page in enumerate(pages):
            markdown_data = page.get("markdown")
            markdown = markdown_data.get("text") if isinstance(markdown_data, dict) else ""
            pruned = page.get("prunedResult")
            blocks = pruned.get("parsing_res_list") if isinstance(pruned, dict) else None
            typed_blocks = (
                [block for block in blocks if isinstance(block, dict)]
                if isinstance(blocks, list)
                else []
            )
            if typed_blocks:
                _mapped_markdown, current_section = self._map_blocks(
                    result, typed_blocks, page_idx, current_section
                )
            elif isinstance(markdown, str):
                current_section = build_from_page_texts(
                    result,
                    [markdown],
                    page_offset=page_idx,
                    current_section=current_section,
                )
            markdown_pages.append(markdown if isinstance(markdown, str) else "")
        write_outputs(result, req.output_dir, "\n\n".join(markdown_pages))
        return result

    @staticmethod
    def _response_object(response: httpx.Response, context: str) -> dict[str, Any]:
        body = response.json()
        if not isinstance(body, dict):
            raise ParserProviderError(
                provider="paddle",
                retryable=False,
                cause=TypeError(f"{context} response must be a JSON object"),
            )
        return body

    @staticmethod
    def _check_api_code(body: dict[str, Any], context: str) -> None:
        code = body.get("code")
        if isinstance(code, int) and code != 0:
            message = body.get("msg") or "unknown"
            raise ParserProviderError(
                provider="paddle",
                retryable=code in {500, 10010, 12002},
                cause=Exception(f"PaddleOCR API error at {context}: code={code} msg={message}"),
            )

    def _map_blocks(
        self,
        result: PDFParseResult,
        blocks: list[dict[str, Any]],
        page_idx: int,
        current_section: str,
    ) -> tuple[str, str]:
        markdown_parts: list[str] = []
        for block in blocks:
            label = block.get("block_label", block.get("type", block.get("category", "text")))
            content = block.get("block_content", block.get("text", block.get("content", "")))
            if not content:
                continue
            text = str(content).strip()
            if label in ("doc_title", "title", "heading"):
                current_section = add_block(
                    result,
                    TextBlock(text, page_idx, (0, 0, 0, 0), level=1, block_type="title"),
                    current_section,
                )
                markdown_parts.append(f"# {text}")
            elif label in ("section_title", "title2", "paragraph_title"):
                current_section = add_block(
                    result,
                    TextBlock(text, page_idx, (0, 0, 0, 0), level=2, block_type="title"),
                    current_section,
                )
                markdown_parts.append(f"## {text}")
            elif label == "table":
                result.tables.append(
                    TableInfo(path="", page_idx=page_idx, bbox=(0, 0, 0, 0), html_body=text)
                )
                markdown_parts.append(text)
            else:
                current_section = add_block(
                    result,
                    TextBlock(text, page_idx, (0, 0, 0, 0), block_type="paragraph"),
                    current_section,
                )
                markdown_parts.append(text)
        return "\n\n".join(markdown_parts), current_section
