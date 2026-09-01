# 输入输出 Schema 附录

## CLI

| 参数 | 格式 | 说明 |
|---|---|---|
| `--task` | string | 非交互模式必需 |
| `--interactive` | flag | 多轮输入；首轮后保留 history |
| `--output` | path string | 一个 exact run 根；非空且无有效 manifest 时拒绝，缺省则从 workspace 分配唯一 run |
| `--model/api-url/api-key` | string | 远程模型覆盖 |
| `--use-vllm/--no-vllm` | mutually exclusive flag | local compatible endpoint |
| `--vllm-*` | string | local endpoint/model/token |
| `--headless/--headed` | flag | 后者可能被 DISPLAY 检查覆盖 |
| `--browser-profile-mode` | `persistent\|temporary` | profile 复用或隔离 |
| `--strict-eval` | flag | 临时 profile、新输出、搜索引擎唯一发现路径、无持久 PDF cache、写 trace 与 verification certificate |
| `--search-engine-only` | flag | 与 strict discovery contract 相同；禁止 GitHub/arXiv/official-report 直连发现与未见 URL |

## BrowserState

```json
{
  "screenshot": "PIL.Image.Image or null（不可直接 JSON）",
  "dom_summary": "Markdown string",
  "url": "https://...",
  "title": "page title",
  "timestamp": "ISO-8601 string"
}
```

## Snapshot element

```json
{
  "tag": "button",
  "text": "Submit",
  "attrs": {
    "id": "submit",
    "name": "submit",
    "type": "button",
    "role": "button",
    "aria-label": "Submit form",
    "class": "primary"
  },
  "css_path": "html > body > form > button#submit",
  "bbox": {"x":100,"y":200,"width":120,"height":40},
  "is_visible": true,
  "_priority": 78.5,
  "_index": "i_deadbeef"
}
```

AX 路径可能没有 `css_path`；字段都是开放 dict，不是 Pydantic model。

## ToolCall / ToolResult

```json
{
  "tool_name": "search",
  "parameters": {"query":"web agents","engine":"google","recency":"year"},
  "reasoning": "Find recent work"
}
```

```json
{
  "success": true,
  "tool_name": "search",
  "error": null,
  "data": {"results":[{"title":"...","link":"...","snippet":"..."}],"count":1},
  "audit": {"policy":"search_engine_only","decision":"allow","provenance":{}}
}
```

## AgentStep / AgentResult

```json
{
  "step_number": 1,
  "timestamp": "ISO string",
  "browser_state": "BrowserState before action",
  "tool_call": "ToolCall",
  "tool_result": "ToolResult",
  "duration_seconds": 2.41
}
```

```json
{
  "success": true,
  "status": "completed",
  "steps_taken": 5,
  "total_duration": 31.2,
  "final_result": {"summary":"answer","attachments":["path"]},
  "history": ["AgentStep", "..."],
  "planner_attempts": ["PlannerAttempt", "..."]
}
```

`trajectory/trace.json` 不含 screenshot/base64，也不等同于完整原始事件流；它保存 strict/profile/cache
状态、逐次 planner 耗时/usage/错误、逐步 `tool_duration_seconds`，以及经
`planner_context()` 投影和 secret redaction 的工具证据；启用执行策略时，每一步另有不进入
planner data 的 `policy` 审计字段。Figure 分析结果还可包含
`vision_duration_seconds` 和不含密钥的视觉 usage metadata。

运行级文件契约还包括：`observations/screenshots/` 保存观察截图，
`control/checkpoints/latest.json` 保存可恢复控制状态，`result/summary.txt` 保存 Agent 最新声明，
`evaluation/` 留给独立判分。旧版 trace/certificate/checkpoint 在 run 的 `artifacts/` 下只保留读取兼容，
新 writer 不再使用这些位置。

普通 interactive session 还写不可变的 `trajectory/turns/turn-NNN.json` 与
`result/turns/turn-NNN/{summary.txt,attachments/}`；顶层 canonical 文件可以随最新 turn 更新，历史
turn 快照不覆盖。strict/search-only 不允许一个 run 含多个 turn。

trace 内指向当前 run 的文件路径统一写成 run-relative POSIX 路径；运行时返回值仍可使用绝对路径。
这使冻结后的 run 可以整体移动，同时不改变证据指向。latest trace 与 turn trace 字节完全一致时，
两条规范路径通过硬链接共享内容，但逻辑语义仍分别是“最新视图”和“不可变 turn 快照”。

## BrowserGym 外部报告

WebArena/VisualWebArena 不复用内部 `AgentResult` 判分。adapter 保存 BrowserGym 原生 episode
reward、terminated/truncated、step 数、错误、task id 与源指纹，再汇总成独立
`browsergym-results.json`。两层 portfolio 只绑定内部 longitudinal report 与每个模型各一份完整
WebArena-Verified Hard、VisualWebArena report；不会把不同 evaluator 的 reward 求一个总平均分。

报告只有在相应 `WA_*`/`VWA_*` 站点与 reset endpoint 可达、task catalog 匹配且原生 evaluator
产物完整时才计为 ready。安装 BrowserGym Python 包本身不产生有效 benchmark episode。

## Planner request

OpenAI-compatible text-only：

```json
{
  "model": "model-id",
  "messages": [
    {"role":"system","content":"planner rules"},
    {"role":"user","content":"TASK... URL... PAGE... TOOLS... HISTORY..."}
  ],
  "temperature": 0.7,
  "max_tokens": 4096
}
```

视觉时 user content 变为数组：

```json
[
  {"type":"text","text":"prompt"},
  {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,...","detail":"high"}}
]
```

响应期望：`choices[0].message.content`，为空时尝试 `reasoning_content`；再尝试顶层 `response` 或 `data.content`。

## CAPTCHA result

```json
{
  "detected": true,
  "type": "recaptcha",
  "confidence": 0.9,
  "reason": "Detected recaptcha via DOM patterns",
  "selectors": ["iframe[src*=\"recaptcha\"]"]
}
```

## DocumentProfile

```json
{
  "suffix": ".pdf",
  "page_count": 12,
  "avg_chars_per_page": 1340.5,
  "image_ratio": 0.5,
  "has_text_layer": true,
  "is_likely_scanned": false,
  "size_bytes": 1234567
}
```

这是 frozen dataclass，驱动 parser routing。

## ParseRequest

```text
file_path: pathlib.Path
profile: DocumentProfile
output_dir: pathlib.Path
images_dir: pathlib.Path
config: AgentConfig
```

## PDF structured types

```json
{
  "ImageInfo": {
    "path":"string", "page_idx":0, "bbox":[0,0,0,0],
    "caption":"string", "footnote":"string", "figure_number":"1a"
  },
  "TableInfo": {
    "path":"string", "page_idx":0, "bbox":[0,0,0,0],
    "caption":"string", "footnote":"string", "html_body":"<table>...", "table_number":"2"
  },
  "TextBlock": {
    "text":"string", "page_idx":0, "bbox":[0,0,0,0],
    "level":1, "block_type":"title"
  }
}
```

`page_idx` 是 0-based；人类显示时通常加一。BBox 坐标语义依 provider，当前多数为零。

## 常见工具参数模式

| 模式 | JSON |
|---|---|
| selector | `{"selector":{"type":"css\|text","value":"..."}}` |
| URL | `{"url":"https://..."}` |
| path | `{"path":"relative/to/artifacts"}` |
| search | `{"query":"...","engine":"google\|bing\|duckduckgo","recency":"..."}` |
| official report | `{"subject":"project/model","official_owner":"exact GitHub owner"}` |
| PDF figure | `{"path":"paper.pdf","figure":"1","question":"..."}` |
| finish | `{"summary":"actual final answer","attachments":["..."]}` |

每个注册工具现在都通过 `ToolRegistry.specs()` 导出 provider-neutral `ToolSpec` 和 JSON
Schema；原生 function-tools 请求直接使用这些 parameter schema。具体工具的
`validate_params()` 仍是执行前的最终格式校验，而 URL provenance、文件 containment 与
高风险授权由独立 policy 决定，不能仅凭 schema 绕过。
