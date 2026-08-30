# Parser 与 PDF Pipeline

## 目标与边界

`webagent.parser` 把 PDF 或图片转换为统一 `PDFParseResult`。它不是单一 OCR，而是：本地画像 → provider 路由 → provider 内重试 → 输出质量门 → 下一个 provider → 本地文本 fallback。

PDF 工具层另有两级复用：进程内 single-flight 避免并发工具重复提交同一文件；可选持久
缓存以 PDF 内容 SHA-256 为键，把成功且非本地降级的解析结果原子写入 `pdf_cache_dir`，
下一进程再复制到本次 artifacts。使用内容而非路径作为 key 可避免文件被覆盖后复用旧结果。
`--strict-eval` 禁用持久缓存，并把进程内 key 绑定到本次 artifacts 根；缓存 manifest 中的
恢复路径也必须保持在输出目录内。

OCR（Optical Character Recognition，光学字符识别）把图像中文字恢复为文本；Parser Cascade 是多个 parser 顺序尝试的降级链；Quality Gate 是决定当前输出是否足够可用的启发式门槛。

## Figure 本地渲染快路径

`pdf_analyze_figure` 收到精确的 `Figure N` 时，先调用
`utils.pdf_figures.detect_and_render_local_figure()`。检测器不读取任务答案，也不依赖模型名；
它在每页寻找以 `Figure/Fig. N:` 开头的 caption block，将 PyMuPDF 的 vector drawing 与
raster image bbox 聚类，再按上下位置、间距、横向重叠、面积和页边规则线过滤计算置信度。
高于 `local_figure_min_confidence`（默认 0.9）且全文件恰好只有一个同号候选时，直接以
`local_figure_render_dpi`（默认 144）渲染 crop，并把 caption 注入 vision question。

以下情况不降低阈值，而是继续原有 structured parser cascade：重复 figure number、caption
不是精确编号引用、图形对象不足、caption/图形间距过大、默认阈值以下或本地渲染失败。
本地结果没有结构化表格，因此 `related_tables=[]`；需要表格交叉验证时可关闭
`local_figure_fast_path`。这一设计优化的是 Figure 视觉入口，不改变 `pdf_parse`、`pdf_qa`
或其他结构化 PDF 工具。

## Parser 公开输入

`parse_structured_async(pdf_path, output_dir=None, config=None)`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pdf_path` | `str | Path` | 输入 PDF/图片/其他文档路径 |
| `output_dir` | `str | Path | None` | parsed.md、JSON、images 目录 |
| `config` | `AgentConfig | None` | provider URL/key、timeout、页数上限、soft hint |

同步 wrapper `parse_pdf()` 在没有 event loop 时直接 `asyncio.run`；已有 loop 时新建单线程 executor，在另一个线程中启动新 event loop。工具通常通过 `asyncio.to_thread(parse_pdf, ...)` 调用。

## 异步入口完整原代码

来源：`src/webagent/parser/cascade.py::parse_structured_async`。输入如上；输出总是 `PDFParseResult`，预期失败不抛异常。

```python
async def parse_structured_async(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: AgentConfig | None = None,
) -> PDFParseResult:
    """Async cascade entry — see module docstring."""
    if config is None:
        from webagent.core.config import AgentConfig

        config = AgentConfig()

    pdf_path = Path(pdf_path)
    out_dir = Path(output_dir) if output_dir else pdf_path.parent
    images_dir = out_dir / IMAGES_SUBDIR

    if not pdf_path.exists():
        return _error_result(out_dir, images_dir, f"file not found: {pdf_path}")

    profile = profile_document(pdf_path)
    if profile.page_count and profile.page_count > config.max_parse_pages:
        return _error_result(
            out_dir,
            images_dir,
            f"document has {profile.page_count} pages, exceeding max_parse_pages={config.max_parse_pages}",
        )

    order = select_parsers(profile, user_hint=config.ocr_provider)
    logger.info(
        "Parser routing for %s: %s (pages=%d avg_chars=%.0f scanned=%s)",
        pdf_path.name,
        order,
        profile.page_count,
        profile.avg_chars_per_page,
        profile.is_likely_scanned,
    )

    timeout = float(config.parser_http_timeout_seconds)
    async with build_client(timeout, config.parser_proxy or None) as client:
        result = await _run_cascade(client, order, pdf_path, profile, out_dir, images_dir, config)
        if result is not None:
            return result
        # All cloud providers failed — last-resort local extraction.
        req = ParseRequest(pdf_path, profile, out_dir, images_dir, config)
        try:
            logger.warning(
                "All cloud parsers failed for %s — falling back to local PyMuPDF", pdf_path.name
            )
            return await _LOCAL.parse(client, req)
        except Exception as exc:
            logger.error("Local fallback failed for %s: %s", pdf_path.name, exc)
            return _error_result(out_dir, images_dir, f"all parsers failed: {exc}")
```

缺文件、页数超限、所有 parser 失败都通过 `result.error` 表达。外部 API 凭证缺失不是全局失败：相应 provider 返回 non-retryable `NOT_CONFIGURED`，cascade 继续。

## Cascade 核心完整原代码

```python
async def _run_cascade(
    client,
    order: tuple[str, ...],
    pdf_path: Path,
    profile: DocumentProfile,
    out_dir: Path,
    images_dir: Path,
    config: AgentConfig,
) -> PDFParseResult | None:
    """Try cloud providers in order. Returns a result, or None if all failed."""
    deadline = time.monotonic() + config.parse_timeout_seconds
    errors: list[ParserProviderError] = []
    req = ParseRequest(pdf_path, profile, out_dir, images_dir, config)

    for name in order:
        provider = _PROVIDERS[name]
        retries = MAX_RETRIES
        while retries >= 0:
            if time.monotonic() > deadline:
                logger.warning("Parse timeout budget exhausted for %s", pdf_path.name)
                return None
            try:
                # Bound the provider to the remaining cascade budget so a single
                # hung provider (e.g. a stuck MinerU poll) can't outlive it.
                remaining = max(1.0, deadline - time.monotonic())
                result = await asyncio.wait_for(provider.parse(client, req), timeout=remaining)
                quality = assess_quality(result, profile)
                if not quality.is_satisfactory:
                    logger.warning(
                        "parser=%s file=%s quality_failed score=%.1f reasons=%s",
                        name,
                        pdf_path.name,
                        quality.score,
                        ";".join(quality.reasons),
                    )
                    errors.append(ParserProviderError(provider=name, retryable=False))
                    break
                logger.info(
                    "parser=%s file=%s parse_ok score=%.2f", name, pdf_path.name, quality.score
                )
                return result
            except TimeoutError:
                logger.warning("parser=%s file=%s timed out (cascade budget)", name, pdf_path.name)
                errors.append(
                    ParserProviderError(
                        provider=name, retryable=False, reason=FailureReason.NETWORK_TIMEOUT
                    )
                )
                break
            except ParserProviderError as ppe:
                if ppe.retryable and retries > 0:
                    retries -= 1
                    # Exponential backoff — a transient ConnectError/5xx often
                    # clears within a couple of seconds (e.g. a proxy hiccup).
                    backoff = min(
                        _RETRY_BASE_DELAY * 2 ** (MAX_RETRIES - retries - 1), _RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "parser=%s retryable failure (%s); retrying in %.1fs (%d left)",
                        name,
                        ppe,
                        backoff,
                        retries,
                    )
                    await asyncio.sleep(backoff)
                    continue
                errors.append(ppe)
                logger.warning("parser=%s file=%s failed: %s", name, pdf_path.name, ppe)
                break

    if errors:
        logger.info(
            "Cloud cascade exhausted for %s: %s", pdf_path.name, AllParsersFailedError(errors)
        )
    return None
```

每个 provider 最多 1 次初始尝试 + 2 次 retry；retryable failure 指数退避约 1.5s、3s，上限 8s。整体 deadline 由 `parse_timeout_seconds` 约束，并把剩余时间传给 `asyncio.wait_for`。

Quality failure 不重试同 provider，而是记录 non-retryable error 并切换下一 provider。所有 cloud 失败后才调用 Local PyMuPDF。

## 路由规则

| 文档画像 | 默认顺序 | 原因 |
|---|---|---|
| 单张图片 | Paddle → Marker | Paddle 作为 layout specialist；MinerU v4 要 PDF |
| PDF 且平均每页字符 < 50 | MinerU → Marker → Paddle | 视为扫描件，优先 OCR |
| 普通文本/混排 PDF | Marker → MinerU → Paddle | 优先结构化 Markdown |
| 其他格式 | Marker → MinerU → Paddle | 保守默认 |

`ocr_provider` 只能把候选中同名 provider 提到第一位，不会禁止 fallback，也不会把 MinerU 加入单图候选。

## Quality Gate 完整原代码

来源：`src/webagent/parser/_quality.py::assess_quality`。

```python
class QualityResult:
    """Quality assessment outcome."""

    is_satisfactory: bool
    score: float  # 0.0–1.0
    reasons: tuple[str, ...]


def result_text(result: PDFParseResult) -> str:
    """Concatenate all extracted text from a parse result."""
    return "\n".join(b.text for b in result.text_blocks if b.text)


def assess_quality(result: PDFParseResult, profile: DocumentProfile) -> QualityResult:
    """Check whether a parsed document meets minimum quality thresholds.

    Designed to catch extraction *failures*, not to grade content.  Scanned /
    image-heavy documents are exempt from volume-based checks.
    """
    if result.error:
        return _fail(f"provider_error:{result.error}", 0.0)

    stripped = result_text(result).strip()

    # A result that produced structured assets (tables/images) but little text
    # is still useful for image/scanned PDFs.
    has_assets = bool(result.tables or result.images)

    if not stripped and not profile.is_likely_scanned and not has_assets:
        return _fail("empty_text", 0.0)

    if profile.is_likely_scanned:
        chars_per_page = len(stripped) / max(1, profile.page_count)
        if chars_per_page < MIN_SCANNED_CHARS_PER_PAGE and not has_assets:
            return _fail(f"scanned_text_too_short({chars_per_page:.1f}/pg)", 0.2)
        return QualityResult(is_satisfactory=True, score=0.8, reasons=())

    reasons: list[str] = []
    score = 0.9

    expected = profile.avg_chars_per_page * profile.page_count
    if expected > 100 and len(stripped) < expected * MIN_TEXT_RATIO:
        reasons.append(f"text_too_short(ratio={len(stripped) / expected:.2f})")
        score -= 0.3

    ctrl = sum(1 for c in stripped if ord(c) < 0x20 and c not in "\n\r\t")
    if len(stripped) > 50 and ctrl / len(stripped) > MAX_CONTROL_CHAR_RATIO:
        reasons.append(f"high_control_chars(ratio={ctrl / len(stripped):.3f})")
        score -= 0.2

    score = max(0.0, score)
    if reasons:
        return QualityResult(is_satisfactory=False, score=score, reasons=tuple(reasons))
    return QualityResult(is_satisfactory=True, score=score, reasons=())


def _fail(reason: str, score: float) -> QualityResult:
    return QualityResult(is_satisfactory=False, score=score, reasons=(reason,))
```

输出 `QualityResult(is_satisfactory: bool, score: float, reasons: tuple[str,...])`。这里的 score 只是手工规则分数，不是模型置信度，也未校准。

普通文本 PDF 检查空文本、相对原文预计字符数是否低于 5%、控制字符比例是否高；扫描件要求每页至少 80 字符，除非提取到了图/表资产。

## 统一输出 Schema

```python
PDFParseResult(
    markdown_path: str | None,
    json_path: str | None,
    images_dir: str,
    output_dir: str,
    method: str = "cascade",
    backend: str | None = None,
    error: str | None = None,
    images: list[ImageInfo],
    tables: list[TableInfo],
    text_blocks: list[TextBlock],
    sections: dict[str, list[TextBlock]],
)
```

`ImageInfo/TableInfo/TextBlock` 均使用 0-based `page_idx` 和 `(x0,y0,x1,y1)` bbox。多数 cloud 映射目前把 bbox 设为 `(0,0,0,0)`；位置查询能力因此只有 provider 真正提供坐标时才有意义。

示例：

```json
{
  "markdown_path": "<run>/artifacts/documents/report-a1b2c3d4e5f6/parsed.md",
  "json_path": "<run>/artifacts/documents/report-a1b2c3d4e5f6/parsed_content_list.json",
  "images_dir": "<run>/artifacts/documents/report-a1b2c3d4e5f6/images",
  "output_dir": "<run>/artifacts/documents/report-a1b2c3d4e5f6",
  "method": "cascade",
  "backend": "marker",
  "error": null,
  "images": [{"path":".../fig1.png","page_idx":2,"bbox":[0,0,0,0],"caption":"Figure 1: Architecture","figure_number":"1"}],
  "tables": [],
  "text_blocks": [{"text":"Introduction","page_idx":0,"bbox":[0,0,0,0],"level":1,"block_type":"title"}],
  "sections": {"1:Introduction": []}
}
```

dataclass 本身不提供 JSON serializer；`write_outputs()` 只在 provider 给出 content list 时写 `parsed_content_list.json`。

## Provider 输入输出

所有 provider 实现 `Provider` structural protocol：`name: str` 与 `async parse(httpx.AsyncClient, ParseRequest) -> PDFParseResult`。`ParseRequest` 是冻结 dataclass，包含 file/profile/output/images/config。

### Datalab Convert（Marker/Chandra）

当前使用官方推荐的 [`POST /api/v1/convert`](https://documentation.datalab.to/api-reference/convert-document)，而不是已废弃的 `/api/v1/marker`。multipart POST 文件和 `output_format=markdown,paginate=true,mode=fast|accurate`；随后轮询响应中的 `request_check_url`；输出 Markdown、page texts 和 base64 images。通过 Markdown 图片 alt 或同页 `Figure N:` 关联 caption；若 provider 生成的 alt 与 PDF 中后出现的同编号独立 caption 冲突，以 PDF caption 为准。为兼容既有配置，provider 名和配置键仍保留 `marker`。

### MinerU

按 [MinerU Precision Extract v4](https://mineru.net/doc/docs/index_en/) 请求 `/file-urls/batch` signed upload URL → PUT 二进制 → 轮询 `/extract-results/batch/{batch_id}` → 下载 zip → 解压 `full.md`、`content_list.json` 和 images。content list 直接映射 text/equation/image/table，结构最丰富。

### PaddleOCR

当前使用[官方异步 Jobs API](https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5)：multipart 上传原始文件并指定 `PP-StructureV3` → 轮询 `jobId` → 下载 `resultUrl.jsonUrl` 的 JSONL → 映射每页 `layoutParsingResults`。鉴权为 `Authorization: Bearer`。旧的 Base64 同步调用已经移除。

### Local PyMuPDF

不做 OCR、图像或表格理解，只读取现有 PDF text layer，按页构造段落。这是保证“至少有文本”的应急降级，不保证扫描 PDF 可用。

## 图表与章节构造

`_build.py` 解析 Markdown heading、paragraph 和 pipe table。标题形成 key `level:title`，正文附到当前 section。Markdown table 转 HTML，因为下游结构表工具使用 HTML parser。Figure caption 规则先找同编号的独立 caption（重复时取最后一个源 caption），否则才采用 alt 中的 figure mention或同页最近 caption。`pdf_analyze_figure` 的视觉调用若抛异常、返回空值、缺少 planner 或图片打不开，会返回失败并提供 caption/页码文本兜底指引，不再以 `vision_analysis=null` 报成功。

## 缓存和工具层

下载的 PDF 默认位于 `artifacts/downloads/`。`get_pdf_extract_dir()` 根据净化后的 source stem 与
文件内容 SHA-256 前 12 位生成 `artifacts/documents/<doc-id>/`，因此同一 run 的多篇文档不会互相
覆盖。cloud provider 的 `parsed.md`、可选 `parsed_content_list.json` 与原始 `images/` 都在该
文档目录内；本地 Figure 快路径写 `figures/local/`，结构化解析器抽出的 Figure 写
`figures/extracted/`。`source_path=None` 的 `documents/default/` 只用于尚未解析出文档身份的兼容
调用。`pdf_qa_tools` 的进程内缓存只存无 error 结果；持久 PDF cache 默认关闭，strict eval 强制
绕过跨运行缓存。

## 限制

- Quality gate 只检测明显失败，不评估公式、阅读顺序、表格结构或 caption 正确率。
- cloud provider 行为和 API schema 会变化；契约单测加一次性线上 smoke test 只能证明验证时刻的兼容性，不能替代持续监控。
- 429 被定义为 non-retryable，cascade 会立即换 provider；这是一种策略选择。
- local fallback 对扫描 PDF 基本无能为力，但仍可能返回空的“成功”结果，且 local 结果没有再次经过 quality gate。
- Paddle 的 JSONL 若缺少 `layoutParsingResults` 会整体失败；当前只把文本与表格映射到统一结构，云端结果图 URL 尚未落盘。
- 同一内容寻址文档目录内的 provider 输出文件名仍固定为 `parsed.*`；重复解析同一内容会更新该
  文档目录，但不同内容 hash 或不同文档 stem 不会互相覆盖。
