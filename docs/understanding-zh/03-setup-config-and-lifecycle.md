# 安装、配置与资源生命周期

## 环境要求与安装

`pyproject.toml` 要求 Python `>=3.13`，构建后端是 Hatchling，CLI entry point 为 `webagent.cli:main`。

```bash
pip install -e ".[dev]"
playwright install chromium
```

四个本地质量门：

```bash
ruff check src/ benchmarks/ scripts/ tests/
ruff format --check src/ benchmarks/ scripts/ tests/
mypy src/ benchmarks/ scripts/
pytest tests/unit/ -v
python scripts/check_docs.py
```

integration test 会启动真实 Chromium。两条生命周期用例使用 StubPlanner；另有一条严格评测
流程用确定性虚构证据覆盖多源发现→下载→Figure 1→done，都不需要真实模型 API。

## 配置读取优先级

`AgentConfig` 基于 `pydantic-settings`：字段默认值 < `.env`/环境变量 < 构造参数；进入 `run_task()` 后，显式 CLI 参数再次覆盖配置对象。

环境变量统一带 `AGENT_` 前缀，例如：

```dotenv
AGENT_MODEL_API_URL=https://example.test/v1/chat/completions
AGENT_MODEL_API_KEY=secret
AGENT_BROWSER_HEADLESS=true
AGENT_MAX_STEPS=30
AGENT_USE_CDP=true
AGENT_OUTPUT_DIR=./outputs
```

仓库提供 `.env.example`，可以复制为 `.env` 后填写本地配置；`.env` 含凭证且明确禁止提交。

## 配置消费关系

完整字段、默认值和环境变量统一由[配置参考](../reference/configuration.md)维护。本节只说明源码接线，
避免在源码手册中复制会随 `AgentConfig` 变化的完整默认值表。

| 配置组 | 主要消费者 | 接线边界 |
|---|---|---|
| Planner endpoint、timeout、token、output mode | CLI、`APIPlanner`、Agent `_think()` | CLI 显式参数覆盖环境配置；hard timeout 包住完整请求 |
| Browser channel、profile、proxy、stability | `BrowserController`、Agent `_observe()` | strict 模式覆盖不兼容的 profile、stealth 和 TLS 行为 |
| Search、discovery、Google opt-in | Tool exposure、`SearchTool`、browser-grounded policy | Hybrid 与 browser-grounded/strict 暴露不同工具面 |
| Checkpoint、strategy、loop limits | `WebAgent`、`CheckpointStore`、loop detector | strict/search-only 禁止把恢复轨迹认证为一次连续运行 |
| CAPTCHA 与 high-risk policy | Agent loop、`ToolExecutor` | CAPTCHA 不求解；外部状态动作默认拒绝 |
| OCR、parser、Figure fast path、cache | parser router、PDF tools | provider 结果经 quality gate；持久 cache 显式开启 |
| Output workspace | CLI、`RunLayout`、tools | 缺省值是 workspace；显式 `--output` 是 exact run root |

## CLI 构造核心（关键路径）

来源：`src/webagent/cli.py::_apply_cli_overrides/run_task`。调用者是 `main()`；它先解析
workspace/run 边界，再构造 planner、browser、registry、policy、executor、agent，并保证释放资源。
下面只摘录与输出分配直接相关的当前源码；完整 lifecycle 应直接阅读该文件。

```python
def _apply_cli_overrides(cfg: AgentConfig, args: argparse.Namespace) -> None:
    _apply_scalar_overrides(cfg, args)
    _apply_browser_overrides(cfg, args)
    _apply_evaluation_overrides(cfg, args)
    if args.output:
        cfg.output_dir = Path(args.output).expanduser().resolve()
    else:
        task = str(getattr(args, "task", None) or "interactive-session")
        cfg.output_dir = (
            OutputWorkspace.from_root(cfg.output_dir)
            .allocate_run(task=task, model=cfg.model_name)
            .root
        )

async def run_task(args: argparse.Namespace) -> None:
    resume_path = _apply_resume_arguments(args)
    cfg = AgentConfig()
    _apply_cli_overrides(cfg, args)
    planner = _build_planner(cfg)
    await planner.load()
    browser = _build_browser(cfg)
    try:
        await browser.start()
        registry = _build_tool_registry(browser, cfg, planner)
        # 此后构造 tool exposure、browser/search policy、risk policy 与 executor。
        # WebAgent.run() 接收 cfg.output_dir 对应的 exact run，并处理可选 resume。
        ...
    finally:
        try:
            await browser.close()
        finally:
            await planner.unload()
```

输入 `args` 是 `argparse.Namespace`；输出是 `None`，运行结果打印到 stdout，持久化写入
`cfg.output_dir` 对应的 exact run。恢复时 `_apply_resume_arguments()` 会从默认的
`control/checkpoints/latest.json`（或配置的纯文件名）推导 run 根并校验显式 `--output` 是否一致；旧 checkpoint 路径
只作为读取兼容入口。

## 输出目录的破坏性边界

未显式传 `--output` 时，`AGENT_OUTPUT_DIR` 是 workspace；CLI 通过 `OutputWorkspace` 为本次进程
分配 `outputs/runs/<UTC-date>/<model>/<task>-<run-id>/`，不会清空同 workspace 中的旧 run、study、
campaign 或 legacy archive。显式 `--output` 则准确指向一个 run 根目录。

`RunLayout.prepare()` 拒绝文件系统根、当前工作目录，以及没有有效 `manifest.json` 的非空目录。
只有确认属于 webagent 的旧 run 才会重新初始化；此时仅移除 manifest、trajectory、observations、
control、artifacts、result、evaluation 等已知生成 namespace，未知同级文件仍保留。因此依然不应把
手工工作目录当作 `--output`，但它不再对任意路径执行无条件递归清空。

`RunLayout.prepare()` 只创建 run 根和 ownership manifest；其余 namespace 在首次写入时生成。
因此缺失的 `artifacts/` 或 `evaluation/` 明确表示该 run 没有产生对应内容，而不是遗留空目录。

交互模式在进程启动时只分配一个 run 根。后续任务调用 `run(reset_history=False)` 保留会话
history、artifacts 与 owned run，不再重新初始化目录；step 与 turn 编号继续递增。顶层
`trajectory/trace.json` 和 `result/` 表示最新一轮，同时原子发布不可覆盖的
`trajectory/turns/turn-NNN.json` 与 `result/turns/turn-NNN/{summary.txt,attachments/}`。若实验需要
独立样本，应使用多个普通 CLI run 或 benchmark execution，而不是把同一 interactive session 的
turn 当成独立 run。strict/search-only 为保持单次连续证书，直接拒绝 follow-up turn。

## Browser 生命周期

`BrowserController` 使用 `launch_persistent_context`，但默认 `browser_profile_mode=temporary`，
会创建本次进程独占的临时目录并在关闭后删除。显式 `persistent` 才使用 `./browser_profile`。
临时目录创建时写入 owner PID 与时间标记；后续启动只回收超过配置阈值、标记有效且 PID 已
消失的孤儿目录。活跃目录、无标记旧目录和持久 profile 均不会被扫描删除。
持久 profile 启动前和关闭后只修复 clean-exit 标志，不删除可能属于活进程的
`Singleton*` 锁。关闭 context 会同时关闭页面和底层 browser。`accept_downloads=True` 支持
普通网页下载捕获；PDF 工具仍采用带内容校验的独立 HTTP 下载流程。
下载完成后还会检查前 1024 bytes 内的 `%PDF-` 文件头；后缀为 `.pdf` 的 HTML 预览页会
被删除，下载器本身不会解析或返回 raw/download URL。planner 必须先导航到预览页并调用
`inspect_download_links`；只有该独立浏览器步骤明确返回给 planner 的 DOM 属性或页面声明
元数据 URL 才能成为后续下载的 provenance。

`--strict-eval` 与 search-engine-only 采用同一无捷径策略，同时关闭持久 PDF 缓存；未显式传
`--output` 时生成独立 run。每次 Agent 运行都会写 `trajectory/trace.json`，严格运行还在同目录写
与 trace SHA-256 绑定的 `verification.json`，机械检查单一 run_id、搜索优先、planner 可见 URL
provenance、任务所需 PDF/Figure 阶段及 latest 来源覆盖。
latest 的代码仓库覆盖要求 `site:` 带已验证的 owner 路径，不能把 `site:github.com` 当作
`site:github.com/QwenLM`；查询还必须包含当前年份和主题词。官方身份搜索先背书 owner 后，
这里的当前年份必须作为 query 字面文本出现，只有 `recency=year` 不等价。
版本限定主题也可用于独立范围搜索，但结果仍必须属于同 owner，并覆盖最终候选仓库。
发布谱系门不再只看查询关键词，还要求结果 title/URL/snippet 呈现主题相关版本或发布语义；
提前下载的拒绝会一次列出全部缺失项，并在 audit 中保存 `missing_prerequisites`。
每个有效搜索结果的 audit 都会进入 history，直接告诉 planner 尚缺哪些条件；全部满足时明确
标记 checklist complete。`done` 只有在工具结果 `success=true` 时才能写终态并结束循环，策略
拒绝会作为普通失败步骤继续规划，避免 false completion。
进入 `.pdf` 预览 URL 时策略会提前记录最终候选；如果它是代码仓库文件，清单会分别列出
“官方身份搜索尚未背书该 host/owner”和“仍缺独立当前年份候选范围搜索”。因此只背书厂商主页
后反复改写 GitHub scope query 不会形成无法完成的循环。

控制器只在 Linux 且 `headless=False`、同时没有 `DISPLAY/WAYLAND_DISPLAY` 时强制
headless；macOS/Windows 不再因缺少 X11 `DISPLAY` 而误降级。
