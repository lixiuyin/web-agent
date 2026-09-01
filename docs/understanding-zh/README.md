# web-agent 中文源码理解手册

这套文档面向准备 Web Agent、LLM/VLM Agent 与可靠智能体方向 RA 的读者。它不是 README 的翻译，而是对当前 checkout 的源码、测试与实际接线关系进行重建。所有“已实现”判断以源码调用链为准；外部服务效果、真实网站兼容性和模型质量若未实际运行，会明确标为未验证。

项目文档的总入口与各目录职责见 [文档索引](../README.md)。

## 推荐入口

1. 每天打开：[00-reading-guide.md](00-reading-guide.md)——阅读顺序、学习方法和进度检查。
2. 第一次理解系统：[01-project-overview.md](01-project-overview.md) → [02-architecture-and-dataflow.md](02-architecture-and-dataflow.md)。
3. 顺着真实执行路径：[03-setup-config-and-lifecycle.md](03-setup-config-and-lifecycle.md) → [05-agent-loop.md](05-agent-loop.md) → [06-browser-system.md](06-browser-system.md) → [07-planner-system.md](07-planner-system.md) → [08-tool-system.md](08-tool-system.md)。
4. 理解论文任务：[09-parser-and-pdf-pipeline.md](09-parser-and-pdf-pipeline.md)。
5. 理解实验产物与证据边界：先读 [../research/README.md](../research/README.md) 及其
   experiment lifecycle/failure taxonomy，再读
   [11-tests-and-reproducibility.md](11-tests-and-reproducibility.md)。
6. 形成研究判断：[10-error-handling-and-fallbacks.md](10-error-handling-and-fallbacks.md) → [12-implementation-status-audit.md](12-implementation-status-audit.md) → [13-research-analysis.md](13-research-analysis.md)。
7. 准备申请与面试：[14-ra-interview-preparation.md](14-ra-interview-preparation.md) 和 [15-study-plan.md](15-study-plan.md)。

## 文档地图

| 文件 | 解决的问题 | 类型 |
|---|---|---|
| `00-reading-guide` | 应该按什么顺序读、怎样验证自己真的懂了 | 日常入口，手工维护 |
| `01-project-overview` | 项目是什么、不是什么、边界在哪里 | 总览，手工维护 |
| `02-architecture-and-dataflow` | 模块怎样连接、数据怎样流动 | 架构，随源码维护 |
| `03-setup-config-and-lifecycle` | 如何安装、配置、启动和释放资源 | 运维，随 CLI/config 维护 |
| `04-core-models-and-protocols` | 核心类型与结构化接口 | API 契约，随模型维护 |
| `05-agent-loop` | Observe–Think–Act–Record 如何执行 | 核心源码精读 |
| `06-browser-system` | Playwright、CDP、snapshot、stealth、captcha | 子系统源码精读 |
| `07-planner-system` | prompt、视觉探测、响应解析与 API 调用 | 子系统源码精读 |
| `08-tool-system` | 67 个工具怎样注册、导出 schema、校验和执行 | 子系统源码精读 |
| `09-parser-and-pdf-pipeline` | PDF/OCR cascade 与图表解析 | 子系统源码精读 |
| `10-error-handling-and-fallbacks` | 超时、重试、降级和错误传播 | 横切机制 |
| `11-tests-and-reproducibility` | 测试覆盖什么、如何复现 | 验证记录 |
| `12-implementation-status-audit` | 声明、接线、测试和风险是否一致 | 审计结果，更新源码后重跑 |
| `13-research-analysis` | 研究问题、实验、指标和局限 | 研究判断，人工审阅 |
| `14-ra-interview-preparation` | 中英文介绍、理解题、面试题 | 申请材料 |
| `15-study-plan` | 7 天/14 天学习与动手路线 | 日常入口 |
| `appendix-*` | 术语、调用图、输入输出 schema | 查询型附录 |
| `../research/` | run/study artifact contract、失败证据、校准与 transfer 边界 | 研究工作流 |
| `../research/evaluation-protocol` | 两层方法、指标、控制变量与 readiness | 稳定评测协议 |
| `../research/results/` | 日期化模型结果、验证记录与轨迹案例 | 实证快照索引 |

## 证据标签

- **源码确认**：当前源码存在，而且能追踪到调用方。
- **测试确认**：当前测试直接覆盖该行为。
- **文档声明**：README/注释如此描述，但没有足够运行证据。
- **静态推断**：从代码结构推断，尚未做真实浏览器或外部 API 验证。
- **研究建议**：未来方向，不是项目已有贡献。

## 源码块约定

关键函数在相应章节中给出完整原代码块，并注明调用者、下游、输入、输出和失败路径。辅助函数不整文件复制，而以相对源码链接定位。这样既能逐行学习，也避免文档变成一份难维护的源码镜像。

## 更新规则

改动 `core/models.py` 或 `core/config.py` 后更新输入输出附录；改动 `agent/loop.py` 后更新架构、错误与时序图；改动 `evaluation/artifacts.py` 或 benchmark runner 后同时更新
`docs/research/` 与 benchmark 命令；新增 `@tool` 后更新工具清单；改动 parser provider 或质量阈值后更新 PDF pipeline；最后重跑 [11-tests-and-reproducibility.md](11-tests-and-reproducibility.md) 中的检查。
