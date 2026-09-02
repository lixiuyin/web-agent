# WebAgent

[![CI](https://github.com/lixiuyin/web-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lixiuyin/web-agent/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml) [![Lint: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff) [![Typed: mypy](https://img.shields.io/badge/typed-mypy-blue.svg)](https://mypy-lang.org/)

[English](README.md) · **简体中文**

一个自主视觉语言网页智能体：把自然语言指令转换为真实浏览器搜索、导航、PDF 阅读、图表解读和
有证据依据的最终报告。

![严格浏览器模式从搜索到 Figure 1 解读](docs/assets/strict-run-demo.gif)

该动图包含通过证书校验的 strict 轨迹的全部 17 张浏览器记录帧，末尾为抽取出的 Figure 1。
每帧播放两秒；说明文字来自 `trace.json`，失败动作还会显示记录中的原始错误。

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
| 工程质量 | 67 个注册工具、严格类型检查、Ruff 和 85% 分支覆盖率门槛 |

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

benchmarks/      可执行环境、套件、study 和 manifest
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
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
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

2026-09-02 的保留案例使用同一模型和 Qwen 报告任务完成四次运行，对比普通 API 增强发现和纯
浏览器执行。

| 模式 | 终态 | 动作数 | 动图 |
|---|---|---:|---|
| Hybrid | 完成 | 5 | [查看](outputs/runs/qwen-report-figure1-20260902/hybrid/trajectory-demo.gif) |
| Browser-grounded | 中止 | 11 | [查看](outputs/runs/qwen-report-figure1-20260902/browser-grounded/trajectory-demo.gif) |
| Browser-grounded retry | 中止 | 21 | [查看](outputs/runs/qwen-report-figure1-20260902/browser-grounded-r2/trajectory-demo.gif) |
| Strict | 完成；证书有效 | 17 | [查看](outputs/runs/qwen-report-figure1-20260902/strict/trajectory-demo.gif) |

两个 `success=false` 运行都执行了有效浏览器动作，但没有到达 PDF 下载、Figure 分析和成功
`done`。Strict 运行把缺失的身份/范围证据转化为明确下一步，拒绝伪装成 PDF 的 HTML，通过
`inspect_download_links` 公开 raw 下载地址后完成任务。

![在 GitHub 中观察到官方 Qwen 报告 PDF](outputs/runs/qwen-report-figure1-20260902/strict/observations/screenshots/step_014.jpg)

[基于实际 trace 的案例分析](docs/research/results/qwen-report-modes-2026-09-02.md)记录 planner
attempt、搜索回退、challenge、失败动作和解释边界。完整 Git/LFS 证据包位于
[`outputs/runs/qwen-report-figure1-20260902/`](outputs/runs/qwen-report-figure1-20260902/)。

## 评测状态

| 层级 | 范围 | 当前状态 |
|---|---|---|
| 仓库诊断层 | 开放网页、受控 sandbox 和强制恢复长程任务 | 已有一个完整共同日期，纵向证据仍属阶段性 |
| WebArena-Verified Hard | BrowserGym 原生任务/evaluator | 尚未运行；需要官方站点和 reset 校准 |
| VisualWebArena | BrowserGym 原生任务/evaluator | 尚未运行；需要官方站点、reset 校准和 evaluator 资源 |

不同层级的分数不做平均。精确日期结果见[结果索引](docs/research/results/README.md)，稳定方法见
[Evaluation protocol](docs/research/evaluation-protocol.md)，可执行套件见
[Benchmark 指南](benchmarks/README.md)。

## 文档导航

| 目标 | 入口 |
|---|---|
| 安装并运行 Agent | [Getting started](docs/guides/getting-started.md) |
| 选择 Hybrid、browser-grounded 或 strict | [Discovery modes](docs/guides/discovery-modes.md) |
| 排查 provider、浏览器和运行时问题 | [Troubleshooting](docs/guides/troubleshooting.md) |
| 配置运行时 | [Configuration reference](docs/reference/configuration.md) |
| 理解输出与恢复状态 | [Run artifacts](docs/reference/run-artifacts.md) |
| 查看浏览器和动作安全边界 | [Browser and security](docs/reference/browser-and-security.md) |
| 运行评测套件 | [Benchmarks](benchmarks/README.md) |
| 精读中文源码调用链 | [中文源码理解手册](docs/understanding-zh/README.md) |
| 浏览全部文档 | [Documentation index](docs/README.md) |

## 开发

```bash
ruff check src/ benchmarks/ scripts/ tests/
ruff format --check src/ benchmarks/ scripts/ tests/
mypy src/ benchmarks/ scripts/
pytest tests/unit/ -v
pytest tests/integration/ -v --no-cov
python scripts/check_docs.py
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
