# 当前评测方法与阶段性结果：v6-final-r7

> 状态日期：2026-09-01（UTC）  
> Campaign：`v6-final-r7`  
> 源码提交：`a7a14e47f67995b77f2106e9f6d8822cb8e1b42a`  
> 当前结论：单日诊断层采集完整；三日期纵向统计和 BrowserGym 外部层尚未完成。

## 1. 文档定位

本文记录当前冻结候选源码上的评测方法、执行约束、机器生成证据和第一天结果。它回答四个问题：

1. 当前系统究竟评测了什么？
2. 结果由什么终态和证据判定，而不是由 Agent 自己宣布成功？
3. GLM 与 Qwen 在同一协议下表现如何？
4. 哪些结论已经有证据，哪些仍需后续日期或外部 benchmark？

本文不是 leaderboard 声明，也不是最终统计报告。当前 portfolio 只有一个共同 UTC 日期，因此状态仍为 `insufficient`。正式纵向结论要求两个模型在至少三个共同日期都具有完整的开放网页、sandbox 和长程单元。

## 2. 两层评测结构

当前设计将内部诊断和外部标准 benchmark 分开，避免把不同任务总体、环境和判分器的分数强行平均。

| 层级 | 组成 | 目的 | 当前状态 |
|---|---|---|---|
| 仓库诊断层 | 30 个开放网页任务、5 个受控 sandbox 任务、1 个 60 阶段长程任务 | 定位搜索、页面读取、交互、文件、记忆、恢复和终态验证问题 | 已完成 2026-09-01 单日双模型采集 |
| BrowserGym 外部层 | WebArena-Verified Hard 258 题、VisualWebArena 910 题 | 使用标准环境接口和原生 evaluator 提供外部可比证据 | 尚未运行，需要独立 Python 3.12 环境和网站服务器 |

诊断层先形成每个 `provider/model/date` 的 36 题单元，再聚合至少三个共同日期。外部层保持独立分数。最终的 `two-layer-portfolio.json` 只绑定并列展示两层报告，不产生混合总分。

## 3. 诊断层任务设计

### 3.1 开放网页：30 题

任务清单来自 [`benchmarks/manifests/open_web_general.json`](../../benchmarks/manifests/open_web_general.json)，覆盖 10 个公共官方来源：IANA、Python、Playwright、Git、GitHub、MDN、NumPy、pandas、FastAPI 和 Go。

| 主题 | 任务数 | 代表性目标 |
|---|---:|---|
| IANA | 3 | 示例域名、保留域名、机构职责 |
| Python | 3 | 创建者、教程目标读者、`asyncio` 用途 |
| Playwright | 3 | Python 安装、`Page`、Locator |
| Git 与 GitHub | 6 | Git 概览、`clone`、`status`、Git/GitHub 区别、克隆、认证 |
| MDN | 3 | HTML、CSS、JavaScript 的作用 |
| NumPy | 3 | 初学者指南、`numpy.array`、`numpy.mean` |
| pandas | 3 | 表格结构、`DataFrame`、`read_csv` |
| FastAPI | 3 | 项目用途、首个应用对象、标准化特性 |
| Go | 3 | 文档中心、模块初始化命令、Effective Go |

其中 10 题从 `about:blank` 开始，必须通过真实浏览器搜索发现官方页面；另外 20 题直接从官方页面开始，用于把“搜索发现失败”和“页面读取失败”分开测量。

开放网页题不仅检查答案文本，还可要求：

- 最终答案包含指定事实和精确 URL；
- 浏览历史确实观察过目标 URL；
- 搜索发现题的首个成功动作是浏览器搜索；
- 轨迹满足单次连续运行、来源可见和无直接来源捷径约束；
- TLS 证书、页面或其他任务指定条件成立。

### 3.2 受控 sandbox：5 题

任务定义来自 [`sandbox_tasks.py`](../../benchmarks/suites/controlled_web/sandbox_tasks.py)。环境由本地、可重置、相互独立的两个 HTTP origin 组成，判分器可同时读取浏览器终态、服务端 JSON 状态、历史来源和文件哈希。

| 任务 | 测量内容 |
|---|---|
| `spa_hydration_route` | 等待 SPA hydration、筛选、客户端路由和重渲染 |
| `authenticated_account` | 登录、cookie、受保护页面和成功输入动作 |
| `cross_origin_intake` | 跨 origin 读取、表单填写、优先级选择和服务端状态 |
| `download_upload_handoff` | 下载、跨 origin 上传、等待就绪和 SHA-256 一致性 |
| `sandbox_checkout` | 购物车、地址、条款、提交和服务端订单终态 |

这些任务允许在隔离环境内产生状态变化，但不会触及真实外部账户或交易。

### 3.3 长程任务：1 题

任务定义来自 [`long_horizon_tasks.py`](../../benchmarks/suites/controlled_web/long_horizon_tasks.py)。`mission_60_stage_resume` 要求 Agent：

- 完成全部 60 个页面阶段；
- 在最多 100 个 Agent 步骤内结束；
- 恢复瞬时反馈失败；
- 在第 35 个 Agent 步骤强制关闭并从 checkpoint 恢复；
- 长期保存 `CEDAR`、`ORBIT`、`LANTERN`、`DELTA` 四个线索；
- 最终按原始顺序报告线索；
- 使服务端 `completed=true`、`stage=60`、`memory_errors=0`。

这一题针对上下文丢失、记忆漂移、工具循环、checkpoint 恢复和延迟反馈。单次通过只证明这一条模型轨迹成功，不等同于普遍长程可靠性。

## 4. 实验契约与控制变量

不可变 campaign 契约位于 [`campaign.json`](../../outputs/campaigns/v6-final-r7/campaign.json)。当前关键配置如下：

| 项目 | 值 |
|---|---|
| Provider | OpenRouter |
| 模型 | `z-ai/glm-5.3-flash`、`qwen/qwen3.8-flash` |
| 模型执行顺序 | 按 UTC 日期轮换（`rotate-by-date`） |
| 开放网页步数 | 普通题 8；搜索发现题 12 |
| Sandbox 步数 | 每题 18 |
| 长程步数 | 100；第 35 步强制恢复 |
| 长程 planner 输出上限 | 1024 tokens，reasoning effort 为 `low` |
| 浏览器 | bundled Chromium、headless、临时 Profile、1280×720 |
| 浏览器伪装 | `stealth_mode=false`、不启用 humanized delays |
| 搜索 | browser-grounded；默认 Bing；Google 禁用；记录搜索引擎失败与回退 |
| CAPTCHA | `fail`，不绕过、不自动求解 |
| 风险策略 | 高风险动作拒绝；sandbox mutation 仅限本地受控环境 |
| PDF 缓存 | 持久缓存禁用 |
| Endpoint 预检 | 每个模型先执行一次最小真实推理请求 |

可比日期必须保持模型集合、任务 manifest、源码指纹、benchmark 指纹、步数、CAPTCHA 策略和预检策略不变。任一项变化都必须新建 campaign，不能追加到 r7。

当前绑定指纹：

- Agent source SHA-256：`29b5cb81ec7159bf22a38e81e1d86d8e389674f1c0d9779354e4750165c9009c`
- Benchmark source SHA-256：`36cb18374b261da649313be261e2dfb2ab5798ff91a05c35aa2563f66b0f49de`
- Campaign combined source SHA-256：`786cd1238bc9cc60b2fed8b220e6906a6298b8a5ce89a1c2487170852ec9138f`
- Open-web manifest SHA-256：`611ee2574de9cc725755a35e9a0c26ff368bd8843f9949b201f9a1239c21d6f8`

## 5. 判分与诊断指标

`done` 只是 Agent 的完成声明，不会自动让任务通过。外部 evaluator 重新检查任务声明中的必需断言。

| 指标 | 含义 |
|---|---|
| Task success | 所有必需终态、事实、URL、历史或文件断言均通过 |
| Mean score | 按任务断言权重计算的部分得分均值；不等于严格通过率 |
| Agent completion rate | Agent 是否主动提交完成 |
| False completion rate | Agent 宣布完成，但外部 evaluator 判定未完全通过 |
| Action validity rate | 实际执行成功的工具动作比例 |
| Answer grounding rate | 最终答案中的必需事实和引用断言通过比例 |
| Mean steps / duration | 每题平均 Agent 步数与墙钟时间 |
| Planner failures | 规划请求未产生可执行动作等可观察失败；不自动归因为“推理失败” |
| Collapse / stagnation | 重复动作坍缩与页面状态长期不变的轨迹诊断 |
| Calibration | 任务完成前的自报成功概率与外部结果之间的 Brier、ECE 等描述性统计 |

自动 failure taxonomy 只记录可观察症状，例如工具动作失败、planner attempt 失败、答案断言失败或 false completion。没有受控干预或人工审阅时，不把症状自动归因为模型推理、记忆或浏览器实现。

## 6. 2026-09-01 执行结果

### 6.1 Endpoint 预检

预检证据位于 [`endpoint-probes.json`](../../outputs/campaigns/v6-final-r7/batches/2026-09-01/20260901T122544047928Z/evidence/endpoint-probes.json)。

| 模型 | HTTP | 尝试次数 | 瞬时重试 | 耗时 |
|---|---:|---:|---:|---:|
| GLM | 200 | 1 | 0 | 3.66 s |
| Qwen | 200 | 1 | 0 | 1.75 s |

预检只证明当时端点可用；它不证明后续请求不会限流，也不独立证明上游路由或 BYOK 归属。

### 6.2 分套件结果

| 模型 | 套件 | 通过 | Mean score | Action validity | Mean steps | Mean duration | Grounding |
|---|---|---:|---:|---:|---:|---:|---:|
| GLM | 开放网页 | 29/30 | 98.89% | 73.10% | 6.07 | 70.03 s | 98.89% |
| Qwen | 开放网页 | 29/30 | 98.33% | 85.96% | 6.77 | 89.55 s | 97.78% |
| GLM | Sandbox | 5/5 | 100% | 100% | 5.60 | 99.53 s | 100% |
| Qwen | Sandbox | 5/5 | 100% | 95.65% | 5.60 | 38.60 s | 100% |
| GLM | 60 阶段长程 | 1/1 | 100% | 100% | 70 | 580.09 s | 100% |
| Qwen | 60 阶段长程 | 1/1 | 100% | 100% | 70 | 411.64 s | 100% |

所有六个套件报告中的 timeout、CAPTCHA、blocked 和 max-steps rate 均为 0。两模型都完成全部 36 个任务的 Agent 提交；严格判分各有一题未满分，因此 false completion rate 在开放网页套件中均为 3.33%。

### 6.3 综合描述

| 模型 | 严格通过 | 综合通过率 | 按 36 题加权 Mean score |
|---|---:|---:|---:|
| GLM | 35/36 | 97.22% | 99.07% |
| Qwen | 35/36 | 97.22% | 98.61% |

这里的综合通过率是同一诊断层内 36 个任务的计数汇总。它不能与未来的 WebArena 或 VisualWebArena 原生 reward 合并。

### 6.4 两个未完全通过的任务

| 模型 | 任务 | 得分 | 观察到的行为 | 失败性质 |
|---|---|---:|---|---|
| GLM | `python_tutorial` | 2/3 | 正确说明教程受众，也浏览过要求的 `/3/tutorial/index.html`，但最终引用更具体的 `/3/tutorial/appetite.html` | 最终精确 URL 选择失败 |
| Qwen | `playwright_python_intro` | 2/4 | 正确给出安装命令和官方 `/python/docs/library`，但任务要求访问并引用 `/python/docs/intro` | 目标页面选择与历史 URL 断言失败 |

两题都以 `completed` 结束，没有浏览器终止、API 限流或 CAPTCHA。严格判分将“内容上合理的相邻官方页面”和“任务指定的精确页面”区分开，因此这两项应解释为证据选择精度问题，而不是网络可用性问题。

### 6.5 长程结果

两模型均满足：

- 60 个服务端阶段全部完成；
- 70 个 Agent 步骤内结束；
- `resume_count=1`，确实经过第 35 步强制恢复；
- `memory_errors=0`；
- 最终按顺序输出 `CEDAR → ORBIT → LANTERN → DELTA`；
- collapse incidence 和 stagnation incidence 均为 0。

Qwen 用时约 6 分 52 秒，GLM 约 9 分 40 秒。该时间差是本次单轨迹的描述性结果；此外，GLM sandbox 曾观察到一次接近 API hard-timeout 边界的规划等待，但现有自动证据不足以给它分配上游或本地因果标签。因此不能据此推断稳定的模型速度差异。

### 6.6 Generality、transfer 与 calibration

每个模型的单日诊断单元都达到仓库定义的 breadth floor：36 个任务、17 个类别、10 个公共来源、10 个搜索发现任务，覆盖 public web 与 sandbox、development、held-out task 和 held-out setting。

| 项目 | GLM | Qwen | 解释边界 |
|---|---:|---:|---|
| Development success | 95.65%（23 题） | 95.65%（23 题） | 两个未满分题都在 development |
| Held-out task success | 100%（6 题） | 100%（6 题） | 单日描述，不是干预迁移因果效应 |
| Held-out setting success | 100%（7 题） | 100%（7 题） | 包含 sandbox 与长程 setting |
| Confidence coverage | 91.67% | 97.22% | 未记录的 confidence 不做插补 |
| Brier score | 0.0038 | 0.0284 | 仅覆盖有自报概率的任务 |
| ECE | 0.0461 | 0.0094 | 单日、小样本，不能作稳定校准结论 |

“Generality ready”只说明任务覆盖达到预注册下限，不代表模型已被证明具有通用网页能力。“Held-out 比 development 高”也不证明某项系统改动产生迁移收益，因为这里没有成对 baseline/intervention。

## 7. 模型比较的当前解释

在第一天数据上，两模型严格通过率相同。可观察差异是：

- GLM 的开放网页 mean score 略高，平均步骤更少、平均耗时更低；
- Qwen 的开放网页 action validity 更高；
- Qwen 在本次 sandbox 和长程单轨迹中更快；
- 两模型都通过受控交互和强制恢复长程任务；
- 两模型各有一个精确 URL 选择错误，但发生在不同任务。

这些差异不能在当前阶段解释为稳定优劣。真实网页会随时间变化，provider 延迟也会变化；至少需要另外两个共同日期，才能报告跨日期均值、波动和复现性。

## 8. 当前证据完整性

第一天批次状态见 [`batch.json`](../../outputs/campaigns/v6-final-r7/batches/2026-09-01/20260901T122544047928Z/batch.json)，当前 portfolio 见 [`latest.json`](../../outputs/campaigns/v6-final-r7/analysis/portfolios/latest.json)。

当前本地 evidence 包含：

- 6 个正式 `results.json`；
- 每题 manifest、trace、planner attempt、工具结果和判分断言；
- 966 张逐步截图：GLM 462 张、Qwen 504 张；
- endpoint probes、批次状态、组件日志和 hash-bound portfolio；
- 单一 Agent source fingerprint 与单一 Benchmark source fingerprint。

这些机制使本地修改和跨日期协议漂移可检测，但本地时间戳不是独立的外部时间证明，忽略版本控制的 `outputs/` 也不是永久档案。正式发布前仍需保留源码标签、依赖锁、完整 campaign 目录和独立校验和或只读归档。

## 9. 当前限制

1. **只有一个日期。** `portfolio.status=insufficient`，缺少两个共同 UTC 日期。
2. **外部层为空。** 尚无 WebArena-Verified Hard 或 VisualWebArena 成绩。
3. **真实网页非平稳。** 页面结构、搜索索引、网络与 provider 延迟都会变化。
4. **任务规模有限。** 30 个开放网页题覆盖 10 个来源，但不能代表整个 Web。
5. **精确 URL evaluator 较严格。** 它能暴露证据选择误差，也可能拒绝内容上合理的相邻官方页面；任务契约应在运行前冻结，不能看到结果后修改。
6. **Calibration 样本少且不完整。** 当前指标只能描述已有自报概率，不能支持强校准结论。
7. **自动失败标签不是因果解释。** 工具失败、planner failure 或 false completion 仍需轨迹审阅和受控干预。

## 10. 复现实验命令

后续两个新的 UTC 日期必须继续使用同一输出根目录和完全相同的命令：

```bash
.venv/bin/python -m benchmarks.studies.generality_campaign \
  --provider openrouter \
  --models z-ai/glm-5.3-flash qwen/qwen3.8-flash \
  --output outputs/campaigns/v6-final-r7 \
  --manifest benchmarks/manifests/open_web_general.json \
  --shards 1 \
  --open-max-steps 8 \
  --open-discovery-max-steps 12 \
  --open-study-name browser-grounded \
  --sandbox-max-steps 18 \
  --long-max-steps 100 \
  --resume-at-step 35 \
  --long-planner-max-tokens 1024 \
  --long-planner-reasoning-effort low \
  --captcha-handling fail \
  --model-order rotate-by-date
```

同一 UTC 日期默认拒绝重复运行。第三个共同日期完成后，应检查：

```bash
jq '{
  status,
  common_complete_dates,
  missing_requirements,
  overall_success_rate
}' outputs/campaigns/v6-final-r7/analysis/portfolios/latest.json
```

预期 `status=ready`、三个共同日期且 `missing_requirements=[]`。之后再运行 BrowserGym 外部矩阵，并通过 `two_layer_portfolio` 绑定两层结果。

## 11. 当前结论

当前证据支持以下有限结论：在 2026-09-01 的冻结协议下，GLM 与 Qwen 都完整执行了 30 个公共网页任务、5 个受控交互任务和 1 个强制恢复长程任务，各自严格通过 35/36；浏览器、endpoint、sandbox 和 durable-memory 主路径没有发生任务级崩溃，历史上的多匹配 frame 提取与 Unicode durable-note 问题在正式运行中得到回归验证。

当前证据不支持以下更强结论：两模型已经具有稳定的通用网页能力、一个模型显著优于另一个、当前系统优于外部标准 baseline，或该成绩可跨日期和网站状态复现。完成另外两个日期以及 WebArena/VisualWebArena 后，才能形成完整的两层评测报告。
