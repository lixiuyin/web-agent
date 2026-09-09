# 调用图附录

## 关键源码定位（本次 checkout）

| 主题 | 源文件 | 符号 |
|---|---|---|
| CLI planner 选择 | [cli.py](../../src/webagent/cli.py) | `_build_planner` |
| CLI 完整生命周期 | [cli.py](../../src/webagent/cli.py) | `run_task` |
| Agent 构造与任务入口 | [agent/loop.py](../../src/webagent/agent/loop.py) | `WebAgent.__init__ / run` |
| Agent 单步执行 | [agent/loop.py](../../src/webagent/agent/loop.py) | `WebAgent._execute_step` |
| Observe / Think / Act | [agent/loop.py](../../src/webagent/agent/loop.py) | `WebAgent._observe / _think / _act` |
| Planning coordination | [agent/planning.py](../../src/webagent/agent/planning.py) | `PlanningCoordinator.plan_action / elicit_terminal_confidence` |
| Search evidence analysis | [tools/policies/discovery.py](../../src/webagent/tools/policies/discovery.py) | `DiscoveryEvidence` |
| Loop detector | [agent/loop_detector.py](../../src/webagent/agent/loop_detector.py) | `LoopDetector` |
| Browser 生命周期 | [browser/controller.py](../../src/webagent/browser/controller.py) | `BrowserController.start / close` |
| Browser profile | [browser/profiles.py](../../src/webagent/browser/profiles.py) | `create_temporary_profile / mark_profile_clean` |
| Browser checkpoint | [browser/checkpoint.py](../../src/webagent/browser/checkpoint.py) | `export_checkpoint_state / restore_checkpoint_tabs` |
| 稳定等待与快照 | [browser/snapshot.py](../../src/webagent/browser/snapshot.py) | `wait_for_page_stability / take_snapshot` |
| Rendered 不可用时的 AX/JS fallback | [browser/snapshot.py](../../src/webagent/browser/snapshot.py) | `_extract_elements_enhanced / _extract_from_ax_tree` |
| JS 元素提取 | [browser/interactive_detector.py](../../src/webagent/browser/interactive_detector.py) | `extract_interactive_elements` |
| Priority | [browser/priority.py](../../src/webagent/browser/priority.py) | `sort_elements_by_priority` |
| CAPTCHA | [browser/captcha_detector.py](../../src/webagent/browser/captcha_detector.py) | `CaptchaDetector` |
| API planner | [planner/api.py](../../src/webagent/planner/api.py) | `APIPlanner` |
| Vision routing | [planner/vision.py](../../src/webagent/planner/vision.py) | `VisionSupport` |
| Provider response | [planner/provider_response.py](../../src/webagent/planner/provider_response.py) | `_strip_thinking_tags / _structured_output_unsupported` |
| Prompt/response parser | [planner/base.py](../../src/webagent/planner/base.py) | `build_prompt / parse_llm_response` |
| Tool registry | [tools/registry.py](../../src/webagent/tools/registry.py) | `tool / ToolRegistry` |
| Tool 执行与授权 | [tools/executor.py](../../src/webagent/tools/executor.py) | `ToolExecutor.execute` |
| Evidence policy | [tools/policy.py](../../src/webagent/tools/policy.py) | `SearchEngineOnlyPolicy / BrowserGroundedPolicy` |
| Parser entry/cascade | [parser/cascade.py](../../src/webagent/parser/cascade.py) | `parse_structured_async / _run_cascade` |
| Parser route | [parser/_router.py](../../src/webagent/parser/_router.py) | `select_parsers` |
| Parser quality | [parser/_quality.py](../../src/webagent/parser/_quality.py) | `assess_quality` |
| Parser schema | [parser/models.py](../../src/webagent/parser/models.py) | `PDFParseResult` |

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
│   │       ├── capture_geometry + page.content
│   │       ├── capture_rendered（主路径：跨 frame viewport/document blocks + bound refs）
│   │       ├── page.screenshot（viewport；可选额外 full-page audit）
│   │       ├── assert_rendered_current（主路径 revision/node 一致性）
│   │       ├── _extract_elements_enhanced（rendered projection 不可用且 use_cdp）
│   │       │   ├── CDPService.get_ax_tree
│   │       │   └── extract_interactive_elements（无可定位 AX 元素或异常时 fallback）
│   │       ├── _extract_elements_basic（禁用 CDP 时的 JS fallback）
│   │       ├── _filter_and_dedupe
│   │       ├── _rank_snapshot_elements
│   │       ├── _sanitize_html
│   │       ├── _generate_llm_markdown
│   │       ├── _snapshot_contexts（viewport/document 分预算）
│   │       └── URL + geometry generation consistency check
│   ├── _handle_captcha -> report / fail / bounded human wait / re-observe
│   ├── _think -> PlanningCoordinator.plan_action
│   │   ├── ToolExecutor.get_tool_descriptions
│   │   ├── SessionHistory.format_for_llm
│   │   ├── _planning_history + _with_action_budget
│   │   ├── _loop_recovery_hint（可更新 strategy）
│   │   ├── LoopDetector.is_looping
│   │   ├── _plan_validated_action -> Planner.plan_action (bounded repair attempts)
│   │   ├── ToolExecutor.validate_tool_call
│   │   └── LoopDetector.add_action
│   ├── pending_action write-ahead checkpoint (checkpoint_redaction)
│   ├── _act -> ToolExecutor.execute
│   │   ├── exposure gate
│   │   ├── evidence policy
│   │   ├── risk policy
│   │   └── timeout -> ToolRegistry.execute -> Tool.execute
│   ├── post_action_wait + _observe（非 done）
│   ├── SessionHistory.add
│   ├── hooks.on_step_complete
│   ├── strategy update + clear pending_action + checkpoint
│   └── done -> run_outputs._select_figure -> run_outputs._persist_final_outputs
├── run_outputs._persist_run_trace -> trajectory/trace.json
└── finally hooks.on_task_end
```

## Planner 调用图

```text
APIPlanner.load
└── VisionSupport.load
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
└── VisionSupport.analyze_image
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
└── wait_for(ToolRegistry.execute(canonical_name, params))
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
