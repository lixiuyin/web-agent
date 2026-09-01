# 调用图附录

## 关键源码定位（本次 checkout）

| 主题 | 路径与起始行 |
|---|---|
| CLI planner 选择 | `src/webagent/cli.py:168` |
| CLI 完整生命周期 | `src/webagent/cli.py:349` |
| Agent 构造 | `src/webagent/agent/loop.py:132` |
| Agent 单步执行 | `src/webagent/agent/loop.py:600` |
| Observe / Think / Act | `src/webagent/agent/loop.py:897`, `:947`, `:1149` |
| Loop detector | `src/webagent/agent/loop_detector.py:63` |
| Browser controller | `src/webagent/browser/controller.py:184` |
| 页面稳定等待 / Snapshot | `src/webagent/browser/snapshot.py:52`, `:99` |
| AX 提取 | `src/webagent/browser/snapshot.py:180` |
| JS 元素提取 | `src/webagent/browser/interactive_detector.py:155` |
| Priority | `src/webagent/browser/priority.py:180` |
| CAPTCHA | `src/webagent/browser/captcha_detector.py:17` |
| API planner | `src/webagent/planner/api.py:132` |
| Prompt/response parser | `src/webagent/planner/base.py:124`, `:239` |
| Tool decorator/registry | `src/webagent/tools/registry.py:62`, `:98` |
| Tool exposure/evidence/risk/timeout | `src/webagent/tools/exposure.py`, `policy.py`, `risk.py`, `executor.py:19` |
| Parser entry/cascade | `src/webagent/parser/cascade.py:84`, `:138` |
| Parser route/quality | `src/webagent/parser/_router.py:19`, `_quality.py:35` |
| Parser schema | `src/webagent/parser/models.py:56` |

## 顶层入口

```text
console script webagent
└── webagent.cli:main
    ├── configure_logging
    ├── parse_args
    └── asyncio.run(run_task)
        ├── _apply_resume_arguments
        ├── _apply_cli_overrides
        │   └── OutputWorkspace.allocate_run（无显式 --output 时）
        ├── AgentConfig
        ├── _build_planner
        │   ├── APIPlanner(remote)
        │   ├── APIPlanner(local vLLM)
        │   └── StubPlanner
        ├── planner.load
        ├── BrowserController.start
        ├── _build_tool_registry
        │   ├── import webagent.tools.builtin
        │   └── ToolRegistry.auto_discover
        ├── allowed_tools_for_discovery_mode
        ├── BrowserGroundedPolicy / SearchEngineOnlyPolicy
        ├── ActionRiskPolicy
        ├── ToolExecutor(registry, exposure, policies, timeout)
        ├── WebAgent + LoggingHook
        ├── WebAgent.run
        └── finally BrowserController.close + planner.unload
```

兼容入口：`python main.py` 先把 `src` 插入 `sys.path` 再委托 CLI；`python -m webagent` 直接委托 CLI。

## Agent 调用图

```text
WebAgent.run
├── RunLayout.prepare / ensure_for_resume
│   ├── manifest.json
│   └── trajectory/observations/control/artifacts/result/evaluation namespaces
├── hooks.on_task_start
├── loop
│   ├── _observe
│   │   ├── page.wait_for_load_state (best effort)
│   │   ├── wait_for_page_stability
│   │   └── take_snapshot
│   │       ├── page.content/screenshot/title
│   │       ├── _extract_elements_enhanced
│   │       │   ├── CDPService.get_ax_tree
│   │       │   └── extract_interactive_elements (无可定位 AX 元素或异常时 fallback)
│   │       ├── _filter_and_dedupe
│   │       ├── sort_elements_by_priority
│   │       ├── _sanitize_html
│   │       ├── _generate_llm_markdown
│   │       └── URL generation consistency check
│   ├── _handle_captcha -> report / fail / bounded human wait / re-observe
│   ├── _think
│   │   ├── ToolExecutor.get_tool_descriptions
│   │   ├── SessionHistory.format_for_llm
│   │   ├── planning/strategy/evidence/transient hints
│   │   ├── LoopDetector.is_looping
│   │   ├── Planner.plan_action (bounded repair attempts)
│   │   ├── ToolExecutor.validate_tool_call
│   │   └── LoopDetector.add_action
│   ├── pending_action write-ahead checkpoint
│   ├── _act -> ToolExecutor.execute
│   │   ├── exposure gate
│   │   ├── evidence policy
│   │   ├── risk policy
│   │   └── timeout -> ToolRegistry.execute -> Tool.execute
│   ├── post_action_wait + _observe（非 done）
│   ├── SessionHistory.add
│   ├── hooks.on_step_complete
│   ├── strategy update + clear pending_action + checkpoint
│   └── done -> _select_figure -> _persist_final_outputs
├── _persist_run_trace -> trajectory/trace.json
└── finally hooks.on_task_end
```

## Planner 调用图

```text
APIPlanner.load
├── _probe_vision
└── optional _probe_vlm

APIPlanner.plan_action
├── build_prompt
├── _initial_planning_mode
├── _call_structured
│   ├── native-tools:required
│   ├── native-tools:auto（仅 required 明确不受支持）
│   ├── json-schema（仅 structured capability 不受支持）
│   └── prompt-json（最后的格式兼容路径）
│       └── _call
├── _post_data / _bounded_post -> httpx.AsyncClient.post + asyncio.wait_for
└── parse_provider_tool_call 或 parse_llm_response
    └── ToolCall

APIPlanner.analyze_image
├── _analyze_image_vlm（独立 endpoint 可用）
└── _analyze_image_chat（chat vision 可用）
```

## Tool 调用图

```text
@tool import time -> _TOOL_REGISTRY[name] = class
ToolRegistry.auto_discover(browser, config, planner)
    -> instance per registered class
allowed_tools_for_discovery_mode
    -> exact exposed ToolSpec set

ToolExecutor.execute(ToolCall)
├── exposed-tool authorization
├── BrowserGroundedPolicy / SearchEngineOnlyPolicy.authorize
├── ActionRiskPolicy.authorize
└── wait_for(ToolRegistry.execute(lower_name, params))
    ├── lookup + JSON/schema invariants
    ├── validate_params
    └── implementation.execute
        ├── BrowserController
        ├── Planner.analyze_image
        ├── Search/arXiv HTTP
        ├── filesystem under artifacts
        └── parser/PDF helpers
```

## Parser 调用图

```text
pdf_parse tool
└── parse_pdf (sync wrapper)
    └── parse_structured_async
        ├── profile_document
        ├── select_parsers(profile, ocr_provider)
        ├── build_client
        ├── _run_cascade
        │   └── for provider in order
        │       ├── provider.parse
        │       ├── retry retryable error
        │       └── assess_quality
        ├── LocalPyMuPDFParser.parse (all cloud failed)
        └── _error_result (local failed)
```

## 清理后的边界

旧的 events、provider adapters、重复 Python 元素检测器、CDP CSS/DOM helpers、空壳 FileLoggingHook 等不可达实现已经删除。当前图只保留运行路径和明确的兼容/public API。
