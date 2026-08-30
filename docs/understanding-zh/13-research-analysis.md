# RA 研究分析与实验设计

## 项目在 Web Agent 研究中的位置

它是一个“混合感知 + 工具动作 + 规则可靠性护栏”的端到端原型：状态同时含截图和压缩 DOM；模型每轮选一个工具；运行时提供超时、循环提示、搜索/PDF fallback。当前已有确定性 real-Chromium harness、终态与答案事实判分、统计报告、工具集/循环检测消融入口，以及带日期的开放网页 manifest/history。运行证据按 trajectory/control/observations/artifacts/result/evaluation 分区；benchmark 按不可覆盖 execution 保存，并把 development、held-out task、held-out setting 与缺失 confidence 显式保留。仍缺的是跨模型、跨日期、足够样本量的实证，而不是 runner 本身。

相关基准可用于定位研究问题：[WebArena](https://arxiv.org/abs/2307.13854) 强调可复现的功能型网站和长程任务；[Mind2Web](https://arxiv.org/abs/2306.06070) 提供真实网站任务与动作序列，特别适合元素筛选/grounding；[WebVoyager](https://arxiv.org/abs/2401.13919) 研究多模态模型在真实网站的端到端交互。这里仅把它们作为候选 baseline/环境，没有声称本项目已在这些基准上运行。

## 工程机制与潜在研究问题

| 已有机制 | 工程作用 | 可研究的问题 |
|---|---|---|
| screenshot + DOM Markdown | 给 planner 两种观测 | 两种模态何时互补、何时冲突？ |
| heuristic Top-N | 限制 token | 在固定预算下如何最大化 action recall？ |
| AX Tree | 语义元素 | 如何得到稳定、可执行的 grounding？ |
| history + nudge | 减少重复 | 什么 loop signal 能预测失败且不过早干预？ |
| parser cascade | 服务降级 | 如何在质量、延迟、成本间动态路由？ |
| caption-aware figures | 避免 logo 误配 | 如何评估跨 provider 的 figure grounding？ |
| hard timeout/failure budget | 控制长尾 | 预算如何影响成功率、成本和恢复能力？ |

## 五个可执行 RA 方向

### 1. 可执行元素 Grounding

- 研究问题：AX/DOM/视觉元素如何映射成跨页面变化仍可执行的 locator？
- 假设：融合 accessible name、role、DOM path 和坐标，比当前 CSS-only 输出提高 action execution rate。
- Baseline：当前 AX path；当前 JS CSS path；text selector。
- 数据：本地可控页面 + Mind2Web 子集；记录目标元素。
- 指标：candidate recall@K、selector execution success、跨 DOM perturbation 稳定率、token 数。
- Ablation：去掉 screenshot/role/text/geometry/历史。
- MVP：制作 50 个本地页面状态，每个自动扰动 class/id/nesting，比较三种 selector。

### 2. Snapshot Token Budget 分配

- 研究问题：固定 6k 字符/50 元素是否最优？
- 假设：任务条件化选择正文与控件，比固定 Top-N 提高成功且降低 token。
- Baseline：当前 heuristic；DOM first-N；仅 screenshot。
- 指标：target recall、任务成功率、输入 token、延迟、cost-normalized success。
- MVP：从 100 个状态离线预测下一动作，不先跑完整网站。

### 3. Loop Detection 与恢复策略

- 研究问题：nudge 应何时触发、触发后选择什么恢复动作？
- 假设：结合工具结果、页面变化和不确定性比固定 threshold 更少误报。
- Baseline：当前五信号；仅重复动作；无 detector。
- 指标：loop precision/recall、time-to-recovery、额外步骤、任务成功率。
- MVP：构造 selector 失效、登录墙、双页振荡、正常多步 PDF 分析四类 trace。

### 4. 成本感知 Parser Routing

- 研究问题：能否根据文档画像和在线质量预测选择 provider，而非固定序列？
- 假设：学习型或 bandit router 在相同质量下减少 API 延迟/成本。
- Baseline：当前 route；固定单 provider；oracle best provider。
- 数据：文本、扫描、公式、表格、图文混排 PDF，人工或规则标注质量。
- 指标：字符/结构准确性、table/figure recall、延迟、费用、fallback 次数。
- MVP：先记录每个 provider 对 30 篇文档的结果，不改变线上路由。

### 5. 失败校准与停止决策

- 研究问题：Agent 何时应继续、换策略、求助或停止？
- 假设：基于 planner entropy/自评、工具失败类型和状态变化的 calibrated policy 优于连续失败阈值。
- Baseline：当前 `max_consecutive_failures=5`；固定 max steps；oracle。
- 指标：risk-coverage、Brier/ECE（若产生概率）、无效步骤、成功率、错误终止率。
- MVP：离线标注 trace 中“下一步仍有恢复机会”，训练轻量分类器或规则评分。

## 实验协议

每项实验至少固定：commit、浏览器/模型版本、task set、随机种子或重复次数、最大步骤/时间/token、网站快照、评价器。主指标用端到端 task success；诊断指标包括 action grounding、planner JSON valid rate、tool success、fallback、steps、latency、cost。

报告均值之外给 bootstrap confidence interval 或多次运行方差。真实网站具有非平稳性，应将 self-hosted 可重复环境作为主实验，live-web 作为外部有效性补充。

实现与实验入口应保持分工：受控站点放 `benchmarks/environments/controlled_web/`，单次套件放
`benchmarks/suites/`，重复矩阵/纵向聚合放 `benchmarks/studies/`，协议与失败证据规则见
[`docs/research/`](../research/README.md)。脚本条件统一称 `scripted-harness-baseline`，只能证明
harness 可执行，不应作为竞争性 Agent baseline 或模型校准结果。

## 不能夸大的内容

- 有 loop detector 不等于证明减少 loop；需要对照实验。
- 有 stealth 脚本不等于通过 bot detection。
- 有 vision probe 不等于视觉 grounding 准确。
- 有 cascade 不等于比单 provider 更优。
- 有 60+ tools 不等于能力更强；工具选择空间也可能增加错误，因此应比较 policy-filtered tool set 的消融。
- 课程/个人贡献必须由 commit、报告或可复现实验支持。

## 适合 RA 申请的最小成果包

选择一个方向，交付：问题定义；20–100 个可重复任务；baseline runner；一个明确改动；3 个以上随机重复；主/诊断指标；失败案例分类；代码与运行命令；一页结果表。相比“读过全部源码”，这更能证明研究训练、实验严谨性和工程执行力。
