# 架构与数据流

## 模块架构

```mermaid
flowchart TB
    U[自然语言任务] --> CLI[webagent.cli]
    CLI --> CFG[AgentConfig]
    CFG --> P[APIPlanner / StubPlanner]
    CFG --> B[BrowserController]
    CFG --> R[ToolRegistry: 67 tools + JSON Schemas]
    R --> X[Discovery-mode exposure set]
    CFG --> EP[Browser-grounded / search-only evidence policy]
    CFG --> RP[ActionRiskPolicy]
    X --> E[ToolExecutor]
    EP --> E
    RP --> E
    P --> A[WebAgent]
    B --> A
    E --> A
    A --> O[Observe: load + DOM stability + snapshot]
    O --> CDP[CDP Accessibility Tree]
    O --> JS[JavaScript fallback extraction]
    A --> P
    P --> TC[ToolCall]
    TC --> E
    E --> BR[Browser / Search / File / PDF tools]
    BR --> TR[ToolResult + policy audit]
    TR --> A
    BR --> CAS[Profile-aware parser cascade]
    CAS --> MK[Marker]
    CAS --> MU[MinerU]
    CAS --> PD[PaddleOCR]
    CAS --> LP[Local PyMuPDF fallback]
```

虚线式“可插拔”关系由 `typing.Protocol` 表达，但 CLI 当前仍显式构造具体的 `BrowserController`、`APIPlanner/StubPlanner` 与 `ToolExecutor`。

## 启动时序

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Config
    participant Planner
    participant Browser
    participant Registry
    participant Policy
    participant Executor
    participant Agent
    User->>CLI: webagent --task ...
    CLI->>Config: AgentConfig() + CLI overrides
    CLI->>Planner: _build_planner(config)
    CLI->>Planner: load() / vision probe
    CLI->>Browser: start()
    CLI->>Registry: import builtin + auto_discover(dependencies)
    CLI->>Registry: allowed_tools_for_discovery_mode(...)
    CLI->>Policy: evidence policy + ActionRiskPolicy
    CLI->>Executor: registry + exposure + policies + timeout
    CLI->>Agent: WebAgent(planner, browser, executor, config)
    CLI->>Agent: add_hook(LoggingHook)
    CLI->>Agent: run(task)
    Agent-->>CLI: AgentResult
    CLI->>Browser: close()
    CLI->>Planner: unload()
```

浏览器启动失败时，CLI 会先 `planner.unload()` 再抛 `RuntimeError`；浏览器启动成功后的任何后续结果都由 `finally` 保证关闭 browser 和 planner。

## 单步 Observe–Think–Act–Record

```mermaid
sequenceDiagram
    participant A as WebAgent
    participant B as Browser/Page
    participant S as Snapshot
    participant L as LoopDetector
    participant P as Planner
    participant E as ToolExecutor
    A->>B: wait_for_load_state(domcontentloaded, 5s)
    A->>B: wait_for_page_stability(有界稳定窗口)
    A->>S: take_snapshot(page, task, use_cdp, max_elements)
    S-->>A: markdown + PNG + meta
    A->>B: check_captcha()
    alt 检出且允许人工接管
        A->>B: 等待人工处理并重新观察
    else headless/严格模式或超时
        A->>A: BLOCKED + checkpoint
    end
    A->>L: is_looping()
    A->>P: plan_action(task, state, history + strategy/evidence hints, exposed tools)
    P-->>A: ToolCall 或 None
    A->>E: validate_tool_call（planner retry 内）
    A->>L: add_action(计划动作, URL, DOM hash)
    A->>A: pending_action 写前 checkpoint
    A->>E: execute(ToolCall)
    E-->>A: ToolResult
    alt 非 done 动作
        A->>A: post_action_wait
        A->>B: 稳定等待 + 再观察
    else done
        A->>A: 复用动作前 BrowserState
    end
    A->>A: 保存动作后截图
    A->>A: AgentStep + history + hooks + strategy update
    A->>A: 清除 pending_action + checkpoint
```

一个容易忽略的事实：loop detector 在 `_think()` 返回后、工具真正执行前记录动作。因此它检测的是“规划重复/页面停滞”，不是严格的“成功执行重复”。

## Browser snapshot 数据流

```mermaid
flowchart LR
    PAGE[Playwright Page] --> LOAD[DOMContentLoaded best effort]
    LOAD --> STABLE[URL / readyState / DOM metrics 稳定窗口]
    STABLE --> GEN[记录 initial URL]
    GEN --> HTML[page.content]
    GEN --> PNG[page.screenshot PNG]
    GEN --> AX{use_cdp 且 AX 可用?}
    AX -->|是| AXT[Accessibility.getFullAXTree]
    AXT --> USE{得到可定位的 actionable 元素?}
    USE -->|是| E[元素字典]
    USE -->|否| JSE[JS interactive extraction]
    AX -->|否/异常| JSE
    JSE --> E
    E --> FD[广告过滤 + 签名去重]
    FD --> PR[任务相关优先级 + Top N]
    HTML --> SAN[移除 script/style/ad containers]
    SAN --> MD[Markdown 正文]
    PR --> MD2[Interactive Controls]
    MD --> OUT[snapshot dict]
    MD2 --> OUT
    PNG --> OUT
    OUT --> CONSIST{结束 URL == initial URL?}
    CONSIST -->|否| RETRY[本次 snapshot 失败，由 observe 重试]
    CONSIST -->|是| BS[BrowserState]
```

`snapshot` 返回完整 HTML，但 `_observe()` 只把 Markdown、截图、URL、标题放入 `BrowserState`；完整 HTML 不进入 planner。

## Planner 与 Tool 数据流

```text
BrowserState
  ├─ screenshot -> 按需压缩/base64 -> image_url（仅视觉可用且当前动作需要）
  ├─ dom_summary -> 最多 6000 字符
  ├─ url/title
History -> 最近 N 步文本摘要
Controller -> planning/strategy/evidence/recovery hints
Registry -> 仅暴露工具的 ToolSpec(name, description, JSON Schema)
                       ↓
 native-tools:required
       ↓ provider 明确不支持 required 时
 native-tools:auto
       ↓ provider 明确不支持 native structured output 时
 json-schema
       ↓ provider 明确不支持 JSON Schema 时
 prompt-json -> JSON extraction + aliases
                       ↓
ToolCall(tool_name, parameters, reasoning)
                       ↓
planner preflight validation（可修复错误不消耗环境步）
                       ↓
exposure gate -> evidence policy -> risk policy -> timeout
                       ↓
registry schema validation -> implementation.execute
                       ↓
ToolResult(success, tool_name, error, data, audit)
```

上述降级只响应“能力不支持”类错误；鉴权失败、429、5xx 和超时不会被误判为格式不兼容而静默降级。

## PDF cascade 数据流

```mermaid
flowchart TB
    F[PDF/image] --> PROF[DocumentProfile]
    PROF --> ROUTE{类型/扫描画像/soft hint}
    ROUTE -->|文本 PDF| TEXT[Marker → MinerU → Paddle]
    ROUTE -->|扫描 PDF| SCAN[MinerU → Marker → Paddle]
    ROUTE -->|单张图片| IMG[Paddle → Marker]
    TEXT --> NEXT[依序运行 provider]
    SCAN --> NEXT
    IMG --> NEXT
    NEXT --> RETRY{retryable error?}
    RETRY -->|是且预算未耗尽| NEXT
    RETRY -->|否/重试结束| Q{Quality Gate}
    Q -->|通过| RES[PDFParseResult]
    Q -->|未通过且仍有 provider| NEXT
    Q -->|全部未通过| LOCAL[Local PyMuPDF text]
    LOCAL --> RES
    LOCAL -->|也失败| ERR[PDFParseResult.error]
```

正常文本 PDF 默认 `Marker → MinerU → Paddle`；扫描 PDF 默认 `MinerU → Marker → Paddle`；单张图片默认 `Paddle → Marker`。配置 `ocr_provider` 只是把已在候选中的 provider 提到第一位。
