"""Local bilingual case viewer. All mutations go through the shared Workbench service."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from rag_audit.demo import DEMO_QUERIES, DEMO_WITHDRAWALS, demo_bundle
from rag_audit.importers import load_input
from rag_audit.i18n import t
from rag_audit.report import render_markdown, sharing_summary
from rag_audit.schema import source_ref
from rag_audit.service import Workbench

FULL_SOURCE = "__full_source__"
ALL_FINDINGS = "all"


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


def _error(action: str, error: Exception, lang: str = "zh") -> None:
    st.error(t(lang, "{action}失败：{kind} · {error}", action=action, kind=type(error).__name__, error=error))


def _option_widget_key(name: str, lang: str) -> str:
    # Translated option strings need a fresh frontend widget on language changes.
    return name if lang == "zh" else f"{name}_en"


def _prepare_translated_options(lang: str) -> None:
    """Capture canonical choices before remounting translated selectboxes."""
    previous_lang = st.session_state.get("settings_widget_locale", "zh")
    for name in st.session_state.get("settings_option_widgets", []):
        previous_key = _option_widget_key(name, previous_lang)
        saved_key = f"settings_{name}"
        if previous_key in st.session_state:
            st.session_state[saved_key] = st.session_state[previous_key]
        if previous_lang != lang and saved_key in st.session_state:
            next_key = _option_widget_key(name, lang)
            # Explicitly seed the new widget to send set_value/raw_value, including
            # on returning to a language whose frontend widget existed earlier.
            st.session_state[next_key] = st.session_state[saved_key]
    st.session_state["settings_widget_locale"] = lang


def _set_translated_choice(name: str, value: str) -> None:
    """Programmatic navigation updates both storage and the active widget."""
    st.session_state[f"settings_{name}"] = value
    lang = st.session_state.get("settings_widget_locale", "zh")
    st.session_state[_option_widget_key(name, lang)] = value


def _translated_selectbox(label: str, options: list[str], name: str, lang: str, format_func) -> str:
    """Keep option identities independent of locale-specific frontend strings."""
    names = list(st.session_state.get("settings_option_widgets", []))
    if name not in names:
        names.append(name)
        st.session_state["settings_option_widgets"] = names
    widget_key = _option_widget_key(name, lang)
    saved_key = f"settings_{name}"
    selected = st.session_state.get(saved_key, st.session_state.get(widget_key, options[0]))
    if selected not in options:
        selected = options[0]
    if widget_key in st.session_state and st.session_state[widget_key] not in options:
        del st.session_state[widget_key]
    index = 0 if widget_key in st.session_state else options.index(selected)
    value = st.selectbox(label, options, index=index, key=widget_key, format_func=format_func)
    st.session_state[saved_key] = value
    return value


def _select_case(bundle_id: str) -> None:
    st.session_state["active_case"] = bundle_id
    _set_translated_choice("case_picker", bundle_id)


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


def _ingest_panel(workbench: Workbench, lang: str = "zh") -> None:
    with st.sidebar:
        st.markdown(t(lang, '### ① 建立案件'))
        st.caption(t(lang, '先用内置案例走通流程，再导入自己的记录。'))
        if st.button(t(lang, '载入内置示例'), key="load_demo", type="primary", use_container_width=True):
            try:
                bundle_id = workbench.ingest(demo_bundle(), label="Built-in example · Source withdrawal")
                _select_case(bundle_id)
                st.session_state["demo_case"] = bundle_id
                st.success(t(lang, '示例已载入；重复载入会复用相同证据。'))
            except Exception as error:
                _error(t(lang, '载入示例'), error, lang=lang)
        upload = st.file_uploader(t(lang, '导入证据文件'), type=["json", "jsonl"], key="evidence_upload", help=t(lang, 'UTF-8；统一 bundle JSON 或受支持的 M4/M5 JSONL；不超过 25 MiB。'))
        label = st.text_input(t(lang, '案件名称（可选）'), key="import_label", placeholder=t(lang, '例如：知识库版本更新复核'))
        if st.button(t(lang, '导入并建立案件'), key="ingest_upload", disabled=upload is None, use_container_width=True):
            try:
                assert upload is not None
                bundle_id = workbench.ingest(load_input(upload.getvalue(), upload.name), label=label or upload.name)
                _select_case(bundle_id)
                st.success(t(lang, '证据已导入。'))
            except Exception as error:
                _error(t(lang, '导入'), error, lang=lang)
        with st.expander(t(lang, '也可粘贴小段 JSON / JSONL')):
            pasted = st.text_area(t(lang, '粘贴 JSON / JSONL'), key="pasted_input", height=100)
            input_format = st.selectbox(t(lang, '粘贴格式'), ["JSON", "JSONL"], key="pasted_format")
            if st.button(t(lang, '导入粘贴内容'), key="ingest_text", disabled=not pasted.strip()):
                try:
                    filename = "pasted.jsonl" if input_format == "JSONL" else "pasted.json"
                    bundle_id = workbench.ingest(load_input(pasted.encode("utf-8"), filename), label=label or "Pasted evidence")
                    _select_case(bundle_id)
                    st.success(t(lang, '证据已导入。'))
                except Exception as error:
                    _error(t(lang, '导入'), error, lang=lang)


def _findings(audit: dict, bundle: dict, bundle_id: str, lang: str = "zh") -> None:
    findings = audit.get("findings", [])
    if audit.get("passed"):
        st.success(t(lang, '已提供记录的完整性检查通过。检查范围见下方覆盖信息。'))
    elif findings:
        st.warning(t(lang, '发现待核验问题。先定位原文与依赖，再决定撤回集合。'))
    else:
        st.info(t(lang, '没有可定位的问题记录；请同时检查覆盖信息。'))
    sources = {source_ref(item): item for item in bundle.get("sources", [])}
    artifacts = {item["artifact_id"]: item for item in bundle.get("artifacts", [])}
    selected = list(enumerate(findings))
    if len(findings) > 25:
        severity_filter = _translated_selectbox(t(lang, '问题级别'), [ALL_FINDINGS, "error", "warning", "info"], f"finding_severity_{bundle_id}", lang, format_func=lambda value: t(lang, "全部") if value == ALL_FINDINGS else value)
        codes = sorted({item.get("code", "finding") for item in findings if isinstance(item, dict)})
        code_filter = _translated_selectbox(t(lang, '问题类型'), [ALL_FINDINGS, *codes], f"finding_code_{bundle_id}", lang, format_func=lambda value: t(lang, "全部") if value == ALL_FINDINGS else value)
        selected = [(index, item) for index, item in selected if isinstance(item, dict)
                    and (severity_filter == ALL_FINDINGS or item.get("severity") == severity_filter)
                    and (code_filter == ALL_FINDINGS or item.get("code") == code_filter)]
        total = len(selected)
        pages = max(1, (total + 24) // 25)
        page_number = st.number_input(t(lang, '问题页（每页 25 条）'), min_value=1, max_value=pages, step=1,
                                      key=f"finding_page_{bundle_id}_{severity_filter}_{code_filter}")
        start = (page_number - 1) * 25
        st.caption(t(lang, "筛选后 {total} 条问题；当前显示第 {first}–{last} 条。完整问题保留在审计 JSON 中。", total=total, first=start + 1 if total else 0, last=min(start + 25, total)))
        selected = selected[start:start + 25]
    for index, finding in selected:
        if not isinstance(finding, dict):
            st.write(finding)
            continue
        severity = finding.get("severity", "info")
        code = finding.get("code", "finding")
        message = finding.get("message", finding.get("detail", t(lang, '详情见证据')))
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
            if ref in sources and st.button(t(lang, '定位这个来源的原文'), key=f"locate_{bundle_id}_{index}"):
                st.session_state[f"source_{bundle_id}"] = ref
                if artifact_id in artifacts:
                    _set_translated_choice(f"artifact_{bundle_id}", artifact_id)
                else:
                    _set_translated_choice(f"artifact_{bundle_id}", FULL_SOURCE)
                st.session_state[f"located_{bundle_id}"] = ref
                st.rerun()
    with st.expander(t(lang, '审计覆盖与计数'), expanded=not findings):
        st.json({"counts": audit.get("counts", {}), "coverage": audit.get("coverage", {})})
        st.caption(t(lang, '缺失日志只代表覆盖不足；不能据此断言某个来源没有影响。正文中的自称可信不会更改 trust 元数据。'))


def _sources(bundle: dict, bundle_id: str, lang: str = "zh") -> None:
    sources = bundle.get("sources", [])
    artifacts = bundle.get("artifacts", [])
    if not sources:
        st.info(t(lang, '本案件未提供原文来源。历史日志中的缺失内容不会被补造。'))
        return
    st.dataframe([{t(lang, '来源版本'): source_ref(item), t(lang, '标题'): item.get("title", ""), t(lang, '信任'): item.get("trust", "unreviewed"), t(lang, '状态'): item.get("status", "active"), "SHA-256": item.get("sha256", "")} for item in sources], use_container_width=True, hide_index=True)
    source_map = {source_ref(item): item for item in sources}
    ref = st.selectbox(t(lang, '选择来源版本'), list(source_map), key=f"source_{bundle_id}")
    source = source_map[ref]
    supported = {item["artifact_id"]: item for item in artifacts if ref in item.get("source_refs", [])}
    artifact_options = [FULL_SOURCE] + list(supported)
    artifact_id = _translated_selectbox(t(lang, '选择派生产物并定位跨度'), artifact_options, f"artifact_{bundle_id}", lang, format_func=lambda value: t(lang, "（显示整篇原文）") if value == FULL_SOURCE else value)
    artifact = supported.get(artifact_id, {})
    start, end = artifact.get("start"), artifact.get("end")
    st.caption(t(lang, "{ref} · 字符数 {count} · trust={trust} · status={status}", ref=ref, count=len(source.get("text", "")), trust=source.get("trust", "unreviewed"), status=source.get("status", "active")))
    if artifact:
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(source.get("text", "")):
            st.caption(t(lang, "高亮原文区间 [{start}, {end})，按 Python Unicode 字符计数。", start=start, end=end))
        else:
            st.warning(t(lang, '这个产物没有有效的原文跨度；展示完整原文，不猜测位置。'))
    st.markdown(source_html(source.get("text", ""), start, end), unsafe_allow_html=True)
    if artifact:
        with st.expander(t(lang, '该产物的完整记录')):
            st.json(artifact)
    st.markdown(t(lang, '#### 来源支持与派生依赖'))
    st.caption(t(lang, '每行显示真实记录的支持来源和上游产物；空依赖不代表已证明与其他来源无关。'))
    rows = [{t(lang, '产物'): item.get("artifact_id"), t(lang, '类型'): item.get("kind"), t(lang, '支持来源'): ", ".join(item.get("source_refs", [])), t(lang, '依赖产物'): ", ".join(item.get("dependencies", []))} for item in artifacts]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info(t(lang, '没有提供派生产物。'))


def _run_view(run: Any, lang: str = "zh") -> None:
    if not isinstance(run, dict):
        st.json(run)
        return
    candidates = run.get("candidates", [])
    if candidates:
        rows = [{t(lang, '排序'): item.get("rank") if type(item.get("rank")) in (int, float) else None,
                 t(lang, '来源'): str(item.get("source_ref", t(lang, '未记录'))),
                 t(lang, '分数'): item.get("score") if type(item.get("score")) in (int, float) else None,
                 t(lang, '产物 ID'): str(item.get("artifact_id", ""))} for item in candidates]
        st.dataframe(rows, use_container_width=True, hide_index=True, column_config={
            t(lang, '排序'): st.column_config.NumberColumn(width="small"),
            t(lang, '来源'): st.column_config.TextColumn(width="medium"),
            t(lang, '分数'): st.column_config.NumberColumn(format="%.6f", width="small"),
            t(lang, '产物 ID'): st.column_config.TextColumn(width="large"),
        })
    else:
        st.caption(t(lang, '候选集合为空。'))
    context_ids = run.get("context_ids", [])
    st.caption(t(lang, "实际上下文：{count} 个产物", count=len(context_ids)))
    context_text = run.get("context_text")
    if not isinstance(context_text, str):
        st.warning(t(lang, '没有提供可核验的实际上下文文本。'))
        context_text = t(lang, '（实际上下文未记录）')
    st.markdown(source_html(context_text or t(lang, '（空上下文）')), unsafe_allow_html=True)
    with st.expander(t(lang, '查看上下文产物 ID')):
        st.code("\n".join(context_ids) or t(lang, '（空）'), language=None)
    if run.get("answer") is not None:
        st.write(t(lang, '记录中的输出：'))
        st.code(str(run["answer"]), language=None)
        st.caption(t(lang, "输出类型：{kind}；仅展示记录，不据此计算答案恢复率。", kind=run.get("answer_kind", t(lang, '未声明'))))


def _report_view(report: dict, lang: str = "zh") -> None:
    validation = report.get("validation", {})
    equivalent = validation.get("equivalent_to_full_rebuild")
    if equivalent is True:
        st.success(t(lang, '回放结果与独立完整重建一致。'))
    elif equivalent is False:
        st.error(t(lang, '回放结果与独立完整重建不一致，请保留报告并检查。'))
    else:
        st.warning(t(lang, '本报告没有提供完整重建一致性结论。'))
    st.caption(t(lang, '比较的是此后端、此配置、此查询集合的检索记录；没有调用 LLM 的运行不报告答案恢复率。'))
    for index, comparison in enumerate(report.get("queries", [])):
        with st.expander(t(lang, "查询 {number} · {query}", number=index + 1, query=comparison.get("query", "")), expanded=index == 0):
            st.write(t(lang, '**候选排序变化：**'), comparison.get("ranking_changed", t(lang, '未提供')))
            left, right = st.columns(2)
            with left:
                st.markdown(t(lang, '**撤回前**'))
                _run_view(comparison.get("before", {}), lang=lang)
            with right:
                st.markdown(t(lang, '**撤回后**'))
                _run_view(comparison.get("after", {}), lang=lang)
            st.json({"context_added": comparison.get("context_added", []), "context_removed": comparison.get("context_removed", []), "equivalent": comparison.get("equivalent")})
            with st.expander(t(lang, '完整重建参照记录')):
                _run_view(comparison.get("reference", {}), lang=lang)
    with st.expander(t(lang, '失效传播、重建指标与验证详情')):
        st.json({"invalidation": report.get("invalidation", {}), "metrics": report.get("metrics", {}), "validation": validation})
    report_id = str(report.get("report_id", "replay"))
    st.caption(t(lang, '完整报告可能包含原文、查询和来源标识。对外分享可使用只保留哈希与汇总值的摘要；摘要不能完整回放。'))
    first, second, third = st.columns(3)
    with first:
        st.download_button(t(lang, '完整报告（含原文） · JSON'), _json(report), file_name=f"{report_id}.json", mime="application/json", key=f"download_json_{report_id}", use_container_width=True)
    with second:
        st.download_button(t(lang, '完整报告（含原文） · Markdown'), render_markdown(report, lang=lang), file_name=f"{report_id}.md", mime="text/markdown", key=f"download_md_{report_id}", use_container_width=True)
    with third:
        st.download_button(t(lang, '分享摘要（不含原文）'), _json(sharing_summary(report)), file_name=f"{report_id}-summary.json", mime="application/json", key=f"download_summary_{report_id}", use_container_width=True)


def _replay(workbench: Workbench, bundle: dict, bundle_id: str, lang: str = "zh") -> None:
    executable = can_replay(bundle)
    if executable:
        st.info(t(lang, '在保留原始证据的副本中撤回来源，重新构建 BM25 索引并执行查询，再与独立完整重建比较。'))
    else:
        st.warning(t(lang, '本案件为历史导入或不受支持的后端：支持审计、查看和比较已有记录，不能在这里执行源撤回。'))
    refs = [source_ref(source) for source in bundle.get("sources", []) if source.get("status", "active") == "active"]
    defaults = [ref for ref in DEMO_WITHDRAWALS if ref in refs] if st.session_state.get("demo_case") == bundle_id else []
    selected = st.multiselect(t(lang, '② 选择要撤回的来源版本（可多选）'), refs, default=defaults, key=f"withdraw_{bundle_id}", disabled=not executable)
    original_queries = list(dict.fromkeys(run.get("query", "") for run in bundle.get("runs", []) if run.get("query")))
    query_text = st.text_area(t(lang, '③ 查询集合（每行一条，可添加新查询；留空沿用原查询）'), value="\n".join(original_queries or DEMO_QUERIES), key=f"queries_{bundle_id}", height=100, disabled=not executable)
    if st.button(t(lang, '执行撤回与完整重建验证'), key="execute_replay", type="primary", disabled=not executable or not selected):
        try:
            queries = list(dict.fromkeys(line.strip() for line in query_text.splitlines() if line.strip())) or None
            with st.spinner(t(lang, '正在重建索引、重新检索并核验结果……')):
                report = workbench.replay(bundle_id, selected, queries)
            st.session_state[f"last_report_{bundle_id}"] = report
            if report.get("report_id"):
                st.session_state[f"report_picker_{bundle_id}"] = report["report_id"]
            st.success(t(lang, '已执行并保存回放报告。'))
        except Exception as error:
            _error(t(lang, '撤回回放'), error, lang=lang)
    report = st.session_state.get(f"last_report_{bundle_id}")
    try:
        reports = workbench.list_reports(bundle_id=bundle_id)
        if reports:
            report_map = {item["report_id"]: item for item in reports}
            report_ids = list(report_map)
            default_index = report_ids.index(report["report_id"]) if report and report.get("report_id") in report_ids else 0
            selected_report = st.selectbox(t(lang, '查看已保存的回放报告'), report_ids, index=default_index, format_func=lambda value: f"{value[:16]} · {report_map[value].get('created_at', '')}", key=f"report_picker_{bundle_id}")
            report = workbench.get_report(selected_report)
    except Exception as error:
        _error(t(lang, '读取历史报告'), error, lang=lang)
    if report:
        _report_view(report, lang=lang)
    elif executable:
        st.caption(t(lang, '选择来源后执行，报告会在这里显示；完整报告包含撤回后的证据快照。'))


def _history(bundle: dict, lang: str = "zh") -> None:
    runs = bundle.get("runs", [])
    if not runs:
        st.info(t(lang, '本案件没有运行记录。'))
    if len(runs) >= 2:
        with st.expander(t(lang, '比较两条已保存的运行记录')):
            st.caption(t(lang, '这是记录之间的观察比较，不代表本次重新执行或已证明干预因果。'))
            left_picker, right_picker = st.columns(2)
            with left_picker:
                before_index = st.selectbox(t(lang, '比较前记录'), list(range(len(runs))), format_func=lambda index: f"{index + 1} · {runs[index].get('query', '')}", key="history_before")
            with right_picker:
                after_index = st.selectbox(t(lang, '比较后记录'), list(range(len(runs))), index=1, format_func=lambda index: f"{index + 1} · {runs[index].get('query', '')}", key="history_after")
            before, after = runs[before_index], runs[after_index]
            left, right = st.columns(2)
            with left:
                st.markdown(t(lang, '**记录 A**'))
                _run_view(before, lang=lang)
            with right:
                st.markdown(t(lang, '**记录 B**'))
                _run_view(after, lang=lang)
            before_ids, after_ids = set(before.get("context_ids", [])), set(after.get("context_ids", []))
            st.json({"context_added": sorted(after_ids - before_ids), "context_removed": sorted(before_ids - after_ids)})
    for index, run in enumerate(runs):
        with st.expander(t(lang, "运行 {number} · {query}", number=index + 1, query=run.get("query", "")), expanded=index == 0):
            execution = run.get("execution", t(lang, '未声明'))
            st.caption(t(lang, "执行状态：{execution} · 后端：{backend} · 运行 ID：{run_id}", execution=execution, backend=run.get("backend", t(lang, '未声明')), run_id=run.get("run_id", "")))
            if execution == "imported_history":
                st.info(t(lang, '导入的历史结果。本页面没有重新执行这条运行。'))
            _run_view(run, lang=lang)
            with st.expander(t(lang, '完整运行条件与记录')):
                st.json(run)
    with st.expander(t(lang, '案件元数据与接入能力')):
        st.json(bundle.get("metadata", {}))


def _case_label(label: str, lang: str) -> str:
    defaults = {
        "Built-in example · Source withdrawal": "内置示例 · 来源撤回",
        "内置示例 · 来源撤回": "内置示例 · 来源撤回",
        "Built-in: duplicate sources and normal queries": "内置：重复来源与正常查询",
        "内置：重复来源与正常查询": "内置：重复来源与正常查询",
        "Pasted evidence": "粘贴证据", "粘贴证据": "粘贴证据",
    }
    return t(lang, defaults[label]) if label in defaults else label


def main() -> None:
    lang = "en" if st.session_state.get("ui_language") == "English" else "zh"
    _prepare_translated_options(lang)
    st.set_page_config(page_title=t(lang, 'RAG 来源审计工作台'), page_icon="◈", layout="wide", initial_sidebar_state="expanded")
    st.sidebar.selectbox(t(lang, "语言 / Language"), ["简体中文", "English"], key="ui_language")
    _style()
    st.markdown(t(lang, '<div class="eyebrow">RAG AUDIT WORKBENCH / 证据先于结论</div>'), unsafe_allow_html=True)
    st.title(t(lang, 'RAG 来源审计工作台'))
    st.write(t(lang, '导入记录 → 核验来源 → 撤回回放 → 对照完整重建'))
    try:
        db_path = Path(os.environ.get("RAG_AUDIT_HOME", ".rag-audit")) / "evidence.sqlite"
        workbench = Workbench(db_path)
        _ingest_panel(workbench, lang=lang)
        cases = workbench.list_bundles()
    except Exception as error:
        _error(t(lang, '打开本地证据库'), error, lang=lang)
        st.stop()
    with st.sidebar:
        st.divider()
        st.markdown(t(lang, '### ④ 核验证据账本'))
        if st.button(t(lang, '验证事件链'), key="verify_ledger", use_container_width=True):
            try:
                result = workbench.verify_ledger()
                valid = result.get("valid", result.get("passed", result.get("ok")))
                if valid is True:
                    st.success(t(lang, '事件链验证通过。'))
                elif valid is False:
                    st.error(t(lang, '事件链验证失败。'))
                st.json(result)
            except Exception as error:
                _error(t(lang, '事件链验证'), error, lang=lang)
        st.caption(t(lang, '哈希链用于发现修改；它不证明原始内容真实。外部检查点可增强复核。'))
        st.caption(t(lang, "本地数据库：{path}", path=db_path.resolve()))
    if not cases:
        st.markdown(t(lang, '### 从一个可复核的案例开始'))
        st.write(t(lang, '点击左侧 **载入内置示例**，查看来源版本与原文；随后选择一个或多个来源，执行撤回并比较真实检索结果。'))
        st.markdown(t(lang, '<div class="scope">首版使用离线 CPU BM25。GraphRAG 重建与真实 LLM 生成尚未实现；本工具不自动判断来源恶意。</div>'), unsafe_allow_html=True)
        st.stop()
    case_map = {item["bundle_id"]: item for item in cases}
    bundle_id = _translated_selectbox(t(lang, '当前案件'), list(case_map), "case_picker", lang, format_func=lambda value: f"{_case_label(case_map[value].get('label') or t(lang, '未命名案件'), lang)} · {value[:12]}")
    st.session_state["active_case"] = bundle_id
    try:
        bundle = workbench.get_bundle(bundle_id)
        audit = workbench.audit(bundle_id)
    except Exception as error:
        _error(t(lang, '读取案件'), error, lang=lang)
        st.stop()
    st.caption(t(lang, "案件 {bundle_id} · {mode}", bundle_id=bundle_id, mode=t(lang, '可执行离线检索回放' if can_replay(bundle) else '历史导入 / 仅审计与比较')))
    for column, label, value in zip(st.columns(4), [t(lang, '来源版本'), t(lang, '派生产物'), t(lang, '运行记录'), t(lang, '审计问题')], [len(bundle.get("sources", [])), len(bundle.get("artifacts", [])), len(bundle.get("runs", [])), len(audit.get("findings", []))]):
        column.metric(label, value)
    audit_tab, source_tab, replay_tab, history_tab = st.tabs([t(lang, '01 · 审计问题'), t(lang, '02 · 原文与依赖'), t(lang, '03 · 撤回与比较'), t(lang, '04 · 运行记录')])
    with audit_tab:
        _findings(audit, bundle, bundle_id, lang=lang)
    with source_tab:
        if st.session_state.get(f"located_{bundle_id}"):
            st.info(t(lang, "已定位：{ref}。下方来源选择与跨度已更新。", ref=st.session_state[f"located_{bundle_id}"]))
        _sources(bundle, bundle_id, lang=lang)
    with replay_tab:
        _replay(workbench, bundle, bundle_id, lang=lang)
    with history_tab:
        _history(bundle, lang=lang)
        st.download_button(t(lang, '导出本案件统一格式 JSON'), _json(bundle), file_name=f"bundle-{bundle_id[:12]}.json", mime="application/json", key="download_bundle")
    st.divider()
    st.caption(t(lang, '撤回改变了结果，仅说明指定条件下存在影响。恶意判断、攻击者归因和现实因果需要独立证据。'))


if __name__ == "__main__":
    main()
