import json

from rag_audit.cli import main


def test_cli_demo_inspect_replay_verify(tmp_path, capsys):
    db, bundle_path, report = tmp_path / "evidence.sqlite", tmp_path / "bundle.json", tmp_path / "report.json"
    assert main(["demo", "--db", str(db), "--output", str(bundle_path)]) == 0
    capsys.readouterr()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    ref = bundle["sources"][0]["source_id"] + "@" + bundle["sources"][0]["revision"]
    assert main(["inspect", str(bundle_path)]) == 0
    capsys.readouterr()
    assert main(["replay", str(bundle_path), "--db", str(db), "--withdraw", ref, "--output", str(report)]) == 0
    capsys.readouterr()
    assert json.loads(report.read_text(encoding="utf-8"))["validation"]["equivalent_to_full_rebuild"]
    assert main(["verify", "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]


def test_cli_bad_input_is_error_two(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version":"wrong"}', encoding="utf-8")
    assert main(["inspect", str(broken)]) == 2
    assert "error" in capsys.readouterr().err.lower()


def test_cli_preserves_existing_output(tmp_path, capsys):
    target = tmp_path / "protected.json"
    target.write_text("keep", encoding="utf-8")
    assert main(["demo", "--db", str(tmp_path / "case.sqlite"), "--output", str(target)]) == 2
    assert target.read_text() == "keep"


def test_cli_findings_have_distinct_exit_code(tmp_path, capsys):
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps({"schema_version": "1.0", "sources": [{"source_id": "a", "revision": "1", "text": "text", "sha256": "wrong"}], "artifacts": [], "runs": []}), encoding="utf-8")
    assert main(["inspect", str(path)]) == 1


def test_cli_replay_by_stored_id_and_share_summary(tmp_path, capsys):
    db = tmp_path / "evidence.sqlite"
    assert main(["demo", "--db", str(db)]) == 0
    bundle_id = json.loads(capsys.readouterr().out)["bundle_id"]
    assert main(["replay", bundle_id, "--db", str(db), "--withdraw", "copied-note-a@1"]) == 0
    report_id = json.loads(capsys.readouterr().out)["report_id"]
    assert main(["report", report_id, "--db", str(db), "--format", "summary"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["replayable"] is False
    assert summary["contains_original_text"] is False
    assert "copied-note-a" not in json.dumps(summary)


def test_force_output_cannot_overwrite_database_sidecar_or_input(tmp_path, capsys):
    db, evidence = tmp_path / "evidence.sqlite", tmp_path / "input.json"
    assert main(["demo", "--db", str(db), "--output", str(evidence)]) == 0
    original = db.read_bytes()
    for destination in (str(db), str(db) + "-wal", str(evidence)):
        assert main(["inspect", str(evidence), "--db", str(db), "--output", destination, "--force"]) == 2
    assert main(["demo", "--db", str(db), "--output", str(db), "--force"]) == 2
    assert db.read_bytes() == original
    assert main(["verify", "--db", str(db)]) == 0


def test_import_retains_failed_evidence_but_returns_incomplete_status(tmp_path, capsys):
    path, db = tmp_path / "bad.json", tmp_path / "evidence.sqlite"
    path.write_text(json.dumps({"schema_version": "1.0", "sources": [{"source_id": "a", "revision": "1", "text": "x", "sha256": "wrong"}]}), encoding="utf-8")
    assert main(["import", str(path), "--db", str(db)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["audit"]["counts"]["error"] > 0
    assert result["bundle_id"]
    assert main(["list", "--db", str(db)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
