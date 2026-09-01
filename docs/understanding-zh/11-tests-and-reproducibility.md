# 测试与可复现性

## 四个质量门

在仓库根目录执行：

```bash
ruff check src/ benchmarks/ scripts/ tests/
ruff format --check src/ benchmarks/ scripts/ tests/
mypy src/ benchmarks/ scripts/
pytest tests/unit/ -v
python scripts/check_docs.py
```

浏览器集成：

```bash
pytest tests/integration/ -v --no-cov
```

本文档生成过程结束时会把实际结果写入下面的“本次验证快照”，不能用 README 徽章替代当前运行。

## 单元测试覆盖地图

| 区域 | 已覆盖行为 | 明显缺口 |
|---|---|---|
| Core/config/models | 默认、构造/env override、模型字段 | 所有配置实际接线的一致性 |
| Planner | native tools、JSON Schema、显式能力降级、prompt fallback、hard timeout、vision request | 真实 provider 兼容性、vision probe 准确性 |
| Agent | 输出/trace、checkpoint/恢复、策略切换、captcha block/人工等待 | 真实 planner 长程恢复收益、外部人工通知 |
| Loop detector | 五类 loop、window/reset、参数 fingerprint、checkpoint round-trip | nudge/replan 对模型行为的因果效果 |
| Tools/registry | 装饰器、全量 schema、发现、校验、异常、超时、policy | 外部网站变化与工具语义正确率 |
| Browser | captcha detector、priority、Controller actions、snapshot、link clicking | CDP/stealth 的真实浏览器分支仍有限 |
| Search | engine cascade、blocked、failure taxonomy/attempt trace | 实时搜索 DOM 与网站改版 |
| Parser | routing、quality、fallback、caption、三家 provider HTTP contract | 大规模真实文档质量与长期云端兼容性 |
| PDF | path containment、caption、figure resolve、metadata | 大规模文档质量、复杂表格/公式 |
| Evaluation | 终态/答案 assertion、JSON state、虚假完成率、动作有效率、typed v8 trace、30 题 dated matrix、BrowserGym/WebArena/VisualWebArena adapter | 内部开放网页尚缺三个共同日期；外部标准层尚缺部署后的 WA/VWA 后端 |

## Integration test 的准确解释

当前 integration suite 启动真实 headless Chromium，并使用运行后删除的临时 profile。
它覆盖 browser/registry/loop 生命周期、iframe/Shadow DOM/tab、上传下载、会话清理、
search-engine-only policy、strict trace、十一项基础网页交互，以及五项双 origin 复杂工作流。
其中 agent 生命周期用例使用 `StubPlanner`，交互 benchmark 使用预设动作；因此这些测试能证明
浏览器控制、策略、状态和判分链路真实运行，但不能被当作真实模型自主任务成功率，也不验证
外部 OCR、实时搜索引擎或第三方网站的长期稳定性。

## 推荐的最小真实验证

1. 本地静态 HTML：按钮、输入、ARIA-only 控件、shadow DOM、iframe、隐藏元素；验证 snapshot 与 selector grounding。
2. 本地故障服务：延迟、trickle、500、429、坏 JSON；验证 planner timeout/parse recovery。
3. 合成 PDF：文本 PDF、扫描 PDF、含 logo+Figure 1、跨页表格；验证 route、quality 和 figure resolution。
4. 固定的 self-hosted web tasks：避免真实网站非平稳性，测端到端 success。
5. 真实网站只做补充 robustness 检查，记录日期、浏览器版本、页面和账号状态。

## 通用网页交互 benchmark

`python -m benchmarks.suites.controlled_web.general --mode scripted-harness-baseline
--tool-set browser-only` 会启动确定性本地 HTTP 站点，并通过真实 Chromium 与
`WebAgent` 循环执行 11 项任务：多页商品导航、跨页人员查找、表单和下拉框、购物车服务端
状态修改、延迟动态 DOM、HTTP 503 恢复、登录、表格过滤、地点查询、预订与结账。页面终态、
独立 `/api/state` 和必要的答案事实/URL 分别判分，`done` 只代表模型声明完成，不决定成功。

`scripted-harness-baseline` 用于校准 benchmark 基础设施；`--mode agent` 才调用配置的 API/vLLM planner。
`--tool-set all` 和 `--disable-loop-detection` 分别提供工具集与循环检测消融。`results.json`
报告 success/score、agent completion、false completion、action validity、answer grounding、步数、延迟及类别成功率。
同时记录 planner 尝试、失败尝试与 token；成本需由调用方按具体 provider 定价换算。

## 带日期的开放网页 benchmark

`python -m benchmarks.suites.open_web.runner --manifest
benchmarks/manifests/open_web_smoke.json` 使用临时 profile 在多个公共站点运行真实 planner。每个网络任务必须
声明来源 URL、snapshot ID 与期待值有效期；过期清单会拒绝运行。判分同时检查页面、历史中
实际观察的 URL、答案必要/禁止事实和引用；每次摘要连同 manifest SHA-256 追加到
`ledger/time-slices.jsonl`。这解决了“无法重复量化开放网页波动”的基础设施缺口，但真正的跨日期证据
只能由未来多次真实运行积累，不能由一次 smoke 替代。

默认 `open-web-general-v2` 有 30 项/10 个公共域；其中 10 项从 `about:blank` 开始并在
search-engine-only contract 下做真实浏览器搜索，另外 20 项把页面读取与搜索波动分开测量。
`benchmarks.studies.open_web_matrix` 只写入当前 UTC 日期，要求 2–3 个不同 provider/model endpoint；
`benchmarks.studies.open_web_longitudinal` 会重读
保留的 `results.json`，重算报告、日期、study/task manifest、最终生效 config、task-set 及
agent/benchmark 源码绑定，并且仅在
每个模型有三个共同、每次 repetition 都恰好 30 项的日期时返回 ready。同日重复只参与当日均值，
不能充当新日期。该本地 append-only ledger 能发现本地证据漂移，但不能独立证明主机墙钟真实。

## 复杂双源沙箱 benchmark

`python -m benchmarks.suites.controlled_web.sandbox --mode scripted-harness-baseline` 启动两个动态 loopback origin，覆盖
fetch hydration + client-side route 的 SPA、cookie 登录保护页、跨 origin 多步表单、浏览器下载
再上传并校验 SHA-256，以及无支付沙箱结账。mutation `allow` 只绑定到启动后验证过的 loopback
origins。`scripted-harness-baseline` 校准工具/policy/evaluator；必须改用 `--mode agent` 才是模型成绩。

## PDF Figure 快路径 benchmark

`python -m benchmarks.suites.document_figures.fast_path`
会离线生成 10 份 PDF，覆盖矢量/栅格、caption 上/下、多图、logo 干扰、碎片化 path、
双栏、横版以及 mention/table 负例。ground truth 同时约束 figure number、page、crop coverage
（至少 85%）和 purity（至少 55%）。`results.json` 分开报告原始 detector precision/recall 与
默认 0.9 阈值下真正允许绕过云解析的 coverage、fallback rate 和 false bypass，避免把
“检测到候选”偷换成“可以安全跳过 parser”。
每次默认分配新的 execution；合成 PDF 位于 `inputs/corpus/`，渲染 crop 位于
`artifacts/renders/`，输入和派生产物不会混放。

这些 canonical runner 未传 `--output` 时会在
`outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/` 下分配新的、
不可覆盖的 execution；单 task 目录是 `runs/<task-id>/`。显式 `--output` 表示一个准确的
execution 根，而不是可反复覆盖的 suite 根。旧 flat module 仅保留一个发布周期的 CLI/import wrapper。

## 可复现运行记录模板

```text
commit:
date/timezone:
python/playwright/chromium versions:
model endpoint + model id（不记录 key）:
config snapshot:
task set + version:
randomness controls:
commands:
pass/fail/skip counts:
artifact path:
known external-state changes:
```

## 日期化验证记录

本文只维护验证方法和解释边界，不复制会随 checkout 变化的测试数量、覆盖率和模型分数。
2026-08-29 至 2026-09-01 的历史工程与外部 provider 探针已归档到
[工程验证记录](../research/results/engineering-validation-2026-08-29-09-01.zh-CN.md)；模型评测见
[研究结果索引](../research/results/README.md)。当前代码必须重新执行本页命令。

## 解释边界

测试通过证明被断言的行为在当前环境成立，不证明部署、外部 API、实时搜索、验证码恢复或研究基准性能。格式/类型检查通过也不证明 runtime dependency wiring 正确。
