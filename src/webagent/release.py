"""Release-state and distribution-artifact validation utilities."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

_FORBIDDEN_ROOTS = frozenset(
    {
        ".mypy_cache",
        ".obsidian",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "browser_profile",
        "build",
        "dist",
        "outputs",
        "uploads",
        "venv",
    }
)
_DISTRIBUTION_NAME = "lixiuyin-webagent"
_JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_REQUIRED_SCHEMA_IDS = {
    "run-manifest-v1.schema.json": (
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/run-manifest-v1.schema.json"
    ),
    "run-trace-v8.schema.json": (
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/run-trace-v8.schema.json"
    ),
    "study-manifest-v1.schema.json": (
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/study-manifest-v1.schema.json"
    ),
    "study-run-record-v1.schema.json": (
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/study-run-record-v1.schema.json"
    ),
}
_REQUIRED_WHEEL_SUFFIXES = (
    "webagent/py.typed",
    *(f"webagent/schemas/{name}" for name in _REQUIRED_SCHEMA_IDS),
    "benchmarks/README.md",
    "benchmarks/manifests/open_web_general.json",
    "benchmarks/manifests/open_web_smoke.json",
    "benchmarks/manifests/qwen_strict_search.json",
)
_REQUIRED_SDIST_SUFFIXES = (
    "/PKG-INFO",
    "/LICENSE",
    "/README.md",
    "/pyproject.toml",
    "/src/webagent/py.typed",
    *(f"/src/webagent/schemas/{name}" for name in _REQUIRED_SCHEMA_IDS),
    "/benchmarks/README.md",
    "/benchmarks/manifests/open_web_general.json",
    "/benchmarks/manifests/open_web_smoke.json",
    "/benchmarks/manifests/qwen_strict_search.json",
)


def check_release_state(
    root: Path,
    *,
    tag: str | None = None,
    require_clean: bool = False,
) -> list[str]:
    """Return release blockers visible in source control and package metadata."""
    failures: list[str] = []
    name = project_name(root / "pyproject.toml")
    version = project_version(root / "pyproject.toml")
    if _normalize_distribution_name(name) != _normalize_distribution_name(_DISTRIBUTION_NAME):
        failures.append(f"project distribution name {name!r} does not match {_DISTRIBUTION_NAME!r}")
    if tag is not None:
        if not tag.startswith("v"):
            failures.append(f"release tag must use a v prefix: {tag!r}")
        if tag.removeprefix("v") != version:
            failures.append(f"tag {tag!r} does not match project version {version!r}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    release_pattern = rf"(?m)^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$"
    if re.search(release_pattern, changelog) is None:
        failures.append(f"CHANGELOG.md has no dated release section for {version}")
    if tag is not None:
        unreleased = _changelog_section(changelog, "Unreleased")
        if unreleased is None:
            failures.append("CHANGELOG.md has no Unreleased section")
        elif unreleased:
            failures.append("CHANGELOG.md Unreleased section must be empty for a tagged release")

    tracked = _git_lines(root, "ls-files")
    failures.extend(
        check_tracked_paths(
            tracked,
            allowed_prefixes=project_allowed_tracked_prefixes(root / "pyproject.toml"),
        )
    )
    if require_clean and _git_lines(root, "status", "--porcelain=v1"):
        failures.append("working tree is not clean")
    return failures


def check_tracked_paths(
    paths: Iterable[str],
    *,
    allowed_prefixes: Iterable[str] = (),
) -> list[str]:
    """Reject generated/local paths except explicitly allowlisted evidence prefixes."""
    allowed = tuple(PurePosixPath(prefix) for prefix in allowed_prefixes)
    failures: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        is_allowed = any(path == prefix or prefix in path.parents for prefix in allowed)
        if path.parts and path.parts[0] in _FORBIDDEN_ROOTS and not is_allowed:
            failures.append(f"generated/local path is tracked: {raw_path}")
        if path.name.startswith(".env") and path.name != ".env.example":
            failures.append(f"secret environment file is tracked: {raw_path}")
    return failures


def project_allowed_tracked_prefixes(pyproject_path: Path) -> tuple[str, ...]:
    """Read narrowly scoped generated-path exceptions from release configuration."""
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    value = (
        payload.get("tool", {})
        .get("webagent", {})
        .get("release", {})
        .get("allowed-tracked-prefixes", [])
    )
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("tool.webagent.release.allowed-tracked-prefixes must be a string array")
    normalized: list[str] = []
    for raw_prefix in value:
        prefix = PurePosixPath(raw_prefix)
        if (
            prefix.is_absolute()
            or len(prefix.parts) < 2
            or ".." in prefix.parts
            or prefix.parts[0] != "outputs"
        ):
            raise ValueError(
                "release allowed tracked prefixes must be relative subdirectories below outputs/"
            )
        normalized.append(prefix.as_posix())
    return tuple(normalized)


def project_version(pyproject_path: Path) -> str:
    """Read and validate the static PEP 621 project version."""
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    value = payload.get("project", {}).get("version")
    if not isinstance(value, str) or not value:
        raise ValueError("pyproject.toml must declare a non-empty project.version")
    return value


def project_name(pyproject_path: Path) -> str:
    """Read and validate the PEP 621 distribution name."""
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    value = payload.get("project", {}).get("name")
    if not isinstance(value, str) or not value:
        raise ValueError("pyproject.toml must declare a non-empty project.name")
    return value


def _changelog_section(changelog: str, section: str) -> str | None:
    pattern = rf"(?ms)^## \[{re.escape(section)}\][ \t]*\r?\n(.*?)(?=^## \[|\Z)"
    match = re.search(pattern, changelog)
    return match.group(1).strip() if match is not None else None


def check_release_artifacts(dist_dir: Path) -> list[str]:
    """Inspect one wheel and one sdist for identity, content, and path safety."""
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    failures: list[str] = []
    if len(wheels) != 1:
        failures.append(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        failures.append(f"expected exactly one sdist, found {len(sdists)}")
    if failures:
        return failures

    failures.extend(_check_wheel(wheels[0]))
    failures.extend(_check_sdist(sdists[0]))
    wheel_name = _artifact_distribution(wheels[0].name)
    sdist_name = _artifact_distribution(sdists[0].name)
    if _normalize_distribution_name(wheel_name) != _normalize_distribution_name(sdist_name):
        failures.append(f"wheel/sdist distribution names differ: {wheel_name!r} != {sdist_name!r}")
    wheel_version = _artifact_version(wheels[0].name)
    sdist_version = _artifact_version(sdists[0].name)
    if wheel_version != sdist_version:
        failures.append(f"wheel/sdist versions differ: {wheel_version!r} != {sdist_version!r}")
    return failures


def compare_release_artifacts(first: Path, second: Path) -> list[str]:
    """Require two build directories to contain byte-identical artifacts."""
    first_hashes = _artifact_hashes(first)
    second_hashes = _artifact_hashes(second)
    failures: list[str] = []
    if first_hashes.keys() != second_hashes.keys():
        failures.append("artifact filenames differ between reproducibility builds")
        return failures
    for name, digest in first_hashes.items():
        if second_hashes[name] != digest:
            failures.append(f"artifact is not reproducible: {name}")
    return failures


def _check_wheel(path: Path) -> list[str]:
    failures: list[str] = []
    artifact_name = _artifact_distribution(path.name)
    expected_name = _normalize_distribution_name(_DISTRIBUTION_NAME)
    if _normalize_distribution_name(artifact_name) != expected_name:
        failures.append(
            f"wheel distribution name {artifact_name!r} does not match {_DISTRIBUTION_NAME!r}"
        )
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        failures.extend(_check_archive_names(names))
        failures.extend(
            f"wheel is missing {suffix}"
            for suffix in _REQUIRED_WHEEL_SUFFIXES
            if suffix not in names
        )
        for filename, schema_id in _REQUIRED_SCHEMA_IDS.items():
            schema_path = f"webagent/schemas/{filename}"
            count = names.count(schema_path)
            if count > 1:
                failures.append(f"wheel must contain exactly one {schema_path}")
            elif count == 1:
                failures.extend(
                    _validate_json_schema(
                        archive.read(schema_path),
                        label=f"wheel {schema_path}",
                        expected_id=schema_id,
                    )
                )
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            failures.append("wheel must contain exactly one METADATA file")
        else:
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            expected = _artifact_version(path.name)
            metadata_name = metadata.get("Name")
            if not isinstance(metadata_name, str) or (
                _normalize_distribution_name(metadata_name) != expected_name
            ):
                failures.append("wheel METADATA name does not match the release distribution")
            if metadata.get("Version") != expected:
                failures.append("wheel METADATA version does not match its filename")
    return failures


def _check_sdist(path: Path) -> list[str]:
    failures: list[str] = []
    artifact_name = _artifact_distribution(path.name)
    expected_name = _normalize_distribution_name(_DISTRIBUTION_NAME)
    if _normalize_distribution_name(artifact_name) != expected_name:
        failures.append(
            f"sdist distribution name {artifact_name!r} does not match {_DISTRIBUTION_NAME!r}"
        )
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        failures.extend(_check_archive_names(names))
        if any(member.issym() or member.islnk() for member in members):
            failures.append("sdist contains a symbolic or hard link")
        failures.extend(
            f"sdist is missing *{suffix}"
            for suffix in _REQUIRED_SDIST_SUFFIXES
            if not any(name.endswith(suffix) for name in names)
        )
        for filename, schema_id in _REQUIRED_SCHEMA_IDS.items():
            suffix = f"/src/webagent/schemas/{filename}"
            schema_members = [member for member in members if member.name.endswith(suffix)]
            if len(schema_members) > 1:
                failures.append(f"sdist must contain exactly one *{suffix}")
            elif len(schema_members) == 1:
                schema_file = archive.extractfile(schema_members[0])
                if schema_file is None:
                    failures.append(f"sdist schema cannot be read: *{suffix}")
                else:
                    failures.extend(
                        _validate_json_schema(
                            schema_file.read(),
                            label=f"sdist *{suffix}",
                            expected_id=schema_id,
                        )
                    )
        metadata_members = [member for member in members if member.name.endswith("/PKG-INFO")]
        if len(metadata_members) != 1:
            failures.append("sdist must contain exactly one PKG-INFO file")
        else:
            metadata_file = archive.extractfile(metadata_members[0])
            if metadata_file is None:
                failures.append("sdist PKG-INFO cannot be read")
            else:
                metadata = BytesParser().parsebytes(metadata_file.read())
                metadata_name = metadata.get("Name")
                if not isinstance(metadata_name, str) or (
                    _normalize_distribution_name(metadata_name) != expected_name
                ):
                    failures.append("sdist PKG-INFO name does not match the release distribution")
                if metadata.get("Version") != _artifact_version(path.name):
                    failures.append("sdist PKG-INFO version does not match its filename")
    return failures


def _check_archive_names(names: Iterable[str]) -> list[str]:
    failures: list[str] = []
    for raw_name in names:
        path = PurePosixPath(raw_name)
        if path.is_absolute() or ".." in path.parts:
            failures.append(f"archive contains an unsafe path: {raw_name}")
            continue
        parts = set(path.parts)
        if parts & _FORBIDDEN_ROOTS:
            failures.append(f"archive contains generated/local content: {raw_name}")
        if any(part.startswith(("build-", "dist-")) for part in path.parts[:-1]):
            failures.append(f"archive contains generated build content: {raw_name}")
        if path.name.startswith(".env") and path.name != ".env.example":
            failures.append(f"archive contains a secret environment file: {raw_name}")
    return failures


def _validate_json_schema(data: bytes, *, label: str, expected_id: str) -> list[str]:
    """Require one packaged file to be the declared Draft 2020-12 schema."""
    try:
        schema: object = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{label} is not valid JSON: {exc}"]
    if not isinstance(schema, dict):
        return [f"{label} must contain a JSON object"]

    failures: list[str] = []
    if schema.get("$schema") != _JSON_SCHEMA_DRAFT:
        failures.append(f"{label} does not declare JSON Schema Draft 2020-12")
    if schema.get("$id") != expected_id:
        failures.append(f"{label} has an unexpected $id")
    try:
        from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
        from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
    except ImportError:
        failures.append(f"{label} cannot be validated because jsonschema is not installed")
        return failures
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        failures.append(f"{label} is not a valid Draft 2020-12 schema: {exc.message}")
    return failures


def _artifact_version(filename: str) -> str:
    return _artifact_identity(filename)[1]


def _artifact_distribution(filename: str) -> str:
    return _artifact_identity(filename)[0]


def _artifact_identity(filename: str) -> tuple[str, str]:
    stem = filename.removesuffix(".tar.gz").removesuffix(".whl")
    parts = stem.split("-")
    if len(parts) < 2:
        raise ValueError(f"cannot determine artifact identity from {filename!r}")
    return parts[0], parts[1]


def _normalize_distribution_name(value: str) -> str:
    """Return the PEP 503 normalized spelling used for identity comparisons."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _artifact_hashes(directory: Path) -> dict[str, str]:
    paths = sorted((*directory.glob("*.whl"), *directory.glob("*.tar.gz")))
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _git_lines(root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def _report(failures: list[str]) -> int:
    if not failures:
        print("release check passed")
        return 0
    for failure in failures:
        print(f"release check failed: {failure}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    state = subparsers.add_parser("state", help="check git and release metadata")
    state.add_argument("--root", type=Path, default=Path.cwd())
    state.add_argument("--tag")
    state.add_argument("--require-clean", action="store_true")

    artifacts = subparsers.add_parser("artifacts", help="inspect wheel and sdist content")
    artifacts.add_argument("directory", type=Path)

    reproduce = subparsers.add_parser("repro", help="compare two build directories")
    reproduce.add_argument("first", type=Path)
    reproduce.add_argument("second", type=Path)

    args = parser.parse_args()
    if args.command == "state":
        failures = check_release_state(
            args.root,
            tag=args.tag,
            require_clean=args.require_clean,
        )
    elif args.command == "artifacts":
        failures = check_release_artifacts(args.directory)
    else:
        failures = compare_release_artifacts(args.first, args.second)
    raise SystemExit(_report(failures))


if __name__ == "__main__":
    main()


__all__ = [
    "check_release_artifacts",
    "check_release_state",
    "check_tracked_paths",
    "compare_release_artifacts",
    "main",
    "project_allowed_tracked_prefixes",
    "project_name",
    "project_version",
]
