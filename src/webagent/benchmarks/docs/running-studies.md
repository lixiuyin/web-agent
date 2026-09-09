# Running benchmark studies

Suite runners produce one execution. Study runners repeat suites across models,
conditions, or dates and preserve immutable comparison contracts.

## Repeated controlled runs

```bash
uv run python -m webagent.benchmarks.studies.controlled_web_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash \
  --repetitions 3 \
  --max-steps-per-task 12 \
  --parallel-repetitions 3 \
  --minimum-success-rate 0.8 \
  --maximum-false-completion-rate 0.1
```

Immutable matrix snapshots aggregate task-level counts rather than rounded run-level
percentages. Reduce parallelism when the provider quota is not independently controlled.

## Dated open-web matrix

```bash
uv run python -m webagent.benchmarks.studies.open_web_matrix \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --manifest src/webagent/benchmarks/manifests/open_web_general.json \
  --output outputs/studies/open-web-model-matrix \
  --shards 3
```

There is no date override. Same-date reruns are refused unless explicitly documented.
Model order rotates by UTC date to counterbalance provider warm-up or rate-limit order.
Longitudinal readiness requires dates common to every model, complete 30-task cells, and
one manifest, configuration, task-set, study, agent-source, and benchmark-source identity.

## Generality campaign

Collect public, sandbox, and long-horizon evidence together:

```bash
uv run python -m webagent.benchmarks.studies.generality_campaign \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --manifest src/webagent/benchmarks/manifests/open_web_general.json \
  --shards 1 \
  --open-max-steps 8 \
  --open-discovery-max-steps 12 \
  --open-direct-task-timeout-seconds 600 \
  --open-discovery-task-timeout-seconds 2400 \
  --sandbox-max-steps 18 \
  --long-max-steps 100 \
  --long-task-timeout-seconds 2400 \
  --resume-at-step 35 \
  --long-planner-max-tokens 1024 \
  --long-planner-reasoning-effort low \
  --captcha-handling fail \
  --model-order rotate-by-date
```

Each model receives the 30-task open-web suite, five sandbox tasks, and one 60-stage
forced-resume task. Endpoint preflight occurs before browser allocation. Provider,
privacy, or transport rejection is recorded as unavailable rather than converted into 30
model failures.

Open-web execution is serial by default. Use shards only when quota is controlled.
Direct open-web tasks have a shorter default timeout than search-discovery tasks because
live search, challenge handling, and recovery can legitimately take longer. The
long-horizon timeout must cover a complete 60-stage run, including the forced restart;
keep all three values explicit in a published campaign contract.
Campaign state is written atomically, and retained paths are campaign-relative. A
portfolio becomes ready only after each requested endpoint has all complementary suites
on at least three common real dates under unchanged source fingerprints.

The latest complete single-date example is the
[2026-09-09 R7 campaign](../../../../docs/research/results/generality-campaign-2026-09-09.zh-CN.md).
Its 71/72 result is complete evidence even though the longitudinal portfolio remains
insufficient until two more common dates are collected.

## Intervention transfer

For a preregistered baseline/intervention study:

```bash
uv run python -m webagent.benchmarks.studies.intervention_transfer \
  outputs/studies/<study-id> \
  --baseline-condition baseline \
  --intervention-condition memory-change \
  --output outputs/studies/<study-id>/analysis/intervention-transfer.json
```

The command verifies ledger rows against retained evaluations and rejects escaped or
missing evidence, report drift, identity mismatch, taxonomy drift, and duplicate
task-run cells. Development, held-out-task, and held-out-setting effects remain separate.

## Resume and retry

During execution, each independently judged task is atomically persisted to
`runs/<task>/evaluation/task.json` before the next task starts. `analysis/progress.json`
records the active task, planned/evaluated counts, remaining task IDs, and a summary
over evaluated tasks only. Interruption preserves these judgments, but this progress
file is not a complete suite report and never publishes partial study-ledger records.
An empty evaluated set has a null summary. Hard process termination may leave the last
progress status as `running`; inspect process liveness rather than assuming it is live.
If an agent catches cancellation to preserve its interrupted trace, the benchmark runner
still propagates the pending cancellation rather than starting the next task.

- `--resume` continues an interrupted execution only under the identical contract.
- `--retry-errors` reruns system-error episodes, not ordinary failed model outcomes.
- Do not retry or discard an accepted task merely to force failed-action counts to zero.
  Keep bounded recovered external failures in the immutable report; retry only under the
  declared system-error policy.
- Changing tasks, source, backend, evaluator, model, or bound policy requires a new
  execution or campaign.
- A root `matrix.json` or `latest.json` is a compatibility view; immutable batch reports
  remain authoritative.

## Two-layer portfolio

After the diagnostic longitudinal portfolio and both official BrowserGym suites are
complete, bind them without pooling scores:

```bash
uv run python -m webagent.benchmarks.studies.two_layer_portfolio \
  --diagnostic outputs/campaigns/<campaign>/analysis/portfolios/latest.json \
  --external <one-WebArena-and-one-VWA-report-per-model> \
  --output outputs/campaigns/<campaign>/analysis/two-layer-portfolio.json \
  --require-ready
```

The external list must contain one complete official report from each suite for every
diagnostic endpoint. Source and backend fingerprints must match the declared comparison.
