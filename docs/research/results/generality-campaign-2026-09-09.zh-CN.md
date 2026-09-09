# Generality campaign：2026-09-09 R7

本记录保存一次双模型、三套件的完整日期化诊断 campaign。它同时运行 30 个真实公开网页
任务、5 个受控多域 sandbox 任务和 1 个强制恢复长程任务；每个模型共 36 个任务。结果用于
定位当前实现的能力与失败边界，不等同于跨日期稳定性或 BrowserGym 排名。

## 运行契约

```bash
uv run python -m webagent.benchmarks.studies.generality_campaign \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --output outputs/campaigns/generality-2026-09-09-rerun-r7 \
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

Campaign 状态为 `completed`。六份 suite report 具有相同 source binding：

- agent source SHA-256：
  `6c1b3109711ca0a3450a0b8ee5ca286a91fee74a673fe9db437bb33ec94601a0`；
- benchmark source SHA-256：
  `a7bcdefe53c9fe03bc960677d44911362aad5a1bfeff1fe6c9b076565c9cd92b`；
- campaign contract SHA-256：
  `f745c00301d5b37f5dc4329c04e8d74ab1bb326f76b0985843568d52f4f72f66`；
- open-web manifest SHA-256：
  `df96bea42bea63bf6d44e228e4aa6ff0460a24d41d48feb1d182e141f99f0c93`。

## 结果

| 模型 | 套件 | 通过 | 成功率 | action validity | 平均步数 | 平均耗时 |
|---|---|---:|---:|---:|---:|---:|
| GLM-5.3-Flash | Open web | 30/30 | 100% | 94.87% | 3.6 | 92.28 秒 |
| Qwen3.8-Flash | Open web | 30/30 | 100% | 88.03% | 4.9 | 80.68 秒 |
| GLM-5.3-Flash | Sandbox | 5/5 | 100% | 91.30% | 5.6 | 33.08 秒 |
| Qwen3.8-Flash | Sandbox | 4/5 | 80% | 90.91% | 7.6 | 96.78 秒 |
| GLM-5.3-Flash | Long horizon | 1/1 | 100% | 100% | 72 | 1324.62 秒 |
| Qwen3.8-Flash | Long horizon | 1/1 | 100% | 100% | 70 | 445.68 秒 |

总计 **71/72（98.61%）**。两个模型的 open-web 与 long-horizon 均完整通过；GLM 为
36/36，Qwen 为 35/36。所有任务的超时率、CAPTCHA 率、blocked 率和 max-steps 率均为 0。

## 唯一失败及归因边界

Qwen 的 `sandbox_checkout` 得分为 0.375。它执行 18 步、17 个动作，其中 2 个动作失败；
19 次 planner 尝试中有 1 次失败。独立 evaluator 确认购物车中已有 1 件 Orbit Notebook，且
两个 loopback origin 均被访问，但以下必需终态未出现：

- URL 包含 `/order/complete`，并显示 `#order-complete`；
- 地址保存为 `42 Orbit Road`；
- sandbox terms 被接受；
- 订单被提交。

轨迹在 shop、`/files`、upload portal 和 checkout 间来回跳转，最后停在 shop。Agent 以
`completed` 终止，但自己的最终摘要明确承认订单未被验证，自报成功概率也只有 0.15。汇总报告
仍将其记录为 `false_completion`，因为当前 evaluator 直接把运行级 `result.success` 写入
`agent_reported_success`；成功执行 `done` 因而被视为“报告成功”。任务失败和终态断言结果成立，
但这个标签不能进一步解读成“模型在最终文本中自信地宣称已经成功”。

这是真实保留的任务失败，而不是需要删除或重跑的异常样本：第 2 步 checkout observation 已经
完整提供地址输入框、terms checkbox 和提交按钮，但模型没有操作它们，反而通过多步 `back`
进入前序 sandbox 流程残留在浏览器历史中的 `/upload` 与 `/files`。直接原因是本次 planner
偏航；跨任务导航历史没有清空是促成偏航的隔离弱点，但并未迫使失败。同一 campaign 中 GLM
在同一受控环境和 evaluator 下通过该任务，其余九个 sandbox/model 单元也正常；没有 DOM
缺失、server、超时或 CAPTCHA 故障证据。按照预先声明的规则，不为追求 72/72 而放宽断言、
丢弃结果或重试普通模型失败。

## 本轮前修复的评测契约

最终 campaign 只在确认评测器自身问题后启动。修复包括：

- long-horizon 超时从不可配置的 1200 秒边界改为显式 2400 秒；
- open-web 将 direct task 与 discovery task 分别设为 600 秒和 2400 秒；
- Playwright 缺失时的安装提示同时覆盖 Python plugin 与 Chromium；
- Git/GitHub 区别任务接受三篇内容等价的官方 GitHub Docs 页面，不再把某一个官方 URL
  硬编码为唯一正确路径。

这些修改修复的是执行/判分契约，而不是针对模型答案打补丁。R7 在修复后的统一 source
fingerprint 下从头运行，没有拼接旧结果。

## 与 Qwen strict 任务的关系

“寻找最新 Qwen technical report PDF 并解释 Figure 1”由独立的 browser-search-only strict
manifest 验证。2026-09-09 paired R5 中 Qwen 与 GLM 都选择官方
`QwenLM/Qwen3.8-Flash-Next` 仓库中的 `tech_report.pdf`，并分别通过 10/10 独立 assertions
与 6/6 strict certificate checks；详见
[Qwen strict-search 验证](qwen-strict-search-2026-09-09.zh-CN.md)。该专项运行与本 campaign
使用不同 task set，且 source fingerprint 不完全相同，因此不能并入 R7 的 36-task cell。

## 完整性与限制

Portfolio 的 `status=insufficient` 只表示纵向门槛尚未满足：当前两个模型都有三套件完整
report，但共同真实日期只有 1 个，协议要求至少 3 个不变契约的共同日期。它不表示 R7
缺少任务或未完成。WebArena-Verified Hard 与 VisualWebArena 尚未运行，因而也没有外部
BrowserGym 可比结论。

本地产物根目录为：

```text
outputs/campaigns/generality-2026-09-09-rerun-r7/
```

权威入口是 `campaign.json`、日期 batch 的 `batch.json`、六份 `results.json` 以及
`analysis/portfolio.json`。完整原始 campaign 默认 gitignored；经审阅的
[冻结证据包](../../../outputs/published/2026-09-09/README.md)保留了上述汇总、唯一失败轨迹、双模型
strict 证据闭包与长程恢复 checkpoint；`MANIFEST.json` 逐文件记录来源和 SHA-256，而不是只复制
汇总分数。
