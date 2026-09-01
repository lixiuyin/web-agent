# 7 天与 14 天学习计划

## 7 天基础计划

### Day 1：运行与画图

读 01–03；安装依赖；运行 CLI help、unit tests 和 StubPlanner integration；手画启动时序。产物：一页项目地图和环境记录。

### Day 2：数据契约

读 04、schema 附录和 `docs/research/experiment-lifecycle.md`；在 REPL 构造
`BrowserState/ToolCall/ToolResult`；追踪每个字段生产者/消费者，并解释
trajectory/control/observations/artifacts/result/evaluation 的边界。产物：数据生命周期表。

### Day 3：主循环

逐行读 `WebAgent.run/_observe/_think`；用 mock planner 制造 done、None、坏工具、连续失败、timeout。产物：五条 trace。

### Day 4：浏览器感知

读 06；制作本地 HTML，包含 button、ARIA role、隐藏元素、重复文本；打印 snapshot Markdown 和 elements。产物：感知错误清单。

### Day 5：Planner 与 tools

读 07–08；手工输入五种 LLM response；新增一个仅在个人分支上的小工具并测试。产物：ToolCall contract 说明。

### Day 6：PDF pipeline

读 09；制作文本 PDF 和图片 PDF；关闭所有 cloud key 观察 local fallback；验证 Figure 1 caption。产物：provider 路由 trace。

### Day 7：审计与口述

读 10–14；完成 30 个自测题；录制 3 分钟中英文介绍；选择一个研究问题。产物：RA evidence draft。

## 14 天深入计划

第 1–7 天同上，随后：

### Day 8：建立可重复页面集

写 20 个本地任务状态，覆盖 DOM nesting、动态 id、ARIA、iframe、loading 和 error page；定义 target locator ground truth。

### Day 9：Baseline runner

先用 `scripted-harness-baseline` 校准环境/工具/判分链，再自动运行 JS CSS path、text selector、
AX-only 三种可比较 baseline；记录 candidate recall@K 和 execution rate，不把 scripted harness 成绩
当模型 baseline。

### Day 10：故障注入

为 planner endpoint 模拟 500、429、慢响应、trickle、坏 JSON；为工具模拟超时；验证错误矩阵。

### Day 11：一个最小改进

只改一个变量，例如 AX node 解析后通过 backend node resolve 得到 locator，或为 selector 增加 role+accessible-name 表达。

### Day 12：Ablation 与重复

固定任务、模型、预算；对 baseline/改进至少重复三次；每次写入新的
`outputs/studies/<suite>/executions/...`，保存 task-level run/trace 而不只保存均值。
若延续当前能力评测，应按[研究结果索引](../research/results/README.md)列出的缺口补齐真实 UTC
日期；不要用同日重复替代日期。

### Day 13：失败分类

把失败标为 observation、grounding、planning、execution、environment、stopping；检查改进是否把一种错误转移成另一种。

### Day 14：研究报告

写问题、假设、方法、指标、结果、限制和复现命令；更新证据模板与 3 分钟介绍。
将内部诊断层与 BrowserGym 外部标准层分开报告。只有在 WA/VWA 网站和 reset endpoint 部署、
每个模型各完成一个校准 task 后，才运行 258+910 项标准矩阵。

## 每日检查清单

- 今天读的是主路径还是未调用代码？
- 我能给出输入/输出的具体格式吗？
- 我实际运行了什么，而不是只阅读了什么？
- 哪个结论来自测试，哪个只是静态推断？
- 我保存了 commit、环境、命令和不可覆盖的 run/study artifact 吗？
- 这个机制的失败案例是什么？

## 完成标准

基础完成：可以不看文档讲清一次端到端任务、六个核心模型、四层 timeout、parser 路由和五个已知缺口。

RA-ready：能够展示一个可重复 benchmark、一项受控改动、一张结果表、一套失败 taxonomy，以及清楚的 ownership boundary。
