"""Shared path resolution, parse caching, and base class for PDF tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import weakref
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.parser import ImageInfo, PDFParseResult, TableInfo, TextBlock, parse_pdf
from webagent.utils.paths import get_artifacts_dir, get_pdf_extract_dir, resolve_pdf_path

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


class PdfResultCache(dict[str, PDFParseResult]):
    """Cache successful cloud parses while excluding degraded local fallbacks."""

    _DEGRADED_BACKENDS = frozenset({"pymupdf", "local"})

    def __setitem__(self, key: str, value: PDFParseResult) -> None:
        if value.error or value.backend in self._DEGRADED_BACKENDS:
            return
        super().__setitem__(key, value)


pdf_result_cache = PdfResultCache()
_pdf_parse_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_PERSISTENT_CACHE_VERSION = 1


def pdf_cache_key(path: Path, artifacts_dir: Path | None = None) -> str:
    """Return a content fingerprint, optionally scoped to one artifact root.

    The unscoped form is used only for the portable persistent-cache entry.  In-memory
    parse results contain absolute artifact paths, so their keys must include
    ``artifacts_dir`` and must never be shared directly between runs.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    fingerprint = digest.hexdigest()
    if artifacts_dir is None:
        return fingerprint
    return _memory_cache_key(fingerprint, artifacts_dir)


def get_cached_pdf_result(path: Path, artifacts_dir: Path | None = None) -> PDFParseResult | None:
    """Return a run-scoped cached parse result.

    An artifact root is required for a safe lookup.  The optional argument preserves
    source compatibility for callers that only probe the cache, but deliberately
    returns no result rather than exposing an object whose paths may belong to a
    different run.
    """
    if artifacts_dir is None:
        return None
    return pdf_result_cache.get(pdf_cache_key(path, artifacts_dir))


def resolve_pdf_input(
    path_str: str,
    artifacts_dir: Path,
    tool_name: str,
) -> tuple[Path | None, ToolResult | None]:
    """Resolve a PDF path and reject reads outside the current output root."""
    path, _was_fallback, error = resolve_pdf_path(path_str, artifacts_dir, use_fallback=False)
    if error:
        return None, ToolResult(success=False, tool_name=tool_name, error=error)
    assert path is not None
    try:
        header = path.read_bytes()[:1024]
    except OSError as exc:
        return None, ToolResult(success=False, tool_name=tool_name, error=f"Cannot read PDF: {exc}")
    if b"%PDF-" not in header:
        return None, ToolResult(
            success=False,
            tool_name=tool_name,
            error="File content is not a PDF (missing %PDF header)",
        )
    return path, None


async def load_pdf_result(
    path: Path,
    artifacts_dir: Path,
    tool_name: str,
    *,
    config: AgentConfig | None = None,
) -> tuple[PDFParseResult | None, ToolResult | None]:
    """Return a cached parse or parse once and normalize failures for a tool."""
    fingerprint = pdf_cache_key(path)
    key = _memory_cache_key(fingerprint, artifacts_dir)
    result = pdf_result_cache.get(key)
    if result is not None:
        return result, None

    lock = _pdf_parse_locks.setdefault(key, asyncio.Lock())
    async with lock:
        result = pdf_result_cache.get(key)
        if result is not None:
            return result, None
        output_dir = get_pdf_extract_dir(
            artifacts_dir,
            path,
            content_sha256=fingerprint,
        )
        if _persistent_cache_enabled(config):
            assert config is not None
            result = await asyncio.to_thread(
                _load_persistent_result, fingerprint, output_dir, config
            )
            if result is not None:
                pdf_result_cache[key] = result
                return result, None
        try:
            result = await asyncio.to_thread(
                parse_pdf,
                path,
                output_dir,
                config=config,
            )
        except Exception as exc:
            return None, ToolResult(
                success=False,
                tool_name=tool_name,
                error=f"Failed to parse PDF: {exc}",
            )
        pdf_result_cache[key] = result
        if not result.error and _persistent_cache_enabled(config):
            assert config is not None
            await asyncio.to_thread(_persist_result, fingerprint, result, config)

    if result.error:
        return None, ToolResult(success=False, tool_name=tool_name, error=result.error)
    return result, None


def persist_pdf_result(
    path: Path,
    result: PDFParseResult,
    config: AgentConfig | None = None,
    artifacts_dir: Path | None = None,
) -> None:
    """Seed memory and optional disk caches after an explicit ``pdf_parse`` call."""
    fingerprint = pdf_cache_key(path)
    scope = artifacts_dir if artifacts_dir is not None else path.parent
    key = _memory_cache_key(fingerprint, scope)
    pdf_result_cache[key] = result
    if not result.error and _persistent_cache_enabled(config):
        assert config is not None
        _persist_result(fingerprint, result, config)


def _memory_cache_key(
    fingerprint: str,
    artifacts_dir: Path,
) -> str:
    """Scope memory reuse to one run's artifact root in every execution mode."""
    return f"run:{artifacts_dir.resolve()}:{fingerprint}"


def _persistent_cache_enabled(config: AgentConfig | None) -> bool:
    return bool(
        config is not None
        and config.persistent_pdf_cache
        and not config.strict_eval_mode
        and config.pdf_cache_dir
    )


def _persist_result(key: str, result: PDFParseResult, config: AgentConfig) -> None:
    if result.backend in PdfResultCache._DEGRADED_BACKENDS:
        return
    source_root = Path(result.output_dir).resolve()
    if not source_root.is_dir():
        return
    manifest = _result_manifest(result, source_root)
    if manifest is None:
        return
    cache_root = config.pdf_cache_dir
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / key
    if (target / "manifest.json").is_file():
        return
    temporary = Path(tempfile.mkdtemp(prefix=f".{key[:12]}-", dir=cache_root))
    try:
        shutil.copytree(source_root, temporary / "files", dirs_exist_ok=True)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            temporary.replace(target)
        except OSError:
            if not target.exists():
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _load_persistent_result(
    key: str, output_dir: Path, config: AgentConfig
) -> PDFParseResult | None:
    entry = config.pdf_cache_dir / key
    manifest_path = entry / "manifest.json"
    files = entry / "files"
    if not manifest_path.is_file() or not files.is_dir():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != _PERSISTENT_CACHE_VERSION:
            return None
        result = _result_from_manifest(manifest, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(files, output_dir, dirs_exist_ok=True)
        return result
    except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _result_manifest(result: PDFParseResult, root: Path) -> dict[str, Any] | None:
    def relative(value: str | None) -> str | None:
        if value is None or value == "":
            return value
        path = Path(value).resolve()
        if path != root and not path.is_relative_to(root):
            raise ValueError("parse output escaped output directory")
        return str(path.relative_to(root)) if path != root else "."

    try:
        return {
            "version": _PERSISTENT_CACHE_VERSION,
            "method": result.method,
            "backend": result.backend,
            "markdown_path": relative(result.markdown_path),
            "json_path": relative(result.json_path),
            "images_dir": relative(result.images_dir),
            "images": [{**asdict(item), "path": relative(item.path)} for item in result.images],
            "tables": [{**asdict(item), "path": relative(item.path)} for item in result.tables],
            "text_blocks": [asdict(item) for item in result.text_blocks],
            "sections": {
                key: [asdict(item) for item in blocks] for key, blocks in result.sections.items()
            },
        }
    except ValueError:
        return None


def _result_from_manifest(manifest: dict[str, Any], root: Path) -> PDFParseResult:
    def absolute(value: str | None) -> str | None:
        if value is None or value == "":
            return value
        resolved = (root / value).resolve()
        safe_root = root.resolve()
        if resolved != safe_root and not resolved.is_relative_to(safe_root):
            raise ValueError("cached parse path escaped output directory")
        return str(resolved)

    result = PDFParseResult(
        markdown_path=absolute(manifest.get("markdown_path")),
        json_path=absolute(manifest.get("json_path")),
        images_dir=absolute(manifest.get("images_dir")) or str(root),
        output_dir=str(root),
        method=str(manifest.get("method") or "cascade"),
        backend=manifest.get("backend"),
    )
    result.images = [
        ImageInfo(**{**item, "path": absolute(item.get("path")) or "", "bbox": tuple(item["bbox"])})
        for item in manifest.get("images", [])
    ]
    result.tables = [
        TableInfo(**{**item, "path": absolute(item.get("path")) or "", "bbox": tuple(item["bbox"])})
        for item in manifest.get("tables", [])
    ]
    result.text_blocks = [
        TextBlock(**{**item, "bbox": tuple(item["bbox"])})
        for item in manifest.get("text_blocks", [])
    ]
    result.sections = {
        key: [TextBlock(**{**item, "bbox": tuple(item["bbox"])}) for item in blocks]
        for key, blocks in manifest.get("sections", {}).items()
    }
    return result


class PdfToolBase:
    """Shared construction and PDF-loading boilerplate for PDF tools.

    Extra keyword arguments (e.g. ``browser``, ``planner``) are ignored here so
    subclasses that need them can override ``__init__`` and forward the rest via
    ``super().__init__(**kw)``.
    """

    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)
        self.config = config

    def _resolve_pdf(
        self, params: dict[str, Any], tool_name: str
    ) -> tuple[Path | None, ToolResult | None]:
        """Resolve the ``path`` param to a safe on-disk path -> (path, error)."""
        return resolve_pdf_input(params["path"].strip(), self.artifacts_dir, tool_name)

    async def _load_pdf(
        self, params: dict[str, Any], tool_name: str
    ) -> tuple[PDFParseResult | None, ToolResult | None]:
        """Resolve the ``path`` param and parse the PDF -> (result, error)."""
        path, error = self._resolve_pdf(params, tool_name)
        if error:
            return None, error
        assert path is not None
        return await load_pdf_result(path, self.artifacts_dir, tool_name, config=self.config)


__all__ = [
    "PdfResultCache",
    "PdfToolBase",
    "get_cached_pdf_result",
    "load_pdf_result",
    "pdf_cache_key",
    "pdf_result_cache",
    "persist_pdf_result",
    "resolve_pdf_input",
]
