# 架构与数据流

## 模块架构

```mermaid
flowchart TB
    U["自然语言任务"] --> CLI["CLI / benchmark adapter"]
    CLI --> CFG["AgentConfig<br/>验证后的配置与 discovery mode"]
    CFG --> A["WebAgent<br/>Observe → Think → Act → Record"]

    P["APIPlanner / StubPlanner"]
    B["BrowserController"]
    R["ToolRegistry<br/>67 tools + JSON Schemas"]
    EP["Evidence policy"]
    RISK["ActionRiskPolicy"]
    R --> X["Discovery-mode exposure set"]
    X -->|同一暴露集合| P
    X -->|同一允许集合| E["ToolExecutor"]
    EP --> E
    RISK --> E

    A --> O["Observe<br/>load + stability + snapshot"]
    B --> O
    O --> BS["BrowserState<br/>viewport image + viewport/document context + refs"]
    BS --> P
    P --> TC["validated ToolCall"]
    TC --> E
    E --> BR["Browser / Search / File / PDF tools"]
    BR --> TR["ToolResult + policy audit"]
    TR --> A
    BR --> B

    BR --> CAS["Profile-aware parser cascade"]
    CAS --> CLOUD["Marker / MinerU / PaddleOCR"]
    CLOUD --> Q{质量通过?}
    Q -->|是| DOC["PDFParseResult"]
    Q -->|全部未通过| LP["Local PyMuPDF fallback"]
    LP --> DOC

    A --> CP["Atomic checkpoint"]
    A --> TRACE["Paired observations + versioned trace"]

    classDef entry fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E;
    classDef core fill:#CCFBF1,stroke:#0F766E,color:#134E4A;
    classDef planner fill:#EEF2FF,stroke:#4F46E5,color:#312E81;
    classDef guard fill:#FFF7ED,stroke:#D97706,color:#7C2D12;
    classDef evidence fill:#ECFDF5,stroke:#15803D,color:#14532D;
    class U,CLI,CFG,B,O,BS,BR entry;
    class A core;
    class P,TC planner;
    class R,X,EP,RISK,E,Q guard;
    class TR,CAS,CLOUD,DOC,LP,CP,TRACE evidence;
```

虚线式“可插拔”关系由 `typing.Protocol` 表达，但 CLI 当前仍显式构造具体的 `BrowserController`、`APIPlanner/StubPlanner` 与 `ToolExecutor`。

## 启动时序

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant CLI
    participant Config
    participant Planner
    participant Browser
    participant Registry
    participant Exposure
    participant Policy
    participant Executor
    participant Agent
    User->>CLI: webagent --task ...
    CLI->>Config: AgentConfig() + CLI overrides
    CLI->>Planner: _build_planner(config)
    CLI->>Planner: load() / vision probe
    CLI->>Browser: start()
    alt browser start 失败
        CLI->>Browser: close() 清理部分启动状态
        CLI->>Planner: unload()
    else browser start 成功
        CLI->>Registry: import builtin + auto_discover(dependencies)
        CLI->>Exposure: allowed_tools_for_discovery_mode(...)
        CLI->>Policy: evidence policy + ActionRiskPolicy
        CLI->>Executor: registry + exposure + policies + timeout
        CLI->>Agent: WebAgent(planner, browser, executor, config)
        CLI->>Agent: add_hook(LoggingHook)
        CLI->>Agent: run(task)
        Agent-->>CLI: AgentResult
        CLI->>Browser: close()（finally）
        CLI->>Planner: unload()（finally）
    end
```

浏览器启动失败时，CLI 会先 `planner.unload()` 再抛 `RuntimeError`；浏览器启动成功后的任何后续结果都由 `finally` 保证关闭 browser 和 planner。

## 单步 Observe–Think–Act–Record

```mermaid
sequenceDiagram
    autonumber
    participant A as WebAgent
    participant B as Browser/Page
    participant S as Snapshot
    participant L as LoopDetector
    participant P as PlanningCoordinator
    participant E as ToolExecutor
    A->>B: wait_for_load_state(domcontentloaded, 5s)
    A->>B: wait_for_page_stability(有界稳定窗口)
    A->>S: take_snapshot(page, task, use_cdp, max_elements)
    S-->>A: viewport PNG + scoped contexts + refs + meta
    A->>B: check_captcha()
    alt 未检出 challenge
        A->>A: 继续
    else headed 且人工在时限内解决
        A->>B: 重新稳定观察
    else headless / fail / 人工等待超时
        A->>A: BLOCKED + checkpoint；停止本步
    end
    Note over A,E: 以下流程只适用于 challenge clear / resolved 分支
    A->>A: 保存 step_NNN/pre.json + pre.png
    A->>P: plan_action(task, state, history + strategy/evidence hints, exposed tools)
    P->>L: is_looping() + recovery hint
    P->>E: validate_tool_call（有界 repair attempts）
    P->>L: add_action(计划动作, URL, DOM hash)
    P-->>A: validated ToolCall 或 None
    alt 无有效 ToolCall
        A->>A: 保存 not_attempted post + failure checkpoint
    else 有效 ToolCall
        A->>A: set pending_action + 写前 checkpoint
        A->>E: execute(ToolCall)
        E-->>A: ToolResult + audit
        alt 非 done 动作
            A->>A: post_action_wait
            A->>B: 稳定等待 + 再观察
        else done
            A->>A: 复用动作前 BrowserState，标记 reused
        end
        A->>A: 保存 step_NNN/post.json + post.png
        A->>A: failure tracking + AgentStep + hooks
        A->>A: strategy update + clear pending_action + commit checkpoint
    end
```

一个容易忽略的事实：loop detector 在 `_think()` 返回后、工具真正执行前记录动作。因此它检测的是“规划重复/页面停滞”，不是严格的“成功执行重复”。

## Browser snapshot 数据流

```mermaid
flowchart TB
    PAGE["Playwright Page"] --> LOAD["DOMContentLoaded best effort"]
    LOAD --> STABLE["URL / readyState / DOM metrics 稳定窗口"]
    STABLE --> CAPTURE["记录 initial URL、geometry、HTML、observation id<br/>采集 rendered projection 与 viewport PNG<br/>按配置额外采集 full-page audit PNG"]
    CAPTURE --> READY{rendered projection 可用?}
    READY -->|是| CURRENT{bound nodes 与 rendered revision 仍一致?}
    CURRENT -->|否| RETRY["拒绝混合快照<br/>由 observe 有界重试"]
    CURRENT -->|是| E["Rendered controls + geometry"]
    READY -->|否| FALLBACK["元素 fallback<br/>use_cdp 时先 CDP/AX；无可定位结果再用 JS<br/>禁用 CDP 时直接使用 JS"]
    FALLBACK --> E
    E --> CONSIST{URL 与 geometry 仍一致?}
    CONSIST -->|否| RETRY
    CONSIST -->|是| PR["广告过滤 + 签名去重<br/>任务相关优先级 + Top N"]
    CAPTURE --> MD["HTML 去 script/style/ad containers<br/>生成正文 Markdown"]
    PR --> PACK["分别压缩 viewport/document context"]
    MD --> PACK
    PACK --> OUT["snapshot dict<br/>scope / omission / consistency metadata"]
    OUT --> BS["BrowserState<br/>viewport screenshot + optional full-page audit<br/>不包含原始 HTML"]

    classDef capture fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E;
    classDef decision fill:#FFF7ED,stroke:#D97706,color:#7C2D12;
    classDef evidence fill:#ECFDF5,stroke:#15803D,color:#14532D;
    classDef failure fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D;
    class PAGE,LOAD,STABLE,CAPTURE,FALLBACK,E,MD capture;
    class READY,CURRENT,CONSIST decision;
    class PR,PACK,OUT,BS evidence;
    class RETRY failure;
```

`snapshot` 返回完整 HTML，但 `_observe()` 不把原始 HTML 放入 `BrowserState`。Planner 收到的是
viewport screenshot、与截图同范围的 `viewport_context`、明确标注为非视觉证据的
`document_context`，以及 observation id、元素、URL、标题和覆盖/省略元数据。

## Planner 与 Tool 数据流

```text
BrowserState
  ├─ screenshot -> 按需压缩/base64 -> image_url（仅视觉可用且当前动作需要）
  ├─ viewport_context -> 默认 5000 字符（与 viewport screenshot 同范围）
  ├─ document_context -> 默认 2500 字符（屏幕外补充，非视觉证据）
  ├─ observation_id + [observation_id/fN:eN] controls
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
    F["PDF / image"] --> PROF["DocumentProfile"]
    PROF --> ROUTE{类型 / 扫描画像 / soft hint}
    ROUTE -->|文本 PDF| TEXT["Marker → MinerU → Paddle"]
    ROUTE -->|扫描 PDF| SCAN["MinerU → Marker → Paddle"]
    ROUTE -->|单张图片| IMG["Paddle → Marker"]
    TEXT --> NEXT["调用当前 cloud provider"]
    SCAN --> NEXT
    IMG --> NEXT
    NEXT --> OUTCOME{调用结果}
    OUTCOME -->|成功返回| Q{Quality Gate 通过?}
    OUTCOME -->|retryable 且重试/总预算充足| BACKOFF["指数退避"]
    BACKOFF --> NEXT
    OUTCOME -->|不可重试 / 重试或总预算耗尽| MORE{还有 provider?}
    Q -->|是| RES["PDFParseResult"]
    Q -->|否| MORE
    MORE -->|是| NEXT
    MORE -->|否| LOCAL["Local PyMuPDF text fallback"]
    LOCAL -->|成功| RES
    LOCAL -->|失败| ERR["PDFParseResult.error"]

    classDef input fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E;
    classDef decision fill:#FFF7ED,stroke:#D97706,color:#7C2D12;
    classDef cloud fill:#F5F3FF,stroke:#7C3AED,color:#4C1D95;
    classDef success fill:#ECFDF5,stroke:#15803D,color:#14532D;
    classDef failure fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D;
    class F,PROF input;
    class ROUTE,OUTCOME,Q,MORE decision;
    class TEXT,SCAN,IMG,NEXT,BACKOFF cloud;
    class LOCAL,RES success;
    class ERR failure;
```

正常文本 PDF 默认 `Marker → MinerU → Paddle`；扫描 PDF 默认 `MinerU → Marker → Paddle`；单张图片默认 `Paddle → Marker`。配置 `ocr_provider` 只是把已在候选中的 provider 提到第一位。
