# 工程验证记录：2026-08-29 至 2026-09-01

本文归档两个历史 checkout 的验证结果。数字只描述对应日期的源码，不能替代当前分支重新运行。

## 2026-09-01 快照

- Ruff format check：248 个 Python 文件通过。
- Ruff lint：通过。
- Mypy：152 个 source files 无问题。
- Unit：1,202 passed，branch coverage 85.58%，达到 85% 门槛。
- Integration：11 passed，使用真实 headless Chromium。
- Controlled sandbox harness：5/5，通过工具、策略和 evaluator 校准。
- PDF Figure fast path：10 documents、9 positives；precision/recall 1.0/1.0，7/9
  进入快路径，false bypass 0，render success 100%。
- Release：固定 `SOURCE_DATE_EPOCH` 的两次 wheel/sdist 构建通过 artifact 和
  reproducibility 检查。

同日模型评测不在本文重复，见
[`v6-final-r7` 结果](v6-final-r7-2026-09-01.zh-CN.md)。

## 2026-08-29 快照

- Ruff lint/format、strict Mypy 和 package build 通过。
- Unit 与 integration 合计 774 tests passed；确定性测试没有调用真实规划模型或搜索引擎。
- 11-task controlled-web harness 通过，只证明站点、动作链和判分器组成正确。
- 历史三来源 open-web smoke 在当时的单一模型、单一日期为 3/3；它不含真实搜索发现。
- 历史 Figure benchmark 的 detector precision/recall 为 1.0/1.0，默认阈值覆盖 7/9，
  false bypass 0。
- 历史 Hybrid report run 使用直接 GitHub/arXiv 报告发现，因此不能作为浏览器搜索成绩。
- 历史 search-engine-only 个案在浏览器搜索后下载并分析 Qwen 报告；它是旧 trace contract 下
  的单任务记录，不可外推为通用开放网页成功率。

## 外部 parser 探针

2026-08-29 的有界临时输入探针记录为：

- Datalab/Marker：health、convert、poll 路径返回预期文本；
- MinerU：batch、signed upload、poll、ZIP 路径返回预期文本；
- PaddleOCR：job、poll、JSONL 路径返回预期结构。

这些记录证明当时的 API 接线可用，不保证当前凭证、schema、配额或网络仍相同。

## 解释边界

测试通过只证明被断言的行为在对应 checkout 成立。它不证明部署、外部 API、实时搜索、CAPTCHA
恢复或模型能力。当前代码质量必须重新执行 `AGENTS.md` 的质量门；当前实证状态必须从
[结果索引](README.md)读取。
