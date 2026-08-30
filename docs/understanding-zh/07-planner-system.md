# Planner 子系统

## 职责与调用链

Planner 负责把当前任务状态变成一项 `ToolCall | None`，以及回答独立的图像问题；它不直接操作浏览器。主路径是：

```text
WebAgent._think
  -> ToolExecutor.get_tool_descriptions()
  -> APIPlanner.plan_action(task, BrowserState, history_text, available_tools)
       -> build_prompt()
       -> _initial_planning_mode()
       -> _call_structured() / _call()
       -> parse_provider_tool_call() / parse_llm_response()
  -> ToolCall
  -> ToolExecutor.execute()
```

`WebAgent.__init__` 还会把 `ToolExecutor.get_tool_specs()` 返回的、已经按 exposure policy 过滤过的工具目录交给 `APIPlanner.configure_tools()`。因此 provider 收到的工具集合与运行时允许执行的集合一致；隐藏的 API-augmented 工具不会因为 schema 导出而重新暴露。

## 四种输出模式

配置入口是 `AgentConfig.planner_output_mode` 和 CLI `--planner-output-mode`：

| 模式 | 请求字段 | 返回解析 | 用途 |
|---|---|---|---|
| `auto`（默认） | 先发 `tools` + `tool_choice=required` + `parallel_tool_calls=false` | `tool_calls` / 旧 `function_call` | 自动选择 provider 能力 |
| `native-tools` | 同上 | 必须正好一项函数调用 | 强制原生工具调用 |
| `json-schema` | `response_format.type=json_schema` | `tool/parameters/reasoning` JSON | provider 不支持 function tools 时的结构化备选 |
| `prompt-json` | 只靠 system/user prompt | 容错 JSON parser | 最低兼容路径 |

`auto` 的降级梯子固定为：

```text
native-tools -> json-schema -> prompt-json
```

它不会把认证失败、限流、超时或服务端错误误判为“provider 不支持”。只有 HTTP 400/404/415/422，同时响应正文明确包含“不支持/未知/非法”等含义和相应 feature 名（例如 `tools`、`tool_choice` 或 `response_format`），才会降级。每次降级都会进入 `structured_fallbacks`，并随 `PlannerAttempt` 写入 trace。

显式选择某个模式时不会静默降级；配置或 provider 不兼容会直接暴露出来。

## 工具 schema 的来源与边界

`tools/schemas.py` 为所有注册工具提供紧凑 JSON Schema；`ToolRegistry.specs()` 将 name、压缩后的 description 和 parameters 组装成 `ToolSpec`。Schema 会在注册时做结构校验，暴露时按 policy 过滤。

原生 tools 模式把每个 `ToolSpec` 转为：

```json
{
  "type": "function",
  "function": {
    "name": "goto",
    "description": "Navigate to a browser-grounded URL.",
    "parameters": {
      "type": "object",
      "properties": {"url": {"type": "string"}},
      "required": ["url"],
      "additionalProperties": false
    }
  }
}
```

JSON Schema fallback 为兼容不同 OpenAI-compatible provider，只严格约束 action envelope 和工具名枚举；`parameters` 保持开放。真正执行前仍会经过具体工具的 `validate_params()`，所以 provider schema 不是最终授权边界。URL provenance、搜索证据、高风险动作和文件 containment 继续由 `ToolExecutor` 的 execution/risk policy 决定。

## Prompt 与输入

`build_prompt()` 接收：

- 原始 task；
- `BrowserState.url/title/dom_summary/screenshot`；
- 近期步骤、持久 evidence、active milestone 和当前 strategy hint；
- policy notice 与当前允许的工具描述。

DOM 最多取 6000 字符。非空截图压成 JPEG；若 vision probe 不通过，或当前 `file://` 预览已经有结构化 PDF/image 工具证据，规划请求不重复发送截图。所有 transport 共享 `TRANSPORT_AGNOSTIC_PLANNING_RULES`，所以 latest/newest、官方来源、显式日期、Figure 排名核对和禁止臆造等规则不会因降级而消失。

## 响应解析

原生模式只接受正好一个 `tool_calls`，也兼容旧式单个 `function_call`。多个并行调用会被拒绝，因为控制器每步只允许一次 observe→act；擅自取第一项会丢失模型意图并使副作用状态含糊。函数参数既可为对象，也可为 JSON 字符串；解析后工具名还必须存在于当前 `_tool_specs`。

`prompt-json` 与 `json-schema` 使用 `parse_llm_response()`。它支持标准字段和少量旧别名、Markdown code fence、常见前导语和尾逗号修复，但最终必须得到非空工具名及对象参数。解析失败返回 `None`，由 `WebAgent._think()` 在 `planner_max_attempts` 范围内修复重试。

## HTTP、超时与观测数据

`_bounded_post()` 同时使用 httpx read/connect timeout 和 `asyncio.wait_for` 的硬墙钟上限。后者防止流式/细水长流响应不断重置 read timeout。非 2xx 通过 `raise_for_status()` 传播；API body 不是对象也会失败，不会伪造成空 action。

每次 planner 调用记录：

- response length 与 finish reason；
- prompt/completion/total tokens；
- requested/effective output mode；
- structured fallback 事件；
- 成功、异常和墙钟耗时。

这些字段进入 `AgentResult.planner_attempts` 和 schema-v8 `trajectory/trace.json`，不保存模型隐藏思维链。

## 视觉路径

`APIPlanner.load()` 用运行时生成的红色 JPEG 探测 chat endpoint 是否真正接收并理解图像；只接受格式但看不到图像时会禁用 chat vision。MiniMax 一类服务还会探测独立 VLM endpoint。`analyze_image()` 对短问答和详细解释使用不同 token 预算，单次短暂失败会重试，连续失败才把 chat vision 锁为不可用。

视觉不可用时返回明确的文本边界，并建议 `pdf_get_figure_info`、`pdf_extract_text` 或 `pdf_search`；不会把空视觉输出标成成功分析。

## `StubPlanner`

没有远程凭证且未启用 vLLM 时，CLI 构造 `StubPlanner`。它不会用启发式假装完成任务，而是立即调用 `done`，说明需要配置 LLM backend。它只用于生命周期/基准基础设施测试，不代表自主规划能力。

## 仍需外部验证的边界

- OpenAI-compatible 不是统一规范；某些 provider 对 `tools`、`response_format` 或 reasoning 参数的实现仍可能漂移。
- Schema 与 runtime validator 能约束格式，不能保证选择的动作正确。
- Vision probe 是能力冒烟，不是视觉正确率 benchmark。
- 策略切换与 checkpoint 提高恢复性，但其真实模型收益必须通过重复 benchmark 与消融测量。
