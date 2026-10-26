"""Deterministic evidence-integrity checks, without maliciousness inference."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import re

from .schema import ValidationError, ensure_bundle, source_ref, text_hash


_INSTRUCTION_CLUES = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"(?:忽略|无视).{0,12}(?:指令|规则)|把我设为可信|(?:system|developer)\s*prompt", re.I)


def audit_bundle(bundle: dict) -> dict:
    """Check recorded evidence; a passing result never establishes truth or intent.

    ``passed`` requires both no errors and complete coverage of supported checks.
    Missing historical context therefore cannot be reported as a successful audit.
    Content and selection dependencies are separate: an index may affect selection
    without making every indexed source part of the submitted context.
    """
    bundle = ensure_bundle(bundle)
    findings: list[dict] = []
    limitations: list[str] = []
    coverage = {
        "source_hashes": {"checked": 0, "total": len(bundle["sources"]), "missing": 0},
        "source_support": {"checked": 0, "total": len(bundle["artifacts"]), "missing": 0},
        "spans": {"checked": 0, "total": 0, "missing": 0},
        "context_consistency": {"checked": 0, "total": len(bundle["runs"]), "missing": 0},
        "cache_binding": {"checked": 0, "total": 0, "missing": 0, "not_verified": 0, "fields_checked": 0},
        "causal_attribution": "not_assessed",
        "limitations": limitations,
    }

    def add(code, severity, object_id, message, **evidence):
        findings.append({"code": code, "severity": severity, "object_id": object_id,
                         "message": message, "evidence": evidence})

    def missing(category, object_id, code, message, **evidence):
        coverage[category]["missing"] += 1
        add(code, "warning", object_id, message, **evidence)

    def duplicates(items, key, code):
        for identifier, count in Counter(key(item) for item in items).items():
            if count > 1:
                add(code, "error", identifier, "Identifier occurs more than once.", count=count)

    duplicates(bundle["sources"], source_ref, "duplicate_source_ref")
    duplicates(bundle["artifacts"], lambda a: a["artifact_id"], "duplicate_artifact_id")
    duplicates(bundle["runs"], lambda r: r["run_id"], "duplicate_run_id")
    sources = {source_ref(source): source for source in bundle["sources"]}
    artifacts = {artifact["artifact_id"]: artifact for artifact in bundle["artifacts"]}
    active_revisions = defaultdict(set)
    for source in bundle["sources"]:
        ref = source_ref(source)
        coverage["source_hashes"]["checked"] += 1
        actual = text_hash(source["text"])
        if actual != source["sha256"]:
            add("source_hash_mismatch", "error", ref, "Source text does not match its recorded SHA-256.",
                recorded=source["sha256"], computed=actual)
        if source["status"] == "active":
            active_revisions[source["source_id"]].add(source["revision"])
        matches = list(_INSTRUCTION_CLUES.finditer(source["text"]))
        if matches:
            add("instruction_like_text", "info", ref,
                "Instruction-like text is a review clue only; it does not establish maliciousness or change trust.",
                spans=[[m.start(), m.end()] for m in matches[:20]], externally_recorded_trust=source["trust"])
    for identifier, revisions in active_revisions.items():
        if len(revisions) > 1:
            add("multiple_active_revisions", "error", identifier,
                "A logical source has multiple active revisions in one snapshot.", revisions=sorted(revisions))

    def check_span(object_id, span, default_refs):
        coverage["spans"]["total"] += 1
        refs = [span["source_ref"]] if "source_ref" in span else default_refs
        if len(refs) != 1 or not all(k in span for k in ("start", "end", "text")):
            missing("spans", object_id, "span_not_recorded",
                    "An exact span bound to one source revision was not recorded.", source_refs=refs)
            return
        ref = refs[0]
        if not isinstance(ref, str) or not ref:
            add("invalid_span_source_ref", "error", object_id,
                "A support span must reference a nonempty source-revision string.")
            return
        if ref not in sources:
            missing("spans", object_id, "span_source_unavailable",
                    "The span cannot be checked because source text is unavailable.", source_ref=ref)
            return
        start, end, text = span["start"], span["end"], span["text"]
        coverage["spans"]["checked"] += 1
        length = len(sources[ref]["text"])
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= length):
            add("span_out_of_bounds", "error", object_id, "Span is outside its original Unicode text.",
                source_ref=ref, start=start, end=end, source_length=length)
        elif not isinstance(text, str) or sources[ref]["text"][start:end] != text:
            add("span_text_mismatch", "error", object_id, "Span text is not the exact original substring.",
                source_ref=ref, start=start, end=end)

    for artifact in bundle["artifacts"]:
        identifier = artifact["artifact_id"]
        refs = set(artifact["source_refs"])
        metadata = artifact.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        empty_context = (artifact["kind"] == "context" and metadata.get("content_dependencies") == [])
        empty_index = (artifact["kind"] == "bm25_index" and not artifact["dependencies"]
                       and not any(source["status"] == "active" for source in sources.values()))
        if refs or empty_context or empty_index:
            coverage["source_support"]["checked"] += 1
        else:
            missing("source_support", identifier, "source_support_missing",
                    "No source-revision support was recorded for this artifact.")
        for ref in sorted(refs):
            if ref not in sources:
                add("missing_source_ref", "error", identifier, "Referenced source revision does not exist.", source_ref=ref)
            elif artifact["kind"] != "source" and sources[ref]["status"] != "active":
                add("inactive_source_used", "error", identifier, "Derived evidence still uses an inactive source revision.",
                    source_ref=ref, status=sources[ref]["status"])
        for dependency in artifact["dependencies"]:
            if dependency not in artifacts:
                add("missing_dependency", "error", identifier, "An artifact dependency does not exist.", dependency=dependency)
            elif artifact["kind"] == "path" and artifacts[dependency]["kind"] != "edge":
                add("path_dependency_not_edge", "error", identifier, "A path dependency must be an edge artifact.",
                    dependency=dependency, dependency_kind=artifacts[dependency]["kind"])
        def check_role(role, value):
            if not isinstance(value, list) or not all(isinstance(d, str) and d for d in value):
                add(f"invalid_{role}", "error", identifier,
                    "Dependency roles must be lists of nonempty artifact IDs.", role=role)
                add("ROLE_DEPENDENCY_INVALID", "error", identifier,
                    "Dependency role is not a list of nonempty artifact IDs.", role=role)
                return []
            for dependency in value:
                declared = dependency in artifact["dependencies"]
                exists = dependency in artifacts
                if not declared or not exists:
                    add("ROLE_DEPENDENCY_INVALID", "error", identifier,
                        "Every role dependency must exist and be declared in dependencies.",
                        role=role, dependency=dependency, declared=declared, exists=exists)
                if role == "content_dependencies" and not declared:
                    add("undeclared_content_dependency", "error", identifier,
                        "Content dependency is absent from the dependency list.", dependency=dependency)
            return value

        selection_list = check_role("selection_dependencies", metadata.get("selection_dependencies", []))
        selection = set(selection_list)
        content_deps = check_role("content_dependencies", metadata.get(
            "content_dependencies", [d for d in artifact["dependencies"] if d not in selection]))
        inherited = {ref for dependency in content_deps if dependency in artifacts
                     for ref in artifacts[dependency]["source_refs"]}
        if inherited - refs:
            add("source_support_mismatch", "error", identifier,
                "Declared source support omits content-dependency sources.",
                omitted_source_refs=sorted(inherited - refs))
        if artifact["kind"] == "cache":
            cache_coverage = coverage["cache_binding"]
            cache_coverage["total"] += 1
            cache_coverage["fields_checked"] += 1
            binding = metadata.get("cache_binding")
            binding = binding if isinstance(binding, dict) else {}
            invalid = [field for field in ("input_sha256", "config_sha256")
                       if not isinstance(binding.get(field), str)
                       or re.fullmatch(r"[0-9a-fA-F]{64}", binding[field]) is None]
            if not isinstance(binding.get("processor_version"), str) or not binding["processor_version"].strip():
                invalid.append("processor_version")
            if invalid:
                missing("cache_binding", identifier, "CACHE_BINDING_MISSING",
                        "Cache requires input/config SHA-256 hashes and a nonempty processor version; a cache key alone is insufficient.",
                        invalid_or_missing_fields=invalid)
            else:
                cache_coverage["not_verified"] += 1
                add("CACHE_BINDING_NOT_VERIFIED", "warning", identifier,
                    "Cache binding fields are present, but their actual input, configuration, and processor provenance cannot be verified by this adapter.",
                    processor_version=binding["processor_version"])
        if artifact["kind"] in {"chunk", "edge"}:
            spans = metadata.get("support_spans")
            if isinstance(spans, list) and spans:
                span_refs = set()
                for index, span in enumerate(spans):
                    if isinstance(span, dict):
                        check_span(identifier, span, artifact["source_refs"])
                        if isinstance(span.get("source_ref"), str):
                            span_refs.add(span["source_ref"])
                    else:
                        coverage["spans"]["total"] += 1
                        missing("spans", identifier, "span_not_recorded", "Support span must be an object.", span_index=index)
                if span_refs - refs:
                    add("source_support_mismatch", "error", identifier,
                        "A support span references a source outside declared support.",
                        undeclared_source_refs=sorted(str(r) for r in span_refs - refs))
            else:
                check_span(identifier, artifact, artifact["source_refs"])

    # Kahn's algorithm avoids recursion limits for large imported dependency graphs.
    incoming = {identifier: 0 for identifier in artifacts}
    children = defaultdict(list)
    for identifier, artifact in artifacts.items():
        for dependency in set(artifact["dependencies"]):
            if dependency in artifacts:
                incoming[identifier] += 1
                children[dependency].append(identifier)
    queue = deque(identifier for identifier, degree in incoming.items() if degree == 0)
    while queue:
        for child in children[queue.popleft()]:
            incoming[child] -= 1
            if incoming[child] == 0:
                queue.append(child)
    cyclic = sorted(identifier for identifier, degree in incoming.items() if degree)
    if cyclic:
        add("dependency_cycle", "error", cyclic[0],
            "Dependency graph contains a cycle; listed nodes include the cycle and its blocked descendants.",
            artifact_ids=cyclic)

    for run in bundle["runs"]:
        identifier = run["run_id"]
        for candidate in run["candidates"]:
            artifact_id = candidate.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                add("invalid_candidate_artifact_id", "error", identifier,
                    "A candidate must reference a nonempty artifact-ID string.")
            elif artifact_id not in artifacts:
                add("missing_candidate_artifact", "error", identifier,
                    "A retrieval candidate references an unavailable artifact.", artifact_id=artifact_id)
            elif "source_ref" in candidate and candidate["source_ref"] not in artifacts[artifact_id]["source_refs"]:
                add("candidate_source_mismatch", "error", identifier,
                    "Candidate source differs from its artifact's recorded support.",
                    artifact_id=artifact_id, source_ref=candidate["source_ref"])
        texts = []
        context_refs = set()
        context_available = True
        for artifact_id in run["context_ids"]:
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                add("missing_context_artifact", "error", identifier,
                    "A context reference points to an unavailable artifact.", artifact_id=artifact_id)
                context_available = False
                continue
            context_refs.update(artifact["source_refs"])
            if not isinstance(artifact.get("text"), str):
                context_available = False
            else:
                texts.append(artifact["text"])
        for ref in sorted(context_refs):
            if ref in sources and sources[ref]["status"] != "active":
                add("inactive_source_used", "error", identifier,
                    "Recorded context uses an inactive source revision.", source_ref=ref, status=sources[ref]["status"])
        if "context_source_refs" in run:
            recorded_refs = run["context_source_refs"]
            if not isinstance(recorded_refs, list) or not all(isinstance(ref, str) for ref in recorded_refs):
                add("invalid_context_source_refs", "error", identifier,
                    "Context source refs must be a list of source-revision strings.")
            elif set(recorded_refs) != context_refs:
                add("context_source_refs_mismatch", "error", identifier,
                    "Recorded context sources disagree with context artifact support.",
                    recorded=recorded_refs, resolved=sorted(context_refs))
        actual = run.get("context_text")
        if not isinstance(actual, str) or not context_available:
            missing("context_consistency", identifier, "context_not_recorded",
                    "Actual submitted context and its source text are not both available; consistency is unknown.",
                    execution=run.get("execution", "unknown"))
        else:
            coverage["context_consistency"]["checked"] += 1
            if "\n\n".join(texts) != actual:
                add("context_text_mismatch", "error", identifier,
                    "Recorded context does not exactly match context IDs in order with double-newline separators.",
                    context_ids=run["context_ids"])

    if any(run.get("execution") == "imported_history" for run in bundle["runs"]):
        limitations.append("Historical decisions were imported, not executed by this audit; missing submission logs cannot establish actual generator context.")
    declares_native = (bundle["metadata"].get("backend") == "native-bm25-v1"
                       or any(run.get("backend") == "native-bm25-v1" for run in bundle["runs"])
                       or any(artifact["kind"] == "bm25_index" for artifact in bundle["artifacts"]))
    if declares_native:
        # Only this backend has a complete deterministic execution contract.
        # Historical/custom records retain their explicit coverage boundaries.
        from .backend import _validated_native_bundle

        try:
            _validated_native_bundle(bundle)
        except ValidationError as error:
            add("NATIVE_REPLAY_INTEGRITY", "error", bundle["bundle_id"],
                "Native execution does not match an independent deterministic reconstruction.", reason=str(error))
        coverage["native_recomputation"] = {"checked": 1, "total": 1, "missing": 0, "status": "checked"}
    else:
        coverage["native_recomputation"] = {"checked": 0, "total": 0, "missing": 0, "status": "not_applicable"}
    cache_coverage = coverage["cache_binding"]
    cache_coverage["status"] = ("insufficient" if cache_coverage["missing"] else
                                "not_verified" if cache_coverage["not_verified"] else "not_applicable")
    if cache_coverage["total"]:
        limitations.append("Cache binding field shape is inspected only; this adapter cannot verify the recorded hashes against actual cache inputs/configuration or the processor implementation.")
    coverage["complete"] = not any(coverage[key]["missing"] for key in
                                   ("source_hashes", "source_support", "spans", "context_consistency", "cache_binding")) and not cache_coverage["not_verified"]
    for key in ("source_hashes", "source_support", "spans", "context_consistency"):
        check = coverage[key]
        check["status"] = "insufficient" if check["missing"] else ("checked" if check["total"] else "not_applicable")
    counts = {severity: sum(item["severity"] == severity for item in findings)
              for severity in ("error", "warning", "info")}
    return {"findings": findings, "counts": counts, "coverage": coverage,
            "passed": counts["error"] == 0 and coverage["complete"]}
