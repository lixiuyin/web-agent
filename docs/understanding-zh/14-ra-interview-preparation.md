# RA 面试与项目表达

## 3 分钟中文介绍

这个项目是一个基于 Playwright 和视觉语言模型的 Web Agent 运行时。用户输入自然语言任务后，系统重复执行 Observe、Think、Act、Record：Observe 同时截取浏览器截图，并通过 CDP Accessibility Tree 或 JavaScript 把交互元素压缩成 Markdown；Think 将任务、页面、最近历史和按策略过滤后的 60+ 个工具 schema 发给 OpenAI-compatible planner；Act 对模型生成的 ToolCall 做参数、provenance、风险和墙钟约束，然后执行浏览器、搜索、文件或 PDF 工具；Record 保存结果、checkpoint 和 v8 trace，并检查完成、连续失败、循环、策略切换、步骤和任务时间预算。

项目比较有特点的工程部分是两条可靠性链。第一条是网页侧：CDP/JS 感知 fallback、五信号 loop detector、搜索引擎 cascade、planner hard timeout。第二条是论文侧：先用 PyMuPDF 做文档画像，再在 Marker、MinerU、PaddleOCR 之间路由，每次结果经过质量门，最后退到本地 PyMuPDF；图片通过 caption 与 Figure N 关联，减少把 logo 当成目标图的错误。

我不会把它描述成已经完成研究验证的系统。当前还存在关键缺口，例如 AX backend node 到可执行 selector 的映射不可靠，多 provider 原生 SDK 不在主路径，而且确定性 integration 不能替代大规模真实自主任务评测。Captcha 已能 fail closed 或在 headed 模式等待人工清除，但不会自动求解。当前已有 11 项本地任务、带日期的开放网页 runner、严格可审计 trace 与答案事实判分；它们提供测量基础，尚未形成跨模型/跨日期成功率。我认为最有价值的 RA 方向是继续积累这些重复实验，并对页面表征和 grounding 做 ablation。

## 3-minute English introduction

This project is a Playwright-based web-agent runtime controlled by a vision-language planner. Given a natural-language task, it repeatedly executes an Observe–Think–Act–Record loop. During observation, it captures a screenshot and compresses the page into Markdown using either the Chrome accessibility tree or JavaScript-based interactive-element extraction. During planning, it sends the task, browser state, durable controller state, and policy-filtered schemas for 60+ tools to an OpenAI-compatible endpoint. The resulting provider-native or schema-constrained tool call passes runtime validation, provenance and risk policy, a wall-clock bound, checkpointing, execution, and a versioned audit trace.

The system has two notable reliability pipelines. On the web side, it combines CDP and JavaScript observation fallbacks, five-signal loop detection, search-engine fallback, and hard model-request timeouts. On the document side, it profiles a PDF locally, routes it through Marker, MinerU, and PaddleOCR, rejects degraded outputs through a heuristic quality gate, and finally falls back to local PyMuPDF. It also associates extracted images with real Figure-N captions rather than assuming that the first extracted image is the target figure.

I would describe it as a useful research prototype, not as a fully validated research contribution. Important limitations remain: accessibility nodes are not reliably grounded to executable selectors, native provider SDKs are outside the active planner path, and deterministic integration does not establish a general task-success rate. CAPTCHA handling now fails closed or waits for manual clearance in a headed browser, but never solves a challenge. The repository has an eleven-task local suite, a dated open-web runner, auditable strict traces, and factual answer assertions; it still needs repeated cross-model and cross-date evidence. A strong RA project would turn those runs into controlled grounding and robustness experiments.

## 30 个项目理解问题及答案

1. **Q：系统的中心抽象是什么？** A：状态到动作的循环，不是某个特定模型。
2. **Q：谁创建 BrowserState？** A：`WebAgent._observe()` 从 snapshot dict 构造。
3. **Q：截图和 DOM 为什么同时发送？** A：截图保留视觉/布局，DOM Markdown提供文本和可操作 selector；两者互补。
4. **Q：什么时候不发送截图？** A：截图为空白或 planner probe 判定 chat API 不支持视觉。
5. **Q：DOM 为什么截断 6000 字符？** A：控制上下文；这是固定字符预算，不保证保留最关键尾部信息。
6. **Q：`done` 如何终止任务？** A：工具先返回成功，Agent 再检查 tool name 并设 completed。
7. **Q：没有 API key 会怎样？** A：CLI 选择 StubPlanner，通常第一步立即 done。
8. **Q：Protocol 有何意义？** A：结构化类型允许 mock/替代实现，不要求继承。
9. **Q：工具怎样被发现？** A：import builtin 触发装饰器写全局类表，registry 实例化。
10. **Q：工具名在何处规范化？** A：ToolExecutor 转小写；Agent 的 done 比较没有同步规范化。
11. **Q：参数错了会怎样？** A：validate 的 ValueError 转为失败 ToolResult。
12. **Q：工具卡住怎样处理？** A：`asyncio.wait_for(tool_timeout)` 取消 coroutine并返回失败。
13. **Q：Agent 总 timeout 是硬 watchdog 吗？** A：不是，只在步骤边界检查。
14. **Q：Planner hard timeout 为什么需要？** A：httpx read timeout 可被持续小数据重置，wait_for 限制整个请求。
15. **Q：LoopDetector 记录执行结果吗？** A：不记录；在执行前记录计划动作和页面 hash。
16. **Q：有哪些 loop signal？** A：同动作同页、页面停滞、URL 振荡、多动作无进展。
17. **Q：检测到 loop 会停止吗？** A：不会，只给 planner nudge。
18. **Q：Captcha 会暂停吗？** A：不会，当前只检测并 warning。
19. **Q：CDP 的作用是什么？** A：访问 AX/DOM/CSS/Runtime 等 Chromium 内部语义。
20. **Q：AX Tree 当前最大问题？** A：backendDOMNodeId 没有可靠映射到页面 locator/css path。
21. **Q：`[e1]` 能点击吗？** A：不能，它是 Markdown 展示编号；工具只认 text/css selector。
22. **Q：为什么用 persistent context？** A：保留 profile/cookie 等浏览器状态。
23. **Q：搜索为何容易漂移？** A：依赖真实搜索引擎 DOM selectors 和反爬策略。
24. **Q：PDF 如何选 parser？** A：文档 suffix、文本层、平均字符和扫描画像，加 soft hint。
25. **Q：Quality gate 测什么？** A：空文本、字符量比例、控制字符、扫描件每页字符或结构资产。
26. **Q：cloud 全失败怎样？** A：local PyMuPDF 读取 text layer。
27. **Q：Figure 1 怎样避免命中 logo？** A：保存 image 时关联 alt/附近 caption，再按 figure_number 匹配。
28. **Q：PDF QA 是向量 RAG 吗？** A：不是，是字符 chunk 与规则相关性评分。
29. **Q：每次 run 的输出目录怎样处理？** A：通过安全检查后整个删除重建。
30. **Q：integration test 证明真实任务吗？** A：不证明；两条 Stub 测生命周期，一条虚构证据测完整控制流。真实 strict blind run 只能作为补充个案。

## 20 个 RA 面试问题与参考回答

1. **为什么选择这个项目？** 它把模型、环境、工具和失败恢复放在一条可观测链上，适合研究长程 Agent 的可靠性。
2. **最重要的设计取舍？** 混合 screenshot/DOM 提高信息覆盖，但增加冲突、token 和 grounding 复杂度。
3. **最严重的实现风险？** AX 语义节点未可靠落到可执行 locator，感知与动作之间断层。
4. **你会先修什么？** 先建本地页面测试和 grounding 指标，再改 locator，不先凭直觉重写。
5. **成功率之外看什么？** action success、恢复率、steps、latency、tokens、cost、fallback 和方差。
6. **如何定义可靠？** 在明确任务分布和预算下，高成功、低方差、失败可诊断、对扰动稳健且能适当停止/求助。
7. **如何避免 benchmark leakage？** 固定 task split，隔离 prompt 开发集，报告网站/任务版本，检查静态候选。
8. **真实网站还是模拟环境？** self-hosted 用于因果和复现，live web 用于外部有效性；两者都需要。
9. **如何做 snapshot ablation？** screenshot-only、DOM-only、AX-only、融合；固定模型、prompt、预算和任务。
10. **如何评估 loop detector？** 先对 trace 标注 loop 起点/类型，再评 precision/recall 和干预后的恢复因果效果。
11. **为什么固定阈值可能不好？** 不同任务长度和工具语义不同，同页做 PDF 分析并非停滞。
12. **如何评估 OCR cascade？** 每 provider 全跑得到 oracle，比较质量/成本/延迟，避免只看最终文本长度。
13. **怎样测不确定性？** 要求结构化 confidence 或从重复采样/结果一致性得到信号，再做校准曲线与 risk-coverage。
14. **如何处理外部 API 非平稳？** 保存请求 schema、版本、时间和 artifacts；用 mock contract tests + 定期小规模 live canary。
15. **最有价值的负向结果？** 证明更丰富的观测在固定预算下反而因噪声降低成功，并解释在哪类任务发生。
16. **怎样保证改进不是 prompt 偶然性？** 多任务、多模型/种子重复、预注册主指标、bootstrap interval 和 failure taxonomy。
17. **项目已有贡献和你的贡献如何区分？** 用 commit、实验日志和 PR 逐项列证据，只声明亲自实现/复现的部分。
18. **为什么 60+ tools 可能有害？** action space 变大、schema token 增多、相似工具产生选择混淆；因此必须按 discovery/risk policy 缩小暴露集合。
19. **如何缩小工具空间？** 根据 task/state 动态检索工具，但要测漏召回和成功率。
20. **两周能交付什么？** 一个本地 grounding benchmark、三种 baseline、一个 mapping 改进、可重复 runner 和失败分析。

## 10 个现场读代码问题

1. 追踪 `stealth_mode=False` 从环境变量到 BrowserController 的完整参数链。
2. 解释 `run(reset_history=False)` 保留了什么、又删除了什么。
3. 构造 planner 返回 `Done` 时可能发生的控制流。
4. 指出 `_extract_from_ax_tree` 为什么可能输出 `unknown` selector。
5. 追踪一个坏 selector 从 ClickTool 到 Agent consecutive failures。
6. 解释 provider 500、401、429 各自会否同 provider retry。
7. 证明 parser 的整体 deadline 怎样覆盖 provider polling timeout。
8. 解释 structured parser 为什么直接返回 `ToolCall`，以及历史上下文由谁维护。
9. 说明 local fallback 为什么可能返回空但无 error。
10. 找出 integration test 没有执行 README 五步 walkthrough 的证据。

## 五个对导师可介绍的扩展

1. AX/视觉/DOM 融合的稳定 locator grounding。
2. 固定 token 预算下的任务条件化页面压缩。
3. 带校准不确定性的继续/恢复/停止决策。
4. 面向质量—成本—延迟的文档 parser routing。
5. 具有 failure injection 的可靠 Web Agent evaluation harness。

## “我实际做过什么”证据模板

```markdown
### Claim
我实现/复现/评估了：

### Evidence
- commit/PR：
- 关键文件与行：
- 测试命令与结果：
- 实验任务、版本与日期：
- 指标和置信区间：
- 失败案例：

### Ownership boundary
- 我独立完成：
- 在他人代码上修改：
- 仅阅读或运行、没有实现：
- 尚未验证：
```

申请材料中优先使用“implemented and evaluated”“reproduced”“identified and diagnosed”等可证据化动词，避免把项目总体能力全部写成个人原创。
