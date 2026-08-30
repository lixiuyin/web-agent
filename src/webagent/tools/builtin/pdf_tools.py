"""PDF download and extraction tools with structured content support."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from webagent.core.models import ToolResult
from webagent.parser import (
    PDFParseResult,
    find_images_by_keyword,
    find_section_by_title,
    find_tables_by_keyword,
    generate_content_summary,
    parse_pdf,
)
from webagent.tools.builtin._artifact_publish import (
    publish_immutable_artifact,
    temporary_artifact_path,
)
from webagent.tools.builtin._pdf_common import (
    PdfToolBase,
    load_pdf_result,
    persist_pdf_result,
)
from webagent.tools.registry import tool
from webagent.utils.paths import get_pdf_extract_dir
from webagent.utils.pdf import extract_figure_captions, extract_images, extract_text


def _download_with_urlopen(url: str, out_path: Path, context: ssl.SSLContext | None = None) -> None:
    """Download file using urlopen with SSL context support."""
    with urlopen(url, context=context) as response, out_path.open("wb") as stream:
        stream.write(response.read())


def _is_pdf_file(path: Path) -> bool:
    """Validate content, not the URL suffix or caller-provided filename."""
    try:
        return b"%PDF-" in path.read_bytes()[:1024]
    except OSError:
        return False


def _resolve_parse_output_dir(
    output_dir_str: str | None,
    artifacts_dir: Path,
    source_path: Path | None = None,
) -> Path:
    """Resolve parser output under the current output root."""
    if output_dir_str:
        output_dir = Path(output_dir_str.strip())
        if not output_dir.is_absolute():
            output_dir = artifacts_dir / output_dir
    else:
        output_dir = get_pdf_extract_dir(artifacts_dir, source_path)

    output_root = artifacts_dir.resolve().parent
    resolved = output_dir.resolve()
    if resolved != output_root and not resolved.is_relative_to(output_root):
        raise ValueError(f"output_dir escapes the output directory: {output_dir_str!r}")
    return resolved


@tool(
    "download_pdf",
    "Download and content-validate a PDF from an explicitly observed URL. HTML preview pages are "
    "rejected without discovering retry URLs; navigate to the preview and call "
    "inspect_download_links first. After "
    "downloading, use pdf_parse to extract Markdown and images. params: url (string), filename? (string)",
)
class DownloadPdfTool(PdfToolBase):
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("url"), str) or not params["url"].strip():
            raise ValueError("'url' required")
        parsed = urlparse(params["url"].strip())
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Only http/https URLs are supported, got: {parsed.scheme!r}")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        url = str(params["url"]).strip()
        filename = str(params.get("filename") or Path(urlparse(url).path).name or "downloaded.pdf")
        # Sanitize filename: strip directory components to prevent path traversal
        filename = Path(filename).name
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        out_path = self.artifacts_dir / "downloads" / filename
        ssl_warning: str | None = None
        try:
            with temporary_artifact_path(out_path) as temporary:
                # Try with verified SSL first, falling back only for certificate errors.
                try:
                    ctx = ssl.create_default_context()
                    await asyncio.to_thread(_download_with_urlopen, url, temporary, ctx)
                except ssl.SSLCertVerificationError:
                    import logging

                    logging.getLogger("webagent").warning(
                        "SSL verification failed for %s, retrying without cert check", url
                    )
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    try:
                        await asyncio.to_thread(_download_with_urlopen, url, temporary, ctx)
                    except Exception as exc:
                        return ToolResult(
                            success=False,
                            tool_name="download_pdf",
                            error=f"Download failed (even without SSL verify): {exc}",
                        )
                    ssl_warning = "Downloaded without SSL certificate verification"

                invalid = self._invalid_pdf_result(url, temporary)
                if invalid is not None:
                    return invalid
                try:
                    deduplicated = await asyncio.to_thread(
                        publish_immutable_artifact, temporary, out_path
                    )
                except FileExistsError:
                    return ToolResult(
                        success=False,
                        tool_name="download_pdf",
                        error=(
                            "Artifact already exists with different content; refusing to "
                            f"overwrite: {out_path}"
                        ),
                        data={
                            "path": str(out_path),
                            "filename": filename,
                            "source_url": url,
                        },
                    )
            browser_url = await self._open_download(out_path)
            data: dict[str, Any] = {
                "path": str(out_path),
                "filename": filename,
                "source_url": url,
                "browser_url": browser_url,
                "deduplicated": deduplicated,
            }
            if ssl_warning is not None:
                data["ssl_warning"] = ssl_warning
            return ToolResult(
                success=True,
                tool_name="download_pdf",
                data=data,
            )
        except Exception as exc:
            return ToolResult(
                success=False, tool_name="download_pdf", error=f"Download failed: {exc}"
            )

    def _invalid_pdf_result(self, source_url: str, out_path: Path) -> ToolResult | None:
        if _is_pdf_file(out_path):
            return None
        size = out_path.stat().st_size if out_path.exists() else 0
        return ToolResult(
            success=False,
            tool_name="download_pdf",
            error=(
                "Downloaded content is not a PDF (missing %PDF header). "
                "The URL may be an HTML preview page; navigate to it and call "
                "inspect_download_links before retrying an explicitly reported URL."
            ),
            data={
                "source_url": source_url,
                "downloaded_bytes": size,
            },
        )

    async def _open_download(self, out_path: Path) -> str | None:
        if not self.browser:
            return None
        try:
            result = await self.browser.open_local_file(str(out_path))
            if result.get("success"):
                return str(result.get("url") or "")
        except Exception:
            return None
        return None


@tool(
    "pdf_parse",
    "Parse PDF via the cloud OCR cascade (Marker/MinerU/Paddle) to get structured "
    "Markdown, images, tables, and sections. Use this after download_pdf for best results. "
    "params: path (string), output_dir? (string)",
)
class PdfParseTool(PdfToolBase):
    """Convert PDF to Markdown + structured content via the cloud parser cascade."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path, path_error = self._resolve_pdf(params, "pdf_parse")
        if path_error:
            return path_error
        assert path is not None

        try:
            output_dir = _resolve_parse_output_dir(
                params.get("output_dir"), self.artifacts_dir, path
            )
        except ValueError as e:
            return ToolResult(success=False, tool_name="pdf_parse", error=str(e))

        try:
            result: PDFParseResult = await asyncio.to_thread(
                parse_pdf, path, output_dir, config=self.config
            )
        except Exception as e:
            return ToolResult(success=False, tool_name="pdf_parse", error=str(e))

        if result.error:
            return ToolResult(success=False, tool_name="pdf_parse", error=result.error)

        # Make the explicit parse reusable by downstream PDF tools.  Without
        # this, the common ``pdf_parse -> pdf_analyze_figure`` workflow submits
        # the same document to the cloud parser twice.
        await asyncio.to_thread(
            persist_pdf_result,
            path,
            result,
            self.config,
            self.artifacts_dir,
        )

        # Read markdown content (truncated to avoid context overflow)
        markdown_content = ""
        md_path = result.markdown_path
        if md_path and Path(md_path).exists():
            raw = Path(md_path).read_text(encoding="utf-8", errors="replace")
            markdown_content = raw[:8000] + ("\n\n...[truncated]" if len(raw) > 8000 else "")

        # Build structured image info (convert to absolute paths)
        images_data = [
            {
                "path": str(Path(img.path).resolve()) if img.path else None,
                "page": img.page_idx + 1,  # Convert to 1-indexed
                "caption": img.caption,
                "figure_number": img.figure_number,
                "bbox": img.bbox,
            }
            for img in result.images
        ]

        # Build structured table info (convert to absolute paths)
        tables_data = [
            {
                "path": str(Path(table.path).resolve()) if table.path else None,
                "page": table.page_idx + 1,
                "caption": table.caption,
                "table_number": table.table_number,
                "html_body": table.html_body[:500] + "..."
                if len(table.html_body) > 500
                else table.html_body,
                "bbox": table.bbox,
            }
            for table in result.tables
        ]

        # Build sections info (top-level sections only)
        sections_data = []
        for section_key, blocks in result.sections.items():
            if ":" in section_key:
                level, title = section_key.split(":", 1)
                if level.isdigit() and int(level) <= 2:  # Only H1 and H2
                    sections_data.append(
                        {
                            "level": int(level),
                            "title": title,
                            "block_count": len(blocks),
                        }
                    )

        return ToolResult(
            success=True,
            tool_name="pdf_parse",
            data={
                "markdown": markdown_content,
                "markdown_path": md_path,
                "json_path": result.json_path,
                "images": images_data,
                "image_count": len(result.images),
                "tables": tables_data,
                "table_count": len(result.tables),
                "sections": sections_data,
                "section_count": len(sections_data),
                "output_dir": result.output_dir,
                "method": result.method,
                "backend": result.backend,
            },
        )


@tool(
    "pdf_find_images",
    "Find images in a parsed PDF by keyword in their captions. "
    "params: path (string), keyword (string), case_sensitive? (boolean)",
)
class PdfFindImagesTool(PdfToolBase):
    """Find images with captions containing a specific keyword."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("keyword"), str) or not params["keyword"].strip():
            raise ValueError("'keyword' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        keyword = params["keyword"].strip()
        case_sensitive = params.get("case_sensitive", False)

        result, error = await self._load_pdf(params, "pdf_find_images")
        if error:
            return error
        assert result is not None

        # Find matching images
        matching = find_images_by_keyword(result, keyword, case_sensitive)

        images_data = [
            {
                "path": img.path,
                "page": img.page_idx + 1,
                "caption": img.caption,
                "figure_number": img.figure_number,
                "bbox": img.bbox,
            }
            for img in matching
        ]

        return ToolResult(
            success=True,
            tool_name="pdf_find_images",
            data={
                "keyword": keyword,
                "matching_count": len(images_data),
                "images": images_data,
            },
        )


@tool(
    "pdf_find_tables",
    "Find tables in a parsed PDF by keyword in their captions. "
    "params: path (string), keyword (string), case_sensitive? (boolean)",
)
class PdfFindTablesTool(PdfToolBase):
    """Find tables with captions containing a specific keyword."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("keyword"), str) or not params["keyword"].strip():
            raise ValueError("'keyword' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        keyword = params["keyword"].strip()
        case_sensitive = params.get("case_sensitive", False)

        result, error = await self._load_pdf(params, "pdf_find_tables")
        if error:
            return error
        assert result is not None

        # Find matching tables
        matching = find_tables_by_keyword(result, keyword, case_sensitive)

        tables_data = [
            {
                "path": table.path,
                "page": table.page_idx + 1,
                "caption": table.caption,
                "table_number": table.table_number,
                "html_body": table.html_body[:1000] + "..."
                if len(table.html_body) > 1000
                else table.html_body,
                "bbox": table.bbox,
            }
            for table in matching
        ]

        return ToolResult(
            success=True,
            tool_name="pdf_find_tables",
            data={
                "keyword": keyword,
                "matching_count": len(tables_data),
                "tables": tables_data,
            },
        )


@tool(
    "pdf_find_section",
    "Find a section in a parsed PDF by its title and return its content. "
    "params: path (string), title (string), case_sensitive? (boolean)",
)
class PdfFindSectionTool(PdfToolBase):
    """Find a section by title and return its text blocks."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")
        if not isinstance(params.get("title"), str) or not params["title"].strip():
            raise ValueError("'title' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        title = params["title"].strip()
        case_sensitive = params.get("case_sensitive", False)

        result, error = await self._load_pdf(params, "pdf_find_section")
        if error:
            return error
        assert result is not None

        # Find the section
        blocks = find_section_by_title(result, title, case_sensitive)

        if blocks is None:
            return ToolResult(
                success=True,
                tool_name="pdf_find_section",
                data={
                    "title": title,
                    "found": False,
                    "content": "",
                    "message": f"Section '{title}' not found in the document.",
                },
            )

        # Extract text from blocks
        content = "\n".join(block.text for block in blocks)

        return ToolResult(
            success=True,
            tool_name="pdf_find_section",
            data={
                "title": title,
                "found": True,
                "content": content[:5000] + ("..." if len(content) > 5000 else ""),
                "block_count": len(blocks),
            },
        )


@tool(
    "pdf_content_summary",
    "Generate a structured summary of a PDF's content (images, tables, sections). "
    "params: path (string)",
)
class PdfContentSummaryTool(PdfToolBase):
    """Generate a text summary of the document structure."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result, error = await self._load_pdf(params, "pdf_content_summary")
        if error:
            return error
        assert result is not None

        # Generate summary
        summary = generate_content_summary(result)

        return ToolResult(
            success=True,
            tool_name="pdf_content_summary",
            data={
                "summary": summary,
                "image_count": len(result.images),
                "table_count": len(result.tables),
                "section_count": len(result.sections),
            },
        )


@tool("pdf_extract_text", "Extract text from PDF. params: path (string)")
class PdfExtractTextTool(PdfToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path, path_error = self._resolve_pdf(params, "pdf_extract_text")
        if path_error:
            return path_error
        assert path is not None
        try:
            text = await asyncio.to_thread(extract_text, str(path))
            return ToolResult(success=True, tool_name="pdf_extract_text", data={"text": text})
        except Exception as e:
            return ToolResult(success=False, tool_name="pdf_extract_text", error=str(e))


@tool("pdf_extract_images", "Extract images from PDF. params: path (string)")
class PdfExtractImagesTool(PdfToolBase):
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.browser = browser

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path, path_error = self._resolve_pdf(params, "pdf_extract_images")
        if path_error:
            return path_error
        assert path is not None
        out_dir = get_pdf_extract_dir(self.artifacts_dir, path) / "figures" / "extracted"
        # Ensure absolute path for consistent access
        out_dir = out_dir.resolve()
        try:
            images = await asyncio.to_thread(extract_images, str(path), out_dir)

            # Auto-open first likely figure in browser for vision analysis
            browser_url = None
            if self.browser:
                for img in images:
                    if img.get("likely_figure"):
                        result = await self.browser.open_local_file(img["path"])
                        if result.get("success"):
                            browser_url = result.get("url")
                        break

            # Convert to absolute paths for reliable access
            images_with_abs_paths = []
            for img in images:
                img_copy = dict(img)
                img_copy["path"] = str(Path(img["path"]).resolve())
                images_with_abs_paths.append(img_copy)

            return ToolResult(
                success=True,
                tool_name="pdf_extract_images",
                data={
                    "images": images_with_abs_paths,
                    "browser_url": browser_url,
                    "output_dir": str(out_dir),
                },
            )
        except Exception as e:
            return ToolResult(success=False, tool_name="pdf_extract_images", error=str(e))


@tool(
    "pdf_get_figure_info",
    "Get information about a specific figure from a PDF using its extracted caption. "
    "Use this to understand what a figure shows without needing vision analysis. "
    "params: path (string), figure_number (number)",
)
class GetFigureInfoTool(PdfToolBase):
    """Get figure caption and information from a PDF.

    Academic papers contain structured figure captions that describe
    the purpose, content, and key findings of each figure. This tool
    extracts that information directly from the PDF text.
    """

    def validate_params(self, params: dict[str, Any]) -> None:
        if "path" not in params:
            raise ValueError("'path' required - PDF file path")
        try:
            figure_num = int(params.get("figure_number", 1))
            if figure_num < 1:
                raise ValueError("figure_number must be >= 1")
        except (ValueError, TypeError) as err:
            raise ValueError("figure_number must be a positive integer") from err

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path, path_error = self._resolve_pdf(params, "pdf_get_figure_info")
        if path_error:
            return path_error
        assert path is not None

        # validate_params defaults a missing figure_number to 1; mirror that here
        # instead of a KeyError on direct subscript.
        figure_number = int(params.get("figure_number", 1))

        try:
            # Reuse the shared structured parse so a subsequent PDF tool does
            # not submit the same document to the cloud parser again.
            result, parse_error = await load_pdf_result(
                path,
                self.artifacts_dir,
                "pdf_get_figure_info",
                config=self.config,
            )
            if parse_error:
                return parse_error
            assert result is not None

            if result.images:
                # Find the figure by number
                for img in result.images:
                    if img.figure_number == str(figure_number):
                        return ToolResult(
                            success=True,
                            tool_name="pdf_get_figure_info",
                            data={
                                "path": str(path),
                                "figure_number": figure_number,
                                "caption": img.caption,
                                "image_path": img.path,
                                "page": img.page_idx + 1,
                                "found": True,
                            },
                        )

            # Fallback to regex-based extraction
            captions = await asyncio.to_thread(extract_figure_captions, str(path))
            figure_key = f"figure {figure_number}"

            if figure_key in captions:
                caption = captions[figure_key]
                return ToolResult(
                    success=True,
                    tool_name="pdf_get_figure_info",
                    data={
                        "path": str(path),
                        "figure_number": figure_number,
                        "caption": caption,
                        "found": True,
                    },
                )
            # Try to find any figure mention in the text
            text = await asyncio.to_thread(extract_text, str(path))
            # Look for any mention of the figure
            import re

            patterns = [
                rf"Figure\s+{figure_number}\s*:",
                rf"Fig\.?\s+{figure_number}\s*\.",
                rf"Figure\s+{figure_number}\s+",
            ]
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    # Found a mention but couldn't extract full caption
                    return ToolResult(
                        success=True,
                        tool_name="pdf_get_figure_info",
                        data={
                            "path": str(path),
                            "figure_number": figure_number,
                            "caption": f"Figure {figure_number} is mentioned in the document but the caption could not be extracted. Check the PDF text directly.",
                            "found": False,
                            "mentioned": True,
                        },
                    )

            return ToolResult(
                success=True,
                tool_name="pdf_get_figure_info",
                data={
                    "path": str(path),
                    "figure_number": figure_number,
                    "caption": f"Figure {figure_number} not found in the document. The PDF may have a different figure numbering scheme or the figure may not exist.",
                    "found": False,
                },
            )
        except Exception as e:
            return ToolResult(success=False, tool_name="pdf_get_figure_info", error=str(e))
