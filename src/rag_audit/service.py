"""Shared operations for Python, command-line and browser clients."""
from __future__ import annotations

from pathlib import Path

from .schema import ValidationError
from .store import EvidenceStore


class Workbench:
    def __init__(self, db_path: str | Path):
        self.store = EvidenceStore(db_path)

    def ingest(self, bundle: dict, label: str = "") -> str:
        return self.store.put_bundle(bundle, label)

    def list_bundles(self) -> list[dict]:
        return self.store.list_bundles()

    def get_bundle(self, bundle_id: str) -> dict:
        return self.store.get_bundle(bundle_id)

    def audit(self, bundle_id: str) -> dict:
        from .audit import audit_bundle
        return audit_bundle(self.get_bundle(bundle_id))

    def replay(self, bundle_id: str, withdrawn_refs: list[str], queries: list[str] | None = None) -> dict:
        from .audit import audit_bundle
        from .replay import replay
        bundle = self.get_bundle(bundle_id)
        findings = audit_bundle(bundle)
        if findings["counts"].get("error", 0):
            codes = sorted({item["code"] for item in findings["findings"] if item["severity"] == "error"})
            raise ValidationError("Fix evidence integrity errors before replay: " + ", ".join(codes))
        report = replay(bundle, withdrawn_refs, queries)
        report["audit_before"] = findings
        report["audit_after"] = audit_bundle(report["replayed_bundle"])
        if report["audit_after"]["counts"].get("error", 0):
            raise ValidationError("Replay produced an invalid evidence bundle")
        report_id = self.store.put_report(report, bundle_id)
        return self.store.get_report(report_id)

    def list_reports(self, bundle_id: str | None = None) -> list[dict]:
        return self.store.list_reports(bundle_id)

    def get_report(self, report_id: str) -> dict:
        return self.store.get_report(report_id)

    def verify_ledger(self, expected_checkpoint: str | None = None) -> dict:
        return self.store.verify_ledger(expected_checkpoint)
