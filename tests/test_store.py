import json
import sqlite3

import pytest

from rag_audit.schema import ValidationError, make_source
from rag_audit.store import EvidenceStore


def bundle(text="Original evidence", revision="1"):
    return {"schema_version": "1.0", "sources": [make_source("doc", text, revision)],
            "artifacts": [], "runs": [], "metadata": {}}


def test_content_addressed_bundle_deduplicates_and_preserves_original(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    original = bundle()
    first = store.put_bundle(original, "first")
    original["bundle_id"] = "forged-external-id"
    assert store.put_bundle(original, "second") == first
    assert store.get_bundle(first)["sources"][0]["text"] == "Original evidence"
    assert len(store.list_bundles()) == 1
    assert store.verify_ledger()["event_count"] == 1


def test_source_revision_is_immutable_and_failure_is_atomic(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    first = store.put_bundle(bundle())
    with pytest.raises(ValidationError, match="revision"):
        store.put_bundle(bundle("Overwritten evidence"))
    assert len(store.list_bundles()) == 1
    assert store.verify_ledger()["valid"]
    assert store.verify_ledger()["event_count"] == 1
    second = store.put_bundle(bundle("Legitimate update", "2"))
    assert first != second


def test_modified_payload_and_external_checkpoint_are_detected(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = EvidenceStore(path)
    key = store.put_bundle(bundle())
    checkpoint = store.verify_ledger()["checkpoint"]
    assert store.verify_ledger(checkpoint)["valid"]
    assert not store.verify_ledger("0" * 64)["valid"]
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM bundles WHERE id=?", (key,)).fetchone()[0])
        payload["sources"][0]["text"] = "modified"
        connection.execute("UPDATE bundles SET payload=? WHERE id=?", (json.dumps(payload), key))
    assert not store.verify_ledger()["valid"]
    with pytest.raises(ValidationError, match="integrity"):
        store.get_bundle(key)


def test_report_requires_existing_bundle_and_roundtrips(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    with pytest.raises(ValidationError):
        store.put_report({"execution": "executed"}, "missing")
    key = store.put_bundle(bundle())
    report_id = store.put_report({"execution": "executed", "validation": {"equivalent_to_full_rebuild": True}}, key)
    report = store.get_report(report_id)
    assert report["report_id"] == report_id
    assert store.list_reports(key)[0]["report_id"] == report_id
    assert store.verify_ledger()["event_count"] == 2


def test_unlogged_object_or_deleted_ledger_is_detected(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = EvidenceStore(path)
    store.put_bundle(bundle())
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM ledger")
    assert not store.verify_ledger()["valid"]


def test_deleted_registry_cannot_bypass_revision_immutability(tmp_path):
    path = tmp_path / "evidence.sqlite"
    store = EvidenceStore(path)
    store.put_bundle(bundle())
    checkpoint = store.verify_ledger()["checkpoint"]
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM source_versions")
    assert not store.verify_ledger(checkpoint)["valid"]
    with pytest.raises(ValidationError, match="registry integrity"):
        store.put_bundle(bundle("different text"))
    assert len(store.list_bundles()) == 1
