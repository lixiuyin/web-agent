# Qwen 严格搜索双模型验证：2026-09-09 paired R5

本记录保存一次真实公共网页、真实 planner、临时 Chromium profile 的双模型日期化结果。UTC
与 Asia/Taipei 的运行日期均为 2026-09-09。它回答一个具体问题：当前实现能否在
browser-search-only 约束下找到最新的官方 Qwen technical report PDF，下载文档并正确解释
Figure 1。它不是跨日期稳定性或通用成功率声明。

## 验收规则

单次严格任务只有同时满足以下条件才记为通过：

1. `evaluation/task.json` 的 `passed=true`，全部必需 assertion 通过；
2. `trajectory/verification.json` 的 `valid=true`，且 artifact integrity 有效；
3. 最终答案给出正确报告、第一方 PDF 页面、可审计的精确日期类型、PDF 下载和 Figure 1
   解读；
4. observation 的 pre/post 引用完整，DOM 与截图的 scope 明示，配对一致性没有伪造；
5. 没有未解决 challenge、错误候选、伪 PDF、缺失交付物或靠隐藏直连工具完成发现。

`failed_action_count` 和 `planner_failure_count` 不要求为零。少量失败只有在来源属于外部搜索
波动、selector 漂移或 provider 能力协商，次数有界、原始错误保留、后续成功恢复且不改变
独立判分时才可接受。错误报告、证据缺口、重复耗尽预算、观察配对不一致或 certificate 无效
仍是必须修复的系统/任务失败。

## 运行方式与绑定

两个 endpoint 使用相同 strict manifest 和配置，各自写入不可覆盖的 execution：

```bash
uv run python -m webagent.benchmarks.suites.open_web.runner \
  --manifest src/webagent/benchmarks/manifests/qwen_strict_search.json \
  --search-engine-only \
  --model qwen/qwen3.8-flash \
  --max-steps-per-task 25 \
  --discovery-max-steps-per-task 25 \
  --direct-task-timeout-seconds 300 \
  --discovery-task-timeout-seconds 2400 \
  --output outputs/validation/2026-09-09-qwen-paired-r5/qwen

uv run python -m webagent.benchmarks.suites.open_web.runner \
  --manifest src/webagent/benchmarks/manifests/qwen_strict_search.json \
  --search-engine-only \
  --model z-ai/glm-5.3-flash \
  --max-steps-per-task 25 \
  --discovery-max-steps-per-task 25 \
  --direct-task-timeout-seconds 300 \
  --discovery-task-timeout-seconds 2400 \
  --output outputs/validation/2026-09-09-qwen-paired-r5/glm
```

运行报告把 provider 记为 `unknown`，因为命令没有显式声明 provider identity；本文不从 endpoint
配置反推该字段。两份报告的共同绑定为：

- agent source SHA-256：
  `6c1b3109711ca0a3450a0b8ee5ca286a91fee74a673fe9db437bb33ec94601a0`；
- benchmark source SHA-256：
  `8a7f1ff9f38cc14ca9acd80049831dcc989da05e2c690d38de1bb57fdb2406e3`；
- manifest SHA-256：
  `18c7cd8a6edf19a55d2f4908b088c67cf000a3fc38b58acf304dba2e93836cc5`；
- benchmark config SHA-256：
  `c6ba8ef9e16bfdb34f8e0900c1c28226a5884cda4c475e337ffe4d99d84130e4`。

Strict contract 为 `search_engine_only_v8`；浏览器为 headless、1280×720、temporary profile，
stealth 与持久 PDF cache 关闭，CAPTCHA 策略为 fail closed。

## 结果

| 项目 | Qwen3.8-Flash | GLM-5.3-Flash |
|---|---:|---:|
| 独立任务判分 | 通过，1.0 | 通过，1.0 |
| 必需 assertions | 10/10 | 10/10 |
| strict certificate | 6/6 | 6/6 |
| artifact integrity | 有效，63 项 | 有效，45 项 |
| 报告步数 / 动作数 | 21 / 20 | 16 / 14 |
| 失败动作 | 3 | 2 |
| planner 尝试 / 失败 | 21 / 0 | 16 / 1 |
| action validity | 85.00% | 85.71% |
| 完成时间 | 269.55 秒 | 1479.33 秒 |
| CAPTCHA / blocked / max steps | 0 / 0 / 0 | 0 / 0 / 0 |

Qwen 的三个失败动作分别是一次搜索结果不满足显式版本/PDF 约束，以及两次搜索页没有可抽取的
结构化结果；GLM 的两个失败动作都是搜索结果不满足显式约束。两条轨迹随后都通过其他查询与
官方页面恢复，没有隐藏或删除失败记录。GLM 另有一次 planner attempt 没有形成合法动作，后续
尝试恢复。

## 语义结果

两个 endpoint 都选中
[`QwenLM/Qwen3.8-Flash-Next`](https://github.com/QwenLM/Qwen3.8-Flash-Next) 的
[`tech_report.pdf`](https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf)。
报告文件自己的 GitHub blob/history 证据给出 `2026-08-26T12:29:38Z`；答案明确将其标为
“报告文件最后 commit/update date”，没有误写为 PDF 内声明的 publication date。候选选择基于
当前年度搜索、官方 QwenLM 组织仓库列表、最高观察到的 dotted version，以及候选仓库实际存在
`tech_report.pdf`，而不是仅凭
[官方发布博客](https://qwen.ai/blog?id=qwen3.8-flash-next)标题推断。

Figure 1 被正确识别为架构图而非指标图。解读覆盖：

- 每四层由 3 个 GDN 层与 1 个 QSA 层组成；
- 每个 sublayer 经 GR 读写，残差流被扩宽，并在读取时做逐元素 gating；
- Layer 2 的 n-gram embedding 通过 host-memory prefetch 扩展 accelerator 外的容量；
- MTP 模块在 speculative decoding steps 间复用 QSA indices。

视觉结论、caption 文本和架构推断被分别标注；图中圆点数量只解释为 schematic branch
indicator，没有伪造数值 benchmark。`pdf_analyze_figure` 使用 Figure 所在页并补充前一页的
有界文本上下文，避免只看裁剪图片丢失定义。

## Observation 与 README GIF 完整性

Qwen 轨迹保存 21 个 pre、21 个 post 和 21 个兼容 screenshot preview。42 份 observation 中，
41 份状态为 `complete`，最终无浏览器副作用的 `done` post 为 `reused`；42/42 都记录
`pair_consistent=true`。全部主截图 scope 为 viewport，DOM 则明确拆成与截图同范围的
`viewport_context` 和只作非视觉补充的 `document_context`。因此完整页面 DOM 不再被错误描述为
截图内容；lazy loading 与 virtualized list 仍可能需要 scroll。

根 README 的 `docs/assets/strict-run-demo.gif` 由该 Qwen 轨迹的 21 张 screenshot preview 按顺序
生成，最后追加抽取出的 Figure 1。每张浏览器帧显示 2 秒，Figure 1 显示 6 秒；画面等比缩放并
留白到 960×720，没有裁掉 viewport 内容。生成文件共 24 帧、48 秒、约 1.0 MB，SHA-256 为
`e49ad815ba1e0d3c2e81ae4248ffed8c4aa407854e892f5110b2f7381f2b0000`。GIF 是便于浏览的派生
预览，不替代原始 observation、trace、独立判分或 certificate。

## 产物位置与限制

本地根目录为：

```text
outputs/validation/2026-09-09-qwen-paired-r5/
```

`qwen/results.json` 与 `glm/results.json` 是各自 suite report。机器判分与证书分别位于每个模型
目录下的 `runs/qwen_latest_report_figure1/evaluation/task.json` 和
`runs/qwen_latest_report_figure1/trajectory/verification.json`；PDF 与 Figure 1 位于对应 run 的
`artifacts/downloads/` 和 `result/attachments/`。

完整原始 `outputs/` 默认不是长期文档真相。经审阅的
[冻结证据包](../../../outputs/published/2026-09-09/README.md)按原字节保留了两个 endpoint 的 trace、tool-result
evidence、pre/post observation JSON 及其哈希指向的图片、任务判分、certificate、PDF 和 Figure 1。
这使 certificate 仍可复验；README GIF 仅是可视预览。Strict R5 的 benchmark source hash 与同日 R7
generality campaign 不同，因此二者不能混成同一 longitudinal cell。

本次证明两个 endpoint 在一个日期化任务中各完整通过一次。公共搜索非平稳、provider identity
未显式记录、单样本偶然性以及外部 BrowserGym 尚未运行，仍限制结论外推。
