"""Versioned JSON protocol; structural validation is separate from auditing."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "1.0"
TRUST_LEVELS = {"trusted", "unreviewed", "untrusted"}
SOURCE_STATUSES = {"active", "revoked", "quarantined"}
MAX_INPUT_BYTES = 25 * 1024 * 1024


class ValidationError(ValueError):
    """Input cannot safely be interpreted using this protocol."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_ref(source: dict) -> str:
    return f"{source['source_id']}@{source['revision']}"


def make_source(source_id: str, text: str, revision: str = "1", title: str = "", trust: str = "unreviewed", status: str = "active") -> dict:
    return {"source_id": source_id, "revision": revision, "title": title or source_id,
            "text": text, "sha256": text_hash(text), "trust": trust, "status": status}


def _string(obj: dict, key: str, *, empty: bool = False) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (not empty and not value):
        raise ValidationError(f"{key} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _list(obj: dict, key: str) -> list:
    value = obj.get(key, [])
    if not isinstance(value, list):
        raise ValidationError(f"{key} must be a list")
    return value


def _finite(value: Any, path: str = "input") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError(f"{path}: non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{path}: object keys must be strings")
            _finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")


def ensure_bundle(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("Expected bundle schema_version=1.0")
    _finite(value)
    result = deepcopy(value)
    for key in ("sources", "artifacts", "runs"):
        result[key] = _list(result, key)
    if not isinstance(result.get("metadata", {}), dict):
        raise ValidationError("metadata must be an object")
    result.setdefault("metadata", {})
    for source in result["sources"]:
        if not isinstance(source, dict):
            raise ValidationError("source must be an object")
        for key in ("source_id", "revision", "sha256"):
            _string(source, key)
        if any("@" in source[key] for key in ("source_id", "revision")):
            raise ValidationError("source_id and revision cannot contain @")
        _string(source, "text", empty=True)
        source.setdefault("title", source["source_id"])
        source.setdefault("trust", "unreviewed")
        source.setdefault("status", "active")
        if (not isinstance(source["trust"], str) or not isinstance(source["status"], str)
                or source["trust"] not in TRUST_LEVELS or source["status"] not in SOURCE_STATUSES):
            raise ValidationError("Unknown source trust/status")
    for artifact in result["artifacts"]:
        if not isinstance(artifact, dict):
            raise ValidationError("artifact must be an object")
        _string(artifact, "artifact_id")
        _string(artifact, "kind")
        for key in ("source_refs", "dependencies"):
            refs = _list(artifact, key)
            if not all(isinstance(ref, str) and ref for ref in refs):
                raise ValidationError(f"artifact.{key} must contain nonempty strings")
            artifact[key] = refs
    for run in result["runs"]:
        if not isinstance(run, dict):
            raise ValidationError("run must be an object")
        _string(run, "run_id")
        _string(run, "query", empty=True)
        for key in ("candidates", "context_ids"):
            run[key] = _list(run, key)
        if not all(isinstance(item, dict) for item in run["candidates"]):
            raise ValidationError("run.candidates must contain objects")
        if not all(isinstance(item, str) for item in run["context_ids"]):
            raise ValidationError("run.context_ids must contain strings")
    result.setdefault("bundle_id", digest({k: v for k, v in result.items() if k != "bundle_id"}))
    return result


def parse_json(content: str | bytes) -> Any:
    if isinstance(content, bytes):
        if len(content) > MAX_INPUT_BYTES:
            raise ValidationError("Input exceeds 25 MiB")
        try:
            content = content.decode("utf-8-sig")
        except UnicodeError as error:
            raise ValidationError("Input must be UTF-8") from error
    elif not isinstance(content, str) or len(content.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValidationError("Input must be UTF-8 JSON, at most 25 MiB")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(content, object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValidationError(f"Invalid number: {value}")))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValidationError(f"Invalid JSON: {error}") from error
