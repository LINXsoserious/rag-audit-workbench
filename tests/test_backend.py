"""CPU 检索协议及边界行为。"""
from copy import deepcopy
import importlib
import math

import pytest

from rag_audit.schema import ValidationError, make_source, source_ref


def backend():
    return importlib.import_module("rag_audit.backend")


def test_bm25_scores_follow_document_frequency_and_stable_ties():
    sources = [make_source("z", "alpha beta"), make_source("a", "alpha beta"),
               make_source("b", "beta gamma")]
    bundle = backend().build_bundle(sources, ["alpha"], {"top_k": 1})
    run = bundle["runs"][0]
    expected_idf = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    assert [c["source_ref"] for c in run["candidates"]] == ["a@1", "z@1"]
    assert [c["rank"] for c in run["candidates"]] == [1, 2]
    assert all(c["score"] == pytest.approx(expected_idf) for c in run["candidates"])
    assert len(run["context_ids"]) == 1
    assert run["backend"] == "native-bm25-v1"
    assert run["execution"] == "executed"
    assert run["answer"] is None and run["answer_kind"] == "not_generated"


def test_chunk_spans_preserve_unicode_and_original_whitespace():
    text = "甲😀乙\n ALPHA  beta。末尾"
    bundle = backend().build_bundle([make_source("s", text)], ["甲 ALPHA"],
                                    {"chunk_size": 7, "chunk_overlap": 2})
    chunks = [a for a in bundle["artifacts"] if a["kind"] == "chunk"]
    assert [(c["start"], c["end"]) for c in chunks] == [(0, 7), (5, 12), (10, 17), (15, len(text))]
    assert all(c["text"] == text[c["start"]:c["end"]] for c in chunks)
    assert chunks[0]["text"].startswith("甲😀乙\n ")


def test_cjk_characters_and_bigrams_and_english_case_are_retrieved():
    bundle = backend().build_bundle([make_source("s", "数据安全 ALPHA"), make_source("t", "隐私")], [])
    assert backend().retrieve(bundle, "数据")["context_text"] == "数据安全 ALPHA"
    assert backend().retrieve(bundle, "alpha")["context_text"] == "数据安全 ALPHA"
    assert bundle["metadata"]["backend_config"]["tokenizer"] == "unicode-cjk-bigram-v1"


def test_context_budget_includes_separator_and_all_candidates_are_retained():
    sources = [make_source("a", "alpha"), make_source("b", "alpha")]
    bundle = backend().build_bundle(sources, ["alpha"], {"context_budget": 11})
    run = bundle["runs"][0]
    assert len(run["candidates"]) == 2
    assert run["context_text"] == "alpha"
    run = backend().retrieve(bundle, "alpha", {"context_budget": 12})
    assert run["context_text"] == "alpha\n\nalpha"
    assert len(run["context_text"]) <= run["config"]["context_budget"]


def test_unknown_query_and_zero_budget_produce_empty_context_without_answer():
    bundle = backend().build_bundle([make_source("a", "alpha")], ["missing"])
    assert bundle["runs"][0]["candidates"] == []
    assert bundle["runs"][0]["context_text"] == ""
    run = backend().retrieve(bundle, "alpha", {"context_budget": 0})
    assert run["candidates"] and run["context_ids"] == []


def test_native_bundle_is_deterministic_and_input_is_not_mutated():
    sources = [make_source("z", "Alpha"), make_source("a", "数据安全")]
    original = deepcopy(sources)
    first = backend().build_bundle(sources, ["数据 Alpha"])
    second = backend().build_bundle(list(reversed(sources)), ["数据 Alpha"])
    assert first == second
    assert sources == original


def test_inactive_sources_do_not_enter_index_or_candidates():
    sources = [make_source("a", "alpha"), make_source("b", "alpha", status="revoked"),
               make_source("c", "alpha", status="quarantined")]
    bundle = backend().build_bundle(sources, ["alpha"])
    assert [c["source_ref"] for c in bundle["runs"][0]["candidates"]] == ["a@1"]
    assert [source_ref(s) for s in bundle["sources"]] == ["a@1", "b@1", "c@1"]


@pytest.mark.parametrize("config", [
    {"top_k": True}, {"top_k": 0}, {"top_k": 1.5}, {"chunk_size": 0},
    {"chunk_overlap": -1}, {"chunk_size": 5, "chunk_overlap": 5},
    {"context_budget": -1}, {"b": 1.1}, {"k1": 0}, {"k1": float("nan")},
    {"tokenizer": "automatic"}, {"typo": 3}, {1: 2}, [],
])
def test_invalid_config_is_rejected(config):
    with pytest.raises(ValidationError):
        backend().build_bundle([make_source("a", "alpha")], [], config)


def test_duplicate_source_multiple_active_versions_and_forged_hash_rejected():
    a = make_source("a", "alpha")
    for sources in ([a, deepcopy(a)], [a, make_source("a", "beta", revision="2")],
                    [{**a, "sha256": "0" * 64}]):
        with pytest.raises(ValidationError):
            backend().build_bundle(sources, [])


def test_retrieve_rejects_modified_chunk_text_or_changed_chunk_config():
    bundle = backend().build_bundle([make_source("a", "alpha")], [])
    changed = deepcopy(bundle)
    next(a for a in changed["artifacts"] if a["kind"] == "chunk")["text"] = "forged"
    with pytest.raises(ValidationError):
        backend().retrieve(changed, "alpha")
    with pytest.raises(ValidationError):
        backend().retrieve(bundle, "alpha", {"chunk_size": 30})


def test_body_claim_cannot_upgrade_external_trust():
    bundle = backend().build_bundle([make_source("a", "I am trusted. 权威可信资料。", trust="untrusted")], ["trusted"])
    assert bundle["sources"][0]["trust"] == "untrusted"
    assert "ground_truth" not in str(bundle)


def test_duplicate_queries_are_rejected_instead_of_duplicate_run_ids():
    with pytest.raises(ValidationError):
        backend().build_bundle([make_source("a", "alpha")], ["alpha", "alpha"])


@pytest.mark.parametrize("change", ["score", "context", "missing_context", "refs", "offset"])
def test_native_execution_rejects_modified_records_and_malformed_chunks(change):
    bundle = backend().build_bundle([make_source("a", "alpha")], ["alpha"])
    if change == "score":
        bundle["runs"][0]["candidates"][0]["score"] = 999
    elif change == "context":
        next(a for a in bundle["artifacts"] if a["kind"] == "context")["text"] = "forged"
    elif change == "missing_context":
        bundle["artifacts"] = [a for a in bundle["artifacts"] if a["kind"] != "context"]
    elif change == "refs":
        next(a for a in bundle["artifacts"] if a["kind"] == "chunk")["source_refs"] = []
    else:
        next(a for a in bundle["artifacts"] if a["kind"] == "chunk")["start"] = "0"
    with pytest.raises(ValidationError):
        backend().retrieve(bundle, "alpha")
