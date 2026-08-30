"""Tests for the pure PDF analysis helpers."""

from __future__ import annotations

from webagent.parser.models import PDFParseResult, TextBlock
from webagent.tools.builtin._pdf_analysis import (
    extract_citations,
    extract_metrics_and_results,
    extract_paper_metadata,
    extract_section_hierarchy,
    extract_topics_and_keywords,
    find_figure_mentions,
    find_table_mentions,
    get_section_content,
    parse_table_html,
)


def _result(blocks: list[TextBlock], sections: dict[str, list[TextBlock]] | None = None):
    r = PDFParseResult(markdown_path=None, json_path=None, images_dir="i", output_dir="o")
    r.text_blocks.extend(blocks)
    r.sections.update(sections or {})
    return r


class TestParseTableHtml:
    def test_basic_table(self) -> None:
        parsed = parse_table_html(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        )
        assert parsed["headers"] == ["A", "B"]
        assert parsed["rows"] == [["1", "2"]]
        assert parsed["row_count"] == 1
        assert parsed["col_count"] == 2

    def test_empty_table(self) -> None:
        assert parse_table_html("<table></table>") == {
            "headers": [],
            "rows": [],
            "row_count": 0,
            "col_count": 0,
        }

    def test_cells_are_stripped(self) -> None:
        parsed = parse_table_html("<table><tr><td>  spaced  </td></tr></table>")
        assert parsed["headers"] == ["spaced"]


class TestFindMentions:
    def test_figure_mentions_by_number(self) -> None:
        blocks = [
            TextBlock("As shown in Figure 3, results improve.", 0, (0, 0, 0, 0)),
            TextBlock("Unrelated text.", 1, (0, 0, 0, 0)),
        ]
        mentions = find_figure_mentions(_result(blocks), figure_number="3")
        assert mentions["mention_count"] == 1
        assert mentions["mentions"][0]["page"] == 1

    def test_figure_mention_any_number(self) -> None:
        blocks = [TextBlock("See fig. 7 for details.", 2, (0, 0, 0, 0))]
        mentions = find_figure_mentions(_result(blocks))
        assert mentions["mention_count"] == 1

    def test_figure_number_does_not_match_longer_number_or_captions(self) -> None:
        blocks = [
            TextBlock("Figure 1: Architecture overview.", 0, (0, 0, 0, 0)),
            TextBlock("![Figure 1: Generated alt text.](figure.jpg)", 0, (0, 0, 0, 0)),
            TextBlock("Figure 10 shows the stress test.", 1, (0, 0, 0, 0)),
            TextBlock("As shown in Fig. 1(a), tokens enter the network.", 2, (0, 0, 0, 0)),
        ]
        mentions = find_figure_mentions(_result(blocks), figure_number="1")
        assert mentions["mention_count"] == 1
        assert mentions["mentions"][0]["page"] == 3

    def test_table_mentions(self) -> None:
        blocks = [TextBlock("Table 1 lists baselines.", 0, (0, 0, 0, 0))]
        mentions = find_table_mentions(_result(blocks), table_number="1")
        assert mentions["mention_count"] == 1

    def test_table_number_does_not_match_longer_number_or_caption(self) -> None:
        blocks = [
            TextBlock("Table 1: Main results.", 0, (0, 0, 0, 0)),
            TextBlock("Table 10 contains ablations.", 1, (0, 0, 0, 0)),
            TextBlock("See Table 1 for the comparison.", 2, (0, 0, 0, 0)),
        ]
        mentions = find_table_mentions(_result(blocks), table_number="1")
        assert mentions["mention_count"] == 1
        assert mentions["mentions"][0]["page"] == 3

    def test_no_mentions(self) -> None:
        blocks = [TextBlock("Nothing visual here.", 0, (0, 0, 0, 0))]
        assert find_figure_mentions(_result(blocks), figure_number="9")["mention_count"] == 0


class TestSectionContent:
    def test_section_text_and_subsections(self) -> None:
        intro = [TextBlock("Intro body text.", 0, (0, 0, 0, 0))]
        sub = [TextBlock("Sub detail.", 0, (0, 0, 0, 0), level=2)]
        result = get_section_content(
            _result([], sections={"1:Introduction": intro, "2:Method": sub}),
            "Introduction",
        )
        assert result["text_length"] > 0
        assert result["content"] == "Intro body text."
        assert result["subsection_count"] == 1

    def test_missing_section_returns_error(self) -> None:
        result = get_section_content(_result([]), "Nonexistent")
        assert "error" in result


class TestSectionHierarchy:
    def test_nests_subsections(self) -> None:
        blocks_h1 = [TextBlock("One", 0, (0, 0, 0, 0))]
        blocks_h2 = [TextBlock("Two", 0, (0, 0, 0, 0))]
        result = extract_section_hierarchy(
            _result([], sections={"1:Top": blocks_h1, "2:Child": blocks_h2})
        )
        assert result["total_sections"] == 2
        assert result["hierarchy"][0]["title"] == "Top"
        assert result["hierarchy"][0]["children"][0]["title"] == "Child"


class TestExtractCitations:
    def test_author_year_formats(self) -> None:
        text = "Smith (2020) showed this. Earlier work (Jones et al., 2019) agreed."
        citations = extract_citations(text)
        assert any("Smith (2020)" in c for c in citations)
        assert any("Jones et al., 2019" in c for c in citations)

    def test_numeric_and_doi(self) -> None:
        text = "Methods from [12] and doi:10.1234/abc.def are cited."
        citations = extract_citations(text)
        assert "[12]" in citations
        assert any("doi:" in c for c in citations)


class TestExtractMetrics:
    def test_finds_accuracy_and_improvement(self) -> None:
        blocks = [
            TextBlock(
                "Our accuracy: 94.2 improves over the baseline. improvement: 12% over prior work.",
                0,
                (0, 0, 0, 0),
            )
        ]
        metrics = extract_metrics_and_results(_result(blocks))
        names = {m["metric"] for m in metrics["performance_metrics"]}
        assert "accuracy" in names
        assert "improvement" in names

    def test_comparison_statements(self) -> None:
        blocks = [TextBlock("Our model outperforms BERT on all tasks.", 0, (0, 0, 0, 0))]
        metrics = extract_metrics_and_results(_result(blocks))
        assert metrics["comparison_results"][0]["baseline"] == "BERT"


class TestExtractTopics:
    def test_top_keywords_and_section_topics(self) -> None:
        words_list = ["quantum"] * 10 + ["entanglement"] * 5 + ["filler", "words", "here"]
        blocks = [TextBlock(" ".join(words_list), 0, (0, 0, 0, 0))]
        sections = {"1:Quantum Overview": [TextBlock("q", 0, (0, 0, 0, 0), level=1)]}
        result = extract_topics_and_keywords(_result(blocks, sections), top_n=5)
        words = [k["word"] for k in result["top_keywords"]]
        assert words[0] == "quantum"
        assert result["main_topics"][0]["title"] == "Quantum Overview"


class TestExtractPaperMetadataHelpers:
    def test_abstract_extracted_from_following_paragraphs(self) -> None:
        # The "Abstract" marker must itself be level-0 body text; a level-1
        # heading terminates collection immediately (empty abstract).
        blocks = [
            TextBlock("Abstract: We present a novel method for parsing.", 0, (0, 0, 0, 0)),
            TextBlock("It works well in practice.", 0, (0, 0, 0, 0)),
            TextBlock("Introduction", 0, (0, 0, 0, 0), level=1),
        ]
        md = extract_paper_metadata(_result(blocks))
        assert "novel method" in md["abstract"]

    def test_keywords_extracted(self) -> None:
        blocks = [
            TextBlock("Title Here", 0, (0, 0, 0, 0), level=1),
            TextBlock("Keywords: parsing, retrieval, agents", 0, (0, 0, 0, 0)),
        ]
        md = extract_paper_metadata(_result(blocks))
        assert md["keywords"] == ["parsing", " retrieval", " agents"] or len(md["keywords"]) == 3

    def test_no_first_page_blocks_returns_defaults(self) -> None:
        md = extract_paper_metadata(_result([TextBlock("later page", 1, (0, 0, 0, 0))]))
        assert md == {
            "title": "",
            "authors": [],
            "abstract": "",
            "affiliations": [],
            "keywords": [],
            "date": "",
            "venue": "",
        }
