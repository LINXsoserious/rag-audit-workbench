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
    pager = page.number_input(key=f"finding_page_{key}_全部_全部")
    pager.set_value(3).run()
    assert not page.exception
    assert len([item for item in page.button if item.label == "定位这个来源的原文"]) == 10
