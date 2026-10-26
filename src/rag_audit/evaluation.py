"""Small controlled evaluation; truth is never passed to retrieval or auditing."""
from __future__ import annotations

from itertools import combinations
from statistics import mean

from .backend import build_bundle
from .demo import DEMO_QUERIES, DEMO_WITHDRAWALS, demo_bundle
from .replay import replay
from .schema import make_source, source_ref


def _measure(run: dict, target: str | None, conflicting: str | None = None) -> dict:
    text = run["context_text"]
    expected = (not run["context_ids"]) if target is None else (target in text and (conflicting is None or conflicting not in text))
    return {"query": run["query"], "expected_evidence_pass": expected,
            "empty_context": not bool(run["context_ids"]), "context_ids": run["context_ids"]}


def benchmark() -> dict:
    base = demo_bundle()
    targets = [("二十五日", "三十日"), ("六年", None), (None, None)]
    refs = [source_ref(source) for source in base["sources"] if source["status"] == "active"]
    conditions = [("no_withdrawal", []), ("remove_copy_a", DEMO_WITHDRAWALS[:1]),
                  ("remove_copy_b", DEMO_WITHDRAWALS[1:]), ("joint_manual_withdrawal", DEMO_WITHDRAWALS),
                  ("remove_all_negative_control", refs)]
    rows = []
    for name, withdrawn in conditions:
        result = replay(base, list(withdrawn)) if withdrawn else None
        runs = [row["after"] for row in result["queries"]] if result else base["runs"]
        measures = [_measure(run, *target) for run, target in zip(runs, targets)]
        rows.append({"condition": name, "withdrawn_refs": list(withdrawn), "queries": measures,
                     "expected_evidence_pass_count": sum(item["expected_evidence_pass"] for item in measures),
                     "query_count": len(measures), "empty_context_count": sum(item["empty_context"] for item in measures),
                     "normal_answerable_query_pass": measures[1]["expected_evidence_pass"],
                     "full_rebuild_equivalent": result["validation"]["equivalent_to_full_rebuild"] if result else None,
                     "metrics": result["metrics"] if result else {"generation_calls": 0}})

    subset_checks, failures = 0, []
    for size in range(1, len(refs) + 1):
        for withdrawn in combinations(refs, size):
            result = replay(base, list(withdrawn))
            subset_checks += 1
            if not result["validation"]["equivalent_to_full_rebuild"]:
                failures.append(list(withdrawn))

    random_control = []
    for withdrawn in combinations(refs, len(DEMO_WITHDRAWALS)):
        result = replay(base, list(withdrawn))
        rate = mean(_measure(row["after"], *target)["expected_evidence_pass"]
                    for row, target in zip(result["queries"], targets))
        random_control.append({"withdrawn_refs": list(withdrawn), "evidence_pass_rate": rate})

    # A legitimate update intentionally changes context. Old-answer restoration is not correctness.
    old = make_source("policy-update-control", "港湾项目 报销 deadline：二十五日。", status="revoked")
    new = make_source("policy-update-control", "港湾项目 报销 deadline：二十八日。", revision="2", trust="trusted")
    updated = build_bundle([old, new], ["港湾 报销 deadline"])
    legitimate = _measure(updated["runs"][0], "二十八日", "二十五日")

    # This source never matches query terms. Its length still changes BM25 normalization.
    global_sources = [make_source("alpha-short", "alpha"), make_source("beta-long", "beta beta " + "filler " * 18),
                      make_source("unrelated-short", "orange"), make_source("orange-global", "orange " * 400)]
    global_bundle = build_bundle(global_sources, ["alpha beta"], {"chunk_size": 10000, "chunk_overlap": 0, "top_k": 1})
    global_result = replay(global_bundle, ["orange-global@1"])
    global_row = global_result["queries"][0]
    absent = all(item["source_ref"] != "orange-global@1" for item in global_row["before"]["candidates"])
    by_condition = {row["condition"]: row for row in rows}
    acceptance_checks = {"all_withdrawal_subsets_equivalent": not failures,
                         "joint_evidence_recovery": by_condition["joint_manual_withdrawal"]["expected_evidence_pass_count"] == 3,
                         "single_removals_do_not_hide_joint_effect": all(by_condition[name]["expected_evidence_pass_count"] < 3 for name in ("remove_copy_a", "remove_copy_b")),
                         "normal_query_retained": by_condition["joint_manual_withdrawal"]["normal_answerable_query_pass"],
                         "remove_all_not_counted_as_recovery": by_condition["remove_all_negative_control"]["expected_evidence_pass_count"] < 3,
                         "legitimate_update_retained": legitimate["expected_evidence_pass"],
                         "off_context_selection_effect": absent and global_row["before"]["context_ids"] != global_row["after"]["context_ids"]}

    return {"schema_version": "1.0", "execution": "executed", "backend": "native-bm25-v1",
            "scope": "Self-authored controlled retrieval cases; not an external benchmark or automatic poisoning detector evaluation.",
            "conditions": rows, "subset_equivalence": {"checked": subset_checks, "failed": failures, "passed": not failures},
            "acceptance": {"passed": all(acceptance_checks.values()), "checks": acceptance_checks},
            "equal_removal_count_control": {"selection": "all combinations, uniform", "source_removal_count": 2,
                                             "combination_count": len(random_control),
                                             "mean_evidence_pass_rate": mean(row["evidence_pass_rate"] for row in random_control),
                                             "results": random_control},
            "legitimate_update_control": legitimate,
            "global_selection_control": {"removed_source_absent_from_candidates": absent,
                                          "top_context_changed": global_row["before"]["context_ids"] != global_row["after"]["context_ids"],
                                          "before": global_row["before"], "after": global_row["after"]},
            "generation_calls": 0, "answer_recovery_rate": None, "automatic_detection_false_positive_rate": None,
            "metric_limits": "Expected evidence is checked against separate hand-authored fixture labels. "
                             "Manual withdrawal is not automatic detection. No LLM answer metrics are available."}
