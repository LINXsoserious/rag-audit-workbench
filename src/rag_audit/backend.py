"""确定性的标准库 BM25；这里没有生成器，也不推断来源可信度。

unicode-cjk-bigram-v1 使用 Unicode 字母/数字串、casefold 英文词，以及
CJK 统一汉字范围内连续片段的单字和相邻二元词。不去停用词、不归一化正文。
分块、预算和 start/end 都按 Python Unicode 字符计数；预算包含两个换行分隔符。
BM25 使用 log(1 + (N-df+0.5)/(df+0.5))，查询词去重，稳定同分按来源及跨度排序。
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
import re

from .schema import SCHEMA_VERSION, ValidationError, digest, ensure_bundle, source_ref, text_hash

BACKEND = "native-bm25-v1"
TOKENIZER = "unicode-cjk-bigram-v1"
DEFAULT_CONFIG = {"chunk_size": 400, "chunk_overlap": 40, "top_k": 4,
                  "context_budget": 1600, "k1": 1.5, "b": 0.75, "tokenizer": TOKENIZER}
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def _config(config: dict | None = None, base: dict | None = None) -> dict:
    if config is not None and not isinstance(config, dict):
        raise ValidationError("backend config must be an object")
    supplied = config or {}
    if not all(isinstance(key, str) for key in supplied):
        raise ValidationError("backend config keys must be strings")
    unknown = set(supplied) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValidationError(f"Unknown backend config: {', '.join(sorted(unknown))}")
    result = {**(DEFAULT_CONFIG if base is None else base), **supplied}
    for key, minimum in (("chunk_size", 1), ("chunk_overlap", 0), ("top_k", 1), ("context_budget", 0)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValidationError(f"{key} must be an integer >= {minimum}")
    if result["chunk_overlap"] >= result["chunk_size"]:
        raise ValidationError("chunk_overlap must be smaller than chunk_size")
    for key in ("k1", "b"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError(f"{key} must be a finite number")
    if result["k1"] <= 0 or not 0 <= result["b"] <= 1:
        raise ValidationError("k1 must be positive and b must be between 0 and 1")
    if result["tokenizer"] != TOKENIZER:
        raise ValidationError(f"Only tokenizer {TOKENIZER} is supported")
    return result


def _is_cjk(char: str) -> bool:
    point = ord(char)
    return (0x3400 <= point <= 0x4DBF or 0x4E00 <= point <= 0x9FFF
            or 0xF900 <= point <= 0xFAFF or 0x20000 <= point <= 0x2FA1F)


def _tokens(text: str) -> list[str]:
    tokens = []
    for match in _WORDS.finditer(text):
        word = match.group()
        start = 0
        while start < len(word):
            cjk = _is_cjk(word[start])
            end = start + 1
            while end < len(word) and _is_cjk(word[end]) == cjk:
                end += 1
            part = word[start:end]
            if cjk:
                tokens.extend(part)
                tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
            else:
                tokens.append(part.casefold())
            start = end
    return tokens


def _sources(sources: list[dict]) -> list[dict]:
    if not isinstance(sources, list):
        raise ValidationError("sources must be a list")
    result = ensure_bundle({"schema_version": SCHEMA_VERSION, "sources": sources})["sources"]
    seen = set()
    active = set()
    for source in result:
        ref = source_ref(source)
        if ref in seen:
            raise ValidationError(f"Duplicate source reference: {ref}")
        seen.add(ref)
        if source["sha256"] != text_hash(source["text"]):
            raise ValidationError(f"Source hash mismatch: {ref}")
        if source["status"] == "active":
            if source["source_id"] in active:
                raise ValidationError(f"Multiple active revisions: {source['source_id']}")
            active.add(source["source_id"])
    return sorted(result, key=source_ref)


def _queries(queries: list[str]) -> list[str]:
    if not isinstance(queries, list) or not all(isinstance(query, str) for query in queries):
        raise ValidationError("queries must be a list of strings")
    if len(queries) != len(set(queries)):
        raise ValidationError("Duplicate queries are not supported in one native bundle")
    return list(queries)


def _spans(text: str, config: dict):
    step = config["chunk_size"] - config["chunk_overlap"]
    for start in range(0, len(text), step):
        end = min(len(text), start + config["chunk_size"])
        yield start, end
        if end == len(text):
            break


def _source_artifacts(sources: list[dict]) -> list[dict]:
    return [{"artifact_id": "source:" + source_ref(source), "kind": "source",
             "source_refs": [source_ref(source)], "dependencies": [],
             "text": source["text"], "metadata": {"sha256": source["sha256"]}}
            for source in sources if source["status"] == "active"]


def _chunk_artifact(source: dict, start: int, end: int) -> dict:
    ref = source_ref(source)
    text = source["text"][start:end]
    identity = {"source_ref": ref, "start": start, "end": end, "sha256": text_hash(text)}
    return {"artifact_id": "chunk:" + digest(identity), "kind": "chunk", "source_refs": [ref],
            "dependencies": ["source:" + ref], "start": start, "end": end, "text": text,
            "metadata": {"sha256": identity["sha256"]}}


def _chunk_sources(sources: list[dict], config: dict) -> list[dict]:
    return [_chunk_artifact(source, start, end) for source in sources if source["status"] == "active"
            for start, end in _spans(source["text"], config)]


def _index_artifact(chunks: list[dict]) -> dict:
    documents = {}
    frequency = Counter()
    for chunk in chunks:
        terms = Counter(_tokens(chunk["text"]))
        frequency.update(terms.keys())
        documents[chunk["artifact_id"]] = {"length": sum(terms.values()), "terms": dict(sorted(terms.items()))}
    total_terms = sum(document["length"] for document in documents.values())
    count = len(documents)
    stats = {"tokenizer": TOKENIZER, "document_count": count, "total_terms": total_terms,
             "average_document_length": total_terms / count if count else 0.0,
             "document_frequency": dict(sorted(frequency.items())), "documents": documents}
    refs = sorted({ref for chunk in chunks for ref in chunk["source_refs"]})
    dependencies = [chunk["artifact_id"] for chunk in chunks]
    checksum = digest({"metadata": stats, "source_refs": refs, "dependencies": dependencies})
    return {"artifact_id": "bm25_index:" + checksum, "kind": "bm25_index",
            "source_refs": refs, "dependencies": dependencies, "metadata": stats}


def _chunk_order(chunk: dict) -> tuple:
    return chunk["source_refs"][0], chunk["start"], chunk["end"], chunk["artifact_id"]


def _run(chunks: list[dict], index: dict, query: str, config: dict) -> dict:
    if not isinstance(query, str):
        raise ValidationError("query must be a string")
    stats = index["metadata"]
    terms = sorted(set(_tokens(query)))
    count, average = stats["document_count"], stats["average_document_length"]
    ranked = []
    for chunk in chunks:
        document = stats["documents"][chunk["artifact_id"]]
        score = 0.0
        for term in terms:
            term_frequency = document["terms"].get(term, 0)
            if not term_frequency:
                continue
            document_frequency = stats["document_frequency"][term]
            inverse_frequency = math.log(1 + (count - document_frequency + 0.5) / (document_frequency + 0.5))
            norm = config["k1"] * (1 - config["b"] + config["b"] * document["length"] / average)
            score += inverse_frequency * term_frequency * (config["k1"] + 1) / (term_frequency + norm)
        if score > 0:
            ranked.append((chunk, score))
    ranked.sort(key=lambda item: (-item[1], *_chunk_order(item[0])))
    candidates = [{"artifact_id": chunk["artifact_id"], "rank": rank, "source_ref": chunk["source_refs"][0],
                   "score": score} for rank, (chunk, score) in enumerate(ranked, 1)]
    selected = []
    used = 0
    for chunk, _ in ranked:
        if len(selected) >= config["top_k"]:
            break
        cost = len(chunk["text"]) + (2 if selected else 0)
        if used + cost <= config["context_budget"]:
            selected.append(chunk)
            used += cost
    run = {"execution": "executed", "backend": BACKEND, "query": query, "config": deepcopy(config),
           "index_sha256": index["artifact_id"].split(":", 1)[1], "candidates": candidates,
           "context_ids": [chunk["artifact_id"] for chunk in selected],
           "context_text": "\n\n".join(chunk["text"] for chunk in selected),
           "answer": None, "answer_kind": "not_generated",
           "metrics": {"candidate_count": len(candidates), "context_count": len(selected),
                       "context_characters": used, "indexed_chunks": count}}
    run["run_id"] = "run:" + digest(run)
    return run


def _context_artifact(run: dict, chunks: list[dict], index: dict) -> dict:
    by_id = {chunk["artifact_id"]: chunk for chunk in chunks}
    refs = sorted({ref for identifier in run["context_ids"] for ref in by_id[identifier]["source_refs"]})
    return {"artifact_id": "context:" + run["run_id"], "kind": "context", "source_refs": refs,
            "dependencies": [*run["context_ids"], index["artifact_id"]], "text": run["context_text"],
            "metadata": {"run_id": run["run_id"], "content_dependencies": list(run["context_ids"]),
                         "selection_dependencies": [index["artifact_id"]]}}


def _assemble(sources: list[dict], chunks: list[dict], queries: list[str], config: dict) -> dict:
    """由已验证的分块重算全局索引与运行；回放不会复用旧索引或旧分数。"""
    chunks = sorted(deepcopy(chunks), key=_chunk_order)
    index = _index_artifact(chunks)
    runs = [_run(chunks, index, query, config) for query in queries]
    contexts = {run["run_id"]: _context_artifact(run, chunks, index) for run in runs}
    return ensure_bundle({"schema_version": SCHEMA_VERSION, "sources": sources,
                          "artifacts": [*_source_artifacts(sources), *chunks, index, *contexts.values()],
                          "runs": runs, "metadata": {"backend": BACKEND, "backend_config": config,
                                                       "index_artifact_id": index["artifact_id"]}})


def build_bundle(sources: list[dict], queries: list[str], config: dict | None = None) -> dict:
    """校验来源快照，按固定 Unicode 跨度分块并执行确定性 BM25。"""
    settings = _config(config)
    snapshots = _sources(sources)
    return _assemble(snapshots, _chunk_sources(snapshots, settings), _queries(queries), settings)


def _validated_native_bundle(bundle: dict) -> tuple[dict, list[dict], dict, dict]:
    result = ensure_bundle(bundle)
    metadata = result["metadata"]
    if metadata.get("backend") != BACKEND:
        raise ValidationError(f"Replay/retrieval requires backend {BACKEND}")
    if not isinstance(metadata.get("backend_config"), dict):
        raise ValidationError("Native bundle is missing backend_config")
    config = _config(metadata["backend_config"])
    sources = _sources(result["sources"])
    active = {source_ref(source): source for source in sources if source["status"] == "active"}
    artifacts = result["artifacts"]
    ids = [artifact["artifact_id"] for artifact in artifacts]
    if len(ids) != len(set(ids)):
        raise ValidationError("Duplicate native artifact IDs")
    chunks = [artifact for artifact in artifacts if artifact["kind"] == "chunk"]
    # 逐块核对正文切片和跨度集合，防止缺块或篡改；保留块不从来源重新构造。
    expected_spans = {(ref, start, end) for ref, source in active.items()
                      for start, end in _spans(source["text"], config)}
    actual_spans = set()
    for chunk in chunks:
        refs = chunk["source_refs"]
        if len(refs) != 1 or refs[0] not in active:
            raise ValidationError("Chunk references an inactive or missing source")
        start, end = chunk.get("start"), chunk.get("end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValidationError("Chunk spans must be Unicode integer offsets")
        if not 0 <= start < end <= len(active[refs[0]]["text"]):
            raise ValidationError("Chunk span is out of bounds")
        if chunk != _chunk_artifact(active[refs[0]], start, end):
            raise ValidationError("Chunk content, identity, or dependency mismatch")
        actual_spans.add((refs[0], start, end))
    if actual_spans != expected_spans or len(actual_spans) != len(chunks):
        raise ValidationError("Native chunk coverage is incomplete or duplicated")
    chunks.sort(key=_chunk_order)
    source_artifacts = [artifact for artifact in artifacts if artifact["kind"] == "source"]
    if source_artifacts != _source_artifacts(sources):
        raise ValidationError("Native source artifacts do not match active snapshots")
    index = _index_artifact(chunks)
    recorded_indexes = [artifact for artifact in artifacts if artifact["kind"] == "bm25_index"]
    if recorded_indexes != [index] or metadata.get("index_artifact_id") != index["artifact_id"]:
        raise ValidationError("Native global corpus statistics are stale or modified")
    if any(run.get("backend") != BACKEND or run.get("execution") != "executed" for run in result["runs"]):
        raise ValidationError("Native execution cannot reuse imported history")
    queries = _queries([run["query"] for run in result["runs"]])
    expected_runs = [_run(chunks, index, query, config) for query in queries]
    if result["runs"] != expected_runs:
        raise ValidationError("Recorded native runs differ from recomputed ranking or context")
    contexts = [artifact for artifact in artifacts if artifact["kind"] == "context"]
    if contexts != [_context_artifact(run, chunks, index) for run in expected_runs]:
        raise ValidationError("Native context artifacts are missing, stale, or modified")
    if any(artifact["kind"] not in {"source", "chunk", "bm25_index", "context"} for artifact in artifacts):
        raise ValidationError("Unsupported native artifact kind")
    result["sources"] = sources
    return result, chunks, index, config


def retrieve(bundle: dict, query: str, config: dict | None = None) -> dict:
    """校验已有索引并检索；可覆盖排序/预算配置，分块配置必须与索引一致。"""
    _, chunks, index, original = _validated_native_bundle(bundle)
    settings = _config(config, original)
    if any(settings[key] != original[key] for key in ("chunk_size", "chunk_overlap", "tokenizer")):
        raise ValidationError("Changing chunk/tokenizer config requires rebuilding the bundle")
    return _run(chunks, index, query, settings)
