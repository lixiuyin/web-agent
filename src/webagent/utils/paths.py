"""Path utilities for consistent artifact and output directory handling.

This module provides centralized path resolution that follows Python packaging best practices:
- Uses configuration from AgentConfig
- Resolves to absolute paths
- Supports environment variable overrides
- Avoids hardcoded paths in tool implementations
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


def get_artifacts_dir(config: AgentConfig | Path | None = None) -> Path:
    """Get the artifacts directory.

    Args:
        config: AgentConfig instance, or a Path to use directly, or None for default

    Returns:
        Resolved absolute path to artifacts directory

    Examples:
        >>> from webagent.core.config import AgentConfig
        >>> get_artifacts_dir(AgentConfig())
        PosixPath('/path/to/outputs/artifacts')

        >>> get_artifacts_dir(Path("./custom/artifacts"))
        PosixPath('/absolute/path/to/custom/artifacts')
    """
    if config is None:
        # Default: use AgentConfig with defaults
        from webagent.core.config import AgentConfig

        config = AgentConfig()

    if isinstance(config, Path):
        return config.resolve()

    # It's an AgentConfig instance
    return config.artifacts_dir.resolve()


def get_pdf_extract_dir(artifacts_dir: Path) -> Path:
    """Canonical directory for a PDF's API-extracted content.

    All cloud-OCR output (``parsed.md``, ``parsed_content_list.json`` and the
    ``images/`` figures) is written here so artifacts stay organized: the
    downloaded ``*.pdf`` lives at the artifacts root while everything the parser
    cascade produces is grouped under a single ``pdf/`` subdirectory.
    """
    return artifacts_dir / "pdf"


def get_output_dir(config: AgentConfig | Path | None = None) -> Path:
    """Get the base output directory.

    Args:
        config: AgentConfig instance, or a Path to use directly, or None for default

    Returns:
        Resolved absolute path to output directory
    """
    if config is None:
        from webagent.core.config import AgentConfig

        config = AgentConfig()

    if isinstance(config, Path):
        return config.resolve()

    return config.output_dir.resolve()


def resolve_file_path(
    path_str: str,
    artifacts_dir: Path | None = None,
    config: AgentConfig | None = None,
) -> Path:
    """Resolve a file path with multiple fallback strategies, then enforce containment.

    Priority:
    1. If absolute, use as-is
    2. If relative and exists in current dir, use it
    3. Try artifacts_dir / path_str
    4. Try common subdirectories (pdf_images, images)
    5. Fall back to artifacts_dir / path_str (will fail later if not found)

    The resolved path MUST stay under the output root (the parent of
    ``artifacts_dir``).  Tool inputs are partly LLM/page-controlled, and
    ``read_image`` returns file bytes into the model context, so a traversal or
    absolute-path injection here is an arbitrary-file-read primitive.  Raises
    ``ValueError`` on escape (callers convert it to a ToolResult error).

    Returns:
        Resolved absolute Path contained within the output root.
    """
    if artifacts_dir is None:
        artifacts_dir = get_artifacts_dir(config)
    artifacts_dir = artifacts_dir.resolve()
    output_root = artifacts_dir.parent

    path_str = path_str.strip()
    path = Path(path_str)

    resolved = _pick_candidate(path, artifacts_dir).resolve()

    if resolved != output_root and not resolved.is_relative_to(output_root):
        raise ValueError(f"path escapes the output directory: {path_str!r}")
    return resolved


def _pick_candidate(path: Path, artifacts_dir: Path) -> Path:
    """Apply the resolution strategies and return the chosen (unvalidated) path."""
    if path.is_absolute():
        return path
    if path.exists():
        return path
    candidate = artifacts_dir / path
    if candidate.exists():
        return candidate
    # Parser figures now live under pdf/images; keep the legacy locations too.
    for subdir in ("pdf/images", "pdf_images", "images"):
        candidate = artifacts_dir / subdir / path.name
        if candidate.exists():
            return candidate
    return artifacts_dir / path


def resolve_pdf_path(
    path_str: str,
    artifacts_dir: Path,
    use_fallback: bool = True,
) -> tuple[Path, bool, str | None]:
    """Resolve a PDF path with intelligent fallback to the most recent PDF.

    Args:
        path_str: User-provided path string (may be relative, absolute, or a URL)
        artifacts_dir: Artifacts directory for relative path resolution
        use_fallback: If True and path doesn't exist, fall back to most recent PDF

    Returns:
        Tuple of (resolved_path, was_fallback_used, warning_or_error_message)
    """
    artifacts_dir = artifacts_dir.resolve()
    output_root = artifacts_dir.parent

    if path_str.startswith(("http://", "https://")):
        return (
            Path(path_str),
            False,
            "'path' must be a local file path, not a URL. "
            "Use download_pdf to save the PDF locally first, then pass the returned 'path'.",
        )

    path = _to_artifacts_path(path_str, artifacts_dir)
    resolved_path = path.resolve()

    if resolved_path != output_root and not resolved_path.is_relative_to(output_root):
        return (
            resolved_path,
            False,
            f"path escapes the output directory: {path_str!r}",
        )
    path = resolved_path

    if path.exists():
        return path, False, None

    if not use_fallback:
        return path, False, f"PDF not found: {path}"

    fallback = _find_most_recent_pdf(artifacts_dir, excluded_path=path)
    if fallback:
        return fallback, True, f"Using most recent PDF: {fallback.name}"
    return path, False, f"PDF not found: {path}. No other PDFs found in {artifacts_dir}"


def _to_artifacts_path(path_str: str, artifacts_dir: Path) -> Path:
    """Resolve relative PDF paths deterministically against the artifacts tree.

    A relative path that already exists under the current output root is kept
    as-is, which preserves paths returned by tools (for example
    ``outputs/artifacts/paper.pdf``). Other relative paths are treated as
    artifacts-relative, including nested paths like ``papers/foo.pdf``.
    """
    path = Path(path_str.strip())
    if path.is_absolute():
        return path

    output_root = artifacts_dir.resolve().parent
    cwd_path = path.resolve()
    if cwd_path.exists() and (cwd_path == output_root or cwd_path.is_relative_to(output_root)):
        return cwd_path
    return artifacts_dir / path


def _find_most_recent_pdf(artifacts_dir: Path, excluded_path: Path | None = None) -> Path | None:
    """Find the most recently modified PDF in the artifacts directory."""
    if not artifacts_dir.exists():
        return None

    pdf_files = []
    for pdf_path in artifacts_dir.glob("*.pdf"):
        if excluded_path and pdf_path.resolve() == excluded_path.resolve():
            continue
        if pdf_path.is_file():
            try:
                pdf_files.append((pdf_path, pdf_path.stat().st_mtime))
            except OSError:
                continue

    if not pdf_files:
        return None
    pdf_files.sort(key=lambda x: x[1], reverse=True)
    return pdf_files[0][0]
