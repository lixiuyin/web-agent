# 架构与数据流

## 模块架构

```mermaid
flowchart TB
    U[自然语言任务] --> CLI[webagent.cli]
    CLI --> CFG[AgentConfig]
    CLI --> P[APIPlanner 或 StubPlanner]
    CLI --> B[BrowserController]
    CLI --> R[ToolRegistry + 60+ tools and schemas]
    P --> A[WebAgent]
    B --> A
    R --> E[ToolExecutor]
    E --> A
    A --> S[take_snapshot]
    S --> CDP[CDP Accessibility Tree]
    S --> JS[JavaScript element extraction]
    A --> P
    P --> TC[ToolCall]
    TC --> E
    E --> BR[Browser/Search/File/PDF tools]
    BR --> TR[ToolResult]
    TR --> A
    BR --> CAS[Parser cascade]
    CAS --> MK[Marker]
    CAS --> MU[MinerU]
    CAS --> PD[PaddleOCR]
    CAS --> LP[Local PyMuPDF]
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
    participant Agent
    User->>CLI: webagent --task ...
    CLI->>Config: AgentConfig() + CLI overrides
    CLI->>Planner: _build_planner(config)
    CLI->>Planner: load() / vision probe
    CLI->>Browser: start()
    CLI->>Registry: import builtin + auto_discover(dependencies)
    CLI->>Agent: WebAgent(...)
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
    A->>S: take_snapshot(page, task, use_cdp, max_elements)
    S-->>A: markdown + PNG + meta
    A->>B: check_captcha()
    A->>L: is_looping()
    A->>P: plan_action(task, BrowserState, history, tools)
    P-->>A: ToolCall 或 None
    A->>L: add_action(计划动作, URL, DOM hash)
    A->>E: execute(ToolCall)
    E-->>A: ToolResult
    A->>S: 再观察一次并保存 step screenshot
    A->>A: AgentStep + history + hooks
```

一个容易忽略的事实：loop detector 在 `_think()` 返回后、工具真正执行前记录动作。因此它检测的是“规划重复/页面停滞”，不是严格的“成功执行重复”。

## Browser snapshot 数据流

```mermaid
flowchart LR
    PAGE[Playwright Page] --> HTML[page.content]
    PAGE --> PNG[page.screenshot PNG]
    PAGE --> AX{use_cdp?}
    AX -->|是且 AX 可用| AXT[Accessibility.getFullAXTree]
    AX -->|否/失败| JSE[JS interactive extraction]
    AXT --> E[元素字典]
    JSE --> E
    E --> FD[广告过滤 + 签名去重]
    FD --> PR[任务相关优先级 + Top N]
    HTML --> SAN[移除 script/style/ad containers]
    SAN --> MD[Markdown 正文]
    PR --> MD2[Interactive Controls]
    MD --> OUT[snapshot dict]
    MD2 --> OUT
    PNG --> OUT
    OUT --> BS[BrowserState]
```

`snapshot` 返回完整 HTML，但 `_observe()` 只把 Markdown、截图、URL、标题放入 `BrowserState`；完整 HTML 不进入 planner。

## Planner 与 Tool 数据流

```text
BrowserState
  ├─ screenshot -> JPEG quality=70 -> base64 -> image_url（仅视觉可用）
  ├─ dom_summary -> 最多 6000 字符
  ├─ url/title
History -> 最近 N 步文本摘要
Registry -> 每行 name: description
                       ↓
                  model request
                       ↓ raw text
             JSON extraction + aliases
                       ↓
ToolCall(tool_name, parameters, reasoning)
                       ↓
lower-case name -> registry validation -> implementation.execute
                       ↓
ToolResult(success, tool_name, error, data)
```

## PDF cascade 数据流

```mermaid
flowchart LR
    F[PDF/image] --> PROF[DocumentProfile]
    PROF --> ROUTE{类型/扫描画像/soft hint}
    ROUTE --> P1[Provider 1]
    P1 --> Q1{Quality Gate}
    Q1 -->|通过| RES[PDFParseResult]
    Q1 -->|失败| P2[Provider 2]
    P2 --> Q2{Quality Gate}
    Q2 -->|通过| RES
    Q2 -->|失败| P3[Provider 3]
    P3 --> Q3{Quality Gate}
    Q3 -->|通过| RES
    Q3 -->|失败| LOCAL[Local PyMuPDF text]
    LOCAL --> RES
    LOCAL -->|也失败| ERR[PDFParseResult.error]
```

正常文本 PDF 默认 `Marker → MinerU → Paddle`；扫描 PDF 默认 `MinerU → Marker → Paddle`；单张图片默认 `Paddle → Marker`。配置 `ocr_provider` 只是把已在候选中的 provider 提到第一位。
