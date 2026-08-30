# 项目总览

## 一句话定义

`web-agent` 是一个 Python 3.13+ 的单进程异步 CLI：它用 Playwright 驱动真实 Chromium，用远程或本地 OpenAI-compatible 模型选择下一项工具，再通过浏览器、搜索、文件与 PDF 工具完成开放式网页任务。

它的中心不是“一个模型”，而是一个受步骤数、任务时间、工具时间和连续失败数约束的 Agent 运行时。

## 项目解决的问题

输入是自然语言字符串，例如：

```text
Find the most recent Qwen technical report and interpret Figure 1.
```

期望输出是 `AgentResult`。未传 `--output` 时，CLI 在
`outputs/runs/<UTC-date>/<model>/<task>-<run-id>/` 下分配唯一 run；成功调用 `done` 后形成：

```text
<run>/
├── manifest.json
├── trajectory/
│   ├── trace.json                 # 最新一轮的 schema-v8 审计轨迹
│   ├── verification.json          # strict eval 时存在
│   └── turns/turn-NNN.json        # 每轮不可变快照
├── observations/screenshots/
├── control/checkpoints/latest.json
├── artifacts/
│   ├── downloads/                 # 下载的 PDF/文件
│   ├── documents/<doc-id>/        # 每篇文档独立的解析产物
│   ├── figures/
│   └── files/
├── result/
│   ├── summary.txt                # 最新一轮结果
│   ├── attachments/
│   └── turns/turn-NNN/
│       ├── summary.txt
│       └── attachments/
└── evaluation/
```

`trajectory` 是执行证据，`control` 是恢复状态，`artifacts` 是任务文件，`result` 是 Agent
声明，`evaluation` 是独立判分；这些概念不能混用。可比较 benchmark 的 execution 位于
`outputs/studies/<suite>/executions/<UTC-date>/<model>/<condition>/<execution-id>/`，每项任务仍在
其 `runs/<task-id>/` 下；历史目录经清单与 hash 校验后进入 `outputs/legacy/`。
普通 interactive follow-up 复用同一个 run，step/turn 编号单调递增，并用 `turns/` 保留不可覆盖
快照；strict/search-only 为保持单次连续证书而禁止多轮。

注意：源码没有通用“引用生成器”；README 中“cited answer”属于产品目标描述，最终答案是否带引用依赖 planner 找到的数据和 `done.summary`。

## 九个主要域

| 域         | 职责                        | 核心入口                                     |
| --------- | ------------------------- | ---------------------------------------- |
| `core`    | 配置、Pydantic 状态模型、Protocol | `AgentConfig`, `BrowserState`, `Planner` |
| `agent`   | 循环、历史、hook、循环检测、checkpoint、策略切换 | `WebAgent.run`                 |
| `browser` | Chromium 生命周期、动作、页面快照     | `BrowserController`, `take_snapshot`     |
| `planner` | provider tools/JSON Schema、视觉能力探测、响应解析 | `APIPlanner.plan_action`       |
| `tools`   | 60+ 个能力的注册、schema、policy 与执行 | `ToolRegistry`, `ToolExecutor`          |
| `parser`  | 文档画像、路由、质量门和 OCR fallback | `parse_structured_async`                 |
| `evaluation` | typed trace、run/study 布局、失败模式、校准、transfer 与终态判分 | `RunLayout`, `BenchmarkRunner`, `RunTraceV8` |
| `schemas` | 随包发布的稳定 run/study wire schema | `run-trace-v8.schema.json`, `study-manifest-v1.schema.json` |
| `utils`   | 路径约束、图像/PDF辅助和兼容层         | `resolve_pdf_path` 等                     |

## 项目不是什么

- 不是训练框架：没有模型训练、微调或强化学习代码。
- 不是成熟的通用 benchmark 产品：已有确定性任务集、终态/答案判分器、agent runner 和带日期的开放网页清单，但尚未积累跨模型、跨日期的大样本结果。
- 不是多 Agent 系统：核心是单个 Planner 驱动的单循环。
- 不是浏览器扩展：它从 Python 进程启动 Chromium。
- 不是验证码求解器：可记录、失败阻断，或在 headed 模式轮询等待人工清除，但绝不自动求解/绕过。
- 不是原生的跨 provider SDK：Planner 只实现 OpenAI-compatible HTTP 格式；Azure、Claude、Gemini 等服务必须提供兼容端点。

## 三种 Planner 启动状态

| 条件 | 实例 | 实际意义 |
|---|---|---|
| 有 `model_api_url` 和 `model_api_key` | `APIPlanner` | 调远程 OpenAI-compatible endpoint |
| 无远程凭证但 `use_vllm=True` | `APIPlanner` | 调本地 vLLM compatible endpoint |
| 两者都没有 | `StubPlanner` | 不做真实规划，通常立即 `done` |

StubPlanner/确定性 planner integration 验证浏览器启动、注册、循环、策略、恢复与关闭；
`scripted-harness-baseline` 还校准 11 项基础交互和 5 项双源复杂沙箱工作流。它们都不等于真实模型自主任务
验证；后者必须使用 agent 模式。30 项公开网页清单和纵向 runner 已能采集真实结果，但三个
日期、2–3 个模型的结论只能由实际日期上的运行积累，不能由脚本或日期参数伪造。

## 工程亮点与研究含量

工程上较完整的部分是：清晰的循环边界、provider 级结构化动作、可恢复状态、工具注册与
授权、墙钟超时、路径 containment、parser cascade、版本化 trace、CI/release gate 和失败结果
对象化。研究上可进一步发展的是：页面表征、可执行元素 grounding、策略切换的真实收益、
文档质量路由与跨模型/跨日期评测。

源码按系统职责留在 `src/webagent/`；可控环境、单套件运行与重复研究分别放在
`benchmarks/environments/`、`benchmarks/suites/`、`benchmarks/studies/`；研究协议与失败证据规则放在
`docs/research/`。当前仓库没有给出所有设计相对 baseline 的量化比较，所以应称为
“研究原型/研究平台”，不应把工程机制本身直接称作已验证的研究贡献。

## 历史规模快照

2026-08-29 的旧快照是 774 passed；当前测试数量已经超过 900，固定数字容易随新增用例失效。
发布前应直接执行 AGENTS.md 的四道 gate 与完整 real-browser integration，并把当次命令结果写入
[11-tests-and-reproducibility.md](11-tests-and-reproducibility.md)，而不是沿用徽章或旧快照。
