# 阅读指南：怎样达到“真正理解”

## 先建立正确心智模型

本项目把自然语言任务变成重复的状态转换：浏览器页面被编码成 `BrowserState`，Planner 把状态解码为 `ToolCall`，工具执行得到 `ToolResult`，历史再影响下一轮规划。浏览器、模型、工具和 PDF parser 都只是这个循环中的可替换组件。

```text
Task + BrowserState + History + Tool descriptions
                         │
                         ▼
                      Planner
                         │ ToolCall
                         ▼
ToolResult ◀── ToolExecutor/Registry ◀── Browser/PDF/File/Search implementation
    │
    └────────────── history + next observation ──────────────┘
```

## 三轮阅读法

第一轮只追入口，不钻实现：`pyproject.toml` → `webagent.cli` → `WebAgent.run` → `_observe/_think/_act` → `done`。目标是能不用看代码画出一次任务的时序图。

第二轮追数据：从 `AgentConfig`、`BrowserState`、`ToolCall`、`ToolResult` 到 `AgentStep/AgentResult`，对每个字段回答“谁创建、谁消费、是否持久化”。

第三轮追失败：人为让 planner 返回坏 JSON、让工具超时、让 parser provider 未配置、让 snapshot 失败，确认系统究竟抛异常、返回失败对象、重试、降级还是静默吞掉。

## 推荐源码顺序

1. `src/webagent/core/models.py` 与 `protocols.py`
2. `src/webagent/cli.py`
3. `src/webagent/agent/loop.py`
4. `src/webagent/planner/base.py`、`api.py`
5. `src/webagent/tools/registry.py`、`executor.py`、`task_tools.py`
6. `src/webagent/browser/snapshot.py`、`controller.py`
7. `src/webagent/agent/loop_detector.py`、`history.py`
8. `src/webagent/evaluation/artifacts.py`、`runner.py`、`failures.py`、`calibration.py`、`transfer.py`
9. `src/webagent/parser/cascade.py`、`_router.py`、`_quality.py`
10. 三个 cloud provider 与 local fallback
11. `tools/builtin` 中 PDF、搜索和文件工具
12. `benchmarks/suites/`、`benchmarks/studies/` 与对应单元测试；最后才回头审视 README 的架构声明。

## 自测标准

完成后应能独立回答：为什么 planner 同时需要截图和 DOM；`done` 为什么只是一个普通工具却能结束循环；结构化规划怎样退回普通 `ToolCall`；loop detector 记录的是“计划出的动作”还是“执行后的动作”；run 中 trajectory/control/artifacts/result/evaluation 为什么必须分开；PDF quality gate 在何处决定换 provider；没有模型凭证时 scripted harness baseline 实际验证了什么；哪些异常被转换为 `ToolResult`，哪些仍会终止任务。

## 不应采用的学习方式

- 不要从 1,000 行的 `controller.py` 或 PDF QA 文件逐行开始。
- 不要把 README 的“research-grade”“provider-agnostic”直接当成验证结论。
- 不要只背工具名；应追踪装饰器注册、构造依赖、校验、超时和返回值。
- 不要把运行一次成功当成鲁棒性证据；至少检查失败注入与测试覆盖。

## 学习产物

CLI 的独立运行由 `outputs/runs/` 管理，可比较实验由 `outputs/studies/` 管理，迁移前历史产物只放入带 hash inventory 的 `outputs/legacy/`；不要把手工笔记混入这些机器生成目录。研究流程与证据规则见 [../research/README.md](../research/README.md)。申请材料只写自己实际运行、定位、修改或评估过的部分，模板见 [14-ra-interview-preparation.md](14-ra-interview-preparation.md)。
