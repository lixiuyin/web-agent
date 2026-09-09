# 工程验证记录：2026-09-07

本文记录当前 checkout 的一次本地质量门验证。它是可复现的工程快照，不代表真实模型、外部搜索或云端 parser 的长期可用性。

## 质量门结果

- `ruff check src/ scripts/ tests/`：通过。
- `ruff format --check src/ scripts/ tests/`：通过。
- `mypy src/ scripts/`：172 个 source files 无问题。
- `uv run python scripts/check_docs.py`：50 个 Markdown 文件通过。
- Unit：1,265 passed，综合 statement/branch coverage 86.23%，达到 85% 门槛。
- Integration：13 passed，使用真实 headless Chromium，运行参数为 `--no-cov`。
- Test collection：unit 与 integration 合计收集 1,278 项测试。
- `git diff --check`：通过。

## 本次结构修复

- 将 campaign、open-web matrix、open-web runner 的准备、执行、收尾和证据写入拆开。
- 将受控 benchmark 的执行流程拆成 suite、task execution 和资源管理函数。
- 将受控任务清单改为模块级声明式模板，运行时只负责注入临时站点地址。
- 将 CLI、受控站点、开放网页准备、搜索结果收集、端点探测、agent step、PDF 工具和图形 fast path 的大函数拆成职责明确的辅助函数。
- 将 benchmark 文档和模块说明中的旧 `benchmarks.*` 入口统一迁移到 `webagent.benchmarks.*`。
- 将文档中的 checker 命令统一为 `uv run python scripts/check_docs.py`。
- 对 PyMuPDF 在 Python 3.13 下导入时产生的第三方 SWIG deprecation warning 增加了精确的测试过滤规则。

## 解释边界

这些结果证明当前源码、确定性测试环境和本地 Chromium 在此 checkout 中通过声明的门禁；它们不证明真实 LLM/VLM 规划质量、公共搜索引擎 DOM 稳定性、CAPTCHA 恢复、云端 OCR 长期兼容性、BrowserGym 后端部署或发布仓库的远端状态。历史验证记录保持原日期和原数字，不与本次快照混用。
