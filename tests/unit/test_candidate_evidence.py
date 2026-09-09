"""Latest is candidate evidence, never a query/checklist completion flag."""

import json
from copy import deepcopy

import pytest

from webagent.agent.observations import save_observation
from webagent.core.models import BrowserState, ToolResult
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.evidence_integrity import candidate_failures, has_completed_figure
from webagent.tools.builtin.download_discovery_tools import _publication_dates
from webagent.tools.executor import _qualify_completion
from webagent.tools.policies.candidates import CandidateLedger, document_key, versions
from webagent.tools.search.results import _organic_results
from webagent.tools.search.support import _result_quality_issue

OLD = "https://github.com/Team/Model3.5/blob/main/report.pdf"
NEW = "https://github.com/Team/Model3.8-Flash-Next/blob/main/report.pdf"
KEYWORDS = {"model"}


def test_parameter_sizes_are_not_versions_and_names_are_normalized():
    assert versions("Model-1.8B Model-7B Model1.8 billion", KEYWORDS, include_integer=True) == {}
    assert versions("Model-3.8. Model 3.8-Max", KEYWORDS) == {
        "model3.8": (3, 8),
        "model3.8-max": (3, 8),
    }
    ledger = CandidateLedger()
    ledger.search(
        "models",
        {"results": [{"title": "Model2.5-Old and Model3.8-Max technical report"}]},
        KEYWORDS,
    )
    assert ledger.next_unsearched_variant() == "model3.8-max"
    ledger.query_attempt("Model-3.8-Max report")
    assert ledger.next_unsearched_variant() is None


def test_highest_report_source_prefers_discovered_document_then_stops_after_inspection():
    ledger = CandidateLedger()
    history = "https://github.com/Team/Model3.8-Flash-Next/commits/main/tech_report.pdf"
    blob = "https://github.com/Team/Model3.8-Flash-Next/blob/abc123/tech_report.pdf"
    ledger.search(
        "model reports",
        {
            "results": [
                {"title": "Model3.5 technical report", "url": OLD},
                {"title": "tech_report.pdf", "url": history},
            ]
        },
        KEYWORDS,
    )

    assert ledger.next_report_source() == {
        "name": "model3.8-flash-next",
        "url": history,
        "source": "search_result",
    }

    ledger.inspect(
        {"source_url": history, "candidates": [{"url": blob, "text": "tech_report.pdf"}]},
        official=True,
    )
    assert ledger.next_report_source() == {
        "name": "model3.8-flash-next",
        "url": blob,
        "source": "document",
    }

    ledger.inspect(
        {
            "source_url": blob,
            "candidates": [{"url": blob, "text": "tech_report.pdf"}],
            "date_evidence": [{"datetime": "2026-08-26T12:29:38Z"}],
        },
        official=True,
    )
    assert ledger.next_report_source() is None


def test_report_source_does_not_fall_back_below_external_version_frontier():
    ledger = CandidateLedger()
    ledger.search(
        "model reports",
        {"results": [{"title": "Model3 technical report", "url": OLD}]},
        KEYWORDS,
    )
    ledger.page(
        "https://github.com/Team/Model3.8",
        "Model3.8 release",
        official=True,
        keywords=KEYWORDS,
    )

    assert ledger.next_report_source(minimum_version=(3, 8)) is None


def test_no_selected_target_does_not_call_every_version_newer():
    ledger = CandidateLedger()
    ledger.search("models", {"results": [{"url": NEW}]}, KEYWORDS)
    assert len(ledger.assessment(None, KEYWORDS)["missing"]) == 1


def test_first_named_search_counts_without_inventing_or_double_counting_leads():
    ledger = CandidateLedger()
    query = "Model3.8-Max technical report"
    ledger.query_attempt(query)
    assert not ledger.leads  # A guessed query alone is not release evidence.
    data = {
        "results": [
            {
                "title": "Model3.8-Max technical report",
                "url": "https://third.test/Model3.8-Max",
            }
        ]
    }
    ledger.search(query, data, KEYWORDS)
    assert ledger.leads["model3.8-max"]["query_attempts"] == 1
    ledger.query_attempt(query)
    ledger.search(query, data, KEYWORDS)
    assert ledger.leads["model3.8-max"]["query_attempts"] == 2
    ledger.inspect(_inspection(), official=True)
    assessment = ledger.assessment(NEW, KEYWORDS)
    assert assessment["partial_completion_allowed"]
    assert assessment["status"] == "unresolved"
    assert assessment["missing"]  # A search still does not establish latestness.


def test_candidate_replay_uses_last_selection_without_done():
    steps = [
        {
            "tool": "inspect_download_links",
            "success": True,
            "parameters": {},
            "planner_visible_result": json.dumps(_inspection()),
            "policy": {"selected_candidate_url": NEW, "selected_candidate_identity_endorsed": True},
        }
    ]
    assert not candidate_failures("latest Model PDF", steps)


@pytest.mark.parametrize(
    "data",
    [
        {"found": False},
        {"found": True, "caption": "caption only"},
        {
            "found": True,
            "vision_analysis": "unfinished",
            "vision_metadata": {"finish_reason": "length"},
        },
    ],
)
def test_incomplete_figure_does_not_pass_verifier(data):
    step = {
        "tool": "pdf_analyze_figure",
        "success": True,
        "planner_visible_result": json.dumps(data),
    }
    assert not has_completed_figure([step])
    step["planner_visible_result"] = json.dumps(
        {
            "found": True,
            "vision_analysis": "Complete analysis",
            "vision_metadata": {"finish_reason": "stop"},
        }
    )
    assert has_completed_figure([step])


def _inspection(url=NEW, date="2026-08-26"):
    return {
        "source_url": url,
        "candidates": [{"url": url.replace("/blob/", "/raw/")}],
        "date_evidence": [{"datetime": date}],
    }


def test_repository_date_cannot_be_bound_to_arbitrary_report():
    ledger = CandidateLedger()
    data = _inspection()
    data["source_url"] = "https://github.com/Team/Model3.8-Flash-Next"
    ledger.inspect(data, official=True)
    assert ledger.assessment(NEW, KEYWORDS)["status"] == "unresolved"
    data["source_url"] = OLD
    ledger.inspect(data, official=True)
    assert ledger.assessment(NEW, KEYWORDS)["selected"]["dates"] == []


def test_url_less_pdf_button_binds_preview_date_without_inventing_raw_url():
    ledger = CandidateLedger()
    data = _inspection()
    data["candidates"] = []
    data["download_controls"] = [{"text": "Download raw file"}]
    ledger.inspect(data, official=True)
    result = ledger.assessment(NEW, KEYWORDS)
    assert result["status"] == "supported_within_observed_candidates"
    assert result["selected"]["url"] == NEW


def test_assessments_and_checkpoints_do_not_alias_mutable_ledger_state():
    ledger = CandidateLedger()
    ledger.search("models", {"results": [{"url": NEW}]}, KEYWORDS)
    before = ledger.assessment(NEW, KEYWORDS)
    exported = ledger.export()
    ledger.page(NEW, "", official=True, keywords=KEYWORDS)
    assert before["leads"][0]["status"] == "discovered"
    assert exported["leads"]["model3.8-flash-next"]["status"] == "discovered"


def test_variant_heading_can_bind_generic_official_article_slug():
    ledger = CandidateLedger()
    ledger.page(
        "https://official.test/blog?id=model3.8",
        "Model3.8-Max technical release",
        official=True,
        keywords=KEYWORDS,
    )
    assert ledger.leads["model3.8-max"]["status"] == "official_checked"
    assert versions("Model3-Coder and Model1.5", KEYWORDS, include_integer=True) == {
        "model3-coder": (3,),
        "model1.5": (1, 5),
    }


def test_dated_newer_candidate_blocks_older_even_with_queries_and_visits():
    ledger = CandidateLedger()
    ledger.search("Model3.8", {"results": [{"url": NEW}]}, KEYWORDS)
    ledger.page(NEW, "", official=True, keywords=KEYWORDS)
    ledger.inspect(_inspection(OLD, "2026-02-03"), official=True)
    ledger.inspect(_inspection(), official=True)
    old = ledger.assessment(OLD, KEYWORDS)
    assert len(old["missing"]) == 2
    assert ledger.assessment(NEW, KEYWORDS)["status"] == "supported_within_observed_candidates"


def test_searching_newer_query_with_old_results_does_not_resolve_it():
    ledger = CandidateLedger()
    ledger.search("Model releases", {"results": [{"url": NEW}]}, KEYWORDS)
    ledger.search("Model3.8 technical report", {"results": [{"url": OLD}]}, KEYWORDS)
    assert ledger.leads["model3.8-flash-next"]["status"] == "discovered"
    ledger.page(NEW, "", official=False, keywords=KEYWORDS)
    assert ledger.leads["model3.8-flash-next"]["status"] == "discovered"


def test_official_index_discovers_variants_without_claiming_inspection():
    ledger = CandidateLedger()
    ledger.page(
        "https://official.test/blog",
        "Model3.8-Flash-Next and Model3.8-Max",
        official=True,
        keywords=KEYWORDS,
    )
    assert len(ledger.leads) == 2
    assert {v["status"] for v in ledger.leads.values()} == {"discovered"}
    ledger.inspect(_inspection(), official=True)
    assert "model3.8-max" in " ".join(ledger.assessment(NEW, KEYWORDS)["missing"])
    ledger.page("https://official.test/blog?id=model3.8-max", "", official=True, keywords=KEYWORDS)
    assert not ledger.assessment(NEW, KEYWORDS)["missing"]


def test_unverified_sibling_allows_only_explicit_partial_findings():
    ledger = CandidateLedger()
    ledger.inspect(_inspection(), official=True)
    sibling = "https://third.test/Model3.8-Max-Preview"
    ledger.search(
        "models",
        {"results": [{"title": "Model3.8-Max-Preview technical report", "url": sibling}]},
        KEYWORDS,
    )
    assert not ledger.assessment(NEW, KEYWORDS)["partial_completion_allowed"]
    ledger.query_attempt("Model3.8-Max-Preview technical report")
    assessment = ledger.assessment(NEW, KEYWORDS)
    assert assessment["partial_completion_allowed"] and assessment["missing"]
    original = ToolResult(
        success=True,
        tool_name="done",
        data={"summary": "Findings"},
        audit={"candidate_ledger": assessment},
    )
    result = _qualify_completion(original)
    assert result.data["completion_status"] == "partial"
    assert result.data["summary"].startswith("INCOMPLETE VERIFICATION")
    assert original.data == {"summary": "Findings"}
    assert not ledger.assessment(OLD, KEYWORDS)["partial_completion_allowed"]
    ledger.inspect(_inspection(sibling + "/report.pdf", "2026-09-01"), official=True)
    assert not ledger.assessment(NEW, KEYWORDS)["partial_completion_allowed"]


def test_product_release_without_report_signal_does_not_block_report_selection():
    ledger = CandidateLedger()
    ledger.inspect(_inspection(), official=True)
    ledger.search(
        "Model3.8-Max-0902 official technical report PDF",
        {
            "results": [
                {
                    "title": "Model3.8-Max-0902 released with higher benchmark scores",
                    "url": "https://social.example/Model3.8-Max-0902-release",
                    "snippet": "A product update from a third-party account.",
                }
            ]
        },
        KEYWORDS,
    )

    assessment = ledger.assessment(NEW, KEYWORDS)

    assert assessment["status"] == "supported_within_observed_candidates"
    assert assessment["missing"] == []
    assert "model3.8-max-0902" not in {lead["name"] for lead in assessment["leads"]}


@pytest.mark.parametrize("value", ["unknown", "2026-99-30", "2999-01-01", None])
def test_invalid_or_future_dates_cannot_prove_latest(value):
    ledger = CandidateLedger()
    ledger.inspect(_inspection(date=value), official=True)
    assert ledger.assessment(NEW, KEYWORDS)["missing"]


def test_arxiv_citation_date_is_publication_not_repository_update():
    ledger = CandidateLedger()
    metadata = _publication_dates(
        '<meta name="citation_date" content="2026/08/26"><meta name="date" content="2026-09-01">'
    )
    data = {
        "source_url": "https://arxiv.org/abs/2608.12345",
        "candidates": [{"url": "https://arxiv.org/pdf/2608.12345"}],
        "publication_dates": metadata,
    }
    ledger.inspect(data, official=True)
    selected = ledger.assessment("https://arxiv.org/pdf/2608.12345", set())["selected"]
    assert selected["dates"] == [
        {"value": "2026-08-26", "kind": "report_published", "source": data["source_url"]}
    ]


def test_candidate_checkpoint_round_trip_and_reject_missing_shape():
    ledger = CandidateLedger()
    ledger.inspect(_inspection(), official=True)
    restored = CandidateLedger()
    restored.restore(deepcopy(ledger.export()))
    assert restored.assessment(NEW, KEYWORDS) == ledger.assessment(NEW, KEYWORDS)
    with pytest.raises(ValueError):
        restored.restore({})


def test_versions_and_document_aliases():
    assert versions("Model3.8-Flash-Next", KEYWORDS) == {"model3.8-flash-next": (3, 8)}
    assert document_key(NEW) == document_key(
        "https://raw.githubusercontent.com/Team/Model3.8-Flash-Next/main/report.pdf"
    )
    assert document_key(NEW) == document_key(NEW.replace("blob/main", "raw/refs/heads/main"))


def test_irrelevant_github_and_different_version_serps_fail_quality():
    assert _result_quality_issue(
        "GitHub Qwen technical report",
        [{"title": "GitHub dictionary", "url": "https://dictionary.test/github"}],
    )
    assert _result_quality_issue(
        "Qwen3.8 technical report",
        [{"title": "Qwen3 report", "url": "https://qwen.ai/blog?id=qwen3"}],
    )
    assert (
        _result_quality_issue(
            "Qwen3.8 technical report",
            [{"title": "Qwen3.8 Flash Next", "url": "https://qwen.ai/blog?id=qwen3.8-flash-next"}],
        )
        is None
    )


def test_constraints_must_cooccur_in_one_result():
    results = [
        {"title": "Qwen3 report", "url": "https://github.com/QwenLM/Qwen3"},
        {"title": "Qwen3.8 report", "url": "https://thirdparty.test/report"},
    ]
    assert _result_quality_issue("Qwen3.8 technical report site:github.com", results)


def test_search_image_navigation_is_not_organic_evidence():
    results = [
        {"url": "https://search.yahoo.co.jp/image/search?p=Qwen3.8", "title": "Qwen3.8 images"},
        {"url": "https://qwen.ai/blog?id=qwen3.8", "title": "Qwen3.8"},
    ]
    assert _organic_results(results) == results[1:]


def test_verifier_replays_visible_dates_instead_of_trusting_terminal_flags():
    steps = [
        {
            "tool": "inspect_download_links",
            "success": True,
            "policy": {"selected_candidate_identity_endorsed": True},
            "planner_visible_result": json.dumps(_inspection()),
        },
        {"tool": "done", "success": True, "policy": {"selected_candidate_url": NEW}},
    ]
    assert candidate_failures("latest Model PDF", steps) == []
    steps[0]["planner_visible_result"] = "{}"
    assert candidate_failures("latest Model PDF", steps)


def test_verifier_uses_hash_checked_dom_to_resolve_generic_article_slug(tmp_path):
    page_url = "https://official.test/blog?id=model3.8"
    state = BrowserState(
        url=page_url,
        title="Model",
        timestamp="now",
        dom_summary="Model3.8-Max",
        viewport_context="Model3.8-Max",
        observation_metadata={"status": "complete"},
    )
    pre = save_observation(state, RunLayout.from_root(tmp_path), 1, "pre")
    steps = [
        {
            "tool": "search",
            "success": True,
            "planner_visible_result": json.dumps(
                {"results": [{"title": "Model3.8-Max technical report", "url": page_url}]}
            ),
        },
        {
            "tool": "inspect_download_links",
            "success": True,
            "policy": {"selected_candidate_identity_endorsed": True},
            "planner_visible_result": json.dumps(_inspection()),
        },
        {
            "tool": "done",
            "success": True,
            "policy": {
                "selected_candidate_url": NEW,
                "candidate_ledger": {
                    "leads": [{"status": "official_checked", "checked_url": page_url}]
                },
            },
            "observations": {"pre": pre},
        },
    ]
    assert candidate_failures("latest Model PDF", steps)
    assert candidate_failures("latest Model PDF", steps, tmp_path) == []
    stored = json.loads((tmp_path / pre).read_text())
    stored["dom_summary"] = "tampered"
    (tmp_path / pre).write_text(json.dumps(stored))
    assert candidate_failures("latest Model PDF", steps, tmp_path)
