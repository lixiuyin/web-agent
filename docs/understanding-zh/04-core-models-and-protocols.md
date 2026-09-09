# 核心模型与 Protocol

## 为什么从这里开始

`core` 定义跨模块共同语言。理解字段的生产者和消费者，比先读具体工具更重要。

## 模型契约与字段生命周期

模型的权威定义是 [core/models.py](../../src/webagent/core/models.py)。下表解释生产者、消费者和容易混淆的字段；完整字段、默认值与校验规则请直接查看源码。

| 模型 | 作用与字段边界 |
| --- | --- |
| `TaskStatus` | 区分 pending、running、completed、timeout、failed、interrupted、max_steps_reached 和 blocked；工具成功不代表任务完成。 |
| `ToolCall` | Planner 生成，executor、policy、loop 和 history 消费；`tool_name` 在模型校验时去除首尾空白并统一小写。 |
| `ToolResult` | 工具返回 success/error/data；`audit` 保存执行策略证据，不进入 planner 可见的工具 data。 |
| `BrowserState` | `_observe` 产生 URL/title、viewport/document context、observation id、元素、覆盖元数据和可选截图；完整 HTML 不进入该模型，trace 也不保存图像 payload。 |
| `AgentStep` | 保存观察、调用和结果；`duration_seconds` 是步骤耗时，`tool_duration_seconds` 单独记录工具耗时。 |
| `PlannerAttempt` | 记录单次规划尝试、token 用量、`transport_retries`、请求/实际输出模式以及 `structured_fallbacks`，用于区分传输重试和格式降级。 |
| `AgentResult` | 返回状态、最终结果、history、planner_attempts 和 events；events 保留运行事件。 |

[run_outputs.py](../../src/webagent/agent/run_outputs.py) 负责把步骤、规划尝试和事件写入经过压缩/脱敏的 `trajectory/trace.json`，把摘要写入 `result/summary.txt`。启用 checkpoint 时，[checkpoint.py](../../src/webagent/agent/checkpoint.py) 另行保存恢复所需的历史与状态。持久化表示不是模型的原样 JSON 副本，文件布局以 [run-artifacts.md](../reference/run-artifacts.md) 为准。

## 输入输出示例

Planner 的输出格式：

```json
{
  "tool": "click",
  "parameters": {
    "selector": {"type": "ref", "value": "obs123/f0:e4"},
    "force": false
  },
  "reasoning": "Submit the completed form"
}
```

解析为：

```python
ToolCall(
    tool_name="click",
    parameters={
        "selector": {"type": "ref", "value": "obs123/f0:e4"},
        "force": False,
    },
    reasoning="Submit the completed form",
)
```

执行失败可以正常返回：

```json
{
  "success": false,
  "tool_name": "click",
  "error": "Execution: Stale observation target; re-observe before acting",
  "data": {}
}
```

`success=False` 不等于 Python 异常：它是一等结果，由 Agent 累加连续失败并决定是否终止。

## Protocol 是什么

Protocol 即结构化子类型协议（structural typing protocol）：类不必继承某个父类，只要拥有要求的方法，就能满足接口。运行时可检查的 `Planner` 和 `Tool` 使用了 `@runtime_checkable`。

权威签名见 [core/protocols.py](../../src/webagent/core/protocols.py)：

- `Planner`：`plan_action`、`analyze_image`、`load`、`unload`，分别承担规划、图像分析和资源生命周期。
- `Tool`：通过 `validate_params` 校验参数，异步 `execute` 返回 `ToolResult`。名称、说明和参数 schema 由工具注册元数据管理，不属于此 Protocol 的必需成员。
- `AgentHook`：`on_task_start`、`on_step_complete`、`on_task_end`，接收任务生命周期通知。

设计收益是测试可以传入轻量 mock；代价是实例构造依赖仍通过 `Any` 和 `**kwargs` 注入，运行时不保证每个工具拿到必需依赖。

## Hooks

当前生命周期扩展点是 `AgentHook`，CLI 注册 `LoggingHook` 记录任务开始、步骤结果和任务结束。历史通过 `AgentResult.history` 返回给调用方，并由运行输出模块保存为 trace 中的步骤记录。

完整 schema 速查见 [appendix-input-output-schemas.md](appendix-input-output-schemas.md)。
