from pathlib import Path

import pytest
from scripts import check_docs


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_documentation_checker_accepts_valid_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    _write(tmp_path, "target.md", "# Target\n")
    source = _write(
        tmp_path,
        "source.md",
        "# Source\n\n## Section\n\n[Target](target.md)\n\n![Diagram](image.png)\n",
    )
    _write(tmp_path, "image.png", "placeholder")

    assert check_docs._check_file(source) == []


def test_documentation_checker_reports_structure_and_link_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    source = _write(
        tmp_path,
        "broken.md",
        "# Title\n\n### Jumped\n\n[Missing](absent.md)\n\n![](absent.png)\n",
    )

    problems = check_docs._check_file(source)

    assert any("heading jumps" in problem for problem in problems)
    assert any("missing local link target" in problem for problem in problems)
    assert any("image has empty alt text" in problem for problem in problems)


def test_documentation_checker_ignores_headings_and_links_inside_fences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    source = _write(
        tmp_path,
        "fenced.md",
        "# Title\n\n```markdown\n### Not a heading\n[Missing](absent.md)\n```\n",
    )

    assert check_docs._check_file(source) == []


def test_documentation_checker_reports_table_and_terminology_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    source = _write(
        tmp_path,
        "style.md",
        "# Title\n\nCaptcha prose.\n\n| A | B |\n|---|---|\n| one | two | three |\n",
    )

    problems = check_docs._check_file(source)

    assert any("use CAPTCHA" in problem for problem in problems)
    assert any("table has 3 columns; expected 2" in problem for problem in problems)


def test_documentation_checker_accepts_sparse_tracked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    monkeypatch.setattr(
        check_docs,
        "_tracked_paths",
        lambda _root: frozenset({"outputs/published-run/trace.json"}),
    )
    source = _write(
        tmp_path,
        "source.md",
        "# Source\n\n[Published evidence](outputs/published-run/)\n",
    )

    assert check_docs._check_file(source) == []
