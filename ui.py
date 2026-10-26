"""Local Chinese case viewer. All mutations go through the shared Workbench service."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from rag_audit.demo import DEMO_QUERIES, DEMO_WITHDRAWALS, demo_bundle
from rag_audit.importers import load_input
from rag_audit.report import render_markdown, sharing_summary
from rag_audit.schema import source_ref
from rag_audit.service import Workbench


def source_html(text: str, start: int | None = None, end: int | None = None) -> str:
    """Escape every source character; only application-owned markup is executable."""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
        body = html.escape(text[:start]) + "<mark>" + html.escape(text[start:end]) + "</mark>" + html.escape(text[end:])
    else:
        body = html.escape(text)
    return '<div class="source-text">' + body + "</div>"


def can_replay(bundle: dict) -> bool:
    """History is evidence for inspection, never a claim of executable replay."""
    runs = bundle.get("runs", [])
    metadata = bundle.get("metadata", {})
    if metadata.get("execution") == "imported_history" or metadata.get("imported_history"):
        return False
    if any(run.get("execution") == "imported_history" for run in runs):
        return False
    return (metadata.get("backend") == "native-bm25-v1"
            and isinstance(metadata.get("backend_config"), dict)
            and bool(runs)
            and all(run.get("backend") == "native-bm25-v1" and run.get("execution") == "executed" for run in runs))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _error(action: str, error: Exception) -> None:
    st.error(f"{action}失败：{type(error).__name__} · {error}")


def _select_case(bundle_id: str) -> None:
    st.session_state["active_case"] = bundle_id
    st.session_state["case_picker"] = bundle_id


def _style() -> None:
    st.markdown("""<style>
    .stApp { background: #f4f3ef; color: #202b2b; }
    .block-container { max-width: 1280px; padding-top: 4.5rem; padding-bottom: 3rem; }
    h1,h2,h3 { font-family: 'Palatino Linotype','Noto Serif CJK SC','Songti SC',serif; letter-spacing: -.025em; }
    h1 { font-size: 2.3rem !important; }
    [data-testid="stSidebar"] { background: #e8ece6; border-right: 1px solid #d3dbd0; }
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] [data-testid="stCaptionContainer"], [data-testid="stSidebar"] label { color: #202b2b; }
    [data-testid="stMetric"] { background: #fffefa; border: 1px solid #dcded4; border-top: 3px solid #597262; padding: .8rem 1rem; border-radius: 3px; }
    .source-text { background: #fffefa; border: 1px solid #d8ddd2; border-left: 3px solid #597262; padding: 1.1rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; font-family: 'Microsoft YaHei',sans-serif; }
    .source-text mark { background: #f4d98e; color: #202b2b; border-radius: 2px; padding: 0 .1em; }
    .eyebrow { font-size: .72rem; letter-spacing: .19em; color: #577260; font-weight: 700; }
    .scope { border-left: 3px solid #b99c5a; padding: .65rem 1rem; background: #eee9dc; font-size: .9rem; }
    [data-baseweb="tab-list"] { gap: 1.1rem; }
    button[kind="primary"] { background: #365d4d; border-color: #365d4d; }
    </style>""", unsafe_allow_html=True)


def _ingest_panel(workbench: Workbench) -> None:
    with st.sidebar:
        st.markdown("### ① 建立案件")
        st.caption("先用内置案例走通流程，再导入自己的记录。")
        if st.button("载入内置示例", key="load_demo", type="primary", use_container_width=True):
            try:
                bundle_id = workbench.ingest(demo_bundle(), label="内置示例 · 来源撤回")
                _select_case(bundle_id)
                st.session_state["demo_case"] = bundle_id
                st.success("示例已载入；重复载入会复用相同证据。")
            except Exception as error:
                _error("载入示例", error)
        upload = st.file_uploader("导入证据文件", type=["json", "jsonl"], key="evidence_upload", help="UTF-8；统一 bundle JSON 或受支持的 M4/M5 JSONL；不超过 25 MiB。")
        label = st.text_input("案件名称（可选）", key="import_label", placeholder="例如：知识库版本更新复核")
        if st.button("导入并建立案件", key="ingest_upload", disabled=upload is None, use_container_width=True):
            try:
                assert upload is not None
                bundle_id = workbench.ingest(load_input(upload.getvalue(), upload.name), label=label or upload.name)
                _select_case(bundle_id)
                st.success("证据已导入。")
            except Exception as error:
                _error("导入", error)
        with st.expander("也可粘贴小段 JSON / JSONL"):
            pasted = st.text_area("粘贴 JSON / JSONL", key="pasted_input", height=100)
            input_format = st.selectbox("粘贴格式", ["JSON", "JSONL"], key="pasted_format")
            if st.button("导入粘贴内容", key="ingest_text", disabled=not pasted.strip()):
                try:
                    filename = "pasted.jsonl" if input_format == "JSONL" else "pasted.json"
                    bundle_id = workbench.ingest(load_input(pasted.encode("utf-8"), filename), label=label or "粘贴证据")
                    _select_case(bundle_id)
                    st.success("证据已导入。")
                except Exception as error:
                    _error("导入", error)


def _findings(audit: dict, bundle: dict, bundle_id: str) -> None:
    findings = audit.get("findings", [])
    if audit.get("passed"):
        st.success("已提供记录的完整性检查通过。检查范围见下方覆盖信息。")
    elif findings:
        st.warning("发现待核验问题。先定位原文与依赖，再决定撤回集合。")
    else:
        st.info("没有可定位的问题记录；请同时检查覆盖信息。")
    sources = {source_ref(item): item for item in bundle.get("sources", [])}
    artifacts = {item["artifact_id"]: item for item in bundle.get("artifacts", [])}
    selected = list(enumerate(findings))
    if len(findings) > 25:
        severity_filter = st.selectbox("问题级别", ["全部", "error", "warning", "info"], key=f"finding_severity_{bundle_id}")
        codes = sorted({item.get("code", "finding") for item in findings if isinstance(item, dict)})
        code_filter = st.selectbox("问题类型", ["全部", *codes], key=f"finding_code_{bundle_id}")
        selected = [(index, item) for index, item in selected if isinstance(item, dict)
                    and (severity_filter == "全部" or item.get("severity") == severity_filter)
                    and (code_filter == "全部" or item.get("code") == code_filter)]
        total = len(selected)
        pages = max(1, (total + 24) // 25)
        page_number = st.number_input("问题页（每页 25 条）", min_value=1, max_value=pages, step=1,
                                      key=f"finding_page_{bundle_id}_{severity_filter}_{code_filter}")
        start = (page_number - 1) * 25
        st.caption(f"筛选后 {total} 条问题；当前显示第 {start + 1 if total else 0}–{min(start + 25, total)} 条。完整问题保留在审计 JSON 中。")
        selected = selected[start:start + 25]
    for index, finding in selected:
        if not isinstance(finding, dict):
            st.write(finding)
            continue
        severity = finding.get("severity", "info")
        code = finding.get("code", "finding")
        message = finding.get("message", finding.get("detail", "详情见证据"))
        with st.expander(f"{severity.upper()} · {code} · {message}"):
            st.json(finding)
            location = finding.get("location", {})
            location = location if isinstance(location, dict) else {}
            evidence = finding.get("evidence", {})
            evidence = evidence if isinstance(evidence, dict) else {}
            object_id = finding.get("object_id")
            artifact_id = finding.get("artifact_id", location.get("artifact_id", evidence.get("artifact_id")))
            if artifact_id is None and object_id in artifacts:
                artifact_id = object_id
            artifact = artifacts.get(artifact_id, {})
            ref = finding.get("source_ref", location.get("source_ref", evidence.get("source_ref")))
            if ref is None and object_id in sources:
                ref = object_id
            if ref is None:
                ref = next((item for item in artifact.get("source_refs", []) if item in sources), None)
            if ref in sources and st.button("定位这个来源的原文", key=f"locate_{bundle_id}_{index}"):
                st.session_state[f"source_{bundle_id}"] = ref
                if artifact_id in artifacts:
                    st.session_state[f"artifact_{bundle_id}"] = artifact_id
                else:
                    st.session_state[f"artifact_{bundle_id}"] = "（显示整篇原文）"
                st.session_state[f"located_{bundle_id}"] = ref
                st.rerun()
    with st.expander("审计覆盖与计数", expanded=not findings):
        st.json({"counts": audit.get("counts", {}), "coverage": audit.get("coverage", {})})
        st.caption("缺失日志只代表覆盖不足；不能据此断言某个来源没有影响。正文中的自称可信不会更改 trust 元数据。")


def _sources(bundle: dict, bundle_id: str) -> None:
    sources = bundle.get("sources", [])
    artifacts = bundle.get("artifacts", [])
    if not sources:
        st.info("本案件未提供原文来源。历史日志中的缺失内容不会被补造。")
        return
    st.dataframe([{"来源版本": source_ref(item), "标题": item.get("title", ""), "信任": item.get("trust", "unreviewed"), "状态": item.get("status", "active"), "SHA-256": item.get("sha256", "")} for item in sources], use_container_width=True, hide_index=True)
    source_map = {source_ref(item): item for item in sources}
    ref = st.selectbox("选择来源版本", list(source_map), key=f"source_{bundle_id}")
    source = source_map[ref]
    supported = {item["artifact_id"]: item for item in artifacts if ref in item.get("source_refs", [])}
    artifact_options = ["（显示整篇原文）"] + list(supported)
    artifact_key = f"artifact_{bundle_id}"
    if st.session_state.get(artifact_key) not in artifact_options:
        st.session_state[artifact_key] = artifact_options[0]
    artifact_id = st.selectbox("选择派生产物并定位跨度", artifact_options, key=artifact_key)
    artifact = supported.get(artifact_id, {})
    start, end = artifact.get("start"), artifact.get("end")
    st.caption(f"{ref} · 字符数 {len(source.get('text', ''))} · trust={source.get('trust', 'unreviewed')} · status={source.get('status', 'active')}")
    if artifact:
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(source.get("text", "")):
            st.caption(f"高亮原文区间 [{start}, {end})，按 Python Unicode 字符计数。")
        else:
            st.warning("这个产物没有有效的原文跨度；展示完整原文，不猜测位置。")
    st.markdown(source_html(source.get("text", ""), start, end), unsafe_allow_html=True)
    if artifact:
        with st.expander("该产物的完整记录"):
            st.json(artifact)
    st.markdown("#### 来源支持与派生依赖")
    st.caption("每行显示真实记录的支持来源和上游产物；空依赖不代表已证明与其他来源无关。")
    rows = [{"产物": item.get("artifact_id"), "类型": item.get("kind"), "支持来源": ", ".join(item.get("source_refs", [])), "依赖产物": ", ".join(item.get("dependencies", []))} for item in artifacts]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("没有提供派生产物。")


def _run_view(run: Any) -> None:
    if not isinstance(run, dict):
        st.json(run)
        return
    candidates = run.get("candidates", [])
    if candidates:
        rows = [{"排序": item.get("rank") if type(item.get("rank")) in (int, float) else None,
                 "来源": str(item.get("source_ref", "未记录")),
                 "分数": item.get("score") if type(item.get("score")) in (int, float) else None,
                 "产物 ID": str(item.get("artifact_id", ""))} for item in candidates]
        st.dataframe(rows, use_container_width=True, hide_index=True, column_config={
            "排序": st.column_config.NumberColumn(width="small"),
            "来源": st.column_config.TextColumn(width="medium"),
            "分数": st.column_config.NumberColumn(format="%.6f", width="small"),
            "产物 ID": st.column_config.TextColumn(width="large"),
        })
    else:
        st.caption("候选集合为空。")
    context_ids = run.get("context_ids", [])
    st.caption(f"实际上下文：{len(context_ids)} 个产物")
    context_text = run.get("context_text")
    if not isinstance(context_text, str):
        st.warning("没有提供可核验的实际上下文文本。")
        context_text = "（实际上下文未记录）"
    st.markdown(source_html(context_text or "（空上下文）"), unsafe_allow_html=True)
    with st.expander("查看上下文产物 ID"):
        st.code("\n".join(context_ids) or "（空）", language=None)
    if run.get("answer") is not None:
        st.write("记录中的输出：")
        st.code(str(run["answer"]), language=None)
        st.caption(f"输出类型：{run.get('answer_kind', '未声明')}；仅展示记录，不据此计算答案恢复率。")


def _report_view(report: dict) -> None:
    validation = report.get("validation", {})
    equivalent = validation.get("equivalent_to_full_rebuild")
    if equivalent is True:
        st.success("回放结果与独立完整重建一致。")
    elif equivalent is False:
        st.error("回放结果与独立完整重建不一致，请保留报告并检查。")
    else:
        st.warning("本报告没有提供完整重建一致性结论。")
    st.caption("比较的是此后端、此配置、此查询集合的检索记录；没有调用 LLM 的运行不报告答案恢复率。")
    for index, comparison in enumerate(report.get("queries", [])):
        with st.expander(f"查询 {index + 1} · {comparison.get('query', '')}", expanded=index == 0):
            st.write("**候选排序变化：**", comparison.get("ranking_changed", "未提供"))
            left, right = st.columns(2)
            with left:
                st.markdown("**撤回前**")
                _run_view(comparison.get("before", {}))
            with right:
                st.markdown("**撤回后**")
                _run_view(comparison.get("after", {}))
            st.json({"上下文新增": comparison.get("context_added", []), "上下文移除": comparison.get("context_removed", []), "与完整重建等价": comparison.get("equivalent")})
            with st.expander("完整重建参照记录"):
                _run_view(comparison.get("reference", {}))
    with st.expander("失效传播、重建指标与验证详情"):
        st.json({"invalidation": report.get("invalidation", {}), "metrics": report.get("metrics", {}), "validation": validation})
    report_id = str(report.get("report_id", "replay"))
    st.caption("完整报告可能包含原文、查询和来源标识。对外分享可使用只保留哈希与汇总值的摘要；摘要不能完整回放。")
    first, second, third = st.columns(3)
    with first:
        st.download_button("完整报告（含原文） · JSON", _json(report), file_name=f"{report_id}.json", mime="application/json", key=f"download_json_{report_id}", use_container_width=True)
    with second:
        st.download_button("完整报告（含原文） · Markdown", render_markdown(report), file_name=f"{report_id}.md", mime="text/markdown", key=f"download_md_{report_id}", use_container_width=True)
    with third:
        st.download_button("分享摘要（不含原文）", _json(sharing_summary(report)), file_name=f"{report_id}-summary.json", mime="application/json", key=f"download_summary_{report_id}", use_container_width=True)


def _replay(workbench: Workbench, bundle: dict, bundle_id: str) -> None:
    executable = can_replay(bundle)
    if executable:
        st.info("在保留原始证据的副本中撤回来源，重新构建 BM25 索引并执行查询，再与独立完整重建比较。")
    else:
        st.warning("本案件为历史导入或不受支持的后端：支持审计、查看和比较已有记录，不能在这里执行源撤回。")
    refs = [source_ref(source) for source in bundle.get("sources", []) if source.get("status", "active") == "active"]
    defaults = [ref for ref in DEMO_WITHDRAWALS if ref in refs] if st.session_state.get("demo_case") == bundle_id else []
    selected = st.multiselect("② 选择要撤回的来源版本（可多选）", refs, default=defaults, key=f"withdraw_{bundle_id}", disabled=not executable)
    original_queries = list(dict.fromkeys(run.get("query", "") for run in bundle.get("runs", []) if run.get("query")))
    query_text = st.text_area("③ 查询集合（每行一条，可添加新查询；留空沿用原查询）", value="\n".join(original_queries or DEMO_QUERIES), key=f"queries_{bundle_id}", height=100, disabled=not executable)
    if st.button("执行撤回与完整重建验证", key="execute_replay", type="primary", disabled=not executable or not selected):
        try:
            queries = list(dict.fromkeys(line.strip() for line in query_text.splitlines() if line.strip())) or None
            with st.spinner("正在重建索引、重新检索并核验结果……"):
                report = workbench.replay(bundle_id, selected, queries)
            st.session_state[f"last_report_{bundle_id}"] = report
            if report.get("report_id"):
                st.session_state[f"report_picker_{bundle_id}"] = report["report_id"]
            st.success("已执行并保存回放报告。")
        except Exception as error:
            _error("撤回回放", error)
    report = st.session_state.get(f"last_report_{bundle_id}")
    try:
        reports = workbench.list_reports(bundle_id=bundle_id)
        if reports:
            report_map = {item["report_id"]: item for item in reports}
            report_ids = list(report_map)
            default_index = report_ids.index(report["report_id"]) if report and report.get("report_id") in report_ids else 0
            selected_report = st.selectbox("查看已保存的回放报告", report_ids, index=default_index, format_func=lambda value: f"{value[:16]} · {report_map[value].get('created_at', '')}", key=f"report_picker_{bundle_id}")
            report = workbench.get_report(selected_report)
    except Exception as error:
        _error("读取历史报告", error)
    if report:
        _report_view(report)
    elif executable:
        st.caption("选择来源后执行，报告会在这里显示；完整报告包含撤回后的证据快照。")


def _history(bundle: dict) -> None:
    runs = bundle.get("runs", [])
    if not runs:
        st.info("本案件没有运行记录。")
    if len(runs) >= 2:
        with st.expander("比较两条已保存的运行记录"):
            st.caption("这是记录之间的观察比较，不代表本次重新执行或已证明干预因果。")
            left_picker, right_picker = st.columns(2)
            with left_picker:
                before_index = st.selectbox("比较前记录", list(range(len(runs))), format_func=lambda index: f"{index + 1} · {runs[index].get('query', '')}", key="history_before")
            with right_picker:
                after_index = st.selectbox("比较后记录", list(range(len(runs))), index=1, format_func=lambda index: f"{index + 1} · {runs[index].get('query', '')}", key="history_after")
            before, after = runs[before_index], runs[after_index]
            left, right = st.columns(2)
            with left:
                st.markdown("**记录 A**")
                _run_view(before)
            with right:
                st.markdown("**记录 B**")
                _run_view(after)
            before_ids, after_ids = set(before.get("context_ids", [])), set(after.get("context_ids", []))
            st.json({"上下文新增": sorted(after_ids - before_ids), "上下文移除": sorted(before_ids - after_ids)})
    for index, run in enumerate(runs):
        with st.expander(f"运行 {index + 1} · {run.get('query', '')}", expanded=index == 0):
            execution = run.get("execution", "未声明")
            st.caption(f"执行状态：{execution} · 后端：{run.get('backend', '未声明')} · 运行 ID：{run.get('run_id', '')}")
            if execution == "imported_history":
                st.info("导入的历史结果。本页面没有重新执行这条运行。")
            _run_view(run)
            with st.expander("完整运行条件与记录"):
                st.json(run)
    with st.expander("案件元数据与接入能力"):
        st.json(bundle.get("metadata", {}))


def main() -> None:
    st.set_page_config(page_title="RAG 来源审计工作台", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
    _style()
    st.markdown('<div class="eyebrow">RAG AUDIT WORKBENCH / 证据先于结论</div>', unsafe_allow_html=True)
    st.title("RAG 来源审计工作台")
    st.write("导入记录 → 核验来源 → 撤回回放 → 对照完整重建")
    try:
        db_path = Path(os.environ.get("RAG_AUDIT_HOME", ".rag-audit")) / "evidence.sqlite"
        workbench = Workbench(db_path)
        _ingest_panel(workbench)
        cases = workbench.list_bundles()
    except Exception as error:
        _error("打开本地证据库", error)
        st.stop()
    with st.sidebar:
        st.divider()
        st.markdown("### ④ 核验证据账本")
        if st.button("验证事件链", key="verify_ledger", use_container_width=True):
            try:
                result = workbench.verify_ledger()
                valid = result.get("valid", result.get("passed", result.get("ok")))
                if valid is True:
                    st.success("事件链验证通过。")
                elif valid is False:
                    st.error("事件链验证失败。")
                st.json(result)
            except Exception as error:
                _error("事件链验证", error)
        st.caption("哈希链用于发现修改；它不证明原始内容真实。外部检查点可增强复核。")
        st.caption(f"本地数据库：{db_path.resolve()}")
    if not cases:
        st.markdown("### 从一个可复核的案例开始")
        st.write("点击左侧 **载入内置示例**，查看来源版本与原文；随后选择一个或多个来源，执行撤回并比较真实检索结果。")
        st.markdown('<div class="scope">首版使用离线 CPU BM25。GraphRAG 重建与真实 LLM 生成尚未实现；本工具不自动判断来源恶意。</div>', unsafe_allow_html=True)
        st.stop()
    case_map = {item["bundle_id"]: item for item in cases}
    if st.session_state.get("case_picker") not in case_map:
        st.session_state["case_picker"] = next(iter(case_map))
    bundle_id = st.selectbox("当前案件", list(case_map), format_func=lambda value: f"{case_map[value].get('label') or '未命名案件'} · {value[:12]}", key="case_picker")
    st.session_state["active_case"] = bundle_id
    try:
        bundle = workbench.get_bundle(bundle_id)
        audit = workbench.audit(bundle_id)
    except Exception as error:
        _error("读取案件", error)
        st.stop()
    st.caption(f"案件 {bundle_id} · {'可执行离线检索回放' if can_replay(bundle) else '历史导入 / 仅审计与比较'}")
    for column, label, value in zip(st.columns(4), ["来源版本", "派生产物", "运行记录", "审计问题"], [len(bundle.get("sources", [])), len(bundle.get("artifacts", [])), len(bundle.get("runs", [])), len(audit.get("findings", []))]):
        column.metric(label, value)
    audit_tab, source_tab, replay_tab, history_tab = st.tabs(["01 · 审计问题", "02 · 原文与依赖", "03 · 撤回与比较", "04 · 运行记录"])
    with audit_tab:
        _findings(audit, bundle, bundle_id)
    with source_tab:
        if st.session_state.get(f"located_{bundle_id}"):
            st.info(f"已定位：{st.session_state[f'located_{bundle_id}']}。下方来源选择与跨度已更新。")
        _sources(bundle, bundle_id)
    with replay_tab:
        _replay(workbench, bundle, bundle_id)
    with history_tab:
        _history(bundle)
        st.download_button("导出本案件统一格式 JSON", _json(bundle), file_name=f"bundle-{bundle_id[:12]}.json", mime="application/json", key="download_bundle")
    st.divider()
    st.caption("撤回改变了结果，仅说明指定条件下存在影响。恶意判断、攻击者归因和现实因果需要独立证据。")


if __name__ == "__main__":
    main()
