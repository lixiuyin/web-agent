# 错误、超时、重试与降级

## 分层原则

本项目没有单一错误总线。各层采用不同语义：controller 多返回普通字典；tool registry 统一成 `ToolResult`；planner 解析失败返回 `None`；parser 把预期失败放进 `PDFParseResult.error`；Agent 对少数系统异常重抛。

## 错误传播矩阵

| 位置 | 失败 | 层内行为 | 上层看到什么 |
|---|---|---|---|
| Browser action | selector timeout/Playwright error | 捕获 | `{success:false,error}` |
| Snapshot element extraction | CDP/JS 失败 | fallback 或空 list | 仍可能得到截图/Markdown |
| Snapshot HTML/PNG/title | Page error | 向外抛 | `_observe` 最多重试 3 次 |
| Planner HTTP | 网络/HTTP/hard timeout | 向外抛 | `_think` 对非 hard-timeout 错误有限修复重试 |
| Planner JSON parse | 坏 JSON/无 tool | 返回 `None` | 同一 step 有限重试；耗尽后连续失败 +1 |
| Registry validation | `ValueError` | 捕获 | `ToolResult(error="Validation: ...")` |
| Tool execution | 普通 Exception | 捕获 | `ToolResult(error="Execution: ...")` |
| Tool wall clock | 超过 `tool_timeout` | cancel coroutine | 失败 ToolResult |
| Parser provider | `ParserProviderError` | 同 provider retry 或换 provider | cascade 继续 |
| Parser quality | 未过 gate | 换 provider | cascade 继续 |
| 所有 cloud parser | 全失败 | local PyMuPDF | local result |
| local parser | 也失败 | 捕获 | `PDFParseResult.error` |
| Agent browser disconnect | 关键字匹配 | 标记 failed，不重抛 | AgentResult |
| Agent 其他异常 | 标记 failed 后重抛 | finally 通知 hook | 调用方异常，无 AgentResult |

## 五个独立时间预算

| 配置 | 作用域 | 单位 | 是否真正硬上限 |
|---|---|---|---|
| `browser_timeout` | Playwright 单动作默认 timeout | ms | 对使用 default timeout 的动作是 |
| `api_timeout` | httpx 单次连接/读写 | s | 流式 trickle 可能延长 |
| `api_hard_timeout` | planner 整个 POST | s | 是，`asyncio.wait_for` |
| `tool_timeout` | 整个工具 coroutine | s | 对 coroutine 是；线程/远程副作用未必撤销 |
| `parse_timeout_seconds` | cloud cascade 总预算 | s | provider wait 被剩余预算包住 |
| `task_timeout` | Agent 总任务 | s | 只在步骤边界检查，不是连续 watchdog |

`marker_max_wait_seconds` 和 `mineru_max_wait_seconds` 是各 provider polling 上限，但还会被 cascade 的剩余总预算截断。

## 重试与 fallback

Parser provider 对 retryable failure 最多重试两次；认证失败、未配置、限流和质量失败通常不重试。BrowserController 的动作本身不重试，只有 Agent 重新规划才可能再次选择同工具。Planner 对空/畸形响应与非超时异常默认总计尝试两次；hard timeout 不重试。

Observe 会固定重试三次，每次等待一秒；元素提取内部先 CDP/AX，再 JS，再空结果。Search 工具有独立的搜索引擎 cascade。

## Loop detector 不是恢复器

Loop detector 仅把提示追加给 planner。它不回滚动作、不恢复页面、不强制切换工具，也不改变温度。若模型忽略 nudge，任务最终依靠 `max_steps`、连续失败或 task timeout 停止。

## CAPTCHA 的真实行为

`captcha_pause` 为兼容旧配置保留了名称，true 表示每轮检测。普通默认 `captcha_handling=report` 会先记录事件，并在 headed 浏览器中轮询到用户清除或超时；超时以及所有 headless challenge 都返回 `blocked` 并关闭 BrowserController。`wait_for_human` 保留为显式同义工作流，strict 默认立即 `fail`。它没有外部通知 channel，也绝不自动求解验证码，因此准确说法是“有界人工接管等待”，不是 CAPTCHA recovery/bypass。

## 静默吞异常的影响

Controller、CDP 和 snapshot 多处宽泛 `except Exception`，提高了长任务不中断的概率，却降低诊断性。例如 CDP bbox 获取失败后默认为零，planner 看到的 selector 质量下降但没有显式错误。浏览器 context/Playwright 关闭异常现已显式记录 warning，但其他降级路径仍应统计次数，而不是只看最终 success。

## 建议记录的可靠性指标

- planner parse failure rate；unknown tool/validation/execution failure rate；
- snapshot retry rate、CDP→JS fallback rate、unknown selector rate；
- 每类 loop signal 的 precision/recall；
- provider retry、quality rejection、fallback 与最终 backend 分布；
- task success、steps、wall time、tokens、API cost；
- 同一任务多次运行的方差；
- 失败后恢复成功率和恢复额外步骤。
