# Release procedure

This guide owns packaging, reproducibility, and release publication. Contributor workflow
and code conventions remain in `CONTRIBUTING.md`.

## Prepare the release

1. Move the version entry from `Unreleased` to a dated section in `CHANGELOG.md`.
2. Confirm that `pyproject.toml`, package metadata, and the intended `v<version>` tag agree.
3. Run the full quality gates in `AGENTS.md`.
4. Confirm that the working tree is clean and no generated output or populated `.env` is
   staged.

Tracked evidence bundles are exceptional. Their exact prefixes must be allowed by
`[tool.webagent.release].allowed-tracked-prefixes`; no `outputs/` path may enter a wheel
or source distribution.

## Reproducible build

Build directories must remain outside the source distribution. The configured `build-*`
and `dist-*` exclusions prevent a second build from recursively packaging the first.

```bash
python -m webagent.release state --root . --tag v0.2.0 --require-clean
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
python -m build --outdir build-one
python -m build --outdir build-two
python -m webagent.release repro build-one build-two
python -m webagent.release artifacts build-one
twine check build-one/*
```

`repro` compares wheel and source-distribution digests across the two builds. `artifacts`
checks package contents and rejects forbidden paths.

## Distribution smoke tests

Install both the wheel and source distribution into clean environments. At minimum,
verify:

- `import webagent` and the package version;
- `python -m webagent --help`;
- an offline benchmark or stub lifecycle path;
- packaged JSON schemas and non-Python resources;
- absence of repository outputs, credentials, browser profiles, and build directories.

The CI release workflow performs these checks and creates provenance attestation before
publishing through the protected `pypi` environment with trusted publishing.

## Repository checkout behavior

CI uses a non-cone sparse checkout that excludes `outputs/`. This avoids Windows path
length failures and downloading LFS screenshots that tests do not consume. Documentation
must not assume that selected evidence bundles are available inside packaging jobs.

## Publish

Create an annotated `v<version>` tag only after the commit passes CI and the changelog is
final. Push the tag, monitor the release workflow through artifact verification and smoke
tests, and confirm the published package metadata and provenance on the registry.

A passing local build does not prove that trusted publishing, protected environments, or
the public package registry are configured correctly; those remain external deployment
checks.
