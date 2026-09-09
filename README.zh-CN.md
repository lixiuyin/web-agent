# WebAgent

[![CI](https://github.com/lixiuyin/web-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lixiuyin/web-agent/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml) [![Lint: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff) [![Typed: mypy](https://img.shields.io/badge/typed-mypy-blue.svg)](https://mypy-lang.org/)

[English](README.md) · **简体中文**

一个自主视觉语言网页智能体：把自然语言指令转换为真实浏览器搜索、导航、PDF 阅读、图表解读和
有证据依据的最终报告。

![严格浏览器模式从搜索到 Figure 1 解读](docs/assets/strict-run-demo.gif)

该动图包含 2026-09-09 Qwen paired R5 strict 轨迹的全部 21 张 viewport 截图，末尾为抽取出的
Figure 1。浏览器帧每张播放两秒，最终 Figure 保留六秒。动图只是视觉预览；独立任务断言和
反捷径证书共同证明这次运行通过。

## WebAgent 是什么？

WebAgent 通过 **Observe → Think → Act → Record** 循环驱动真实 Chromium 浏览器。系统把截图
和结构化 DOM 快照组合为状态，要求 OpenAI-compatible planner 每次产生一个类型化工具调用，在
运行时策略约束下执行，并保留可审计轨迹。

运行时不绑定单一模型，支持本地 vLLM，并包含 PDF 下载、OCR/parser 路由、按真实 caption 定位
Figure，以及用视觉模型解读抽取图片的文档智能管线。

## 技术亮点

| 领域 | 能力 |
|---|---|
| Agent 运行时 | 基于 Protocol 的 planner、tool、hook 接口和 checkpoint 执行 |
| 多模态状态 | DOM→Markdown、自适应截图和自动视觉能力探测 |
| 结构化动作 | Provider 原生 function tools 与有界 schema/prompt fallback |
| 浏览器可靠性 | 稳定性等待、循环检测、搜索回退和显式 CAPTCHA 处理 |
| 证据 | 版本化 trace、strict 反捷径证书和独立终态判分 |
| 文档智能 | 按 caption 定位 Figure，配合 quality gate parser cascade |
| 评测 | 仓库诊断套件与独立 BrowserGym WebArena/VWA 证据层 |
| 工程质量 | 67 个注册工具、严格类型检查、Ruff 和 85% 综合覆盖率门槛（含分支统计） |

## 架构

`Planner`、`Tool` 和 `AgentHook` 三个结构化接口把模型规划、执行能力和生命周期观测分开。

![WebAgent 系统架构：策略过滤后的规划工具、浏览器执行、文档解析、checkpoint 与轨迹证据](docs/assets/architecture-overview.svg)

```text
src/webagent/
├── core/        Protocol、数据模型和配置
├── agent/       主循环、历史、策略、hook 和 checkpoint
├── browser/     Playwright 控制器、snapshot、CDP 和 CAPTCHA 检测
├── planner/     API/本地 planner、provider 模式和结构化解析
├── parser/      OCR provider、quality gate 和本地 PDF 恢复
├── tools/       registry、暴露/风险策略和内置工具
├── evaluation/  trace 校验、指标、study 和 portfolio
├── schemas/     随包发布的稳定 wire schema
└── utils/       路径、图像、PDF、日志和运行时辅助

src/webagent/benchmarks/      可执行环境、套件、study 和 manifest
docs/            用户指南、参考、研究记录和源码学习材料
outputs/         默认忽略；可发布经过审阅的选定证据包
```

每一步先观察稳定浏览器状态，再构造 planner context、选择被允许的工具、在时间和风险边界内执行、
记录结果，并原子更新普通运行的恢复状态。

![WebAgent 单步流程：稳定观察、CAPTCHA 处理、规划、写前 checkpoint、工具执行与证据提交](docs/assets/agent-step-sequence.svg)

Figure 请求按编号和 caption 解析，而不是按抽取顺序，因此 logo 或封面装饰不会被误当成
“Figure 1”。

![按 caption 定位 PDF Figure：本地快路径或质量门控的云端解析级联，并以本地解析作为最后回退](docs/assets/figure-resolution-flow.svg)

可编辑的 Graphviz 图源与可复现渲染入口见
[`docs/diagrams/`](docs/diagrams/README.md)。

## 快速开始

```bash
uv sync
uv run playwright install chromium
cp .env.example .env
```

在 `.env` 中设置 `AGENT_MODEL_API_URL`、`AGENT_MODEL_API_KEY` 和
`AGENT_MODEL_NAME`，然后运行：

```bash
webagent \
  --task "Find the most recent Qwen technical report and interpret Figure 1" \
  --headless
```

未配置凭证时会使用 `StubPlanner`：它能展示生命周期行为，但无法自主完成开放式任务。

常用模式：

```bash
# 隐藏直接报告/GitHub/arXiv 工具，只使用浏览器可见发现
webagent --task "..." --discovery-mode browser-grounded --headless

# 隔离的浏览器搜索评测，并生成校验证书
webagent --task "..." --strict-eval --headless

# 本地 OpenAI-compatible vLLM server
webagent --task "..." --use-vllm --headless
```

恢复、校验、交互模式和产物检查见[入门指南](docs/guides/getting-started.md)，三种发现契约见
[Discovery modes](docs/guides/discovery-modes.md)。

## 已记录的效果展示

当前动图来自 2026-09-09 paired R5 验证的 Qwen endpoint。它从 `about:blank` 开始，通过浏览器
可见搜索发现并比较候选，打开官方 `QwenLM/Qwen3.8-Flash-Next` 仓库与
`tech_report.pdf`，下载 PDF 后解读 Figure 1。Qwen 与 GLM endpoint 都通过 10/10 独立断言和
6/6 certificate checks；已恢复的搜索失败仍保留在指标中。

| 模型 | 独立判分 | Certificate | 浏览器帧 | 失败动作 |
|---|---:|---:|---:|---:|
| Qwen3.8-Flash | 10/10 | 6/6 | 21 | 3 |
| GLM-5.3-Flash | 10/10 | 6/6 | 16 | 2 |

[Paired 验证记录](docs/research/results/qwen-strict-search-2026-09-09.zh-CN.md)说明 source hash、
验收规则、Figure 1 结论、observation 完整性、GIF 来源和外推限制。较早的
[2026-09-02 模式对比](docs/research/results/qwen-report-modes-2026-09-02.md)只保留为历史分析；
已清理的本地 output bundle 不是当前 GIF 的来源。

### 当前本地评测快照

下列数字于 2026-09-09 直接读取自机器可读报告。完整的本地 R7 campaign 位于
`outputs/campaigns/generality-2026-09-09-rerun-r7/`；batch 状态为 `completed`，两个指定
endpoint 均已评测，没有排除项。经审阅的子集已保存在
[冻结证据包](outputs/published/2026-09-09/README.md)。

| 模型 | Open web | Sandbox | Long horizon | 总计 |
|---|---:|---:|---:|---:|
| GLM-5.3-Flash | 30/30 | 5/5 | 1/1 | 36/36 |
| Qwen3.8-Flash | 30/30 | 4/5 | 1/1 | 35/36 |
| **合计** | **60/60** | **9/10** | **2/2** | **71/72（98.61%）** |

Qwen 专项 paired strict-search 的完整本地结果位于
`outputs/validation/2026-09-09-qwen-paired-r5/`，两条可复验的 hash 绑定 trace closure 已收入
冻结证据包。两个模型都完成该任务：各自通过 10/10 必需断言与 6/6 轨迹证书检查。
Portfolio 的 `insufficient` 不表示 campaign 未完成，而是因为目前只有 1 个共同完整日期，
未达到预注册纵向门槛要求的 3 个日期。

因此，当前目录共有 74 份 canonical、非 shard 的任务判分：R7 的 72 份加 paired R5 的 2 份，
其中 73 份通过，唯一失败如下分析。由于 paired R5 使用不同的 task set 与 source fingerprint，
二者分开报告、不合并计算总分；`diagnostics/` 下的文件是运行日志，不是计分结果。

原始生成的 `outputs/` 默认被 gitignore，不作为长期文档真相。允许跟踪的冻结证据包用 58 个物理文件
（约 13 MB）保留 324 条证据记录，包括汇总报告、两条 strict 轨迹的完整复验闭包、唯一失败轨迹和
长程恢复证据。大量哈希绑定的小文件收入 3 个可确定重建的归档；
[清单](outputs/published/2026-09-09/MANIFEST.json)记录每条证据的来源、用途、存储位置、字节数和 SHA-256。
对应的文字记录见
[R7 campaign 记录](docs/research/results/generality-campaign-2026-09-09.zh-CN.md)与
[paired strict-search 记录](docs/research/results/qwen-strict-search-2026-09-09.zh-CN.md)。

### 失败轨迹分析

只有一个终局失败任务：Qwen 的 `sandbox_checkout`。该轨迹运行 18 步、包含 17 个非终止
动作，出现 2 次工具动作失败与 1 次 planner attempt 失败，最终得分 0.375。Evaluator 确认
购物车内恰有 1 件 Orbit Notebook，两个要求的 origin 也均被访问；但终态没有
`/order/complete` URL 或完成标记，地址未保存为 `42 Orbit Road`，terms 未接受，订单也未提交。

轨迹证据支持以下因果链：

1. 添加商品后，第 2 步已经位于正确 checkout 页面；当时 observation 中的地址输入框、terms
   checkbox 与提交按钮都是可见、启用、可交互的 DOM 控件，所以根因不是 DOM 缺失、截断或
   截图不可见。
2. Planner 没有填写并点击这些控件，而是只抽取页面文本，随后猜测一个从未观察到的 host root
   URL；browser-grounding policy 正确拒绝了该动作。
3. 多步 `back` 进入了浏览器历史中另一条 sandbox 流程留下的 `/upload` 与 `/files` 页面；模型
   继续追随无关页面，没有回到已知 checkout 控件。跨任务导航历史未清空是一个促成偏航的隔离
   弱点，但不是充分原因，因为偏航前所需控件已经可以直接操作。
4. 到最后一个动作预算时，controller 要求调用 `done`。最终答案明确承认订单没有完成验证，
   自报成功概率只有 0.15。

汇总报告将其标记为 `false_completion`，原因是当前 evaluator 把成功执行 `done` 映射成
`agent_reported_success=true`；这个字段与本例最终答案的语义并不一致。任务失败及 0.375 得分
本身成立，但不能进一步解读为“模型自信地宣称已经成功”。Paired R5 中 Qwen 的 3 次、GLM 的
2 次失败动作均为有界搜索失败，随后成功恢复；这些动作级失败被如实保留，但不属于失败轨迹。

## 评测状态

| 层级 | 范围 | 当前状态 |
|---|---|---|
| 仓库诊断层 | 开放网页、受控 sandbox 和强制恢复长程任务 | R7 完成 71/72（GLM 36/36；Qwen 35/36）；已有一个共同日期和一次独立通过的 Qwen strict 任务，纵向证据仍属阶段性 |
| WebArena-Verified Hard | BrowserGym 原生任务/evaluator | 尚未运行；需要官方站点和 reset 校准 |
| VisualWebArena | BrowserGym 原生任务/evaluator | 尚未运行；需要官方站点、reset 校准和 evaluator 资源 |

不同层级的分数不做平均。精确日期结果见[结果索引](docs/research/results/README.md)，稳定方法见
[Evaluation protocol](docs/research/evaluation-protocol.md)，可执行套件见
[Benchmark 指南](src/webagent/benchmarks/README.md)。

## 文档导航

| 目标 | 入口 |
|---|---|
| 安装并运行 Agent | [Getting started](docs/guides/getting-started.md) |
| 选择 Hybrid、browser-grounded 或 strict | [Discovery modes](docs/guides/discovery-modes.md) |
| 排查 provider、浏览器和运行时问题 | [Troubleshooting](docs/guides/troubleshooting.md) |
| 配置运行时 | [Configuration reference](docs/reference/configuration.md) |
| 理解输出与恢复状态 | [Run artifacts](docs/reference/run-artifacts.md) |
| 查看浏览器和动作安全边界 | [Browser and security](docs/reference/browser-and-security.md) |
| 运行评测套件 | [Benchmarks](src/webagent/benchmarks/README.md) |
| 精读中文源码调用链 | [中文源码理解手册](docs/understanding-zh/README.md) |
| 浏览全部文档 | [Documentation index](docs/README.md) |

## 开发

```bash
ruff check src/ scripts/ tests/
ruff format --check src/ scripts/ tests/
mypy src/ scripts/
pytest tests/unit/ -v
pytest tests/integration/ -v --no-cov
uv run python scripts/check_docs.py
```

工具、planner、代码风格和 PR 规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，可复现打包见
[Release procedure](docs/operations/release.md)。

## 作者与项目沿革

最初项目来自港大 STAT7008A 团队课程项目，[Li Xiuyin](https://github.com/lixiuyin) 担任组长；
原仓库为 [RanJu1122/Web-Agent](https://github.com/RanJu1122/Web-Agent)。本仓库是 Li Xiuyin 在
课程结束后的独立维护与重写版本，详细贡献沿革保留在 Git 历史和
[CHANGELOG](CHANGELOG.md) 中。

## 致谢

项目使用 [Playwright](https://playwright.dev/)、[PyMuPDF](https://pymupdf.readthedocs.io/)、
[Pydantic](https://docs.pydantic.dev/) 以及兼容 Marker/MinerU/PaddleOCR 的文档服务。

## 许可证

[MIT](LICENSE) © WebAgent contributors
