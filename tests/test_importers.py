import json
from pathlib import Path

import pytest

from rag_audit.audit import audit_bundle
from rag_audit.importers import load_input
from rag_audit.schema import MAX_INPUT_BYTES, ValidationError, ensure_bundle

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_native_json_is_structurally_validated():
    bundle = load_input('{"schema_version":"1.0","sources":[],"artifacts":[],"runs":[]}')
    assert bundle == ensure_bundle(bundle)
    with pytest.raises(ValidationError, match="source"):
        load_input('{"schema_version":"1.0","sources":[{}]}')


def test_m4_maps_documents_spans_edges_paths_and_rule_answer():
    bundle = load_input(fixture("m4_synthetic.jsonl"), "m4.jsonl")
    assert len(bundle["sources"]) == 2
    assert {a["kind"] for a in bundle["artifacts"]} >= {"source", "chunk", "edge", "path"}
    run = bundle["runs"][0]
    assert run["execution"] == "imported_history" and run["backend"] == "m4-import"
    assert run["answer_kind"] == "recorded_rule_decision"
    assert run["answer"] == "东厅"
    assert run["context_text"] is None
    assert bundle["metadata"]["evaluation_only"][0]["evaluation_correct"] is True
    assert "evaluation_correct" not in run
    assert all(source["trust"] == "unreviewed" for source in bundle["sources"])
    assert bundle["metadata"]["import_coverage"]["limitations"]
    assert audit_bundle(bundle)["coverage"]["complete"] is False


def test_m5_preserves_both_recorded_phases_and_missing_baseline_sources():
    bundle = load_input(fixture("m5_synthetic.jsonl"), "m5.jsonl")
    assert len(bundle["runs"]) == 2
    assert {run["metadata"]["phase"] for run in bundle["runs"]} == {"baseline", "counterfactual"}
    assert all(run["execution"] == "imported_history" for run in bundle["runs"])
    assert all(run["backend"] == "m5-import" for run in bundle["runs"])
    baseline = next(run for run in bundle["runs"] if run["metadata"]["phase"] == "baseline")
    assert "documents" in baseline["metadata"]["missing_fields"]
    baseline_edges = [a for a in bundle["artifacts"] if a["metadata"].get("phase") == "baseline"]
    assert baseline_edges and all(not a["source_refs"] for a in baseline_edges)
    intervention = bundle["metadata"]["historical_interventions"][0]
    assert intervention["baseline"]["answer"] == "西厅"
    assert intervention["counterfactual"]["answer"] == "东厅"
    assert intervention["execution"] == "imported_history"


def test_multiple_history_records_have_distinct_stable_ids():
    content = fixture("m4_synthetic.jsonl").strip()
    bundle = load_input(content + "\n" + content, "records.jsonl")
    assert len(bundle["runs"]) == 2
    for collection, key in (("runs", "run_id"), ("artifacts", "artifact_id")):
        values = [item[key] for item in bundle[collection]]
        assert len(values) == len(set(values))
    assert len({source["source_id"] for source in bundle["sources"]}) == 4


@pytest.mark.parametrize("content,match", [
    (b"\xff", "UTF-8"),
    (b"x" * (MAX_INPUT_BYTES + 1), "25 MiB"),
    ("", "empty"),
    ("{}", "Unknown"),
    ("[]", "Unknown"),
    ('{"x":NaN}', "Invalid number"),
    ('{"x":Infinity}', "Invalid number"),
    ('{"x":1e999}', "finite"),
    ('{"schema_version":"1.0"}\n{broken}', "line 2"),
    ('{}\n{}', "line 1"),
], ids=["non_utf8", "oversized", "empty", "unknown_object", "unknown_array", "nan", "infinity", "overflow", "bad_second_line", "empty_record"])
def test_invalid_inputs_fail_with_readable_reason(content, match):
    with pytest.raises(ValidationError, match=match):
        load_input(content, "records.jsonl")


def test_unknown_and_empty_records_inside_jsonl_are_rejected():
    good = fixture("m4_synthetic.jsonl").strip()
    for tail in ("{}", "null", "[]", ""):
        content = good + "\n" + tail + "\n"
        with pytest.raises(ValidationError, match="line 2"):
            load_input(content, "records.jsonl")


def test_malformed_history_fields_are_not_silently_dropped():
    record = json.loads(fixture("m4_synthetic.jsonl"))
    record["decision_input"]["documents"] = "not a list"
    with pytest.raises(ValidationError, match="documents"):
        load_input(json.dumps(record))


def test_missing_historical_answer_is_unknown_and_not_generated():
    record = json.loads(fixture("m4_synthetic.jsonl"))
    del record["answer"]
    run = load_input(json.dumps(record))["runs"][0]
    assert run["answer"] is None
    assert run["answer_kind"] == "unknown"


def test_utf8_bom_and_chinese_unicode_spans_are_preserved():
    bundle = load_input(b"\xef\xbb\xbf" + fixture("m4_synthetic.jsonl").encode("utf-8"))
    edge = next(a for a in bundle["artifacts"] if a["kind"] == "edge")
    source = next(s for s in bundle["sources"] if edge["source_refs"] == [s["source_id"] + "@" + s["revision"]])
    assert source["text"][edge["start"]:edge["end"]] == edge["text"]


@pytest.mark.parametrize("field", ["documents", "edges", "paths", "retrieval"])
def test_explicit_null_historical_collections_are_rejected(field):
    record = json.loads(fixture("m4_synthetic.jsonl"))
    record["decision_input"][field] = None
    with pytest.raises(ValidationError, match=field):
        load_input(json.dumps(record))


def test_duplicate_json_keys_and_invalid_unicode_are_rejected():
    with pytest.raises(ValidationError, match="Duplicate JSON key"):
        load_input('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(ValidationError, match="UTF-8"):
        load_input('{"x":"\\ud800"}')


def test_synthetic_m4_has_consistent_recorded_spans_and_dependencies():
    report = audit_bundle(load_input(fixture("m4_synthetic.jsonl")))
    assert report["counts"]["error"] == 0


def test_invalid_unicode_key_and_excessive_nesting_have_readable_errors():
    with pytest.raises(ValidationError, match="UTF-8"):
        load_input('{"schema_version":"1.0","metadata":{"\\ud800":1}}')
    nested = '{"schema_version":"1.0","metadata":{"nested":' + '[' * 600 + '0' + ']' * 600 + '}}'
    with pytest.raises(ValidationError, match="nesting"):
        load_input(nested)
