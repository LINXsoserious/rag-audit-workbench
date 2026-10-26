from rag_audit.evaluation import benchmark


def test_benchmark_reports_joint_effect_and_utility_without_answer_claim():
    result = benchmark()
    rows = {row["condition"]: row for row in result["conditions"]}
    assert result["subset_equivalence"] == {"checked": 31, "failed": [], "passed": True}
    assert rows["joint_manual_withdrawal"]["expected_evidence_pass_count"] == 3
    assert rows["remove_copy_a"]["expected_evidence_pass_count"] < 3
    assert rows["remove_copy_b"]["expected_evidence_pass_count"] < 3
    assert rows["remove_all_negative_control"]["expected_evidence_pass_count"] < 3
    assert rows["joint_manual_withdrawal"]["normal_answerable_query_pass"]
    assert result["answer_recovery_rate"] is None
    assert result["automatic_detection_false_positive_rate"] is None
    assert result["generation_calls"] == 0
    assert result["legitimate_update_control"]["expected_evidence_pass"]


def test_equal_size_control_and_noncontext_selection_are_explicit():
    result = benchmark()
    assert result["equal_removal_count_control"]["combination_count"] == 10
    assert result["global_selection_control"]["removed_source_absent_from_candidates"]
    assert result["global_selection_control"]["top_context_changed"]
