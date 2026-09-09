# Tool 系统

## Tool Calling

Tool Calling（工具调用）是模型输出结构化动作、运行时执行真实能力的模式。模型只能提出 `ToolCall`；参数校验、权限边界、异常捕获和超时均在工具层完成。

执行策略的公开导入入口仍是 `tools/policy.py`，实现位于 `tools/policies/`：

- `contracts.py` 定义 policy Protocol、决策和可见 URL 来源集合；
- `evidence.py` 提供 URL/版本/搜索范围处理、证据规则及 checkpoint 值校验；
- `search.py` 实现 `SearchEngineOnlyPolicy`，持有搜索证据状态并执行授权；
- `grounded.py` 实现 `BrowserGroundedPolicy`，扩展用户 URL 与混合发现路径。

## 装饰器与 Registry 调用链与边界

来源：`src/webagent/tools/registry.py`。导入 `webagent.tools.builtin` 会执行所有 `@tool` 装饰器，把类写入模块级 `_TOOL_REGISTRY`；`auto_discover` 再实例化。

实现：[registry.py](../../src/webagent/tools/registry.py) 的 `tool()`、`ToolRegistry`，
以及 [schemas.py](../../src/webagent/tools/schemas.py) 的参数 schema。

`@tool` 注册不可变 `ToolDefinition(name, description, implementation, parameters)`，
注册前验证 JSON Schema。`auto_discover()` 实例化工具、检查 Tool Protocol，并复制 schema。
`descriptions(names)` 和 `specs(names)` 只返回允许的工具，后者生成带参数约束的 `ToolSpec`。
`validate_call()` 先验证 JSON Schema，再调用工具自身的 `validate_params()`；`execute()`
仅在验证通过后执行工具，并将普通执行异常转换为失败 `ToolResult`。

输入 `auto_discover(**kwargs)` 在 CLI 中是 `browser/config/planner`。每个工具构造函数只消费自己需要的参数并通过 `**kw` 忽略其余参数。构造失败会转为 `ToolRegistrationError`，错误消息包含工具名，`__cause__` 保留原始异常；registry 不再无参重试，因此不会掩盖构造函数内部的 `TypeError`。

`execute(name, params)` 输出始终是 `ToolResult`：unknown、ValueError validation 和普通 execution exception 都被对象化。只有 BaseException 或 registry 自身未覆盖情形会继续传播。

## ToolExecutor 调用链与边界

实现：[executor.py](../../src/webagent/tools/executor.py) 的 `ToolExecutor.execute()`。
以下是调用链说明，不是可直接运行的源码副本：

```text
ToolCall（模型构造时统一 strip + casefold 工具名）
  -> PlanningCoordinator repair loop
       -> ToolExecutor.validate_tool_call
       -> registry schema/validate_params + 当前 observation ref + policy planner preflight
  -> pending-action 写前 checkpoint
  -> ToolExecutor.execute
  -> exposed-tool gate
  -> evidence policy.authorize（异常或拒绝时停止）
  -> risk policy.authorize（异常或拒绝时停止）
  -> wait_for(registry.execute) -> schema/参数校验 -> tool.execute
  -> planner_result_preview -> policy.record_result
  -> 合并 evidence/risk audit -> ToolResult
```

构造函数同时接收 registry、timeout、allowed_tools、evidence policy 和 risk policy。
实际可用集合是显式 allowed_tools 与 evidence policy.allowed_tools 的交集。
`get_tool_descriptions()` 和 `get_tool_specs()` 使用同一集合；planner 预检通过不取代执行时授权。
记录证据失败也会返回失败结果，但已经发生的工具副作用不会自动撤销。

Executor 把模型生成的工具名规范化，以 `asyncio.wait_for` 约束 registry 校验与工具执行；前后的 policy 调用不在这个 wait_for 范围内。超时会取消 coroutine 并返回失败，不保证外部服务已接收的请求或线程中的同步工作被撤销。

## Click 工具的数据流

来源：`browser_tools.py::ClickTool`。

实现：[browser_tools.py](../../src/webagent/tools/builtin/browser_tools.py) 的 `ClickTool`。

它继承 `BrowserToolBase` 获取 browser。若 selector 是当前观察中的 compact ref，registry 先经
`execute_observed()` 找回当时保存的 DOM node，并复验 identity、geometry 与 hit test 后直接执行；
若是 legacy CSS/text，才由 `_resolve_selector()` 转为 Playwright selector 并调用 controller。
失败转换为 `ToolResult.error`；成功保留输入 selector，并按实际响应附加元数据。

输入：

```json
{
  "selector": {"type": "css", "value": "button.submit"},
  "force": false
}
```

selector 也可为 `{"type":"text","value":"Submit"}`，被转换为 Playwright `text="Submit"`。
主路径更推荐从当前页面观察原样复制
`{"type":"ref","value":"observation_id/f0:e0"}`。旧 ref、frame 不匹配、目标被替换/遮挡或
离开 viewport 会失败；`force` 不能绕过这些检查。Legacy selector 的成功输出示例：

```json
{"success":true,"tool_name":"click","error":null,"data":{"selector":{"type":"css","value":"button.submit"}}}
```

控制器失败字典在工具层转成 `ToolResult.error`；成功时只透传明确列出的标签页元数据。

## `done` 调用链与边界

实现：[task_tools.py](../../src/webagent/tools/builtin/task_tools.py) 的 `DoneTool`。

`summary`（兼容 `result`）必须是非空且含字母或数字的答案，纯标点不通过。
可选 `success_probability` 必须是有限、非 bool、位于 0–1 的数值；输出保留 summary、
attachments 和提供的概率。运行时完成状态由 Agent 在成功执行 `done` 后设置，外部评估
仍独立判断任务是否真正成功。

`done` 返回成功结果后，由 Agent loop 把任务设为 completed。`ToolCall` 在模型校验时统一工具名，因此 `Done` 或带首尾空白的名称也会规范为 `done`，供策略、执行器与终止判断共同使用。

## 内置工具分组（非完整目录）

| 组 | 工具 | 输入/输出重点 |
|---|---|---|
| 导航 | `goto`, `click`, `click_link`, `type`, `press`, `scroll`, `wait`, `forward`, `back` | URL、结构化 selector、键盘/像素；输出页面动作结果 |
| 页面交互 | `hover`, `select_dropdown`, `wait_for_element`, `get_attribute`, `get_all_links`, `get_url`, `get_title`, `refresh`, `scroll_to_element`, `get_search_results` | 页面查询与辅助动作 |
| 页面观察 | `screenshot`, `dom_summary`, `extract_text` | 保存图片、返回 Markdown 或文本 |
| 搜索 | `search`, `arxiv_search`, `github_search`, `official_report_search` | 网页结果、arXiv 题名检索、官方仓库报告 PDF 与多源候选比较 |
| 文件/视觉 | `save_image`, `write_text`, `read_image`, `analyze_image` | 路径被限制到 artifacts；视觉依赖 planner |
| PDF基础 | `download_pdf`, `pdf_parse`, `pdf_find_images`, `pdf_find_tables`, `pdf_find_section`, `pdf_content_summary`, `pdf_extract_text`, `pdf_extract_images`, `pdf_get_figure_info` | 下载、parse、结构查询 |
| PDF QA | `pdf_qa`, `pdf_search`, `pdf_list_figures`, `pdf_list_tables`, `pdf_list_sections`, `pdf_analyze_figure` | chunk retrieval、图表枚举、视觉分析 |
| PDF mining | `pdf_extract_table_data`, `pdf_find_mentions`, `pdf_get_section`, `pdf_get_hierarchy`, `pdf_get_metadata`, `pdf_extract_metrics`, `pdf_extract_topics`, `pdf_extract_citations`, `pdf_summarize_sections`, `pdf_compare_entities` | 对缓存的 `PDFParseResult` 做规则型结构分析 |
| 生命周期 | `done` | summary 必填，attachments 可选 |

完整可用名称与参数以 `ToolRegistry.names()` / `specs()` 为准；实际暴露集合还受 discovery mode 与 policy 限制。

## Search 工具

实现位于 [search_tools.py](../../src/webagent/tools/builtin/search_tools.py)。`execute()` 选择
引擎与时间过滤后，由 `_prepare_primary_search()` 完成主路径导航/提交，`_collect_results()`
验证结果。失败时 `_try_fallback_engine()` 记录尝试并调用 `_attempt_engine()`；后者用
`_prepare_fallback_search()` 准备单个回退引擎，不递归启动另一条 cascade。两条成功路径
共享 `_search_result_data()`，统一使用实际页面 URL/title 和 market 元数据。

查询准备、结果相关性判断和重定向解包位于 [search/support.py](../../src/webagent/tools/search/support.py)；
SERP 可用性检查与抽取位于 [search/results.py](../../src/webagent/tools/search/results.py)。
策略的主体、官方身份、候选 scope 和版本证据分析位于
[policies/discovery.py](../../src/webagent/tools/policies/discovery.py)，授权与证据状态仍由 policy 管理。

带明确版本名的 report/PDF 相关性必须在同一条搜索结果中成立，不能把一行的版本名与另一行的
“technical report”拼成命中。严格最新报告流程还可用官方 owner 的已渲染 repository index
建立候选 scope；一旦出现更高 dotted version，恢复动作优先精确 frontier repository 和其
report file，避免较旧但更早出现的 PDF 抢占候选。

`search` 默认按 Bing→Yahoo Japan→Seznam→Yahoo→DuckDuckGo 轮换，并把 Yahoo tracking redirect 还原为目标 URL；Google 因持续触发人机认证而默认关闭，只有 `allow_google_search=true` 或已配置 Google JSON API 才会访问。strict headless 只允许 Bing、Yahoo Japan 与 Seznam。recency 使用 URL 参数，不能把 `dt:y` 等引擎语法污染到查询文本；`latest` 表示比较全量候选，而非硬限制为最近一周。网站 selector 和反爬页面会随时间变化，必须用 integration 测试验证。

`arxiv_search` 请求 arXiv Atom API并处理 rate limit/传输重试；包含“technical report”的普通查询会约束到题名字段，避免把正文仅提及目标模型的第三方论文当作官方报告。

`github_search` 使用 GitHub API 搜索仓库，并从 Git tree 中找 report/whitepaper PDF；显式 owner 完全匹配时才标记 `first_party=true`。它返回文件级 commit 日期、blob URL 与 raw 下载 URL。无 token 时 API 配额较低，因此只检查按创建时间排序后的前三个仓库，API 受限时用常见根目录文件名和公开 Atom feed 兜底。

`official_report_search(subject, official_owner?)` 并发执行题名约束的 arXiv 搜索与 GitHub
报告搜索，拒绝只提及 subject、却没有 technical report/whitepaper 标记的候选。返回值把
`verified_first_party_candidates` 与 `all_candidates` 分开：只有显式 owner 精确匹配的
GitHub 候选进入前者；arXiv 题名命中仍标为 authorship unverified。
两个来源各自受 `official_report_source_timeout_seconds` 限制；一个来源超时会作为部分错误
保留，另一个已成功的精确 owner 结果仍可立即参与排序，不会被慢请求无限拖住。

## 文件与路径安全

文件工具和 PDF 工具借助 `utils.paths` 把相对路径锚定在 artifacts 目录，并拒绝 `..` 或已经存在但位于输出根外的绝对路径。`goto` 另有危险 scheme denylist。安全边界主要在工具层，而不是底层 controller/parser API。

每次普通运行分配新的 output root；显式路径必须满足 ownership/空目录契约，不会任意清空旧证据。
containment 因而把工具写入限制在本次受管 run 的 artifacts 范围。

## PDF QA 与缓存

PDF 工具共享进程内 `PdfResultCache`，key 基于文件内容 SHA-256，只接受无 error 且非本地
降级的结果；同一内容的并发请求以 single-flight lock 合并。配置开启时，成功 parse 还会
原子写入跨进程缓存并在新 artifacts 中重建路径。严格模式禁用持久缓存，并将内存 key 绑定
到本次 artifacts root。文本 QA 是字符 chunk + 关键词/短语启发式检索，不是 embedding RAG，
也不自动调用 LLM 生成答案；`pdf_qa` 返回相关 excerpts。

图像解析按 `figure_number/caption` 匹配，`pdf_list_figures` 将有编号/标题的 figures 与未标注图片分开，从而减少把 logo 当 Figure 1 的错误。`pdf_analyze_figure` 对精确编号还先尝试
caption-grounded 本地矢量/栅格渲染；只有单一且置信度达标的候选才绕过云解析，歧义、低
置信度和非编号 caption 均回到原有 cascade。工具结果的 `local_figure_fast_path` 字段记录
是否命中、耗时、置信度、图形类型、bbox 和渲染尺寸，便于 trace 审计。
视觉请求按“Figure 所在页优先、前一页补充”的顺序加入最多 6000 字符文本上下文，补足跨页
定义但不把整份 PDF 混成视觉证据。

## 新工具契约

新增工具必须：用唯一 `@tool(name, description)`；构造函数接受注入依赖；`validate_params` 对 LLM 不可信输入做类型/范围/路径校验；`async execute` 返回与装饰器同名的 `ToolResult`；在 `builtin/__init__.py` import；增加 mock 外部依赖的单元测试。

## 当前限制

- JSON Schema 能验证参数结构，但不能表达所有跨字段、页面 provenance、风险授权与语义正确性；
  这些仍由 `validate_params()` 和独立 policy 在执行前补足。
- registry 是模块级全局类表，重复 import 安全但同名装饰器会覆盖旧类。
- registry 是进程内全局声明表；测试或动态插件若注册临时工具，必须在隔离边界清理。
- 工具间结果格式不是统一 Pydantic schema，`data` 是开放字典。
- 大型 PDF mining/QA 文件职责较多，规则抽取与 Tool adapter 耦合，维护成本高。
