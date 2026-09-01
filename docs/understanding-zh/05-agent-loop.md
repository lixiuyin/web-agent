# Agent 主循环

## 核心结论

`WebAgent` 是运行时编排器。它组合 planner、browser、tool executor、会话历史、checkpoint、
策略控制器和 hooks，但不在主循环里实现具体网页动作或模型请求。一次任务只有在
`done` 工具执行成功后才是 `completed`；耗尽动作预算则是 `max_steps_reached`。

权威实现位于 `src/webagent/agent/loop.py::WebAgent.run`。下面保留稳定的调用链与状态契约，
不复制整段实现；逐行调试时应直接打开该方法及 `_run_steps()`、`_execute_step()`，避免文档中的
大段源码副本随重构失真。

## `run()` 的实际调用链

当前入口签名为：

```python
async def run(
    self,
    task: str,
    max_steps: int | None = None,
    reset_history: bool = True,
    resume_from: str | Path | None = None,
) -> AgentResult:
    ...
```

它先区分三种生命周期，再进入共同的 step loop：

```text
新任务
  -> 清空会话内历史/attempt/event/loop state
  -> RunLayout.prepare(run_id, task, model)
  -> turn = 1, step = 1

普通 follow-up (reset_history=False)
  -> 复用同一 run_id、history、artifacts 和 checkpoint namespace
  -> turn 与 step 单调递增

checkpoint 恢复 (resume_from=...)
  -> 校验 checkpoint 的 task/config/source/artifact 完整性
  -> 恢复 browser、history、policy、planning/strategy/loop state
  -> RunLayout.ensure_for_resume(...)

共同路径
  -> hooks.on_task_start
  -> _run_steps
       -> _execute_step: observe -> captcha -> think -> checkpoint -> act
                        -> observe -> record -> strategy/checkpoint -> terminal check
  -> finally: hooks.on_task_end -> checkpoint -> turn result -> trace
  -> AgentResult
```

### 输入

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `task` | `str` | 必填 | 当前 turn 的自然语言任务 |
| `max_steps` | `int \| None` | `config.max_steps` | 本次新任务的总预算；follow-up 时是新增预算 |
| `reset_history` | `bool` | `True` | `False` 且已有 session 时创建同一 run 的下一 turn |
| `resume_from` | `str \| Path \| None` | `None` | 从已校验的 checkpoint 继续，不等同于普通 follow-up |

strict-eval/search-only 不允许 checkpoint 恢复，也不允许把多个交互 turn 合并到同一评测 run，
以免先验状态污染可比性。

### 输出与异常

正常返回的 `AgentResult` 包含状态、step 编号、总耗时、`done.data`、完整会话 history、planner
attempts 和 runtime events。常见终止状态为 `completed`、`timeout`、`failed`、`interrupted`、
`blocked` 和 `max_steps_reached`。

browser disconnect 被归一化为 `failed`。其他未处理异常会在 finally 完成 hook、checkpoint、turn
snapshot 和 trace 尝试之后继续抛出，调用方不保证拿到 `AgentResult`。

## 单步执行与状态变化

`_execute_step()` 是长程行为的最小可验证单元：

1. 在观察前检查总超时，随后 `_observe()` 获取 DOM、URL、title 和截图。
2. 若启用 CAPTCHA 检测，`_handle_captcha()` 只报告/等待人工/失败关闭，不求解或绕过挑战。
3. `_think()` 汇入最近历史、controller plan state、strategy hint、剩余动作预算和 loop nudge；每次
   provider 尝试都写入 `PlannerAttempt`。
4. 工具执行前先写 pending-action checkpoint。这样进程在外部副作用期间崩溃时，不会盲目重放
   非安全动作。
5. `_act()` 调用 `ToolExecutor`；除 `done` 外，先执行 `post_action_wait_ms`，再等待页面进入有界
   稳定窗口并观察动作后的页面。截图写入
   `RunLayout.screenshots_dir/step_NNN.jpg`。
6. `_record_step()` 将动作前的 `BrowserState`、ToolCall、ToolResult、总耗时和工具耗时加入历史，
   再通知 `on_step_complete`。
7. 结果会更新证据、failure counter、planning state 与 strategy。动作结果和 pending-action 清除后
   再写 checkpoint。
8. 只有成功的 `done` 才设置 `completed` 并发布 final result；失败的 `done` 仍按普通失败动作处理。

planner 多次尝试后仍没有可执行 ToolCall 时，该 logical step 不写 `AgentStep`，但失败 attempt、
连续失败计数、策略重规划事件和 checkpoint 均保留。

## Observe 与 Think

### `_observe()`

`take_snapshot()` 接收当前 task、CDP 开关、元素上限与广告过滤设置。成功时生成：

- `dom_summary`: 面向 planner 的 Markdown DOM；
- `screenshot`: 可选 PIL image；
- `url`、`title` 和 UTC timestamp。

它最多尝试三次；每次先尽力等待 `domcontentloaded`，再用 URL、`readyState`、DOM 节点数、文本
长度和页面高度判断连续稳定窗口。`take_snapshot()` 还校验采集开始和结束时 URL 一致；导航跨越
截图边界会使本次尝试失败并重试。三次失败后返回
`dom_summary="(page loading)"` 的降级状态，而不是伪造页面内容。

### `_think()`

planner 输入由当前 task、`BrowserState`、tool descriptions 和 `SessionHistory.format_for_llm()`
组成。controller 还会添加里程碑/证据状态、当前策略、loop 信号与最后两步的动作预算提醒。
每次 provider 调用都记录耗时、错误、token usage、requested/effective structured-output mode 和
fallback 轨迹。成功 ToolCall 才写入 loop detector；失败会给下一次尝试加入修复提示，达到重试
上限后返回 `None` 并触发策略层的 planner-failure 观察。

## 历史、规划与恢复

`SessionHistory` 保存已经执行的工具步骤。给模型的上下文只取最近
`history_context_length` 步；大结果先经 `planner_context()` 投影为可继续决策的来源、日期、URL、
caption、路径和表格等证据，再执行字符上限。结构化失败诊断也会保留，但网页响应中未被显式
观察的下载 URL 不能借失败路径升级为下载授权。

`PlanningState` 记录里程碑、已观察证据和 revision；`StrategyManager` 根据连续失败、无进展、
planner failure、policy denial 和 loop 信号切换策略。自动归因属于 candidate 诊断，不等同于已由
受控对照或人工复核的根因。

checkpoint 绑定 task hash、非秘密行为配置、agent source hash、history、planner attempts、events、
policy/strategy/loop/browser state 和已引用 artifact 的哈希。恢复时任何 task/config/source/artifact
不一致都会拒绝继续；具有不安全重放语义的 pending action 会把恢复状态置为 `blocked`。

## CAPTCHA 与 Loop Detection

普通 headed 模式的默认 CAPTCHA 行为是 `report`：记录事件并在有界时间内轮询，等待用户手动
通过；超时后标记 blocked 并关闭浏览器。headless 或 `fail` 直接 fail closed。显式
`wait_for_human` 使用同一人工接管机制，任何模式都不自动求解 CAPTCHA。

Loop Detector 使用 tool/参数签名、URL 与 DOM hash 识别重复动作、页面停滞和 URL 振荡。它提供
nudge 并向 StrategyManager 报告信号；是否切换策略或最终停止由 controller 状态和预算共同决定，
而不是 loop detector 直接篡改工具结果。

## 完成与产物

`RunLayout` 是唯一的 run 级命名空间。新任务通过 ownership manifest 准备目录，不再递归清空
任意已有输出。主要产物为：

```text
<run>/
  artifacts/                 工具下载、解析和派生文件
  observations/screenshots/  step_NNN.jpg
  control/checkpoints/       可恢复状态（普通模式）
  result/summary.txt          最新 turn 的兼容视图
  result/attachments/         最新 turn 的选中附件
  result/turns/turn-NNN/      不覆盖的逐 turn result snapshot
  trajectory/trace.json       最新 turn 的兼容视图
  trajectory/turns/turn-NNN.json
```

成功 `done` 时，Figure 附件必须位于当前 run 根目录内；选中的图片会发布到
`result/attachments/`，同文件系统上的不可变副本使用硬链接避免重复字节。连续步骤若截图完全
相同，也保留独立的 step 路径但共享 inode。无论成功、失败或中断，finally 都会确保当前 turn 至少有 result snapshot，
并尝试发布无截图的可审计 trace。顶层 result/trace 是 latest 兼容视图；逐 turn snapshot 才是
长期分析时应引用的不可变证据。

## 已知边界

- task timeout 在 step 边界和动作落盘后检查；snapshot、planner 与 tool 仍依赖各自的内部超时。
- `post_action_wait_ms` 位于动作完成与动作后观察之间，保证截图前至少等待该时长；随后还会在
  `observation_stability_timeout_ms` 上限内，要求 URL、`document.readyState`、DOM 节点数、文本长度与
  页面高度连续稳定 `observation_stable_ms`。该启发式不依赖 `networkidle`，因此不会被分析请求或
  长轮询永久阻塞；超时后仍进入带重试的一致性快照捕获。
- `AgentStep.browser_state` 是动作前状态，动作后状态用于截图和进展判断；分析轨迹时不要混淆。
- checkpoint 恢复只保存 tabs/URL 等有限 browser coordinates，不承诺恢复全部外部站点会话。
- result 附件写入是 best effort；strict trace/certificate 的持久化失败会按严格模式规则暴露。
- trace 是压缩、脱敏且不含截图的审计记录，不是浏览器和 provider 的逐字节完整事件日志。
