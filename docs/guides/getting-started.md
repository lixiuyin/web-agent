# Getting started

This guide takes a new checkout from installation to one real, auditable browser run.
For all configuration fields, see the [configuration reference](../reference/configuration.md).

## Requirements

- Python 3.13 or newer for the main project environment.
- Chromium installed through Playwright.
- An OpenAI-compatible planner endpoint, or a local OpenAI-compatible vLLM server.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

The distribution is named `lixiuyin-webagent`; the import package and command remain
`webagent`.

## Configure a planner

Copy the template and set the endpoint, key, and model:

```bash
cp .env.example .env
```

```dotenv
AGENT_MODEL_API_URL=https://openrouter.ai/api/v1/chat/completions
AGENT_MODEL_API_KEY=replace-me
AGENT_MODEL_NAME=z-ai/glm-5.3-flash
```

Never commit a populated `.env`. If no planner credentials are configured, the CLI uses
`StubPlanner`, which can exercise lifecycle code but cannot solve an open-ended task.

For a local server:

```bash
webagent --task "Summarize the current page" --use-vllm --headless
```

## Run one task

```bash
webagent \
  --task "Find the most recent Qwen technical report and interpret Figure 1" \
  --headless
```

Ordinary runs use Hybrid discovery by default. The planner can combine browser evidence
with supported first-party discovery tools. Use browser-grounded or strict evaluation
when direct-source APIs must not participate; see
[discovery modes](discovery-modes.md).

The CLI allocates a unique run below `outputs/runs/` unless `--output` names one exact
run root. See the [artifact reference](../reference/run-artifacts.md) before archiving or
resuming a run.

## Useful variants

```bash
# Visible browser
webagent --task "..." --headed

# Direct-source tools hidden, but ordinary resumable execution retained
webagent --task "..." --discovery-mode browser-grounded --headless

# Certificate-bearing, single-run browser-search evaluation
webagent --task "..." --strict-eval --headless

# Interactive follow-up session
webagent --interactive --headless

# Diagnose one planner transport
webagent --task "..." --planner-output-mode native-tools --headless
```

## Resume an ordinary run

Repeat the original task and point to its checkpoint:

```bash
webagent \
  --task "Find the most recent Qwen technical report and interpret Figure 1" \
  --resume outputs/runs/<run>/control/checkpoints/latest.json
```

The task hash, behavior-affecting configuration, source fingerprint, browser state, and
referenced artifact hashes must match. Strict/search-only runs are intentionally
single-run and cannot be resumed into a valid certificate.

## Verify the result

Inspect at least:

- `result/summary.txt` for the agent's claim;
- `trajectory/trace.json` for observed actions and tool results;
- `observations/screenshots/` for browser evidence;
- `artifacts/` for acquired or derived files;
- `trajectory/verification.json` for a strict run's certificate.

Verify a completed strict trace with:

```bash
python -m webagent.evaluation.trace_verifier \
  outputs/runs/<run>/trajectory/trace.json
```

The certificate validates the execution contract, not the scientific correctness of
the final interpretation. Retain and inspect the source PDF and extracted figure.

## Next steps

- For API, browser, and planner failures, use the
  [troubleshooting guide](troubleshooting.md).
- For profiles, proxies, CAPTCHA, and action authorization, use the
  [browser and security reference](../reference/browser-and-security.md).
- For repeatable evaluation, start from the [benchmark guide](../../benchmarks/README.md).
