"""Release-state and archive-content checks."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from webagent.release import (
    check_release_artifacts,
    check_release_state,
    check_tracked_paths,
    compare_release_artifacts,
    main,
    project_name,
    project_version,
)

_SCHEMA_FILENAMES = (
    "run-manifest-v1.schema.json",
    "run-trace-v8.schema.json",
    "study-manifest-v1.schema.json",
    "study-run-record-v1.schema.json",
)
_BENCHMARK_MANIFESTS = (
    "open_web_general.json",
    "open_web_smoke.json",
    "qwen_strict_search.json",
)


def _schema_bytes(filename: str) -> bytes:
    return (Path(__file__).parents[2] / "src" / "webagent" / "schemas" / filename).read_bytes()


def _write_valid_release_artifacts(
    directory: Path,
    *,
    schema_overrides: dict[str, bytes] | None = None,
) -> None:
    overrides = schema_overrides or {}
    wheel = directory / "lixiuyin_webagent-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr("webagent/py.typed", "")
        archive.writestr("benchmarks/README.md", "benchmark docs")
        for filename in _BENCHMARK_MANIFESTS:
            archive.writestr(f"benchmarks/manifests/{filename}", "{}")
        for filename in _SCHEMA_FILENAMES:
            archive.writestr(
                f"webagent/schemas/{filename}",
                overrides.get(filename, _schema_bytes(filename)),
            )
        archive.writestr(
            "lixiuyin_webagent-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: lixiuyin-webagent\nVersion: 1.2.3\n",
        )

    sdist = directory / "lixiuyin_webagent-1.2.3.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        members = {
            "lixiuyin_webagent-1.2.3/PKG-INFO": (
                b"Metadata-Version: 2.4\nName: lixiuyin-webagent\nVersion: 1.2.3\n"
            ),
            "lixiuyin_webagent-1.2.3/LICENSE": b"content",
            "lixiuyin_webagent-1.2.3/README.md": b"content",
            "lixiuyin_webagent-1.2.3/pyproject.toml": b"content",
            "lixiuyin_webagent-1.2.3/src/webagent/py.typed": b"content",
            "lixiuyin_webagent-1.2.3/benchmarks/README.md": b"content",
            **{
                f"lixiuyin_webagent-1.2.3/benchmarks/manifests/{filename}": b"{}"
                for filename in _BENCHMARK_MANIFESTS
            },
            **{
                f"lixiuyin_webagent-1.2.3/src/webagent/schemas/{filename}": overrides.get(
                    filename, _schema_bytes(filename)
                )
                for filename in _SCHEMA_FILENAMES
            },
        }
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_tracked_path_check_rejects_generated_and_secret_files() -> None:
    failures = check_tracked_paths(
        [
            "src/webagent/cli.py",
            "outputs/artifacts/run.json",
            "browser_profile/SingletonLock",
            "uploads/private-input.csv",
            ".env",
            ".env.production",
            ".env.example",
        ]
    )

    assert len(failures) == 5
    assert any("outputs" in failure for failure in failures)
    assert any("browser_profile" in failure for failure in failures)
    assert any("uploads" in failure for failure in failures)
    assert any(".env" in failure for failure in failures)


def test_project_version_requires_a_static_nonempty_value(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")
    assert project_version(pyproject) == "1.2.3"

    pyproject.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"project\.version"):
        project_version(pyproject)


def test_project_name_requires_a_static_nonempty_value(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "lixiuyin-webagent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    assert project_name(pyproject) == "lixiuyin-webagent"

    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"project\.name"):
        project_name(pyproject)


def test_release_state_checks_tag_changelog_git_paths_and_cleanliness(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lixiuyin-webagent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [1.2.3] - 2026-08-30\n",
        encoding="utf-8",
    )
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(("git", "add", "safe.txt"), cwd=tmp_path, check=True)

    assert check_release_state(tmp_path, tag="v1.2.3") == []
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other-project"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    failures = check_release_state(tmp_path, tag="v1.2.3")
    assert any("distribution name" in failure for failure in failures)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lixiuyin-webagent"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    failures = check_release_state(tmp_path, tag="v9.9.9", require_clean=True)
    assert any("does not match" in failure for failure in failures)
    assert "working tree is not clean" in failures

    failures = check_release_state(tmp_path, tag="1.2.3")
    assert "release tag must use a v prefix: '1.2.3'" in failures

    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\nnew feature\n\n## [1.2.3] - 2026-08-30\n",
        encoding="utf-8",
    )
    failures = check_release_state(tmp_path, tag="v1.2.3")
    assert "CHANGELOG.md Unreleased section must be empty for a tagged release" in failures

    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
    failures = check_release_state(tmp_path)
    assert "CHANGELOG.md has no dated release section for 1.2.3" in failures


def test_release_artifact_check_accepts_typed_schema_package(tmp_path: Path) -> None:
    _write_valid_release_artifacts(tmp_path)

    assert check_release_artifacts(tmp_path) == []


def test_release_artifact_check_rejects_malformed_or_misidentified_schema(
    tmp_path: Path,
) -> None:
    malformed = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/wrong-study-schema.json",
            "type": 17,
        }
    ).encode()
    _write_valid_release_artifacts(
        tmp_path,
        schema_overrides={"study-manifest-v1.schema.json": malformed},
    )

    failures = check_release_artifacts(tmp_path)

    assert any("study-manifest-v1" in failure for failure in failures)
    assert any("unexpected $id" in failure for failure in failures)
    assert any("not a valid Draft 2020-12 schema" in failure for failure in failures)


def test_release_artifact_check_rejects_local_workspace_content(tmp_path: Path) -> None:
    wheel = tmp_path / "lixiuyin_webagent-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(".obsidian/workspace.json", "{}")
    with tarfile.open(tmp_path / "lixiuyin_webagent-1.2.3.tar.gz", mode="w:gz") as archive:
        payload = b"content"
        info = tarfile.TarInfo("lixiuyin_webagent-1.2.3/LICENSE")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    failures = check_release_artifacts(tmp_path)

    assert any(".obsidian" in failure for failure in failures)


def test_release_artifact_check_reports_counts_version_and_metadata(tmp_path: Path) -> None:
    assert check_release_artifacts(tmp_path) == [
        "expected exactly one wheel, found 0",
        "expected exactly one sdist, found 0",
    ]

    wheel = tmp_path / "lixiuyin_webagent-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr("webagent/py.typed", "")
        archive.writestr("webagent/schemas/run-trace-v8.schema.json", "{}")
        archive.writestr(
            "lixiuyin_webagent-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: wrong-project\nVersion: 9.9.9\n",
        )
    with tarfile.open(tmp_path / "lixiuyin_webagent-2.0.0.tar.gz", mode="w:gz") as archive:
        payload = b"secret"
        for name in ("lixiuyin_webagent-2.0.0/.env", "../escape"):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("lixiuyin_webagent-2.0.0/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        archive.addfile(link)

    failures = check_release_artifacts(tmp_path)
    assert "wheel METADATA version does not match its filename" in failures
    assert "wheel METADATA name does not match the release distribution" in failures
    assert any("wheel/sdist versions differ" in failure for failure in failures)
    assert "sdist contains a symbolic or hard link" in failures
    assert any("unsafe path" in failure for failure in failures)
    assert any("secret environment" in failure for failure in failures)


def test_release_artifact_check_rejects_wrong_distribution_identity(tmp_path: Path) -> None:
    wheel = tmp_path / "other_project-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr("webagent/py.typed", "")
        archive.writestr("webagent/schemas/run-trace-v8.schema.json", "{}")
        archive.writestr(
            "other_project-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: other-project\nVersion: 1.2.3\n",
        )
    sdist = tmp_path / "other_project-1.2.3.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        members = {
            "other_project-1.2.3/PKG-INFO": (
                b"Metadata-Version: 2.4\nName: other-project\nVersion: 1.2.3\n"
            ),
            "other_project-1.2.3/LICENSE": b"content",
            "other_project-1.2.3/README.md": b"content",
            "other_project-1.2.3/pyproject.toml": b"content",
            "other_project-1.2.3/src/webagent/py.typed": b"content",
            "other_project-1.2.3/src/webagent/schemas/run-trace-v8.schema.json": b"content",
        }
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    failures = check_release_artifacts(tmp_path)

    assert any("wheel distribution name" in failure for failure in failures)
    assert any("sdist distribution name" in failure for failure in failures)
    assert any("wheel METADATA name" in failure for failure in failures)
    assert any("sdist PKG-INFO name" in failure for failure in failures)


def test_reproducibility_check_compares_names_and_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "demo-1.0.tar.gz").write_bytes(b"same")
    (second / "demo-1.0.tar.gz").write_bytes(b"same")
    assert compare_release_artifacts(first, second) == []

    (second / "demo-1.0.tar.gz").write_bytes(b"changed")
    assert compare_release_artifacts(first, second) == [
        "artifact is not reproducible: demo-1.0.tar.gz"
    ]

    (second / "renamed-1.0.whl").write_bytes(b"new")
    assert compare_release_artifacts(first, second) == [
        "artifact filenames differ between reproducibility builds"
    ]


def test_release_cli_reports_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "demo-1.0.whl").write_bytes(b"same")
    (second / "demo-1.0.whl").write_bytes(b"same")
    monkeypatch.setattr(sys, "argv", ["webagent.release", "repro", str(first), str(second)])
    with pytest.raises(SystemExit, match="0"):
        main()
    assert "release check passed" in capsys.readouterr().out

    (second / "demo-1.0.whl").write_bytes(b"different")
    with pytest.raises(SystemExit, match="1"):
        main()
    assert "release check failed" in capsys.readouterr().out
