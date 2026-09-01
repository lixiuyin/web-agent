# 实现状态审计

## 审计表

| 功能/声明 | 实际入口 | 调用状态 | 测试 | 风险 | 结论 |
|---|---|---|---|---|---|
| Observe–Think–Act–Record | `WebAgent.run` | 主路径 | Agent output + integration | 低 | 源码确认 |
| Planner Protocol 可替换 | `core/protocols.py` | API/Stub 实现 | planner tests | 低 | 源码确认 |
| 60+ tools auto-discovery + schema | `registry.py`, `tools/schemas.py` | 主路径 | registry/schema tests | 低/中 | 全量工具具有 provider schema；构造失败保留原始异常链 |
| 截图+DOM 同请求 | `APIPlanner.plan_action` | 视觉支持时 | 直接单测 | 中 | 测试确认 |
| 自动视觉探测 | `APIPlanner.load` | APIPlanner 启动 | 部分 probe 单测 | 中 | 接线确认；真实准确性未验证 |
| local vLLM | CLI 仍构造 APIPlanner | 配置开启时 | CLI selection | 中 | OpenAI-compatible endpoint 路径 |
| provider structured action | `APIPlanner._call_structured` | 默认 `auto` | transport/schema/fallback tests | 低/中 | native tools→JSON Schema→prompt JSON；仅明确能力错误降级 |
| checkpoint/resume | `agent/checkpoint.py`, `WebAgent.run` | 普通模式主路径 | checksum/restore/replay tests | 中 | task/config/source 绑定；不保存 cookie；strict 禁止续跑认证 |
| strategy/replan | `agent/state.py`, `agent/strategy.py` | 默认主路径 | signal/switch/state tests | 中 | 只存公开 milestone/evidence，不存隐藏思维链 |
| 五信号 loop detection | `LoopDetector` | 默认主路径 | 充分单测 | 中 | 含滚动抖动；状态可 checkpoint；触发 nudge/replan |
| Captcha 处置 | `loop.py` | 检测、事件、block/人工等待 | detector + loop tests | 中 | 不求解/绕过；headed 可等待人工清除，strict 未解决则证书无效 |
| Stealth 可配置 | config/controller | CLI 传入 | CLI unit | 中 | 默认/strict 使用原生 Playwright；仅显式 `true` 使用 enhanced profile |
| CDP element extraction | `snapshot.py` | 默认主路径 | 无 | 高 | AX→DOM selector/bbox grounding 可疑 |
| ad filtering 开关 | config | Agent/DOM tool 传入 | snapshot unit | 低 | 同时控制元素与 HTML 过滤 |
| post-action wait 配置 | config | Agent loop | Agent unit | 低 | 毫秒转秒，拒绝负数 |
| Bing→Yahoo→DDG / Google opt-in | `SearchTool` | search 工具 | mock 单测 + 在线诊断 | 中/高 | 默认避免 Google 人机认证；真实 DOM 非平稳 |
| 官方 GitHub 报告发现 | `GitHubSearchTool` | search 工具 | 单测 + QwenLM 在线验证 | 中 | 无 token 配额低；raw/Atom 有兜底 |
| 多源官方报告聚合 | `OfficialReportSearchTool` | 默认 hybrid/API-augmented 模式暴露 | fixture 单测 + QwenLM 历史在线验证 | 中 | 精确 owner、报告 PDF 与 commit 日期可计入 Hybrid 身份/范围证据；arXiv authorship 仍需独立核验 |
| 严格无捷径评测 | CLI/Browser/policy/run trace | `--strict-eval` | 单测 + real-browser integration | 中 | strict 强制 search-only；certificate 检查单一 run 与 provenance，但不能自行证明答案语义正确 |
| 通用网页终态/答案评测 | `evaluation/` + sandbox benchmarks | benchmark 路径 | unit + real-browser calibration | 中 | 11 项基础 + 5 项双源复杂工作流；脚本校准不是模型成绩 |
| 带日期开放网页评测 | `benchmarks.suites.open_web.*`、`benchmarks.studies.open_web_*` + 30-task manifest | benchmark 路径 | manifest/matrix/runner unit | 中/高 | 10 项真实搜索发现；三日期/多模型成绩尚待实际积累 |
| typed trace/release | `evaluation/trace_schema.py`, `release.py`, `.github/` | 每次运行/CI | migration/verifier/package tests | 中 | v8/未知版本 fail closed；CI 远端及 PyPI trust 尚需仓库配置后验证 |
| OCR cascade | `parser/cascade.py` | PDF parse 主路径 | route/fallback/contract tests | 中 | Datalab、MinerU、PaddleOCR 均完成带时间戳的在线冒烟验证；外部协议仍可能变化 |
| Quality Gate | `_quality.py` | cloud result 后 | 单测 | 中 | 仅明显失败启发式 |
| Local PyMuPDF fallback | `providers/local.py` | cloud 全失败 | 单测 | 低/中 | 文本 PDF 可用；扫描件有限 |
| Figure N caption resolve | `_build.py`, QA tools | PDF figure tools | 单测 | 中 | 合成情景确认；真实 provider 需评测 |
| 安全路径 containment | `utils.paths`, file/PDF tools | 多工具 | 多个负向单测 | 低 | 测试确认 |
| 输出自动保存 | `loop.py` | done 后 | 单测 | 低 | summary/figure 确认 |
| 历史与审计轨迹 | `AgentResult.history` + `trajectory/trace.json` | 主路径 | history/agent/integration tests | 低 | trace 压缩且不含截图/完整原始 payload |
| research artifact contract | `evaluation/artifacts.py` + `docs/research/` | CLI/benchmark 主路径 | run/study/migration tests | 低/中 | run 的证据、控制、结果、判分分区；study execution 不覆盖；legacy 按 hash 清点 |
| README `.env.example` | README | 文件存在 | config tests | 低 | 可复制模板；不得提交真实凭证 |
| 覆盖率与集成门 | pyproject/AGENTS | unit branch ≥85% + real browser integration | 本次全通过 | 低 | 数量会变化，以测试命令为准 |
| “cited answer” | README | 无统一 citation schema | — | 高 | 依赖 planner 文本，不是保证 |

## 优先修复建议

P0（影响能力结论）：按 2–3 个真实模型、三个实际日期运行完整 30 项开放网页 matrix；建立 AX backend node 到 Playwright locator/CSS selector 的可靠映射。

P1（影响可配置性和实验）：为 browser snapshot/CDP 增加更多真实页面测试，并评估过滤开关和 stealth 的实际影响。

P2（维护与文档）：从工具 schema 自动生成参考文档；拆分剩余高复杂度模块；让 test badge 从 CI 生成，并在 GitHub/PyPI 配置受保护发布环境。

## 代码结构终审（2026-08-30）

- 当前 88 个 source modules；先前验证的内部 import graph 无循环。
- 66 个 `@tool` 名称唯一，且每个工具都实现 `validate_params` 与 `execute`，并具有可导出的参数 schema。
- Vulture（80% confidence）未发现未使用符号；AST 重复体检查只剩三个无行为逻辑的同形工具构造器。
- 已删除内部 Chandra 兼容层；PDF 工具直接依赖 `webagent.parser`，共享路径解析、缓存和错误归一化集中在 `tools/builtin/_pdf_common.py`。
- 标准 Ruff 规则、格式、strict Mypy 均通过；额外的 `C901` 审计仍报告 5 个 12–15
  复杂度函数（agent run/think、evaluator observe、assertion validation、tool execute），属于后续
  可维护性重构项，不是当前配置的发布门禁。
- unit suite 为 944 passed，branch coverage 86.63%；浏览器、外部 provider 与真实 planner 的
  线上分支仍低于纯逻辑模块。因此当前代码不能宣称达到绝对“最高规范水平”。

## 已确认与需实验区分

静态源码可以确认控制流、参数传递和异常路径；不能确认 stealth 绕过率、搜索实时成功率、vision probe 在各模型上的误判、OCR 服务 schema 与 benchmark success。后四类必须通过带时间戳的外部实验回答。
