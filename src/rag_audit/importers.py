"""Strict native JSON and read-only M4/M5 historical JSONL import adapters.

Historical edge/path rule decisions are recorded observations. Importing does
not rerun a retriever, reconstruct an unrecorded source, or invoke an LLM.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from urllib.parse import quote

from .schema import MAX_INPUT_BYTES, ValidationError, digest, ensure_bundle, make_source, source_ref


def _decode(content: str | bytes) -> str:
    if isinstance(content, bytes):
        if len(content) > MAX_INPUT_BYTES:
            raise ValidationError("Input exceeds 25 MiB")
        try:
            return content.decode("utf-8-sig")
        except UnicodeError as error:
            raise ValidationError("Input must be UTF-8") from error
    if not isinstance(content, str):
        raise ValidationError("Input must be UTF-8 text or bytes")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeError as error:
        raise ValidationError("Input must be valid UTF-8 text") from error
    if size > MAX_INPUT_BYTES:
        raise ValidationError("Input exceeds 25 MiB")
    return content.removeprefix("\ufeff")


def _json(content: str):
    def constant(value):
        raise ValidationError(f"Invalid number: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        result = json.loads(content, parse_constant=constant, object_pairs_hook=pairs)
        pending = [(result, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 128:
                raise ValidationError("JSON nesting exceeds 128 levels")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValidationError("Input contains a non-finite number")
            if isinstance(value, str):
                try:
                    value.encode("utf-8")
                except UnicodeError as error:
                    raise ValidationError("JSON strings must contain valid UTF-8 characters") from error
            elif isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
                pending.extend((key, depth + 1) for key in value)
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return result
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValidationError(f"Invalid JSON: {error}") from error


def _history_kind(record):
    if not isinstance(record, dict) or not record:
        return None
    if "baseline" in record and "counterfactual" in record:
        return "m5"
    if "decision_input" in record and "question_id" in record:
        return "m4"
    return None


def _objects(value, field):
    if not isinstance(value, list) or not all(isinstance(item, dict) and item for item in value):
        raise ValidationError(f"{field} must be a list of nonempty objects")
    return value


def _strings(value, field):
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValidationError(f"{field} must be a list of nonempty strings")
    return value


def _id(value, field):
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a nonempty string")
    return quote(value, safe="")


def _evaluation(value):
    """Keep labels outside evidence used by deterministic audit checks."""
    return {key: deepcopy(item) for key, item in value.items()
            if key in {"evaluation_correct", "evaluation_truth"} or key.startswith("evaluation_")}


def _without_evaluation(value):
    if isinstance(value, dict):
        return {key: _without_evaluation(item) for key, item in value.items()
                if not key.startswith("evaluation_")}
    if isinstance(value, list):
        return [_without_evaluation(item) for item in value]
    return deepcopy(value)


def _phase(bundle, record, payload, kind, prefix, phase, query_hint=""):
    if not isinstance(payload, dict) or not payload:
        raise ValidationError(f"{phase} must be a nonempty object")
    decision = payload.get("decision_input", {})
    if not isinstance(decision, dict):
        raise ValidationError("decision_input must be an object")
    documents = _objects(decision.get("documents", []), "decision_input.documents")
    edges = _objects(decision.get("edges", payload.get("edges", [])), "edges")
    paths = _objects(decision.get("paths", []), "paths")
    retrieval = decision.get("retrieval", {})
    if not isinstance(retrieval, dict):
        raise ValidationError("decision_input.retrieval must be an object")
    run_id = f"{prefix}:{phase}:run"
    scope = f"{prefix}:{phase}"
    missing_fields = [field for field in ("documents", "edges", "paths", "retrieval") if field not in decision]
    # M4/M5 do not record the exact submitted generator context or a portable
    # index/configuration hash. IDs are provenance, never a substitute for it.
    missing_fields.extend(["submitted_context", "index_sha256", "replay_config"])
    source_ids, source_artifact_ids = {}, {}
    sentence_artifacts, edge_ids = {}, {}
    metadata = {"format": kind, "phase": phase, "record_index": record["_record_index"]}

    for document in documents:
        document_id = document.get("document_id")
        encoded = _id(document_id, "document.document_id")
        text = document.get("text")
        if not isinstance(text, str):
            raise ValidationError("document.text must be a string")
        source = make_source(f"{scope}:document:{encoded}", text, revision="recorded",
                             title=document_id, trust="unreviewed")
        source["metadata"] = {**metadata, "original_document_id": document_id,
                              "origin_id": document.get("origin_id"), "hash_origin": "computed_at_import"}
        ref = source_ref(source)
        bundle["sources"].append(source)
        source_ids[document_id] = ref
        artifact_id = f"{scope}:source:{encoded}"
        source_artifact_ids[document_id] = artifact_id
        bundle["artifacts"].append({"artifact_id": artifact_id, "kind": "source", "source_refs": [ref],
                                    "dependencies": [], "text": text, "metadata": {**metadata, "original_document_id": document_id}})

    def make_chunk(sentence_id, document_id, start, end, text=None):
        key = (document_id, sentence_id)
        if key in sentence_artifacts:
            return sentence_artifacts[key]
        if not isinstance(sentence_id, str) or not sentence_id:
            return None
        identifier = f"{scope}:chunk:{_id(str(document_id), 'document_id')}:{_id(sentence_id, 'sentence_id')}"
        chunk = {"artifact_id": identifier, "kind": "chunk",
                 "source_refs": [source_ids[document_id]] if document_id in source_ids else [],
                 "dependencies": [source_artifact_ids[document_id]] if document_id in source_artifact_ids else [],
                 "metadata": {**metadata, "original_sentence_id": sentence_id, "original_document_id": document_id}}
        if start is not None:
            chunk["start"] = start
        if end is not None:
            chunk["end"] = end
        if text is not None:
            if not isinstance(text, str):
                raise ValidationError("sentence text must be a string")
            chunk["text"] = text
        elif document_id in source_ids and type(start) is int and type(end) is int:
            source = next(s for s in bundle["sources"] if source_ref(s) == source_ids[document_id])
            if 0 <= start <= end <= len(source["text"]):
                chunk["text"] = source["text"][start:end]
        sentence_artifacts[key] = identifier
        bundle["artifacts"].append(chunk)
        return identifier

    sentence_hits = _objects(retrieval.get("sentence_hits", []), "retrieval.sentence_hits")
    document_hits = _objects(retrieval.get("document_hits", []), "retrieval.document_hits")
    candidates = []
    for hit in sentence_hits:
        provenance = hit.get("source")
        if provenance is not None and not isinstance(provenance, dict):
            raise ValidationError("sentence hit source must be an object or null")
        provenance = provenance or {}
        document_id = provenance.get("document_id", hit.get("document_id"))
        sentence_id = provenance.get("sentence_id", hit.get("record_id"))
        identifier = make_chunk(sentence_id, document_id, provenance.get("start"), provenance.get("end"))
        if identifier:
            candidate = {"artifact_id": identifier, "original_record_id": hit.get("record_id")}
            for key in ("rank", "score"):
                if key in hit:
                    candidate[key] = hit[key]
            if document_id in source_ids:
                candidate["source_ref"] = source_ids[document_id]
            candidates.append(candidate)
    if not sentence_hits:
        for hit in document_hits:
            document_id = hit.get("document_id", hit.get("record_id"))
            if document_id in source_artifact_ids:
                candidate = {"artifact_id": source_artifact_ids[document_id], "source_ref": source_ids[document_id]}
                candidate.update({key: hit[key] for key in ("rank", "score") if key in hit})
                candidates.append(candidate)

    for edge in edges:
        original_id = edge.get("edge_id")
        identifier = f"{scope}:edge:{_id(original_id, 'edge.edge_id')}"
        document_id = edge.get("document_id")
        raw_text = edge.get("raw_text", edge.get("text"))
        chunk_id = make_chunk(edge.get("sentence_id"), document_id, edge.get("char_start"), edge.get("char_end"), raw_text)
        artifact = {"artifact_id": identifier, "kind": "edge",
                    "source_refs": [source_ids[document_id]] if document_id in source_ids else [],
                    "dependencies": [chunk_id] if chunk_id else [],
                    "metadata": {**metadata, "original_edge_id": original_id,
                                 "original_document_id": document_id, "recorded_fields": _without_evaluation(edge)}}
        for original, target in (("char_start", "start"), ("char_end", "end")):
            if original in edge:
                artifact[target] = edge[original]
        if raw_text is not None:
            if not isinstance(raw_text, str):
                raise ValidationError("edge.raw_text must be a string")
            artifact["text"] = raw_text
        bundle["artifacts"].append(artifact)
        edge_ids[original_id] = identifier

    for path in paths:
        original_id = path.get("path_id")
        dependencies = [edge_ids.get(edge_id, f"{scope}:edge:{_id(edge_id, 'path.edge_id')}")
                        for edge_id in _strings(path.get("edge_ids", []), "path.edge_ids")]
        by_id = {artifact["artifact_id"]: artifact for artifact in bundle["artifacts"]}
        refs = sorted({ref for dependency in dependencies if dependency in by_id
                       for ref in by_id[dependency]["source_refs"]})
        bundle["artifacts"].append({"artifact_id": f"{scope}:path:{_id(original_id, 'path.path_id')}",
                                    "kind": "path", "source_refs": refs, "dependencies": dependencies,
                                    "metadata": {**metadata, "original_path_id": original_id,
                                                 "recorded_fields": _without_evaluation(path)}})

    query = decision.get("question", query_hint)
    if not isinstance(query, str):
        raise ValidationError("decision_input.question must be a string")
    answer = payload.get("answer")
    run = {"run_id": run_id, "execution": "imported_history", "backend": f"{kind}-import",
           "query": query, "config": None, "index_sha256": None, "candidates": candidates,
           "context_ids": [], "context_text": None, "answer": answer,
           "answer_kind": "recorded_rule_decision" if answer is not None else "unknown",
           "metadata": {**metadata, "question_id": record.get("question_id"), "condition": record.get("condition"),
                        "missing_fields": missing_fields, "retrieved_evidence_ids": payload.get("retrieved_evidence_ids"),
                        "recorded_path_ids": payload.get("path_ids", []),
                        "retrieval_document_ids": payload.get("retrieval_document_ids", []),
                        "retrieval_sentence_ids": payload.get("retrieval_sentence_ids", []),
                        "recorded_retrieval": _without_evaluation(retrieval)}}
    bundle["runs"].append(run)
    return run


def load_input(content: str | bytes, filename: str = "bundle.json") -> dict:
    """Return a validated native bundle or explicit historical-import bundle."""
    content = _decode(content)
    if not content.strip():
        raise ValidationError("Input is empty")
    # A JSONL blank line is an empty record, even when JSON's permissive
    # trailing-whitespace grammar would otherwise hide it.
    if str(filename).lower().endswith(".jsonl"):
        for line_number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                raise ValidationError(f"JSONL line {line_number}: empty record")
    try:
        value = _json(content)
    except ValidationError as whole_error:
        lines = content.splitlines()
        if len(lines) <= 1:
            raise
        records = []
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                raise ValidationError(f"JSONL line {line_number}: empty record")
            try:
                records.append(_json(line))
            except ValidationError as error:
                raise ValidationError(f"JSONL line {line_number}: {error}") from error
        if not records:
            raise whole_error
    else:
        if isinstance(value, dict) and "schema_version" in value:
            return ensure_bundle(value)
        records = [value]

    bundle = {"schema_version": "1.0", "sources": [], "artifacts": [], "runs": [],
              "metadata": {"import_filename": filename, "execution": "imported_history", "formats": [],
                           "evaluation_only": [], "historical_interventions": [],
                           "import_coverage": {"limitations": [
                               "Imported historical rule decisions were not re-executed and are not LLM generations.",
                               "No exact submitted context, portable index hash, or complete replay configuration was recorded.",
                               "Source hashes are computed at import and cannot authenticate original historical collection."]}}}
    # Content hash scopes distinct imports as well as distinct JSONL records.
    import_id = digest(records)[:16]
    for index, original in enumerate(records, 1):
        kind = _history_kind(original)
        if kind is None:
            raise ValidationError(f"JSONL line {index}: Unknown or empty input format; expected native bundle, M4, or M5")
        record = {**original, "_record_index": index}
        prefix = f"{kind}:{import_id}:record:{index}"
        if kind not in bundle["metadata"]["formats"]:
            bundle["metadata"]["formats"].append(kind)
        evaluation = _evaluation(record)
        if evaluation:
            bundle["metadata"]["evaluation_only"].append({"record_index": index, **evaluation})
        try:
            if kind == "m4":
                _phase(bundle, record, record, kind, prefix, "recorded")
            else:
                counterfactual = record["counterfactual"]
                if not isinstance(counterfactual, dict):
                    raise ValidationError("counterfactual must be a nonempty object")
                decision = counterfactual.get("decision_input", {})
                query = decision.get("question", "") if isinstance(decision, dict) else ""
                baseline = _phase(bundle, record, record["baseline"], kind, prefix, "baseline", query)
                changed = _phase(bundle, record, counterfactual, kind, prefix, "counterfactual", query)
                for phase in ("baseline", "counterfactual"):
                    labels = _evaluation(record[phase])
                    if labels:
                        bundle["metadata"]["evaluation_only"].append({"record_index": index, "phase": phase, **labels})
                bundle["metadata"]["historical_interventions"].append({
                    "execution": "imported_history", "record_index": index, "condition": record.get("condition"),
                    "question_id": record.get("question_id"), "document_id": record.get("document_id"),
                    "baseline_run_id": baseline["run_id"], "counterfactual_run_id": changed["run_id"],
                    "baseline": _without_evaluation(record["baseline"]),
                    "counterfactual": _without_evaluation(counterfactual),
                    "target_provenance": _without_evaluation(record.get("target_provenance")),
                    "interpretation": "Recorded comparison only; no new execution or malicious-cause inference."})
        except (ValidationError, TypeError, KeyError) as error:
            raise ValidationError(f"JSONL line {index}: {error}") from error
    return ensure_bundle(bundle)
