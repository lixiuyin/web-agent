# Browser 子系统

## 职责分解

| 文件                        | 当前职责                                | 是否在主路径         |
| ------------------------- | ----------------------------------- | -------------- |
| `controller.py`           | Chromium 生命周期与具体动作                  | 是              |
| `profiles.py`             | 临时 profile 创建、过期清理与 clean-exit 元数据 | 启动和关闭时使用 |
| `checkpoint.py`           | tabs/storage 序列化、验证与恢复              | checkpoint 路径使用 |
| `snapshot.py`             | HTML/PNG/交互元素到 LLM Markdown         | 是              |
| `rendered.py` / `rendered_script.py` | viewport/document 投影、frame 映射和节点绑定 | snapshot 主路径 |
| `references.py`           | observation-bound ref 复验与动作执行       | ref 动作主路径    |
| `cdp_service.py`          | 最小 CDP session 与 Accessibility tree 包装 | AX fallback 使用   |
| `interactive_detector.py` | 页面内 JavaScript 交互元素提取               | JS fallback 使用 |
| `priority.py`             | Top-N 元素优先级                         | 是              |
| `stealth.py`              | Chromium 参数和 init script            | 仅显式 opt-in     |
| `captcha_detector.py`     | DOM/URL/title 模式检测                  | 默认每步调用         |
| `__init__.py`             | 仅公开 `BrowserController`             | 是              |

## 浏览器启动调用链与边界

来源：`src/webagent/browser/controller.py::BrowserController.start`。调用者是 CLI；输出通过对象内部状态 `_playwright/_context/_page/_cdp` 表达，无返回值。

实现：[controller.py](../../src/webagent/browser/controller.py) 的 `BrowserController.start()`，
以及 [profiles.py](../../src/webagent/browser/profiles.py) 的 profile 管理函数。

调用顺序：准备独立 profile → 启动 Playwright → 传入 channel/proxy/viewport 等选项启动
persistent context → 尝试建立 CDP session → 注入显式启用的 stealth script → 选择或创建页面。
CDP 初始化失败会退回无 CDP 状态；重复启动被拒绝，其他启动异常交给 CLI 的 finally 清理。

输入来自构造函数：`headless: bool`、viewport 像素、默认 timeout 毫秒、`slow_mo` 毫秒、profile 路径、browser channel、显式 proxy、TLS、locale/timezone 与 stealth 开关。`browser_channel=None` 使用 Playwright bundled Chromium；`chrome` 使用本机稳定版 Chrome，但仍必须配独立自动化 profile，不能指向日常 Chrome 用户目录。浏览器默认直连且不会隐式继承 shell 的 `HTTP_PROXY`；只有配置 `browser_proxy_server` 才改变浏览器网络区域。`slow_mo=0` 就是零固定延迟；随机等待由默认关闭的 `humanize_delays` 显式控制。默认 locale/timezone 为 `None`，保留浏览器/系统原生环境；默认临时 profile，只有显式 persistent 才跨运行复用。异常会直接抛给 CLI。

### 关闭调用链与边界

实现：[controller.py](../../src/webagent/browser/controller.py) 的 `BrowserController.close()`。

关闭时先尽力 detach CDP，再清空活动页面引用；context 关闭最多等待 15 秒，Playwright stop
最多等待 10 秒。关闭异常记录 warning，随后继续清理其他资源。最后删除本实例持有的临时
profile；持久 profile 则调用 `mark_profile_clean()` 尝试修复退出标记。修复标记不等同于
已证明所有 Chromium 子进程退出，代码不会主动删除 `Singleton*` 锁。

`page` property 在未启动或已关闭时抛 `RuntimeError`。绝大多数动作方法捕获 Playwright 异常并返回 `{success, error, ...}`，因此控制器同时承担了异常到结果字典的适配。

## Snapshot 调用链与边界

来源：`src/webagent/browser/snapshot.py::take_snapshot`。调用者是 Agent `_observe()` 和 `dom_summary` 工具。

实现：[snapshot.py](../../src/webagent/browser/snapshot.py) 的 `take_snapshot()`。

调用顺序：可选等待 → 记录起始 URL 与 geometry → 捕获 HTML → 生成跨 frame 的 rendered
projection 与 observation-bound 节点表 → 截取 viewport PNG → 过滤、排序并分别打包
viewport/document context → 校验 rendered fingerprint、URL 与 geometry。若采集期间发生导航、
滚动或相关渲染变化，快照抛异常并由 Agent 重试，避免把不同页面状态的 DOM 与截图拼成一次观察。
只有 rendered projection 不可用时才进入 CDP/AX→JS fallback。

### 输入格式

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | Playwright `Page` | 必填 | 已打开页面 |
| `full_page` | bool | false | 截整页还是 viewport |
| `wait_after_load` | int(ms) | 200 | 快照前额外等待 |
| `task` | str | 空 | 用于元素相关性打分 |
| `max_elements` | int | 50 | 最多保留多少元素 |
| `use_cdp` | bool | true | rendered projection 不可用时允许 CDP/AX fallback |
| `supplemental_full_page` | bool | false | 另存整页审计 PNG；planner 仍使用 viewport PNG |
| `viewport_chars` / `document_chars` | int | 5000 / 2500 | 两个 context 的独立完整块预算 |

### 输出格式

```json
{
  "meta": {
    "url": "https://example.com",
    "title": "Example",
    "timestamp": "ISO-8601 UTC",
    "viewport": {"width": 1280, "height": 720},
    "element_count": 12,
    "screenshot_scope": "viewport",
    "dom_scope": "viewport_and_document_separate",
    "observation_id": "opaque-id",
    "pair_consistent": true,
    "rendered_projection": true
  },
  "viewport_context": "Frame 0 text: Example\n\n[opaque-id/f0:e0] button label='Submit' ...",
  "document_context": "Frame 0 text: Off-screen supplement",
  "elements": [{"tag": "button", "ref": "f0:e0", "observation_id": "opaque-id", "viewport_bbox": {}}],
  "screenshot_bytes": "PNG bytes（JSON 示例中不可直接序列化）",
  "html": "<html>...</html>",
  "title": "Example",
  "url": "https://example.com"
}
```

## 元素检测与排序

主路径的 rendered projection 遍历 DOM、open shadow root 与可映射 frame，用 DOM Range 和
ancestor clipping 区分截图范围内文字与屏幕外补充，并为 native/ARIA/focusable/event-handler/
pointer-hint 控件保存原节点引用、语义属性、可见 bbox 与 hit-test 结果。旧 JS extractor 仍是
CDP/AX 不可执行时的 fallback，不是普通快照的默认元素来源。

优先级是 0–100 的启发式分数：位置最多约 35、类型最高 35、合理尺寸 5、可见 10、文本质量 8、任务词匹配最多 25，再按 id/class 添加正负修正。排序会复制字典，不修改调用者原对象。

## CDP 与 AX Tree

CDP（Chrome DevTools Protocol）是比 Playwright 高一层 API 更接近 Chromium 内部的调试协议。AX Tree（Accessibility Tree）按 button/link/textbox 等语义节点表达页面，理论上比纯 tag 更适合 LLM。

`_extract_from_ax_tree()` 的 AX 投影仍有 backend node 到 CSS path/坐标的 grounding 限制，
但它只在 rendered projection 整体不可用时参与 fallback。`_extract_elements_enhanced()` 会检查
AX 输出是否包含可执行 `css_path`；否则改用 JS extractor。当前主路径的可执行 grounding 来自
rendered projection 保存的原始 DOM node，而不是依赖 AX backend id 猜 locator。

`CDPService` 已收缩为 snapshot 实际需要的生命周期和 `get_ax_tree()`，启动时只启用 Accessibility domain。

## Markdown 与 selector

控件记录显示为 `[observation_id/fN:eN]`，这个完整值可直接作为
`{"type":"ref","value":"observation_id/fN:eN"}` 传给 click/type/hover 等支持 ref 的工具。
Executor 会在动作计步前拒绝旧 observation，执行时再核验同一 DOM node、祖先链、相关属性、
geometry、clipping 和 hit test；不会按 CSS 静默寻找“长得相同”的新节点。屏幕外控件先用
`scroll_to_element`，然后必须从新观察复制 ref。传统 CSS/text selector 仍兼容，但不具有上述绑定。

## Stealth

Stealth 是显式兼容选项，包含启动 flags、随机 UA 和 init script，修改 `navigator.webdriver`、plugins、platform、WebGL、canvas、screen 等指纹。默认与 strict-eval 均关闭。它是反检测启发式，不是绕过风控保证；随机且彼此可能不一致的 fingerprint 也可能成为检测信号。

`goto()` 的随机等待由显式开启的 `browser_humanize_delays` 控制；固定的 Playwright
操作间隔由 `browser_slow_mo_ms` 控制，`0` 就是真正的零延迟，不再偷偷替换成 50–150 ms。
普通运行默认也关闭人类化导航行为；只有显式兼容配置才开启随机等待。旧的重复
`add_human_like_behavior()` 已删除；`cfg.stealth_mode` 也已接入控制器。

持久化 `browser_profile` 在 Chromium 启动时会先写入 crash 标记，只有正常退出才清除。Controller 现在在启动前与 Playwright 停止后修复 `Preferences.profile.exit_type` 和 `Local State...exited_cleanly`；CLI 对“浏览器只启动了一半”的异常也调用 `close()`，以降低下一次 headed 启动显示“上次未正常退出”的概率。清理超时/异常会记录 warning，不再静默吞掉。代码不再主动删除 `Singleton*` 锁，避免误删仍在使用中的 profile 锁。

临时 profile 另写 `.webagent-owner.json`（PID、创建时间、类型）。启动新临时会话前只清理
超过 `browser_stale_profile_max_age_seconds`、marker 可解析且 PID 已不存在的同前缀目录；
活跃 PID、无 marker 或新目录一律保留，以处理异常终止残留而不误删并发会话。

## CAPTCHA

检测器遍历 reCAPTCHA、hCaptcha、Cloudflare、Arkose 等 selectors：DOM 命中返回 confidence 0.9，只有 path/title 关键词为 0.5。它不求解 captcha。`captcha_pause=True` 启用每轮检测；普通 `captcha_handling=report` 会在 headed 模式记录并按 poll interval 等待人工清除，超时/headless fail closed 并关闭浏览器；strict 立即阻断。等待结果、关闭结果和挑战 URL 会进入 runtime events；未解决事件会使严格运行 certificate 无效。

## 控制器操作面

`goto/click/type_text/press_key/wait/screenshot/scroll/get_element_text/wait_for_selector/hover/select_option/get_attribute/get_all_links/open_local_file/refresh/scroll_to_element/get_search_results/check_captcha` 都是公开异步方法。`click_link_by_text` 依次尝试精确文本、模糊文本、关键词、arXiv ID 和 PDF URL；`get_all_links` 先去重，再把 PDF、technical report、paper、arXiv、raw/download 链接排到全站导航之前，最后应用 `max_results`，并同时返回总数与实际返回数。Google、Bing、DuckDuckGo、Yahoo Japan 与 Seznam 有 controller 专用结果解析器；Yahoo 的结果由 Search 工具通用 SERP 抽取路径处理，真实 DOM 改版时仍会脆弱。只有页面存在结果容器仍不够：至少抽取出一条带 URL 的结构化结果，`search` 才会返回成功；否则继续 engine cascade 或诚实失败。

## 安全与边界

- 工具层 `goto` 拒绝 file/data/javascript/blob/about/view-source/chrome scheme，但控制器 `goto()` 本身不拒绝；直接 Python 调用要自行约束。
- `open_local_file()` 能打开任意存在路径，当前没有注册成 LLM 工具，只被受约束的 image 工具间接使用。
- 默认保留 Chromium sandbox、同源策略和 HTTPS 证书校验；`browser_ignore_https_errors` 只用于显式信任的本地自签名测试环境。
- snapshot 的 HTML/截图或一致性校验失败会使整个 snapshot 抛异常，由 `_observe` 最多重试三次；
  rendered projection 不可用时可退到 CDP/AX、JS，最终元素仍可能为空。
- priority 使用采集到的实际 viewport 尺寸；默认参数只服务脱离 snapshot 的直接函数调用。

## 导航失败与恢复边界

[导航证据模块](../../src/webagent/browser/navigation.py) 只在本次请求的主 frame 重定向链收到
成功 HTTP 响应、当前 URL 已离开原页面、且正文可读取时恢复 `ERR_ABORTED`。同域旧页面
本身不构成成功证据。普通站点不移除 `www.` 后猜测归属；已支持搜索服务的区域域名通过
[显式域名表](../../src/webagent/browser/url_identity.py) 识别。未列出的服务不会自动推断归属。
搜索引擎判断只读取 HTTP(S) hostname；验证码 URL 规则读取服务身份和 path，查询参数与
fragment 不作为验证码证据。DOM 与标题检测仍独立生效。

strict 最新报告任务的 scope 不只依赖搜索摘要：官方 owner 的已渲染 repository index 若明确
列出候选仓库，也可形成 planner-visible owner/scope 证据。完整 observation 注册的是 planner
实际看到的当前 URL；不会用动作请求 URL 覆盖重定向后的页面。发现更高 dotted version 后，
恢复提示优先精确 frontier repository 及其报告文件，较旧候选不会因先出现而抢占下载。

## 浏览器存储恢复

[checkpoint.py](../../src/webagent/browser/checkpoint.py) 先校验完整输入，再应用 cookie 与
localStorage。localStorage 通过临时页面的一次性写入恢复：拦截为空白文档并绕过 service
worker，避免执行目标站点应用代码；完成或失败均关闭临时页面。恢复不会注册永久初始化
脚本，因此后续刷新、导航或再次恢复不会重放先前的旧值。浏览器 API 应用失败仍不具有
事务回滚保证。这里只讨论显式包含 storage_state 的浏览器 checkpoint；Agent 的普通
checkpoint 不保存 cookie/localStorage。
