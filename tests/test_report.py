import json

from rag_audit.report import render_markdown, sharing_summary


def test_share_export_omits_raw_text_queries_identifiers_and_arbitrary_metadata():
    private = "alice@example.com"
    report = {"baseline_bundle_id": "abc", "withdrawn_refs": [private], "execution": "executed",
              "replayed_bundle": {"sources": [{"text": private}]},
              "queries": [{"query": private, "before": {"context_ids": [private], "context_text": private},
                           "after": {"context_ids": [], "answer": private}, "equivalent": True}],
              "metrics": {"query_count": 1, "unexpected": private}, "validation": {"equivalent_to_full_rebuild": True}}
    result = sharing_summary(report)
    assert private not in json.dumps(result)
    assert not result["replayable"]
    assert not result["contains_original_text"]
    assert result["metrics"] == {"query_count": 1}


def test_markdown_does_not_execute_source_html_or_break_code_fence():
    report = {"execution": "executed", "withdrawn_refs": ["<script>alert(1)</script>"],
              "invalidation": {"ids": ["```\n<img src=x onerror=alert(1)>"]}, "queries": []}
    content = render_markdown(report)
    assert "\\<script\\>" in content
    assert "````json" in content
