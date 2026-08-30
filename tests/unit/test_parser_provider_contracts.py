"""HTTP contract tests for the Datalab and MinerU parser providers."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from webagent.core.config import AgentConfig
from webagent.parser._errors import ParserProviderError
from webagent.parser._profile import DocumentProfile
from webagent.parser._request import ParseRequest
from webagent.parser.providers.marker import MarkerAPIParser
from webagent.parser.providers.mineru import MinerUAPIParser
from webagent.parser.providers.paddle import PaddleOCRAPIParser


def _request(tmp_path: Path, config: AgentConfig) -> ParseRequest:
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"contract-test-pdf")
    return ParseRequest(
        file_path=source,
        profile=DocumentProfile(
            suffix=".pdf",
            page_count=1,
            avg_chars_per_page=100,
            image_ratio=0.0,
            has_text_layer=True,
            is_likely_scanned=False,
            size_bytes=source.stat().st_size,
        ),
        output_dir=tmp_path / "output",
        images_dir=tmp_path / "output" / "images",
        config=config,
    )


async def test_datalab_convert_submit_and_poll_contract(tmp_path: Path) -> None:
    submit_url = "https://www.datalab.to/api/v1/convert"
    check_url = f"{submit_url}/request-123"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert str(request.url) == submit_url
            assert request.headers["X-API-Key"] == "marker-test-key"
            assert request.headers["Content-Type"].startswith("multipart/form-data;")
            body = await request.aread()
            assert b'name="file"; filename="contract.pdf"' in body
            assert b'name="output_format"' in body and b"markdown" in body
            assert b'name="paginate"' in body and b"true" in body
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "request_id": "request-123",
                    "request_check_url": check_url,
                },
            )
        assert request.method == "GET"
        assert str(request.url) == check_url
        return httpx.Response(
            200,
            json={
                "status": "complete",
                "success": True,
                "markdown": "# Contract\n\nDatalab convert works.",
                "page_count": 1,
                "images": {},
                "metadata": {},
            },
        )

    config = AgentConfig(
        _env_file=None,
        marker_api_key="marker-test-key",
        marker_base_url=submit_url,
        parser_poll_interval_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MarkerAPIParser().parse(client, _request(tmp_path, config))

    assert [request.method for request in requests] == ["POST", "GET"]
    assert result.backend == "marker"
    assert any(block.text == "Datalab convert works." for block in result.text_blocks)


async def test_marker_completed_poll_budget_is_not_retried(tmp_path: Path) -> None:
    config = AgentConfig(
        _env_file=None,
        marker_max_wait_seconds=0,
        parser_poll_interval_seconds=0,
    )
    request = _request(tmp_path, config)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200))
    ) as client:
        with pytest.raises(ParserProviderError) as caught:
            await MarkerAPIParser()._poll(
                client,
                "https://example.test/check",
                {},
                request,
                "request-timeout",
            )

    assert caught.value.retryable is False


async def test_mineru_v4_upload_poll_and_download_contract(tmp_path: Path) -> None:
    api_root = "https://mineru.net/api/v4"
    upload_url = "https://uploads.example/contract.pdf"
    result_url = "https://results.example/contract.zip"
    requests: list[httpx.Request] = []

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("full.md", "# Contract\n\nMinerU works.")
        output.writestr(
            "contract_content_list.json",
            json.dumps([{"type": "text", "text": "MinerU works.", "page_idx": 0}]),
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if request.method == "POST":
            assert url == f"{api_root}/file-urls/batch"
            assert request.headers["Authorization"] == "Bearer mineru-test-key"
            payload = json.loads((await request.aread()).decode())
            assert payload["files"] == [{"name": "contract.pdf", "is_ocr": False}]
            assert payload["enable_formula"] is True
            assert payload["enable_table"] is True
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {"batch_id": "batch-123", "file_urls": [upload_url]},
                },
            )
        if request.method == "PUT":
            assert url == upload_url
            assert await request.aread() == b"contract-test-pdf"
            return httpx.Response(200)
        if url == f"{api_root}/extract-results/batch/batch-123":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-123",
                        "extract_result": [
                            {
                                "file_name": "contract.pdf",
                                "state": "done",
                                "full_zip_url": result_url,
                            }
                        ],
                    },
                },
            )
        assert url == result_url
        return httpx.Response(200, content=archive.getvalue())

    config = AgentConfig(
        _env_file=None,
        mineru_api_key="mineru-test-key",
        mineru_base_url=api_root,
        parser_poll_interval_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MinerUAPIParser().parse(client, _request(tmp_path, config))

    assert [request.method for request in requests] == ["POST", "PUT", "GET", "GET"]
    assert result.backend == "mineru"
    assert any(block.text == "MinerU works." for block in result.text_blocks)


async def test_paddleocr_jobs_submit_poll_and_download_contract(tmp_path: Path) -> None:
    jobs_url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    result_url = "https://results.example/paddle.jsonl"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if request.method == "POST":
            assert url == jobs_url
            assert request.headers["Authorization"] == "Bearer paddle-test-key"
            assert request.headers["Content-Type"].startswith("multipart/form-data;")
            body = await request.aread()
            assert b'name="file"; filename="contract.pdf"' in body
            assert b'name="model"' in body and b"PP-StructureV3" in body
            assert b'name="optionalPayload"' in body
            return httpx.Response(
                200,
                json={"code": 0, "msg": "Success", "data": {"jobId": "job-123"}},
            )
        if url == f"{jobs_url}/job-123":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "Success",
                    "data": {
                        "jobId": "job-123",
                        "state": "done",
                        "resultUrl": {"jsonUrl": result_url},
                    },
                },
            )
        assert url == result_url
        jsonl = json.dumps(
            {
                "result": {
                    "layoutParsingResults": [
                        {
                            "markdown": {"text": "# Contract\n\nPaddleOCR works.", "images": {}},
                            "prunedResult": {
                                "parsing_res_list": [
                                    {"block_label": "doc_title", "block_content": "Contract"},
                                    {"block_label": "text", "block_content": "PaddleOCR works."},
                                ]
                            },
                            "outputImages": {},
                        }
                    ]
                }
            }
        )
        return httpx.Response(200, text=jsonl)

    config = AgentConfig(
        _env_file=None,
        paddleocr_api_key="paddle-test-key",
        paddleocr_base_url=jobs_url,
        paddleocr_model="PP-StructureV3",
        parser_poll_interval_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PaddleOCRAPIParser().parse(client, _request(tmp_path, config))

    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert result.backend == "paddle"
    assert any(block.text == "PaddleOCR works." for block in result.text_blocks)


def test_paddle_markdown_fallback_preserves_page_and_section_state(tmp_path: Path) -> None:
    config = AgentConfig(_env_file=None)
    request = _request(tmp_path, config)
    pages = [
        {
            "markdown": {"text": "# Introduction\n\nFirst page."},
            "prunedResult": {"parsing_res_list": []},
        },
        {
            "markdown": {
                "text": "Second page.\n\n| Metric | Value |\n| --- | --- |\n| Score | 95 |"
            },
            "prunedResult": {"parsing_res_list": []},
        },
    ]

    result = PaddleOCRAPIParser()._build(pages, request)

    assert [block.page_idx for block in result.text_blocks] == [0, 0, 1]
    assert [block.text for block in result.sections["1:Introduction"]] == [
        "First page.",
        "Second page.",
    ]
    assert result.tables[0].page_idx == 1
