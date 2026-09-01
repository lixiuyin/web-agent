#!/usr/bin/env bash
set -euo pipefail

browsergym_env="${1:-.venv-browsergym}"

if [[ -e "$browsergym_env" && ! -f "$browsergym_env/pyvenv.cfg" ]]; then
  echo "Refusing to use a non-venv path: $browsergym_env" >&2
  exit 2
fi

if [[ -f "$browsergym_env/pyvenv.cfg" ]]; then
  browsergym_version="$($browsergym_env/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "$browsergym_version" != "3.12" ]]; then
    echo "BrowserGym requires an isolated Python 3.12 venv; found $browsergym_version" >&2
    exit 2
  fi
fi

if command -v uv >/dev/null 2>&1; then
  if [[ ! -f "$browsergym_env/pyvenv.cfg" ]]; then
    uv venv --python 3.12 "$browsergym_env"
  fi
  uv pip install --python "$browsergym_env/bin/python" \
    browsergym-experiments==0.14.3 \
    browsergym-webarena-verified==0.14.3 \
    browsergym-visualwebarena==0.14.3 \
    webarena-verified==1.2.3 \
    'pydantic>=2.0' 'pydantic-settings>=2.0' 'httpx[socks]>=0.27' \
    'python-dotenv>=1.0' 'jsonschema>=4.25'
else
  if [[ ! -f "$browsergym_env/pyvenv.cfg" ]]; then
    python3.12 -m venv "$browsergym_env"
  fi
  "$browsergym_env/bin/python" -m pip install \
    browsergym-experiments==0.14.3 \
    browsergym-webarena-verified==0.14.3 \
    browsergym-visualwebarena==0.14.3 \
    webarena-verified==1.2.3 \
    'pydantic>=2.0' 'pydantic-settings>=2.0' 'httpx[socks]>=0.27' \
    'python-dotenv>=1.0' 'jsonschema>=4.25'
fi

NLTK_ALLOW_PROXIED_URLOPEN=1 \
  "$browsergym_env/bin/python" -m nltk.downloader \
  -d "$browsergym_env/nltk_data" punkt_tab
"$browsergym_env/bin/python" -m playwright install chromium
PYTHONPATH="$(pwd)/src:$(pwd)" \
  "$browsergym_env/bin/python" -m benchmarks.suites.browsergym.runner --help >/dev/null
PYTHONPATH="$(pwd)/src:$(pwd)" "$browsergym_env/bin/python" - <<'PY'
from benchmarks.suites.browsergym.runner import _task_catalog

expected = {"webarena_verified": 258, "visualwebarena": 910}
for benchmark, expected_count in expected.items():
    _, selected, _ = _task_catalog(benchmark)
    if len(selected) != expected_count:
        raise RuntimeError(
            f"{benchmark} catalog mismatch: expected {expected_count}, found {len(selected)}"
        )
PY

echo "BrowserGym environment ready: $browsergym_env"
