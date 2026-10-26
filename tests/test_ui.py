"""Exercise the real Streamlit page against a temporary evidence database."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from rag_audit.demo import demo_bundle
from rag_audit.service import Workbench

UI_PATH = Path(__file__).resolve().parents[1] / "ui.py"


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_AUDIT_HOME", str(tmp_path))
    return AppTest.from_file(str(UI_PATH), default_timeout=20)


def test_empty_page_and_demo_are_usable(page):
    page.run()
    assert not page.exception
    assert page.title[0].value == "RAG 来源审计工作台"
    page.button(key="load_demo").click().run()
    assert not page.exception
    assert len(page.metric) == 4
    assert page.button(key="execute_replay").disabled is False
    first_id = page.session_state["active_case"]
    page.button(key="load_demo").click().run()
    assert not page.exception
    assert page.session_state["active_case"] == first_id


def test_bad_import_shows_reason_and_preserves_existing_case(page):
    page.run()
    page.button(key="load_demo").click().run()
    bundle_id = page.session_state["active_case"]
    page.text_area(key="pasted_input").set_value("{").run()
    page.button(key="ingest_text").click().run()
    assert not page.exception
    assert any("导入失败" in item.value and "JSON" in item.value for item in page.error)
    assert page.session_state["active_case"] == bundle_id


def test_replay_persists_and_renders_full_rebuild_comparison(page, tmp_path):
    page.run()
    page.button(key="load_demo").click().run()
    bundle_id = page.session_state["active_case"]
    page.button(key="execute_replay").click().run()
    assert not page.exception
    assert any("回放结果与独立完整重建一致" in item.value for item in page.success)
    reports = Workbench(tmp_path / "evidence.sqlite").list_reports(bundle_id)
    assert len(reports) == 1
    report = Workbench(tmp_path / "evidence.sqlite").get_report(reports[0]["report_id"])
    assert report["validation"]["equivalent_to_full_rebuild"] is True
    assert report["queries"]
    assert len(page.get("download_button")) >= 4
    assert any(item.label == "分享摘要（不含原文）" for item in page.get("download_button"))


def test_new_query_replay_selects_new_report(page, tmp_path):
    page.run()
    page.button(key="load_demo").click().run()
    bundle_id = page.session_state["active_case"]
    page.button(key="execute_replay").click().run()
    first_id = page.session_state[f"last_report_{bundle_id}"]["report_id"]
    page.text_area(key=f"queries_{bundle_id}").set_value("全新查询：海岬档案保存多久？").run()
    page.button(key="execute_replay").click().run()
    assert not page.exception
    last_id = page.session_state[f"last_report_{bundle_id}"]["report_id"]
    assert first_id != last_id
    assert page.selectbox(key=f"report_picker_{bundle_id}").value == last_id
    assert len(Workbench(tmp_path / "evidence.sqlite").list_reports(bundle_id)) == 2


def test_imported_history_disables_execution(page, tmp_path):
    bundle = demo_bundle()
    for run in bundle["runs"]:
        run["execution"] = "imported_history"
    bundle.pop("bundle_id", None)
    Workbench(tmp_path / "evidence.sqlite").ingest(bundle, label="历史记录")
    page.run()
    assert not page.exception
    assert page.button(key="execute_replay").disabled is True
    assert any("不能在这里执行源撤回" in item.value for item in page.warning)


def test_event_chain_can_be_verified_from_page(page):
    page.run()
    page.button(key="load_demo").click().run()
    page.button(key="verify_ledger").click().run()
    assert not page.exception
    assert any("事件链验证通过" in item.value for item in page.success)


def test_finding_locates_its_source_and_artifact(page, tmp_path):
    bundle = demo_bundle()
    chunk = next(item for item in bundle["artifacts"] if item["kind"] == "chunk")
    chunk["start"] = 999999
    bundle.pop("bundle_id", None)
    bundle_id = Workbench(tmp_path / "evidence.sqlite").ingest(bundle, label="跨度损坏案件")
    page.run()
    assert not page.exception
    locator = next(button for button in page.button if button.label == "定位这个来源的原文")
    locator.click().run()
    assert not page.exception
    assert page.selectbox(key=f"source_{bundle_id}").value == chunk["source_refs"][0]
    assert page.selectbox(key=f"artifact_{bundle_id}").value == chunk["artifact_id"]
    assert any("没有有效的原文跨度" in item.value for item in page.warning)


def test_raw_source_markup_is_always_escaped():
    spec = importlib.util.spec_from_file_location("audit_ui_helpers", UI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    rendered = module.source_html('<script>alert("x")</script>&原文', 0, 8)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<mark>" in rendered
    assert "&amp;原文" in rendered


def test_many_findings_are_paginated(page, tmp_path):
    bundle = demo_bundle()
    bundle["metadata"]["backend"] = "custom-recording"
    for run in bundle["runs"]:
        run["backend"] = "custom-recording"
    for artifact in bundle["artifacts"]:
        if artifact["kind"] == "bm25_index":
            artifact["kind"] = "custom_index"
    chunk = next(item for item in bundle["artifacts"] if item["kind"] == "chunk")
    bundle["artifacts"].extend({"artifact_id": f"cache-{index}", "kind": "cache", "source_refs": chunk["source_refs"],
                                 "dependencies": [chunk["artifact_id"]], "metadata": {}} for index in range(60))
    key = Workbench(tmp_path / "evidence.sqlite").ingest(bundle)
    page.run()
    assert not page.exception
    assert len([item for item in page.button if item.label == "定位这个来源的原文"]) == 25
    pager = page.number_input(key=f"finding_page_{key}_all_all")
    pager.set_value(3).run()
    assert not page.exception
    assert len([item for item in page.button if item.label == "定位这个来源的原文"]) == 10
    zh_severity = page.selectbox(key=f"finding_severity_{key}").proto.id
    zh_code = page.selectbox(key=f"finding_code_{key}").proto.id
    page.selectbox(key="ui_language").set_value("English").run()
    assert not page.exception
    assert page.number_input(key=f"finding_page_{key}_all_all").value == 3
    assert len([item for item in page.button if item.label == "Locate this source text"]) == 10
    for name, old_id in (("severity", zh_severity), ("code", zh_code)):
        widget = page.selectbox(key=f"finding_{name}_{key}_en")
        assert widget.proto.id != old_id
        assert widget.proto.options[0] == "All"
        assert widget.proto.set_value
        assert widget.proto.raw_value == "All"
        assert widget._widget_state.string_value == "All"
        assert page.session_state[f"settings_finding_{name}_{key}"] == "all"
    page.selectbox(key=f"finding_severity_{key}_en").set_value("warning").run()
    code = page.selectbox(key=f"finding_code_{key}_en").options[1]
    page.selectbox(key=f"finding_code_{key}_en").set_value(code).run()
    page.number_input(key=f"finding_page_{key}_warning_{code}").set_value(3).run()
    page.selectbox(key="ui_language").set_value("简体中文").run()
    assert not page.exception
    assert page.selectbox(key=f"finding_severity_{key}").value == "warning"
    assert page.selectbox(key=f"finding_code_{key}").value == code
    assert page.number_input(key=f"finding_page_{key}_warning_{code}").value == 3
    assert page.selectbox(key=f"finding_severity_{key}").proto.set_value
    assert page.selectbox(key=f"finding_severity_{key}").proto.raw_value == "warning"
    assert page.selectbox(key=f"finding_code_{key}").proto.set_value
    assert page.selectbox(key=f"finding_code_{key}").proto.raw_value == code


def test_english_demo_executes_real_replay_and_downloads(page, tmp_path):
    page.run()
    page.selectbox(key="ui_language").set_value("English").run()
    assert not page.exception
    assert page.title[0].value == "RAG Source Audit Workbench"
    assert page.button(key="load_demo").label == "Load built-in example"
    page.button(key="load_demo").click().run()
    assert not page.exception
    assert [item.label for item in page.metric] == ["Source revisions", "Derived artifacts", "Run records", "Audit findings"]
    page.button(key="execute_replay").click().run()
    assert not page.exception
    assert any(item.value == "Replay matches the independent full rebuild." for item in page.success)
    assert any(item.label == "Sharing summary (no source text)" for item in page.get("download_button"))
    bundle_id = page.session_state["active_case"]
    workbench = Workbench(tmp_path / "evidence.sqlite")
    report = workbench.get_report(page.session_state[f"last_report_{bundle_id}"]["report_id"])
    assert report["validation"]["equivalent_to_full_rebuild"]
    assert report["queries"][0]["query"].startswith("星桥项目")
    assert next(source for source in workbench.get_bundle(bundle_id)["sources"] if source["source_id"] == "policy")["text"].startswith("星桥项目")
    # Raw evidence remains Chinese, while all application-owned visible controls use English.
    for element in [*page.button, *page.text_area, *page.text_input, *page.selectbox, *page.multiselect]:
        assert not any("\u4e00" <= char <= "\u9fff" for char in element.label), element.label


def test_language_switch_preserves_current_case_source_withdrawal_queries_and_report(page, tmp_path):
    page.run()
    page.button(key="load_demo").click().run()
    bundle_id = page.session_state["active_case"]
    workbench = Workbench(tmp_path / "evidence.sqlite")
    another = demo_bundle()
    another.pop("bundle_id", None)
    another["metadata"]["note"] = "Another case to detect an accidental picker reset"
    workbench.ingest(another, label="Another case")
    page.run()
    source_key, withdrawal_key, query_key = f"source_{bundle_id}", f"withdraw_{bundle_id}", f"queries_{bundle_id}"
    page.selectbox(key=source_key).set_value("policy@1").run()
    artifact_id = next(item["artifact_id"] for item in workbench.get_bundle(bundle_id)["artifacts"]
                       if item["kind"] == "chunk" and "policy@1" in item["source_refs"])
    page.selectbox(key=f"artifact_{bundle_id}").set_value(artifact_id).run()
    page.multiselect(key=withdrawal_key).set_value(["copied-note-a@1"]).run()
    page.text_area(key=query_key).set_value("海岬档案 archive retention").run()
    page.button(key="execute_replay").click().run()
    assert not page.exception
    report_id = page.session_state[f"last_report_{bundle_id}"]["report_id"]
    page.text_area(key=query_key).set_value("星桥项目 报销 截止日 deadline").run()
    page.button(key="execute_replay").click().run()
    latest_id = page.session_state[f"last_report_{bundle_id}"]["report_id"]
    assert latest_id != report_id
    page.selectbox(key=f"report_picker_{bundle_id}").set_value(report_id).run()
    original_bundle = workbench.get_bundle(bundle_id)
    original_report = workbench.get_report(report_id)
    original_ledger = workbench.verify_ledger()
    for language in ("English", "简体中文", "English"):
        page.selectbox(key="ui_language").set_value(language).run()
        assert not page.exception
        suffix = "_en" if language == "English" else ""
        assert page.session_state["active_case"] == bundle_id
        assert page.selectbox(key=f"case_picker{suffix}").value == bundle_id
        assert page.selectbox(key=source_key).value == "policy@1"
        assert page.selectbox(key=f"artifact_{bundle_id}{suffix}").value == artifact_id
        assert page.session_state["settings_case_picker"] == bundle_id
        assert page.session_state[f"settings_artifact_{bundle_id}"] == artifact_id
        assert page.multiselect(key=withdrawal_key).value == ["copied-note-a@1"]
        assert page.text_area(key=query_key).value == "星桥项目 报销 截止日 deadline"
        assert page.selectbox(key=f"report_picker_{bundle_id}").value == report_id
        assert page.session_state[f"last_report_{bundle_id}"]["report_id"] == latest_id
        assert len(workbench.list_reports(bundle_id)) == 2
        assert workbench.get_bundle(bundle_id) == original_bundle
        assert workbench.get_report(report_id) == original_report
        assert workbench.verify_ledger() == original_ledger


def test_english_failed_import_and_history_boundary(page, tmp_path):
    bundle = demo_bundle()
    for run in bundle["runs"]:
        run["execution"] = "imported_history"
    bundle.pop("bundle_id", None)
    bundle_id = Workbench(tmp_path / "evidence.sqlite").ingest(bundle, label="Raw case label")
    page.run()
    page.selectbox(key="ui_language").set_value("English").run()
    assert not page.exception
    assert page.button(key="execute_replay").disabled
    assert any("source withdrawal cannot run here" in item.value for item in page.warning)
    page.text_area(key="pasted_input").set_value("{").run()
    page.button(key="ingest_text").click().run()
    assert not page.exception
    assert any("Import failed:" in item.value and "JSON" in item.value for item in page.error)
    assert page.session_state["active_case"] == bundle_id


def test_translated_choices_send_fresh_ids_and_explicit_raw_frontend_values(page):
    page.run()
    page.button(key="load_demo").click().run()
    bundle_id = page.session_state["active_case"]
    source_id = page.selectbox(key=f"source_{bundle_id}").proto.id
    previous_ids = {name: page.selectbox(key=name).proto.id for name in ("case_picker", f"artifact_{bundle_id}")}
    for language, suffix, case_label, artifact_label in (
        ("English", "_en", "Built-in example · Source withdrawal", "(Show full source text)"),
        ("简体中文", "", "内置示例 · 来源撤回", "（显示整篇原文）"),
        ("English", "_en", "Built-in example · Source withdrawal", "(Show full source text)"),
    ):
        page.selectbox(key="ui_language").set_value(language).run()
        assert not page.exception
        assert page.selectbox(key=f"source_{bundle_id}").proto.id == source_id
        expected_labels = {"case_picker": f"{case_label} · {bundle_id[:12]}", f"artifact_{bundle_id}": artifact_label}
        for name, expected in expected_labels.items():
            widget = page.selectbox(key=f"{name}{suffix}")
            assert widget.proto.id != previous_ids[name]
            assert expected in widget.proto.options
            assert widget.proto.set_value
            assert widget.proto.raw_value == expected
            assert widget._widget_state.string_value == expected
            previous_ids[name] = widget.proto.id
        assert page.session_state["settings_case_picker"] == bundle_id
        assert page.session_state[f"settings_artifact_{bundle_id}"] == "__full_source__"
