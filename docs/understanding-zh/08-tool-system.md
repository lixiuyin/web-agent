# Tool 系统

## Tool Calling

Tool Calling（工具调用）是模型输出结构化动作、运行时执行真实能力的模式。模型只能提出 `ToolCall`；参数校验、权限边界、异常捕获和超时均在工具层完成。

## 装饰器与 Registry 完整原代码

来源：`src/webagent/tools/registry.py`。导入 `webagent.tools.builtin` 会执行所有 `@tool` 装饰器，把类写入模块级 `_TOOL_REGISTRY`；`auto_discover` 再实例化。

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    implementation: type[Any]

class ToolRegistrationError(RuntimeError):
    """Raised when a decorated tool cannot be initialized or registered."""

_TOOL_REGISTRY: dict[str, ToolDefinition] = {}

def tool(name: str, description: str) -> Callable[[type[Any]], type[Any]]:
    """Class decorator to register a tool implementation.

    Usage::

        @tool("goto", "Navigate to URL. params: url (string)")
        class GotoTool:
            async def execute(self, params: dict[str, Any]) -> ToolResult: ...
            def validate_params(self, params: dict[str, Any]) -> None: ...
    """

    def decorator(cls: type[Any]) -> type[Any]:
        metadata = {"_tool_name": name, "_tool_description": description}
        for attribute, value in metadata.items():
            setattr(cls, attribute, value)
        _TOOL_REGISTRY[name] = ToolDefinition(name, description, cls)
        return cls

    return decorator

class ToolRegistry:
    """Manages tool instances and provides lookup / discovery."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, tool_instance: Tool) -> None:
        """Register a single tool instance."""
        name = getattr(tool_instance, "_tool_name", None) or getattr(tool_instance, "name", None)
        if not isinstance(name, str) or not name:
            msg = f"Tool {type(tool_instance).__name__} has no non-empty name attribute"
            raise ToolRegistrationError(msg)
        if not isinstance(tool_instance, Tool):
            msg = f"Tool '{name}' does not implement the Tool protocol"
            raise ToolRegistrationError(msg)
        self._tools[name] = tool_instance
        description = getattr(tool_instance, "_tool_description", None) or getattr(
            tool_instance, "description", ""
        )
        self._descriptions[name] = description if isinstance(description, str) else ""
        logger.debug("Registered tool: %s", name)

    def auto_discover(self, **kwargs: Any) -> None:
        """Instantiate and register all tools decorated with @tool.

        Keyword arguments are passed through to each tool's constructor.
        """
        for name, definition in _TOOL_REGISTRY.items():
            if name not in self._tools:
                try:
                    instance = definition.implementation(**kwargs)
                except Exception as exc:
                    msg = f"Failed to initialize tool '{name}': {exc}"
                    raise ToolRegistrationError(msg) from exc
                if not isinstance(instance, Tool):
                    msg = f"Tool '{name}' does not implement the Tool protocol"
                    raise ToolRegistrationError(msg)
                self._tools[name] = instance
                self._descriptions[name] = definition.description
                logger.debug("Auto-discovered tool: %s", name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def descriptions(self) -> str:
        """Return a compact string of all tool descriptions for LLM prompts."""
        return "\n".join(
            f"{name}: {self._descriptions.get(name, '')}" for name in self._tools
        )

    async def execute(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Validate and execute a tool by name."""
        impl = self._tools.get(name)
        if impl is None:
            return ToolResult(success=False, tool_name=name, error=f"Unknown tool: {name}")

        try:
            impl.validate_params(params)
        except ValueError as exc:
            return ToolResult(success=False, tool_name=name, error=f"Validation: {exc}")

        try:
            return await impl.execute(params)
        except Exception as exc:
            return ToolResult(success=False, tool_name=name, error=f"Execution: {exc}")
```

输入 `auto_discover(**kwargs)` 在 CLI 中是 `browser/config/planner`。每个工具构造函数只消费自己需要的参数并通过 `**kw` 忽略其余参数。构造失败会转为 `ToolRegistrationError`，错误消息包含工具名，`__cause__` 保留原始异常；registry 不再无参重试，因此不会掩盖构造函数内部的 `TypeError`。

`execute(name, params)` 输出始终是 `ToolResult`：unknown、ValueError validation 和普通 execution exception 都被对象化。只有 BaseException 或 registry 自身未覆盖情形会继续传播。

## ToolExecutor 完整原代码

```python
class ToolExecutor:
    """Thin wrapper that dispatches ToolCalls to a ToolRegistry."""

    def __init__(self, registry: ToolRegistry, tool_timeout: int = _DEFAULT_TOOL_TIMEOUT) -> None:
        self._registry = registry
        self._tool_timeout = tool_timeout

    def get_tool_descriptions(self) -> str:
        return self._registry.descriptions()

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        name = (tool_call.tool_name or "").lower()
        params = tool_call.parameters or {}
        try:
            return await asyncio.wait_for(
                self._registry.execute(name, params),
                timeout=self._tool_timeout,
            )
        except TimeoutError:
            return ToolResult(
                success=False,
                tool_name=name,
                error=f"Tool '{name}' exceeded {self._tool_timeout}s timeout and was cancelled",
            )
```

Executor 把模型生成的工具名转小写，以 `asyncio.wait_for` 实施全工具墙钟超时。超时会取消 coroutine 并返回失败，不保证外部服务已接收的请求或线程中的同步工作被撤销。

## 一个完整工具例子

来源：`browser_tools.py::ClickTool`。

```python
@tool("click", "Click element. params: selector={type:'text'|'css', value:(string)}, force=false")
class ClickTool:
    """Click on an element using text or CSS selector.

    Examples:
    - Click by text: {"selector": {"type": "text", "value": "Submit Button"}}
    - Click by CSS: {"selector": {"type": "css", "value": "#submit-btn"}}
    - Click with force: {"selector": {"type": "text", "value": "Link"}, "force": true}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        force = bool(params.get("force", False))
        resp = await self.browser.click(selector, force=force)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="click", data={"selector": params["selector"]}
            )
        return ToolResult(success=False, tool_name="click", error=resp.get("error", "Click failed"))
```

输入：

```json
{
  "selector": {"type": "css", "value": "button.submit"},
  "force": false
}
```

selector 也可为 `{"type":"text","value":"Submit"}`，被转换为 Playwright `text="Submit"`。成功输出：

```json
{"success":true,"tool_name":"click","error":null,"data":{"selector":{"type":"css","value":"button.submit"}}}
```

控制器内部失败字典在工具层转成 `ToolResult.error`，不会把 controller 的所有辅助字段透传。

## `done` 完整原代码

```python
@tool(
    "done",
    "Mark task complete with final answer. params: summary (string, REQUIRED - the actual answer to the user's question, not just 'done'), attachments? (list of file paths to include)",
)
class DoneTool:
    def __init__(self, **kw: Any) -> None:
        pass

    def validate_params(self, params: dict) -> None:
        summary = params.get("summary") or params.get("result")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("'summary' is required and must be a non-empty answer")

    async def execute(self, params: dict) -> ToolResult:
        summary = params.get("summary") or params.get("result") or ""
        attachments = params.get("attachments", [])
        return ToolResult(
            success=True,
            tool_name="done",
            data={"summary": summary, "attachments": attachments},
        )
```

`done` 不自己结束任务；它只返回成功结果。`WebAgent.run()` 看到原始 `tool_call.tool_name == "done"` 才设 completed。因此模型若输出 `Done`，Executor 会小写执行成功，但 Agent 的大小写敏感比较不会结束循环，这是一个边界缺陷。

## 67 个内置工具

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

完整名称由本次 import registry 得到，不是根据 README 推测。

## Search 工具

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

每次 run 会清空 output root，所以 containment 也保证这些工具主要操作本次运行的产物。

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

## 新工具契约

新增工具必须：用唯一 `@tool(name, description)`；构造函数接受注入依赖；`validate_params` 对 LLM 不可信输入做类型/范围/路径校验；`async execute` 返回与装饰器同名的 `ToolResult`；在 `builtin/__init__.py` import；增加 mock 外部依赖的单元测试。

## 当前限制

- 工具参数 schema 只是自然语言描述，不是机器可验证的 JSON Schema。
- registry 是模块级全局类表，重复 import 安全但同名装饰器会覆盖旧类。
- registry 是进程内全局声明表；测试或动态插件若注册临时工具，必须在隔离边界清理。
- 工具间结果格式不是统一 Pydantic schema，`data` 是开放字典。
- 大型 PDF mining/QA 文件职责较多，规则抽取与 Tool adapter 耦合，维护成本高。
