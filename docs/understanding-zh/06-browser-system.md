# Browser 子系统

## 职责分解

| 文件                        | 当前职责                                | 是否在主路径         |
| ------------------------- | ----------------------------------- | -------------- |
| `controller.py`           | Chromium 生命周期与具体动作                  | 是              |
| `snapshot.py`             | HTML/PNG/交互元素到 LLM Markdown         | 是              |
| `cdp_service.py`          | CDP session 与 AX/DOM/CSS/Runtime 包装 | AX tree 路径使用   |
| `interactive_detector.py` | 页面内 JavaScript 交互元素提取               | JS fallback 使用 |
| `priority.py`             | Top-N 元素优先级                         | 是              |
| `stealth.py`              | Chromium 参数和 init script            | 仅显式 opt-in     |
| `captcha_detector.py`     | DOM/URL/title 模式检测                  | 默认每步调用         |
| `__init__.py`             | 仅公开 `BrowserController`             | 是              |

## 浏览器启动完整原代码

来源：`src/webagent/browser/controller.py::BrowserController.start`。调用者是 CLI；输出通过对象内部状态 `_playwright/_context/_page/_cdp` 表达，无返回值。

```python
    async def start(self) -> None:
        if self._playwright is not None:
            raise RuntimeError("Browser already started; call close() before starting again")

        if self.temporary_profile:
            self._owned_profile_dir = self._create_temporary_profile()
            self.user_data_dir = str(self._owned_profile_dir)
        else:
            _mark_profile_clean(self.user_data_dir)

        self._playwright = await async_playwright().start()

        # Native Playwright is the default; stealth is explicit opt-in.
        if self.stealth_mode:
            user_agent = get_stealth_user_agent()
            args = get_stealth_args(headless=self.headless)
            stealth_script = ENHANCED_STEALTH_SCRIPT
        else:
            user_agent = None
            args = []
            stealth_script = ""

        # Launch native Chromium unless stealth was explicitly selected.
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=args,
            user_agent=user_agent,
            locale=self.locale,
            timezone_id=self.timezone_id,
            permissions=[],  # 默认不向任意网站预授权定位或通知
            color_scheme="light",
            device_scale_factor=1.0,
            ignore_https_errors=self.ignore_https_errors,
            accept_downloads=True,
            proxy={"server": self.proxy_server} if self.proxy_server else None,
        )

        # Get CDP session for enhanced snapshot extraction.
        try:
            self._cdp = await self._context.new_cdp_session(self._context.pages[0])
            await self._cdp.send("Page.enable")
            await self._cdp.send("Runtime.enable")
        except Exception:
            self._cdp = None

        if stealth_script:
            await self._context.add_init_script(stealth_script)

        self._browser = self._context.browser
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        await self._page.set_viewport_size(
            {"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page.set_default_timeout(self.default_timeout)

        if self.humanize_delays:
            await asyncio.sleep(random.uniform(0.5, 1.5))
```

输入来自构造函数：`headless: bool`、viewport 像素、默认 timeout 毫秒、`slow_mo` 毫秒、profile 路径、browser channel、显式 proxy、TLS、locale/timezone 与 stealth 开关。`browser_channel=None` 使用 Playwright bundled Chromium；`chrome` 使用本机稳定版 Chrome，但仍必须配独立自动化 profile，不能指向日常 Chrome 用户目录。浏览器默认直连且不会隐式继承 shell 的 `HTTP_PROXY`；只有配置 `browser_proxy_server` 才改变浏览器网络区域。`slow_mo=0` 就是零固定延迟；随机等待由默认关闭的 `humanize_delays` 显式控制。默认 locale/timezone 为 `None`，保留浏览器/系统原生环境；默认临时 profile，只有显式 persistent 才跨运行复用。异常会直接抛给 CLI。

### 关闭完整原代码

```python
    async def close(self) -> None:
        # Detach CDP session first (non-fatal)
        if self._cdp is not None:
            try:
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

        self._page = None  # owned by context; don't close separately

        # Closing the persistent context also closes all pages and the browser
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        self._browser = None  # already gone after context.close()

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started; call start() first")
        return self._page
```

`page` property 在未启动或已关闭时抛 `RuntimeError`。绝大多数动作方法捕获 Playwright 异常并返回 `{success, error, ...}`，因此控制器同时承担了异常到结果字典的适配。

## Snapshot 完整原代码

来源：`src/webagent/browser/snapshot.py::take_snapshot`。调用者是 Agent `_observe()` 和 `dom_summary` 工具。

```python
async def take_snapshot(
    page: Page,
    full_page: bool = False,
    wait_after_load: int = 200,
    task: str = "",
    max_elements: int = 50,
    use_cdp: bool = True,
    filter_ads: bool = True,
) -> dict[str, Any]:
    """Capture enhanced DOM + screenshot from an existing Playwright page.

    This enhanced snapshot:
    1. Uses CDP to get semantic understanding via AX Tree
    2. Detects interactive elements with intelligent filtering
    3. Prioritizes elements based on position, type, and task relevance
    4. Generates optimized markdown showing only top N elements

    Args:
        page: Playwright page object
        full_page: Capture full page screenshot
        wait_after_load: Milliseconds to wait after page load
        task: User's task for relevance matching
        max_elements: Maximum number of elements to include in output
        use_cdp: Whether to use CDP for enhanced detection
        filter_ads: Whether to remove ad-like elements and containers

    Returns:
        Snapshot dict with markdown, elements, screenshot, and metadata
    """
    if wait_after_load > 0:
        try:
            await page.wait_for_timeout(wait_after_load)
        except Exception:
            pass

    # Capture basic page info
    html = await page.content()
    screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
    title = await page.title()
    url = page.url

    # Extract interactive elements
    if use_cdp:
        elements = await _extract_elements_enhanced(page)
    else:
        elements = await _extract_elements_basic(page)

    # Filter and prioritize
    elements = _filter_and_dedupe(elements, filter_ads=filter_ads)
    elements = sort_elements_by_priority(elements, task=task, max_elements=max_elements)

    # Generate optimized markdown
    sanitized = _sanitize_html(html, filter_ads=filter_ads)
    markdown = _generate_llm_markdown(sanitized, elements, max_elements)

    # Get viewport info for priority calculation
    viewport = page.viewport_size or {"width": 1280, "height": 720}

    return {
        "meta": {
            "url": url,
            "title": title,
            "timestamp": datetime.now(UTC).isoformat(),
            "viewport": viewport,
            "element_count": len(elements),
        },
        "markdown": markdown,
        "elements": elements,
        "screenshot_bytes": screenshot_bytes,
        "html": html,
        "title": title,
        "url": url,
    }
```

### 输入格式

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | Playwright `Page` | 必填 | 已打开页面 |
| `full_page` | bool | false | 截整页还是 viewport |
| `wait_after_load` | int(ms) | 200 | 快照前额外等待 |
| `task` | str | 空 | 用于元素相关性打分 |
| `max_elements` | int | 50 | 最多保留多少元素 |
| `use_cdp` | bool | true | 先尝试 AX Tree |

### 输出格式

```json
{
  "meta": {
    "url": "https://example.com",
    "title": "Example",
    "timestamp": "ISO-8601 UTC",
    "viewport": {"width": 1280, "height": 720},
    "element_count": 12
  },
  "markdown": "# Example\n...\n## Interactive Controls...",
  "elements": [{"tag": "button", "text": "Submit", "attrs": {}, "bbox": {}, "_priority": 73}],
  "screenshot_bytes": "PNG bytes（JSON 示例中不可直接序列化）",
  "html": "<html>...</html>",
  "title": "Example",
  "url": "https://example.com"
}
```

## 元素检测与排序

JS extractor 遍历 `document.querySelectorAll('*')`，保留原生交互 tag、onclick/contenteditable、交互 ARIA role 或按钮式 class，并提取文本、重要属性、CSS path 与页面坐标。`extract_interactive_elements()` 是唯一的非 CDP fallback，旧的重复 Python 检测器已删除。

优先级是 0–100 的启发式分数：位置最多约 35、类型最高 35、合理尺寸 5、可见 10、文本质量 8、任务词匹配最多 25，再按 id/class 添加正负修正。排序会复制字典，不修改调用者原对象。

## CDP 与 AX Tree

CDP（Chrome DevTools Protocol）是比 Playwright 高一层 API 更接近 Chromium 内部的调试协议。AX Tree（Accessibility Tree）按 button/link/textbox 等语义节点表达页面，理论上比纯 tag 更适合 LLM。

当前 `_extract_from_ax_tree()` 存在 grounding 缺口：它把 `backendDOMNodeId` 当成页面 `data-node-id` 属性查询，而网页通常没有这个属性；因此 bbox 常为零。AX 元素也不生成 `css_path`，Markdown selector 可能为 `unknown`。这是静态代码判断，尚需真实页面 CDP 测试量化。

`CDPService` 已收缩为 snapshot 实际需要的生命周期和 `get_ax_tree()`，启动时只启用 Accessibility domain。

## Markdown 与 selector

Markdown 中显示 `[e1]` 是本次排序后的展示序号，不是可传给工具的 selector。Planner 真正可用的是每行的 CSS path 或可见文本；工具只接受 `{type: "css"|"text", value: string}`。未被任何消费者使用的 `_stable_index` 已删除。

## Stealth

Stealth 是显式兼容选项，包含启动 flags、随机 UA 和 init script，修改 `navigator.webdriver`、plugins、platform、WebGL、canvas、screen 等指纹。默认与 strict-eval 均关闭。它是反检测启发式，不是绕过风控保证；随机且彼此可能不一致的 fingerprint 也可能成为检测信号。

`goto()` 的随机等待由显式开启的 `browser_humanize_delays` 控制；固定的 Playwright
操作间隔由 `browser_slow_mo_ms` 控制，`0` 就是真正的零延迟，不再偷偷替换成 50–150 ms。
普通运行默认也关闭人类化导航行为；只有显式兼容配置才开启随机等待。旧的重复
`add_human_like_behavior()` 已删除；`cfg.stealth_mode` 也已接入控制器。

持久化 `browser_profile` 在 Chromium 启动时会先写入 crash 标记，只有正常退出才清除。Controller 现在在启动前与 Playwright 停止后修复 `Preferences.profile.exit_type` 和 `Local State...exited_cleanly`；CLI 对“浏览器只启动了一半”的异常也调用 `close()`，从而避免下一次 headed 启动显示“上次未正常退出”。清理超时/异常会记录 warning，不再静默吞掉。代码不再主动删除 `Singleton*` 锁，避免误删仍在使用中的 profile 锁。

临时 profile 另写 `.webagent-owner.json`（PID、创建时间、类型）。启动新临时会话前只清理
超过 `browser_stale_profile_max_age_seconds`、marker 可解析且 PID 已不存在的同前缀目录；
活跃 PID、无 marker 或新目录一律保留，以处理异常终止残留而不误删并发会话。

## Captcha

检测器遍历 reCAPTCHA、hCaptcha、Cloudflare、Arkose 等 selectors：DOM 命中返回 confidence 0.9，只有 URL/title 关键词为 0.5。它不求解 captcha。`captcha_pause=True` 启用每轮检测；普通 `captcha_handling=report` 会在 headed 模式记录并按 poll interval 等待人工清除，超时/headless fail closed 并关闭浏览器；strict 立即阻断。等待结果、关闭结果和挑战 URL 会进入 runtime events；未解决事件会使严格运行 certificate 无效。

## 控制器操作面

`goto/click/type_text/press_key/wait/screenshot/scroll/get_element_text/wait_for_selector/hover/select_option/get_attribute/get_all_links/open_local_file/refresh/scroll_to_element/get_search_results/check_captcha` 都是公开异步方法。`click_link_by_text` 依次尝试精确文本、模糊文本、关键词、arXiv ID 和 PDF URL；`get_all_links` 先去重，再把 PDF、technical report、paper、arXiv、raw/download 链接排到全站导航之前，最后应用 `max_results`，并同时返回总数与实际返回数。搜索结果解析对 Google/Bing/DuckDuckGo 使用硬编码 CSS，网站改版时脆弱。只有页面存在结果容器仍不够：至少抽取出一条带 URL 的结构化结果，`search` 才会返回成功；否则继续 engine cascade 或诚实失败。

## 安全与边界

- 工具层 `goto` 拒绝 file/data/javascript/blob/about/view-source/chrome scheme，但控制器 `goto()` 本身不拒绝；直接 Python 调用要自行约束。
- `open_local_file()` 能打开任意存在路径，当前没有注册成 LLM 工具，只被受约束的 image 工具间接使用。
- 默认保留 Chromium sandbox、同源策略和 HTTPS 证书校验；`browser_ignore_https_errors` 只用于显式信任的本地自签名测试环境。
- snapshot 的 HTML/截图失败会使整个 snapshot 抛异常，由 `_observe` 最多重试三次；元素提取失败则可退为空列表。
- priority 使用默认 1280×720 排序，实际 viewport 在排序后才读取，非默认 viewport 的位置分数可能不准确。
