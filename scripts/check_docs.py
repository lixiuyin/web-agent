"""Validate repository Markdown structure and local references."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
EXCLUDED_TOP_LEVEL = {"outputs", "browser_profile", "uploads"}
EXCLUDED_PART_PREFIXES = (".venv", "build-", "dist-")
README_SECTION_PAIRS = (
    ("What is WebAgent?", "WebAgent 是什么？"),
    ("Highlights", "技术亮点"),
    ("Architecture", "架构"),
    ("Quick start", "快速开始"),
    ("Recorded effect showcase", "已记录的效果展示"),
    ("Evaluation status", "评测状态"),
    ("Documentation", "文档导航"),
    ("Development", "开发"),
    ("Authors and project history", "作者与项目沿革"),
    ("Acknowledgements", "致谢"),
    ("License", "许可证"),
)


def _is_excluded(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return bool(parts) and (
        parts[0] in EXCLUDED_TOP_LEVEL
        or any(parts[0].startswith(prefix) for prefix in EXCLUDED_PART_PREFIXES)
    )


def _markdown_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.md",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    candidates: Iterable[Path]
    if result.returncode == 0:
        candidates = (ROOT / item for item in result.stdout.splitlines())
    else:
        candidates = ROOT.rglob("*.md")
    return sorted(path for path in candidates if path.is_file() and not _is_excluded(path))


@cache
def _tracked_paths(root: Path) -> frozenset[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return frozenset()
    return frozenset(item for item in result.stdout.split("\0") if item)


def _exists_or_tracked(path: Path) -> bool:
    if path.exists():
        return True
    try:
        relative = path.relative_to(ROOT).as_posix().rstrip("/")
    except ValueError:
        return False
    tracked = _tracked_paths(ROOT)
    prefix = f"{relative}/"
    return relative in tracked or any(item.startswith(prefix) for item in tracked)


def _link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    return target.split(maxsplit=1)[0]


def _is_external(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:"))


def _table_width(line: str) -> int:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith(r"\|"):
        body = body[:-1]
    return len(re.split(r"(?<!\\)\|", body))


def _check_file(path: Path) -> list[str]:
    relative = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    problems: list[str] = []
    headings: list[tuple[int, int]] = []
    in_fence = False
    fence_char = ""
    blank_run = 0
    table_width: int | None = None

    for line_number, line in enumerate(lines, start=1):
        blank_run, whitespace_problems = _check_line_whitespace(
            relative, line_number, line, blank_run
        )
        problems.extend(whitespace_problems)

        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_char = marker
            elif marker == fence_char:
                in_fence = False
                fence_char = ""
            table_width = None
            continue

        if in_fence:
            continue

        if re.search(r"\bCaptcha\b", line):
            problems.append(
                f"{relative}:{line_number}: use CAPTCHA in prose or the exact code identifier"
            )

        heading = HEADING_RE.match(line)
        if heading:
            headings.append((line_number, len(heading.group(1))))

        problems.extend(_check_line_links(path, relative, line_number, line))

        table_width, table_problems = _check_table_row(relative, line_number, line, table_width)
        problems.extend(table_problems)

    if in_fence:
        problems.append(f"{relative}: unterminated code fence")

    problems.extend(_check_heading_structure(relative, headings))

    return problems


def _check_line_whitespace(
    relative: Path, line_number: int, line: str, blank_run: int
) -> tuple[int, list[str]]:
    problems: list[str] = []
    if line.endswith((" ", "\t")):
        problems.append(f"{relative}:{line_number}: trailing whitespace")
    if "\t" in line:
        problems.append(f"{relative}:{line_number}: tab character")

    if not line:
        blank_run += 1
        if blank_run > 2:
            problems.append(f"{relative}:{line_number}: more than two blank lines")
    else:
        blank_run = 0

    return blank_run, problems


def _check_line_links(path: Path, relative: Path, line_number: int, line: str) -> list[str]:
    problems: list[str] = []
    for alt, raw_target in IMAGE_RE.findall(line):
        if not alt.strip():
            problems.append(f"{relative}:{line_number}: image has empty alt text")
        target = _link_target(raw_target)
        if target and not target.startswith("#") and not _is_external(target):
            local_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
            resolved = (path.parent / local_part).resolve()
            if local_part and not _exists_or_tracked(resolved):
                problems.append(
                    f"{relative}:{line_number}: missing local image target {local_part!r}"
                )

    for _label, raw_target in LINK_RE.findall(line):
        target = _link_target(raw_target)
        if not target or target.startswith("#") or _is_external(target):
            continue
        local_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not local_part:
            continue
        resolved = (path.parent / local_part).resolve()
        if not _exists_or_tracked(resolved):
            problems.append(f"{relative}:{line_number}: missing local link target {local_part!r}")

    return problems


def _check_heading_structure(relative: Path, headings: list[tuple[int, int]]) -> list[str]:
    problems: list[str] = []
    h1_count = sum(level == 1 for _line, level in headings)
    if h1_count != 1:
        problems.append(f"{relative}: expected exactly one H1; found {h1_count}")

    previous_level = 0
    for line_number, level in headings:
        if previous_level and level > previous_level + 1:
            problems.append(
                f"{relative}:{line_number}: heading jumps from H{previous_level} to H{level}"
            )
        previous_level = level

    return problems


def _check_table_row(
    relative: Path, line_number: int, line: str, table_width: int | None
) -> tuple[int | None, list[str]]:
    problems: list[str] = []
    if line.lstrip().startswith("|"):
        width = _table_width(line)
        if table_width is None:
            table_width = width
        elif width != table_width:
            problems.append(
                f"{relative}:{line_number}: table has {width} columns; expected {table_width}"
            )
    else:
        table_width = None

    return table_width, problems


def _readme_link_targets(text: str) -> set[str]:
    """Return factual link targets, excluding language switches and badge wrappers."""
    targets = {_link_target(raw_target) for _label, raw_target in LINK_RE.findall(text)}
    return {
        target
        for target in targets
        if target
        and target not in {"README.md", "README.en.md", "README.zh-CN.md"}
        and not (target.startswith("https://img.shields.io/") or target.endswith("badge.svg"))
    }


def _table_row_counts(text: str) -> list[int]:
    counts: list[int] = []
    current = 0
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            current += 1
        elif current:
            counts.append(max(0, current - 2))
            current = 0
    return counts


def _check_root_readme_parity() -> list[str]:
    """Keep the two audience-equivalent landing pages structurally synchronized."""
    english_path = ROOT / "README.en.md"
    chinese_path = ROOT / "README.md"
    if not english_path.is_file() or not chinese_path.is_file():
        return ["root README bilingual pair is incomplete"]

    english = english_path.read_text(encoding="utf-8")
    chinese = chinese_path.read_text(encoding="utf-8")
    problems: list[str] = []
    for english_heading, chinese_heading in README_SECTION_PAIRS:
        if f"## {english_heading}" not in english:
            problems.append(f"README.en.md: missing bilingual section {english_heading!r}")
        if f"## {chinese_heading}" not in chinese:
            problems.append(f"README.md: missing bilingual section {chinese_heading!r}")

    if _table_row_counts(english) != _table_row_counts(chinese):
        problems.append("root READMEs have different table row structures")
    if _readme_link_targets(english) != _readme_link_targets(chinese):
        problems.append("root READMEs have different factual link targets")
    return problems


def main() -> int:
    files = _markdown_files()
    problems = [problem for path in files for problem in _check_file(path)]
    problems.extend(_check_root_readme_parity())
    if problems:
        print("Documentation check failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Documentation check passed: {len(files)} Markdown files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
