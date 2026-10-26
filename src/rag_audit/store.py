"""Transactional content-addressed evidence and a checkpointable event chain."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import ValidationError, canonical_json, digest, ensure_bundle, source_ref, text_hash

APPLICATION_ID = 0x52414741
ZERO_HASH = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class EvidenceStore:
    """Evidence is append-only through this API; a full database owner can rewrite it."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables and application != APPLICATION_ID:
                raise ValidationError("Database is not a RAG Audit evidence store")
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS bundles (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_versions (
                    source_ref TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, bundle_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, FOREIGN KEY(bundle_id) REFERENCES bundles(id));
                CREATE TABLE IF NOT EXISTS ledger (
                    seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, object_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(kind, object_id));
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _append(connection, kind: str, key: str, payload: str, timestamp: str) -> None:
        last = connection.execute("SELECT seq, event_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        event = {"seq": last["seq"] + 1 if last else 1, "kind": kind, "object_id": key,
                 "payload_hash": text_hash(payload), "prev_hash": last["event_hash"] if last else ZERO_HASH,
                 "created_at": timestamp}
        connection.execute(
            "INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event["seq"], kind, key, event["payload_hash"], event["prev_hash"], digest(event), timestamp))

    def put_bundle(self, bundle: dict, label: str = "") -> str:
        normalized = ensure_bundle(bundle)
        normalized.pop("bundle_id", None)
        key = digest(normalized)
        normalized["bundle_id"] = key
        payload = canonical_json(normalized)
        timestamp = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            registry_errors = self._registry_errors(connection)
            if registry_errors:
                raise ValidationError("Source registry integrity check failed: " + "; ".join(registry_errors))
            existing = connection.execute("SELECT payload FROM bundles WHERE id=?", (key,)).fetchone()
            if existing:
                if existing["payload"] != payload:
                    raise ValidationError("Stored bundle integrity mismatch")
                return key
            for source in normalized["sources"]:
                ref, actual = source_ref(source), text_hash(source["text"])
                previous = connection.execute("SELECT content_hash FROM source_versions WHERE source_ref=?", (ref,)).fetchone()
                if previous and previous["content_hash"] != actual:
                    raise ValidationError(f"Source revision is immutable: {ref}; use a new revision")
                connection.execute("INSERT OR IGNORE INTO source_versions VALUES (?, ?)", (ref, actual))
            connection.execute("INSERT INTO bundles VALUES (?, ?, ?, ?)", (key, payload, label, timestamp))
            self._append(connection, "bundle", key, payload, timestamp)
        return key

    def put_report(self, report: dict, bundle_id: str) -> str:
        normalized = dict(report)
        normalized.pop("report_id", None)
        normalized["baseline_bundle_id"] = bundle_id
        key = digest(normalized)
        normalized["report_id"] = key
        payload, timestamp = canonical_json(normalized), _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT id FROM bundles WHERE id=?", (bundle_id,)).fetchone():
                raise ValidationError(f"Unknown baseline bundle: {bundle_id}")
            existing = connection.execute("SELECT payload FROM reports WHERE id=?", (key,)).fetchone()
            if existing:
                if existing["payload"] != payload:
                    raise ValidationError("Stored report integrity mismatch")
                return key
            connection.execute("INSERT INTO reports VALUES (?, ?, ?, ?)", (key, payload, bundle_id, timestamp))
            self._append(connection, "report", key, payload, timestamp)
        return key

    def _get(self, kind: str, key: str) -> dict:
        table = {"bundle": "bundles", "report": "reports"}[kind]
        with self._connect() as connection:
            row = connection.execute(f"SELECT payload FROM {table} WHERE id=?", (key,)).fetchone()
            event = connection.execute("SELECT * FROM ledger WHERE kind=? AND object_id=?", (kind, key)).fetchone()
        if not row:
            raise ValidationError(f"Unknown {kind}: {key}")
        if not event or text_hash(row["payload"]) != event["payload_hash"]:
            raise ValidationError(f"Stored {kind} integrity check failed: {key}")
        event_content = {name: event[name] for name in ("seq", "kind", "object_id", "payload_hash", "prev_hash", "created_at")}
        if digest(event_content) != event["event_hash"]:
            raise ValidationError(f"Stored {kind} event integrity check failed: {key}")
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError as error:
            raise ValidationError(f"Stored {kind} integrity check failed: invalid JSON") from error

    def get_bundle(self, bundle_id: str) -> dict:
        return self._get("bundle", bundle_id)

    def get_report(self, report_id: str) -> dict:
        return self._get("report", report_id)

    def list_bundles(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, label, created_at FROM bundles ORDER BY created_at DESC, id").fetchall()
        result = []
        for row in rows:
            bundle = self.get_bundle(row["id"])
            result.append({"bundle_id": row["id"], "label": row["label"], "created_at": row["created_at"],
                           "source_count": len(bundle["sources"]), "run_count": len(bundle["runs"])})
        return result

    def list_reports(self, bundle_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            sql = "SELECT id, bundle_id, created_at FROM reports"
            rows = connection.execute(sql + (" WHERE bundle_id=?" if bundle_id else "") + " ORDER BY created_at DESC, id",
                                      (bundle_id,) if bundle_id else ()).fetchall()
        return [{"report_id": row["id"], "bundle_id": row["bundle_id"], "created_at": row["created_at"]} for row in rows]

    @staticmethod
    def _registry_errors(connection) -> list[str]:
        expected, errors = {}, []
        for row in connection.execute("SELECT payload FROM bundles"):
            try:
                for source in json.loads(row["payload"])["sources"]:
                    ref, checksum = source_ref(source), text_hash(source["text"])
                    if ref in expected and expected[ref] != checksum:
                        errors.append(f"Conflicting source revision: {ref}")
                    expected[ref] = checksum
            except (ValueError, KeyError, TypeError):
                errors.append("Source registry cannot be checked against malformed evidence")
        registered = {row["source_ref"]: row["content_hash"] for row in connection.execute("SELECT * FROM source_versions")}
        for ref in sorted(set(expected) | set(registered)):
            if expected.get(ref) != registered.get(ref):
                errors.append(f"Source registry mismatch: {ref}")
        return errors

    def verify_ledger(self, expected_checkpoint: str | None = None) -> dict:
        errors, previous, seen = [], ZERO_HASH, set()
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
            for expected_seq, row in enumerate(rows, 1):
                event = {name: row[name] for name in ("seq", "kind", "object_id", "payload_hash", "prev_hash", "created_at")}
                if row["seq"] != expected_seq or row["prev_hash"] != previous or digest(event) != row["event_hash"]:
                    errors.append(f"Broken event chain at seq={row['seq']}")
                table = {"bundle": "bundles", "report": "reports"}.get(row["kind"])
                obj = connection.execute(f"SELECT payload FROM {table} WHERE id=?", (row["object_id"],)).fetchone() if table else None
                if not obj or text_hash(obj["payload"]) != row["payload_hash"]:
                    errors.append(f"Object mismatch at seq={row['seq']}")
                else:
                    try:
                        value = json.loads(obj["payload"])
                        identity_key = "bundle_id" if row["kind"] == "bundle" else "report_id"
                        declared_id = value.pop(identity_key, None)
                        if digest(value) != row["object_id"] or declared_id != row["object_id"]:
                            errors.append(f"Content-address mismatch at seq={row['seq']}")
                    except (ValueError, TypeError):
                        errors.append(f"Invalid stored JSON at seq={row['seq']}")
                seen.add((row["kind"], row["object_id"]))
                previous = row["event_hash"]
            for kind, table in (("bundle", "bundles"), ("report", "reports")):
                for row in connection.execute(f"SELECT id FROM {table}"):
                    if (kind, row["id"]) not in seen:
                        errors.append(f"Unlogged {kind}: {row['id']}")
            errors.extend(self._registry_errors(connection))
        if expected_checkpoint is not None and expected_checkpoint != previous:
            errors.append("External checkpoint does not match current ledger tip")
        return {"valid": not errors, "event_count": len(rows), "checkpoint": previous, "errors": errors,
                "scope": "Local hash-chain consistency; authenticity requires a separately retained checkpoint. "
                         "A database owner can rewrite an entire unanchored chain."}
