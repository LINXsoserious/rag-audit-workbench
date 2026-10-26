"""来源撤回必须等价于独立完整重建。"""
from copy import deepcopy
import importlib
from itertools import combinations

import pytest

from rag_audit.schema import ValidationError, make_source, source_ref


def modules():
    return importlib.import_module("rag_audit.backend"), importlib.import_module("rag_audit.replay")


def corpus():
    return [make_source("policy", "报销截止日是每月二十五日。费用 receipt deadline."),
            make_source("copy-a", "报销截止日是每月三十日。deadline deadline", trust="untrusted"),
            make_source("copy-b", "报销截止日是每月三十日。deadline deadline", trust="untrusted"),
            make_source("normal", "档案保存 archive retention 为六年。"),
            make_source("off", "无关知识 astronomy orange mountain river")]


def test_every_nonempty_withdrawal_subset_matches_independent_full_rebuild():
    backend, replay_module = modules()
    sources = corpus()
    queries = ["报销截止日 deadline", "archive retention", "不存在的词"]
    original = backend.build_bundle(sources, queries, {"chunk_size": 18, "chunk_overlap": 3, "top_k": 3})
    snapshot = deepcopy(original)
    refs = [source_ref(s) for s in sources]
    for size in range(1, len(refs) + 1):
        for subset in combinations(refs, size):
            report = replay_module.replay(original, list(subset))
            retained = [{**s, "status": "revoked"} if source_ref(s) in subset else s for s in sources]
            expected = backend.build_bundle(retained, queries, original["metadata"]["backend_config"])
            assert report["replayed_bundle"] == expected
            assert report["validation"]["equivalent_to_full_rebuild"] is True
            assert all(q["equivalent"] for q in report["queries"])
            assert all(source_ref(s) not in subset for s in report["replayed_bundle"]["sources"] if s["status"] == "active")
    assert original == snapshot


def test_joint_withdrawal_removes_both_copies_and_preserves_normal_query():
    backend, replay_module = modules()
    bundle = backend.build_bundle(corpus(), ["报销截止日 deadline", "archive retention"], {"top_k": 2})
    report = replay_module.replay(bundle, ["copy-a@1", "copy-b@1"])
    changed, normal = report["queries"]
    assert all(c["source_ref"] not in {"copy-a@1", "copy-b@1"} for c in changed["after"]["candidates"])
    assert "二十五日" in changed["after"]["context_text"]
    assert normal["before"]["context_ids"] == normal["after"]["context_ids"]
    assert report["invalidation"]["recomputed"]["corpus_statistics"] is True
    assert report["invalidation"]["removed_artifact_ids"]
    old_ids = {artifact["artifact_id"] for artifact in bundle["artifacts"]}
    new_ids = {artifact["artifact_id"] for artifact in report["replayed_bundle"]["artifacts"]}
    assert set(report["invalidation"]["removed_artifact_ids"]) == old_ids - new_ids
    assert report["execution"] == "executed"
    assert "answer_recovery_rate" not in report["metrics"]


def test_off_context_source_changes_global_statistics_even_without_query_terms():
    backend, replay_module = modules()
    bundle = backend.build_bundle([make_source("a", "alpha"), make_source("b", "beta beta " + "filler " * 18),
                                   make_source("c", "orange"), make_source("off", "orange " * 400)],
                                  ["alpha beta"], {"chunk_size": 5000, "chunk_overlap": 0, "top_k": 1})
    before = bundle["runs"][0]
    assert all(c["source_ref"] != "off@1" for c in before["candidates"])
    report = replay_module.replay(bundle, ["off@1"])
    after = report["queries"][0]["after"]
    assert before["candidates"][0]["source_ref"] == "b@1"
    assert after["candidates"][0]["source_ref"] == "a@1"
    assert report["queries"][0]["ranking_changed"] is True
    assert before["index_sha256"] != after["index_sha256"]
    assert [c["score"] for c in before["candidates"]] != [c["score"] for c in after["candidates"]]
    assert report["validation"]["equivalent_to_full_rebuild"] is True


@pytest.mark.parametrize("withdrawals", [[], ["unknown@1"], ["policy@1", "policy@1"], "policy@1"])
def test_invalid_withdrawal_requests_are_rejected(withdrawals):
    backend, replay_module = modules()
    with pytest.raises(ValidationError):
        replay_module.replay(backend.build_bundle(corpus(), ["deadline"]), withdrawals)


def test_history_backend_and_revoked_source_and_empty_queries_are_rejected():
    backend, replay_module = modules()
    bundle = backend.build_bundle(corpus(), ["deadline"])
    history = deepcopy(bundle)
    history["runs"][0]["backend"] = "imported-history"
    history["runs"][0]["execution"] = "imported_history"
    with pytest.raises(ValidationError):
        replay_module.replay(history, ["policy@1"])
    with pytest.raises(ValidationError):
        replay_module.replay(bundle, ["policy@1"], [])
    first = replay_module.replay(bundle, ["policy@1"])["replayed_bundle"]
    with pytest.raises(ValidationError):
        replay_module.replay(first, ["policy@1"])


def test_baseline_bundle_id_is_preserved_without_assuming_its_hash_scheme():
    backend, replay_module = modules()
    bundle = backend.build_bundle(corpus(), ["deadline"])
    bundle["bundle_id"] = "store-canonical-id"
    report = replay_module.replay(bundle, ["off@1"])
    assert report["baseline_bundle_id"] == "store-canonical-id"


def test_tampered_index_and_removed_chunk_are_rejected():
    backend, replay_module = modules()
    bundle = backend.build_bundle(corpus(), ["deadline"])
    changed = deepcopy(bundle)
    next(a for a in changed["artifacts"] if a["kind"] == "bm25_index")["metadata"]["document_count"] = 999
    with pytest.raises(ValidationError):
        replay_module.replay(changed, ["off@1"])
    changed = deepcopy(bundle)
    chunk = next(a for a in changed["artifacts"] if a["kind"] == "chunk")
    changed["artifacts"].remove(chunk)
    with pytest.raises(ValidationError):
        replay_module.replay(changed, ["off@1"])


def test_demo_is_self_contained_and_expectations_do_not_enter_backend_inputs():
    demo = importlib.import_module("rag_audit.demo")
    _, replay_module = modules()
    bundle = demo.demo_bundle()
    report = replay_module.replay(bundle, demo.DEMO_WITHDRAWALS)
    assert report["validation"]["equivalent_to_full_rebuild"]
    assert all(run["answer"] is None for run in bundle["runs"])
    assert "ground_truth" not in str(bundle) and "expected_answer" not in str(bundle)
    assert len(demo.DEMO_WITHDRAWALS) >= 2
