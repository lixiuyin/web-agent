# 专业术语表

| 术语 | 全称/中文 | 一般含义 | 本项目中的具体含义 |
|---|---|---|---|
| Web Agent | Web Agent，网页智能体 | 根据目标观察并操作网页的 Agent | Planner 选择 Playwright/PDF/search 工具的单循环系统 |
| LLM Agent | Large Language Model Agent，大语言模型智能体 | 用 LLM 做计划/决策并调用外部能力 | `APIPlanner` 把状态转成 `ToolCall` |
| VLM/LMM | Vision-Language Model / Large Multimodal Model，视觉语言/大型多模态模型 | 同时处理图像与文本 | planner 可同时接收 screenshot 与 DOM 文本；也用于 figure analysis |
| Observe–Think–Act–Record | 观察—思考—行动—记录 | Agent 的迭代控制范式 | `_observe/_think/_act` 加 AgentStep/history |
| Planner | 规划器 | 根据目标和状态选择后续动作 | `Planner` Protocol；API/Stub 两种实现 |
| Tool Calling | 工具调用 | 模型输出结构化函数名和参数 | JSON → `ToolCall` → registry → `ToolResult` |
| Protocol | 结构化类型协议 | 按方法形状而非继承判断接口 | `Planner/Tool/AgentHook` |
| Playwright | 浏览器自动化框架 | 控制 Chromium/Firefox/WebKit | 项目只启动 Playwright Chromium |
| BrowserContext | 浏览器上下文 | 隔离 cookie、storage、permissions 的会话 | persistent context 使用 `browser_profile` |
| Page | 页面对象 | 一个 tab 的操作接口 | `BrowserController.page` |
| CDP | Chrome DevTools Protocol，Chrome 开发者工具协议 | 访问 Chromium 内部调试域 | AX Tree、DOM、Runtime、CSS |
| DOM | Document Object Model，文档对象模型 | HTML 的树状对象表示 | JS 遍历元素，HTML 再压缩为 Markdown |
| Accessibility/AX Tree | 无障碍树 | 浏览器按 role/name 暴露的语义树 | CDP 优先提取 button/link/textbox 等 |
| Grounding | 指代落地/定位 | 把语言或视觉目标映射到可操作实体 | 把“Submit”映射到 Playwright selector |
| Selector/Locator | 选择器/定位器 | 定位网页元素的表达 | 工具接受 text 或 CSS；尚无 role locator schema |
| Persistent Context | 持久化上下文 | 浏览器 profile 跨运行保存 | `launch_persistent_context(user_data_dir)` |
| Browser Fingerprinting | 浏览器指纹 | 用 UA、Canvas、WebGL 等识别客户端 | stealth 脚本尝试修改这些字段 |
| Stealth | 反自动化暴露策略 | 降低 webdriver 特征 | flags + init script + random UA；无绕过保证 |
| CAPTCHA detection | 验证码检测 | 判断人机挑战是否出现 | selector/URL/title 规则；不求解，headed 普通模式可限时等待人工处理 |
| Structured Output | 结构化输出 | 限制模型按 schema/JSON 返回 | 默认 provider native tools；按明确能力错误降级到 provider JSON Schema、prompt JSON |
| Tool Registry | 工具注册表 | 名称到工具实现的映射 | 装饰器全局类表 + 每次运行实例表 |
| Pydantic | Python 数据验证库 | 从类型注解验证/序列化模型 | Core models 与 settings |
| Async/Await | 异步协程 | 单线程在 I/O 等待间切换任务 | browser、HTTP、tools、Agent loop 均为 async |
| Timeout | 超时 | 限制等待或墙钟时间 | browser/API/tool/parser/task 多层预算 |
| Retry | 重试 | 同一操作失败后再次尝试 | Observe 三次；parser retryable provider 两次 |
| Fallback | 降级/备选路径 | 主方案失败后换方案 | CDP→JS、search engines、cloud parser→local |
| Loop Detection | 循环检测 | 识别重复无进展轨迹 | 六个启发式 signal；同时驱动 nudge 与有界 strategy replan |
| Checkpoint | 检查点 | 保存可恢复的控制器状态 | 原子、带校验和；保存历史/策略/policy/非敏感 tab 状态，不把 cookie 或隐藏思维链写入 trace |
| OCR | Optical Character Recognition，光学字符识别 | 从图像恢复文字 | Marker/MinerU/Paddle cloud parsing 的一部分 |
| Parser Cascade | 解析器级联 | 顺序尝试多个 parser | 路由后的 cloud providers + local fallback |
| Quality Gate | 质量门 | 不满足条件则拒绝输出 | 文本量、控制字符、扫描每页字符/资产规则 |
| RAG | Retrieval-Augmented Generation，检索增强生成 | 检索证据再让模型生成 | 本项目 PDF QA 有规则检索，但没有 embedding/vector store，不能笼统称完整 RAG |
| BBox | Bounding Box，边界框 | `(x0,y0,x1,y1)` 空间范围 | parser element schema；很多 provider 当前填零 |
| Headless | 无头模式 | 无可见窗口运行浏览器 | 默认 true；无 DISPLAY 时强制 true |
| Wall-clock timeout | 墙钟超时 | 从开始到结束的真实经过时间上限 | planner/tool/cascade 使用 `asyncio.wait_for` |
| Calibration | 校准 | 预测置信度与真实正确率一致程度 | 可在终态外部判分前采集 `success_probability`，报告 coverage、Brier、NLL、ECE 与 risk-coverage；自报概率不是正确性证明 |
| Ablation | 消融实验 | 移除组件以测其因果贡献 | screenshot-only/DOM-only 等对照 |

## 容易混淆的边界

- screenshot 被捕获不等于模型收到：还要非空白且 vision probe 支持。
- AX node 有语义不等于能点击：仍需可靠 locator grounding。
- `success=True` 的工具不等于任务成功：只有 `done` 触发 completed。
- parser 返回无 `error` 不等于高质量：local fallback 不再经过 quality gate。
- `SessionHistory` 是有限窗口的步骤上下文，不等于可跨任务检索的长期记忆。
