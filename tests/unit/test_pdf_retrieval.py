"""Tests for context-aware PDF retrieval scoring and assembly."""

from __future__ import annotations

from pathlib import Path

from webagent.parser.models import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
)
from webagent.tools.builtin._pdf_retrieval import (
    compute_relevance_score,
    figure_sort_key,
    retrieve_relevant_sections,
    split_text_into_chunks,
)


def _result(
    blocks: list[TextBlock] | None = None,
    images: list[ImageInfo] | None = None,
    tables: list[TableInfo] | None = None,
    markdown_path: str | None = None,
) -> PDFParseResult:
    r = PDFParseResult(markdown_path=markdown_path, json_path=None, images_dir="i", output_dir="o")
    r.text_blocks.extend(blocks or [])
    r.images.extend(images or [])
    r.tables.extend(tables or [])
    return r


class TestFigureSortKey:
    def test_numeric_ordering(self) -> None:
        assert sorted(["10", "2", "1"], key=figure_sort_key) == ["1", "2", "10"]

    def test_letter_suffixes(self) -> None:
        assert sorted(["3b", "3a", "3"], key=figure_sort_key) == ["3", "3a", "3b"]

    def test_non_numeric_sorts_last(self) -> None:
        assert figure_sort_key("x")[0] == 10**9


class TestSplitTextIntoChunks:
    def test_short_text_is_single_chunk(self) -> None:
        assert split_text_into_chunks("hello world", max_chars=50) == ["hello world"]

    def test_splits_at_sentence_boundary(self) -> None:
        text = ("First sentence here. " * 20).strip()
        chunks = split_text_into_chunks(text, max_chars=60, overlap=10)
        assert len(chunks) > 1
        # Break points may be searched up to 50 chars past the limit.
        assert all(len(c) <= 115 for c in chunks)
        assert all(c for c in chunks)

    def test_overlap_between_chunks(self) -> None:
        text = "word " * 100
        chunks = split_text_into_chunks(text, max_chars=50, overlap=10)
        assert len(chunks) > 1


class TestComputeRelevanceScore:
    def test_matching_word_scores_positive(self) -> None:
        score = compute_relevance_score("the transformer architecture", "transformer")
        assert score > 0

    def test_stop_words_ignored(self) -> None:
        assert compute_relevance_score("any chunk text", "the a of") == 0.0

    def test_repeated_word_scores_higher(self) -> None:
        one = compute_relevance_score("cat once", "cat")
        many = compute_relevance_score("cat cat cat cat", "cat")
        assert many > one

    def test_consecutive_word_bonus(self) -> None:
        separate = compute_relevance_score("neural networks", "networks neural")
        consecutive = compute_relevance_score("neural networks", "neural networks")
        assert consecutive > separate


class TestRetrieveRelevantSections:
    def test_ranks_relevant_block_first(self) -> None:
        blocks = [
            TextBlock("irrelevant filler about cooking", 0, (0, 0, 0, 0)),
            TextBlock("The transformer architecture uses attention", 1, (0, 0, 0, 0)),
        ]
        result = retrieve_relevant_sections(_result(blocks), "transformer attention")
        assert "transformer architecture" in result["context"]
        assert result["sources"][0]["page"] == 2

    def test_no_blocks_falls_back_to_markdown(self, tmp_path: Path) -> None:
        md = tmp_path / "doc.md"
        md.write_text("Fallback content about transformers here.", encoding="utf-8")
        result = retrieve_relevant_sections(_result(markdown_path=str(md)), "transformers")
        assert "Fallback content" in result["context"]

    def test_no_blocks_and_no_markdown_returns_empty(self) -> None:
        result = retrieve_relevant_sections(_result(), "anything")
        assert result == {
            "context": "",
            "sources": [],
            "found_figures": [],
            "found_tables": [],
        }

    def test_context_char_budget(self) -> None:
        blocks = [TextBlock("transformer " + "x" * 4000, i, (0, 0, 0, 0)) for i in range(3)]
        result = retrieve_relevant_sections(_result(blocks), "transformer", max_context_chars=1000)
        assert len(result["context"]) <= 1100  # 1000 + separator/truncation slack

    def test_figure_number_lookup(self) -> None:
        blocks = [TextBlock("text mentions transformer", 0, (0, 0, 0, 0))]
        images = [
            ImageInfo("/tmp/a.png", 0, (0, 0, 0, 0), caption="Model overview", figure_number="1"),
            ImageInfo("/tmp/b.png", 1, (0, 0, 0, 0), caption="Other", figure_number="2"),
        ]
        result = retrieve_relevant_sections(_result(blocks, images=images), "Figure 1")
        assert [f["figure_number"] for f in result["found_figures"]] == ["1"]

    def test_figure_keyword_lookup(self) -> None:
        blocks = [TextBlock("text", 0, (0, 0, 0, 0))]
        images = [
            ImageInfo("/tmp/a.png", 0, (0, 0, 0, 0), caption="architecture diagram"),
        ]
        # Gate keyword ("chart") opens figure lookup; the last word ("architecture")
        # is searched against captions.
        result = retrieve_relevant_sections(
            _result(blocks, images=images), "chart about architecture"
        )
        assert len(result["found_figures"]) == 1

    def test_table_number_lookup(self) -> None:
        blocks = [TextBlock("text", 0, (0, 0, 0, 0))]
        tables = [
            TableInfo("/tmp/t.html", 0, (0, 0, 0, 0), caption="Results", table_number="2"),
        ]
        result = retrieve_relevant_sections(_result(blocks, tables=tables), "Table 2")
        assert [t["table_number"] for t in result["found_tables"]] == ["2"]

    def test_no_figure_keywords_no_lookup(self) -> None:
        blocks = [TextBlock("text", 0, (0, 0, 0, 0))]
        images = [ImageInfo("/tmp/a.png", 0, (0, 0, 0, 0), caption="chart")]
        result = retrieve_relevant_sections(_result(blocks, images=images), "introduction")
        assert result["found_figures"] == []
