# 工程验证记录：2026-09-09

本文记录 R7 campaign、paired strict validation、文档与 README GIF 更新后的本地质量门结果。
它验证的是当前未提交 checkout，不是某个已发布 commit；基准 HEAD 为
`024b374da457f872015280e739b68551d7313001`，分支为 `main`。工作区包含用户要求的跨模块修改
和历史 output 删除，因此不把 HEAD 单独称为被测源码。

当前运行时 source SHA-256 为
`7528f7f8d2a54ed2a9858e4ac89d031737bf02a5d3a00a23976b4f15bf0cfd22`，benchmark source
SHA-256 为 `5f42ae4fb634903562c25206f1d754c233a35829280f49e49205a58fdaeb4849`。
后者包含 campaign 完成后的机械格式化，因此与 R7 保存的 benchmark hash 不同；R7 报告仍以
自己的运行时 hash 为权威。

## 质量门结果

| 门禁 | 结果 |
|---|---|
| `ruff check src/ scripts/ tests/` | 通过 |
| `ruff format --check src/ scripts/ tests/` | 301 个文件已格式化 |
| `mypy src/ scripts/` | 190 个 source files 无问题 |
| `uv run python scripts/check_docs.py` | 53 个 Markdown 文件通过 |
| `pytest tests/unit/ -v` | 1,445 passed；综合 statement/branch coverage 86.81%，达到 85% 门槛 |
| `pytest tests/integration/ -v --no-cov` | 37 passed；真实 headless Chromium，128.45 秒 |
| `git diff --check` | 通过 |

运行环境记录为 Python 3.13.0、Playwright 1.58.0、pytest 9.0.2、Mypy 2.1.0。依赖版本只绑定
本次本地运行，不表示远端 CI 已执行。

## 日期化评测与展示产物

- Generality campaign R7 完成 71/72：GLM 36/36，Qwen 35/36；唯一失败是保留的 Qwen
  `sandbox_checkout` false completion。
- Paired strict R5 中 Qwen 与 GLM 都找到官方 Qwen3.8-Flash-Next `tech_report.pdf`，完成 Figure
  1 解读并通过 10/10 assertions 与 6/6 certificate checks。
- README GIF 已由 paired R5 的 Qwen 轨迹重新生成：960×720、24 帧、48 秒、约 1.0 MB，
  SHA-256 为
  `e49ad815ba1e0d3c2e81ae4248ffed8c4aa407854e892f5110b2f7381f2b0000`。

具体模型分数、失败边界与 source binding 分别见
[R7 campaign 记录](generality-campaign-2026-09-09.zh-CN.md)和
[paired strict R5 记录](qwen-strict-search-2026-09-09.zh-CN.md)。

## 解释边界

这些结果证明当前源码、确定性环境、本地 Chromium、文档和派生 GIF 在此 checkout 中通过声明的
检查。它们不证明远端 CI/PyPI 发布、公共搜索长期稳定性、云端 OCR 长期兼容性，或
WebArena-Verified Hard/VisualWebArena 后端已经部署。纵向 portfolio 仍只有一个共同日期，尚需
两个额外真实日期。
