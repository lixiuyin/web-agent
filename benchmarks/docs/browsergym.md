# BrowserGym external evaluation

The external layer runs WebArena-Verified Hard and VisualWebArena through BrowserGym's
standard observation/action interface and native evaluators. Its scores are not pooled
with repository diagnostic metrics.

## Isolated environment

BrowserGym 0.14.3 requires a Python 3.12 environment with older Playwright/greenlet
constraints than the main Python 3.13 project. VisualWebArena also adds a large
Torch/captioning stack.

```bash
scripts/setup_browsergym_env.sh
```

The installer pins packages, installs Chromium and the required NLTK tokenizer resource,
and validates the 258-task and 910-task catalogs. VisualWebArena's image evaluator can
download `Salesforce/blip2-flan-t5-xl`, so reserve model-cache and disk capacity.

## Required sites and variables

The official WebArena and VisualWebArena backends must be deployed and reachable from the
BrowserGym host.

WebArena requires:

- `WA_SHOPPING`
- `WA_SHOPPING_ADMIN`
- `WA_REDDIT`
- `WA_GITLAB`
- `WA_WIKIPEDIA`
- `WA_MAP`
- `WA_HOMEPAGE`
- optional but recommended `WA_FULL_RESET`

VisualWebArena requires:

- `VWA_CLASSIFIEDS`
- `VWA_CLASSIFIEDS_RESET_TOKEN`
- `VWA_SHOPPING`
- `VWA_REDDIT`
- `VWA_WIKIPEDIA`
- `VWA_HOMEPAGE`
- optional but recommended `VWA_FULL_RESET`

Planner credentials must also be visible to the parent process. Reset values contribute
to a redacted backend-configuration hash; plaintext values are not written to reports.

## Calibrate before a full run

Package installation is not backend readiness. Verify non-empty variables, site
reachability, authentication, and reset behavior, then run one task from each suite for
both models:

```bash
.venv/bin/python -m benchmarks.studies.browsergym_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --webarena-task-ids 0 \
  --visual-task-ids 0 \
  --output outputs/studies/browsergym-calibration
```

This report is marked `custom` and cannot satisfy external readiness.

## Official matched-model run

Use a fresh output root after calibration:

```bash
.venv/bin/python -m benchmarks.studies.browsergym_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --output outputs/studies/browsergym-external-model-matrix
```

The official matrix contains 258 WebArena-Verified Hard tasks and 910 VisualWebArena
tasks per model: 2,336 episodes for two models. Each suite uses its standard 30-step
budget and deterministic seed schedule. Task identity, order, package versions, source
fingerprints, and backend hash are part of the comparison contract.

Interrupted executions retain partial reports. Repeat the same contract with `--resume`;
add `--retry-errors` only to rerun episodes recorded as system errors.

## Output and scoring

Native BrowserGym episode evidence lives below each external execution's `runs/`.
`browsergym-results.json` records task-set identity, packages, backend hash, coverage,
system errors, native mean reward, binary success, Wilson 95% interval, and task rows.

The matrix rejects cross-model comparison when backend or package fingerprints differ and
adds exact paired McNemar comparisons. It does not reuse repository substring or
terminal-state evaluators.

See [report contracts](report-contracts.md) for two-layer readiness.
