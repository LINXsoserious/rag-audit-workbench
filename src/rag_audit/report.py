"""Portable reports; all source strings are treated as untrusted text."""
from __future__ import annotations

import json
import re

from .schema import text_hash
from .i18n import t


def _safe(value) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", text)


def render_markdown(report: dict, lang: str = "zh") -> str:
    """Render a human report without modifying evidence or its machine schema."""
    lines = [t(lang, "# RAG 源撤回审计报告"), "",
             t(lang, "执行状态：{value}", value=_safe(report.get("execution", "unknown"))),
             t(lang, "基线证据包：{value}", value=_safe(report.get("baseline_bundle_id", "unknown"))),
             t(lang, "报告 ID：{value}", value=_safe(report.get("report_id", t(lang, "未入库")))), "",
             t(lang, "本报告描述指定输入、配置和干预下的系统变化。它不单独证明来源恶意或识别攻击者。"), "",
             t(lang, "## 撤回来源"), ""]
    lines += [f"- {_safe(ref)}" for ref in report.get("withdrawn_refs", [])]
    lines += ["", t(lang, "## 完整重建对照"), "",
              t(lang, "一致：{value}", value=report.get("validation", {}).get("equivalent_to_full_rebuild", False)), ""]
    for item in report.get("queries", []):
        before, after = item.get("before", {}), item.get("after", {})
        lines += [t(lang, "### 查询：{value}", value=_safe(item.get("query", ""))), "",
                  t(lang, "排序发生变化：{value}", value=bool(item.get("ranking_changed"))),
                  t(lang, "完整重建一致：{value}", value=bool(item.get("equivalent"))),
                  t(lang, "撤回前上下文：{value}", value=_safe(", ".join(before.get("context_ids", [])))),
                  t(lang, "撤回后上下文：{value}", value=_safe(", ".join(after.get("context_ids", [])))), ""]
        if after.get("answer_kind") == "not_generated":
            lines += [t(lang, "未启用生成模型；本项只报告检索与上下文变化，不计算答案恢复。"), ""]
    detail = json.dumps({"invalidation": report.get("invalidation", {}), "metrics": report.get("metrics", {})}, ensure_ascii=False, indent=2)
    fence = "`" * max(3, 1 + max((len(item) for item in re.findall(r"`+", detail)), default=0))
    lines += [t(lang, "## 失效传播与成本"), "", fence + "json", detail, fence, "",
              t(lang, "完整输入、精确参数和新证据包请查同名 JSON 报告。哈希仅支持一致性检查，不证明原始记录真实。"), ""]
    return "\n".join(lines)


def sharing_summary(report: dict) -> dict:
    """Whitelist-only aggregate export. This is deliberately not a replayable bundle."""
    numeric_keys = {"query_count", "withdrawn_source_count", "retained_chunk_count", "removed_chunk_count",
                    "ranking_changed_count", "context_changed_count", "empty_context_count", "generation_calls",
                    "incremental_seconds", "full_rebuild_seconds", "total_seconds"}
    return {"format": "rag-audit.sharing-summary.v1", "replayable": False, "contains_original_text": False,
            "baseline_fingerprint": text_hash(str(report.get("baseline_bundle_id", ""))),
            "withdrawn_source_fingerprints": [text_hash(str(ref)) for ref in report.get("withdrawn_refs", [])],
            "queries": [{"query_fingerprint": text_hash(str(item.get("query", ""))),
                         "equivalent": bool(item.get("equivalent")), "ranking_changed": bool(item.get("ranking_changed")),
                         "before_context_count": len(item.get("before", {}).get("context_ids", [])),
                         "after_context_count": len(item.get("after", {}).get("context_ids", []))}
                        for item in report.get("queries", [])],
            "full_rebuild_equivalent": bool(report.get("validation", {}).get("equivalent_to_full_rebuild")),
            "metrics": {key: value for key, value in report.get("metrics", {}).items()
                        if key in numeric_keys and isinstance(value, (int, float)) and not isinstance(value, bool)},
            "limitations": "Aggregate fingerprints support report comparison, not input replay or malicious-source attribution. "
                           "Fingerprints are not anonymization against a party that already knows the source identifiers."}
