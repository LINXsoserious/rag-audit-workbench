"""对本工具执行过的检索做来源撤回，以独立完整重建核对增量失效结果。"""
from __future__ import annotations

from copy import deepcopy
from time import perf_counter

from . import backend
from .schema import SCHEMA_VERSION, ValidationError, digest, source_ref


def _ranking(run: dict) -> list[str]:
    return [candidate["artifact_id"] for candidate in run["candidates"]]


def _structure(bundle: dict) -> dict:
    return {key: bundle[key] for key in ("schema_version", "sources", "artifacts", "runs", "metadata")}


def replay(bundle: dict, withdrawn_refs: list[str], queries: list[str] | None = None) -> dict:
    """撤回完整来源版本；未知、重复、非活动引用及历史后端均明确拒绝。"""
    started = perf_counter()
    baseline, chunks, index, config = backend._validated_native_bundle(bundle)
    if not isinstance(withdrawn_refs, list) or not withdrawn_refs or not all(isinstance(ref, str) and ref for ref in withdrawn_refs):
        raise ValidationError("withdrawn_refs must be a nonempty list of source references")
    if len(set(withdrawn_refs)) != len(withdrawn_refs):
        raise ValidationError("Duplicate withdrawn source reference")
    withdrawn = set(withdrawn_refs)
    active = {source_ref(source) for source in baseline["sources"] if source["status"] == "active"}
    unknown = withdrawn - active
    if unknown:
        raise ValidationError(f"Withdrawal requires active known sources: {', '.join(sorted(unknown))}")
    selected_queries = backend._queries([run["query"] for run in baseline["runs"]] if queries is None else queries)
    if not selected_queries:
        raise ValidationError("Replay requires at least one query")
    sources = deepcopy(baseline["sources"])
    for source in sources:
        if source_ref(source) in withdrawn:
            source["status"] = "revoked"
    retained_chunks = [chunk for chunk in chunks if not withdrawn.intersection(chunk["source_refs"])]
    before_runs = [backend._run(chunks, index, query, config) for query in selected_queries]
    incremental_started = perf_counter()
    incremental = backend._assemble(sources, retained_chunks, selected_queries, config)
    incremental_seconds = perf_counter() - incremental_started
    reference_started = perf_counter()
    # 独立参照只接收来源快照：它重新分块，绝不使用增量分块、索引或排名。
    reference = backend.build_bundle(deepcopy(sources), selected_queries, config)
    reference_seconds = perf_counter() - reference_started
    structures_match = _structure(incremental) == _structure(reference)
    comparisons = []
    for query, before, after, expected in zip(selected_queries, before_runs, incremental["runs"], reference["runs"]):
        comparisons.append({"query": query, "before": before, "after": after, "reference": expected,
                            "equivalent": after == expected,
                            "context_added": [item for item in after["context_ids"] if item not in before["context_ids"]],
                            "context_removed": [item for item in before["context_ids"] if item not in after["context_ids"]],
                            "ranking_changed": _ranking(before) != _ranking(after)})
    direct_invalidations = {artifact["artifact_id"] for artifact in baseline["artifacts"]
                            if artifact["kind"] in {"source", "chunk"} and withdrawn.intersection(artifact["source_refs"])}
    affected = set(direct_invalidations)
    while True:
        expanded = {artifact["artifact_id"] for artifact in baseline["artifacts"]
                    if affected.intersection(artifact["dependencies"])}
        if expanded.issubset(affected):
            break
        affected.update(expanded)
    new_ids = {artifact["artifact_id"] for artifact in incremental["artifacts"]}
    removed = {artifact["artifact_id"] for artifact in baseline["artifacts"]} - new_ids
    equivalent = structures_match and all(item["equivalent"] for item in comparisons)
    count = len(comparisons)
    report = {"schema_version": SCHEMA_VERSION, "execution": "executed",
              "withdrawn_refs": sorted(withdrawn), "baseline_bundle_id": baseline["bundle_id"],
              "replayed_bundle": incremental, "queries": comparisons,
              "validation": {"equivalent_to_full_rebuild": equivalent, "structure_equal": structures_match,
                             "incremental_sha256": digest(_structure(incremental)),
                             "full_rebuild_sha256": digest(_structure(reference)),
                             "reference_method": "rechunk_from_source_snapshots"},
              "invalidation": {"removed_artifact_ids": sorted(removed), "affected_artifact_ids": sorted(affected),
                               "recomputed": {"corpus_statistics": True, "index": True, "ranking": True,
                                              "contexts": True, "full_reference_rechunk": True},
                               "retained_source_refs": sorted(active - withdrawn)},
              "metrics": {"query_count": count, "withdrawn_source_count": len(withdrawn),
                          "retained_chunk_count": len(retained_chunks), "removed_chunk_count": len(chunks) - len(retained_chunks),
                          "ranking_changed_count": sum(item["ranking_changed"] for item in comparisons),
                          "context_changed_count": sum(item["before"]["context_ids"] != item["after"]["context_ids"] for item in comparisons),
                          "empty_context_count": sum(not item["after"]["context_ids"] for item in comparisons),
                          "incremental_seconds": incremental_seconds, "full_rebuild_seconds": reference_seconds,
                          "total_seconds": perf_counter() - started, "generation_calls": 0,
                          "answer_metrics_available": False}}
    return report
