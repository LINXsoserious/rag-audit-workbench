from copy import deepcopy

import pytest

from rag_audit.backend import build_bundle
from rag_audit.schema import ValidationError, make_source
from rag_audit.service import Workbench


def test_full_case_ingest_audit_replay_and_export(tmp_path):
    app = Workbench(tmp_path / "case.sqlite")
    bundle = build_bundle([make_source("a", "Project Helios retention is thirty days."),
                           make_source("b", "Project Helios retention is nine hundred days.")],
                          ["Helios retention"], {"top_k": 1})
    key = app.ingest(bundle, "first case")
    assert app.audit(key)["counts"]["error"] == 0
    report = app.replay(key, ["b@1"])
    assert report["validation"]["equivalent_to_full_rebuild"]
    assert report["audit_after"]["counts"]["error"] == 0
    assert report["execution"] == "executed"
    assert app.get_report(report["report_id"]) == report
    assert len(app.get_bundle(key)["sources"]) == 2
    assert app.verify_ledger()["event_count"] == 2


def test_replay_blocks_tampered_evidence(tmp_path):
    app = Workbench(tmp_path / "case.sqlite")
    bundle = build_bundle([make_source("a", "Helios retention is thirty days.")], ["Helios"])
    bad = deepcopy(bundle)
    bad["sources"][0]["sha256"] = "forged"
    key = app.ingest(bad)
    with pytest.raises(ValidationError, match="integrity"):
        app.replay(key, ["a@1"])


def test_failed_replay_does_not_write_report(tmp_path):
    app = Workbench(tmp_path / "case.sqlite")
    key = app.ingest(build_bundle([make_source("a", "Helios")], ["Helios"]))
    with pytest.raises(ValueError):
        app.replay(key, ["does-not-exist@1"])
    assert app.list_reports(key) == []
