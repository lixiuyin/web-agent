# 调用图附录

## 关键源码定位（本次 checkout）

| 主题 | 路径与起始行 |
|---|---|
| CLI planner 选择 | `src/webagent/cli.py:76` |
| CLI 完整生命周期 | `src/webagent/cli.py:130` |
| Agent 构造 | `src/webagent/agent/loop.py:47` |
| Agent 主循环 | `src/webagent/agent/loop.py:99` |
| Observe / Think / Act | `src/webagent/agent/loop.py:259`, `:301`, `:357` |
| Loop detector | `src/webagent/agent/loop_detector.py:63` |
| Browser 启动/关闭 | `src/webagent/browser/controller.py:70`, `:460` |
| Snapshot | `src/webagent/browser/snapshot.py:45` |
| AX 提取 | `src/webagent/browser/snapshot.py:161` |
| JS 元素提取 | `src/webagent/browser/interactive_detector.py:310` |
| Priority | `src/webagent/browser/priority.py:16` |
| Captcha | `src/webagent/browser/captcha_detector.py:85` |
| API planner | `src/webagent/planner/api.py:91`, `:167`, `:590` |
| Prompt/response parser | `src/webagent/planner/base.py:33`, `:69` |
| Tool decorator/registry | `src/webagent/tools/registry.py:15`, `:35` |
| Tool timeout | `src/webagent/tools/executor.py:15` |
| Parser entry/cascade | `src/webagent/parser/cascade.py:82`, `:136` |
| Parser route/quality | `src/webagent/parser/_router.py:21`, `_quality.py:35` |
| Parser schema | `src/webagent/parser/models.py:21` |

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
        ├── ToolExecutor
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
│   │   ├── page.wait_for_load_state
│   │   └── take_snapshot
│   │       ├── page.content/screenshot/title
│   │       ├── _extract_elements_enhanced
│   │       │   ├── CDPService.get_ax_tree
│   │       │   └── extract_interactive_elements (fallback)
│   │       ├── _filter_and_dedupe
│   │       ├── sort_elements_by_priority
│   │       ├── _sanitize_html
│   │       └── _generate_llm_markdown
│   ├── _check_for_captcha -> BrowserController.check_captcha
│   ├── _think
│   │   ├── ToolExecutor.get_tool_descriptions
│   │   ├── SessionHistory.format_for_llm
│   │   ├── LoopDetector.is_looping
│   │   ├── Planner.plan_action
│   │   └── LoopDetector.add_action
│   ├── _act -> ToolExecutor.execute -> ToolRegistry.execute -> Tool.execute
│   ├── _observe (post action)
│   ├── SessionHistory.add
│   ├── hooks.on_step_complete
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
├── build_prompt 或 build_enhanced_prompt
├── _call
│   └── _post
│       └── _bounded_post -> httpx.AsyncClient.post + asyncio.wait_for
└── parse_llm_response 或 parse_enhanced_response
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

ToolExecutor.execute(ToolCall)
└── wait_for(ToolRegistry.execute(lower_name, params))
    ├── lookup
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
