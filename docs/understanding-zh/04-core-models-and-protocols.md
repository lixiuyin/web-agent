# 核心模型与 Protocol

## 为什么从这里开始

`core` 定义跨模块共同语言。理解字段的生产者和消费者，比先读具体工具更重要。

## 完整数据模型源码

来源：`src/webagent/core/models.py`。这些 Pydantic 模型被 agent、planner、tools 和 tests 共同使用。

```python
class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    MAX_STEPS_REACHED = "max_steps_reached"

class ToolCall(BaseModel):
    """A planned tool invocation from the LLM."""

    tool_name: str = Field(..., description="Name of the tool to execute")
    parameters: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(default="", description="LLM rationale")

class ToolResult(BaseModel):
    """Result of a tool execution."""

    success: bool
    tool_name: str
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

class BrowserState(BaseModel):
    """Observed browser state at a point in time."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    screenshot: Image.Image | None = None
    dom_summary: str
    url: str
    title: str
    timestamp: str

class AgentStep(BaseModel):
    """Record of a single observe-think-act cycle."""

    step_number: int
    timestamp: str
    browser_state: BrowserState
    tool_call: ToolCall
    tool_result: ToolResult
    duration_seconds: float

class PlannerAttempt(BaseModel):
    step_number: int
    attempt_number: int
    timestamp: str
    duration_seconds: float
    success: bool
    error: str | None = None
    response_length: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

class AgentResult(BaseModel):
    """Final result of an agent task execution."""

    success: bool
    status: str
    steps_taken: int
    total_duration: float
    final_result: dict[str, Any] = Field(default_factory=dict)
    history: list[AgentStep] = Field(default_factory=list)
    planner_attempts: list[PlannerAttempt] = Field(default_factory=list)
```

## 字段生命周期

| 结构             | 生产者                        | 消费者                                | 是否持久化                                    |
| -------------- | -------------------------- | ---------------------------------- | ---------------------------------------- |
| `BrowserState` | `WebAgent._observe`        | Planner、AgentStep                  | screenshot 写 `observations/screenshots/`；trace 排除图像 payload |
| `ToolCall`     | Planner parser/StubPlanner | ToolExecutor、loop detector、history | 包含在 AgentResult history                  |
| `ToolResult`   | ToolRegistry/工具实现          | Agent loop、history、hooks           | 包含在 history；`done.data` 另存最终输出           |
| `AgentStep`    | Agent loop                 | SessionHistory、hooks               | 默认在内存；CLI 不自动写历史 JSON             |
| `PlannerAttempt` | Agent `_think`           | AgentResult、run trace              | `trajectory/trace.json`                 |
| `AgentResult`  | `WebAgent.run`             | CLI/调用方                            | 压缩/脱敏形式写入 `trajectory/trace.json`；summary 另写 `result/summary.txt` |

## 输入输出示例

Planner 的输出格式：

```json
{
  "tool": "click",
  "parameters": {
    "selector": {"type": "css", "value": "button.submit"},
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
        "selector": {"type": "css", "value": "button.submit"},
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
  "error": "Not found: button.submit",
  "data": {}
}
```

`success=False` 不等于 Python 异常：它是一等结果，由 Agent 累加连续失败并决定是否终止。

## Protocol 是什么

Protocol 即结构化子类型协议（structural typing protocol）：类不必继承某个父类，只要拥有要求的方法，就能满足接口。运行时可检查的 `Planner` 和 `Tool` 使用了 `@runtime_checkable`。

来源：`src/webagent/core/protocols.py`，完整核心协议：

```python
@runtime_checkable
class Planner(Protocol):
    """Plans the next agent action given the current state."""

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall | None:
        """Return the next tool call, or *None* if planning fails."""
        ...

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        """Describe / answer a question about an image."""
        ...

    async def load(self) -> None:
        """Initialise any heavyweight resources (model weights, connections)."""
        ...

    async def unload(self) -> None:
        """Release resources."""
        ...

@runtime_checkable
class Tool(Protocol):
    """A single tool that the agent can invoke."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    async def execute(self, params: dict[str, Any]) -> ToolResult: ...

    def validate_params(self, params: dict[str, Any]) -> None: ...

class AgentHook(Protocol):
    """Lifecycle hook for observing / modifying agent behaviour."""

    async def on_task_start(self, task: str) -> None: ...

    async def on_step_complete(
        self,
        step_number: int,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> None: ...

    async def on_task_end(self, status: str, steps: int) -> None: ...
```

设计收益是测试可以传入轻量 mock；代价是实例构造依赖仍通过 `Any` 和 `**kwargs` 注入，运行时不保证每个工具拿到必需依赖。

## Hooks

当前生命周期扩展点是 `AgentHook`，CLI 注册 `LoggingHook` 记录任务开始、步骤结果和任务结束。历史通过 `AgentResult.history` 返回给调用方，CLI 不自动写历史 JSON。

完整 schema 速查见 [appendix-input-output-schemas.md](appendix-input-output-schemas.md)。
