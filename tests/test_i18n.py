"""Localization changes presentation, never evidence or machine protocols."""
from __future__ import annotations

import copy
import json
from string import Formatter

import pytest

from rag_audit.cli import main
from rag_audit.cli_i18n import CLI_MESSAGES
from rag_audit.demo import DEMO_WITHDRAWALS, demo_bundle
from rag_audit.i18n import MESSAGES, t
from rag_audit.report import render_markdown, sharing_summary
from rag_audit.service import Workbench


def test_catalogs_have_matching_keys_and_format_fields():
    for catalog in (MESSAGES, CLI_MESSAGES):
        assert catalog["zh"].keys() == catalog["en"].keys()
        for key in catalog["zh"]:
            fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
            assert fields(catalog["zh"][key]) == fields(catalog["en"][key]), key
            assert catalog["zh"][key] and catalog["en"][key]
    assert t("unknown", "载入内置示例") == t("zh", "载入内置示例")
    with pytest.raises(KeyError):
        t("en", "missing-message")


@pytest.mark.parametrize("argv, expected", [
    (["--lang", "en", "--help"], "Offline RAG evidence auditing"),
    (["replay", "--lang", "en", "--help"], "Execute withdrawal and replay"),
    (["--lang=en", "report", "--help"], "Export a stored intervention report"),
    (["--lang", "zh", "--help"], "离线 RAG 证据审计"),
    (["replay", "--lang", "zh", "--help"], "撤回并回放"),
])
def test_cli_help_respects_language_at_both_positions(argv, expected, capsys):
    with pytest.raises(SystemExit) as status:
        main(argv)
    assert status.value.code == 0
    help_text = capsys.readouterr().out
    assert expected in help_text
    assert "--lang" in help_text
    if "en" in argv or "--lang=en" in argv:
        assert not any("\u4e00" <= char <= "\u9fff" for char in help_text)


@pytest.mark.parametrize("lang, prefix", [("en", "Error: "), ("zh", "错误：")])
def test_cli_failure_prefix_and_exit_code(lang, prefix, tmp_path, capsys):
    assert main(["inspect", str(tmp_path / "missing.json"), "--lang", lang]) == 2
    error = json.loads(capsys.readouterr().err)
    assert set(error) == {"error"}
    assert error["error"].startswith(prefix)


def test_markdown_locales_preserve_evidence_and_report(tmp_path):
    workbench = Workbench(tmp_path / "evidence.sqlite")
    key = workbench.ingest(demo_bundle())
    report = workbench.replay(key, DEMO_WITHDRAWALS)
    original = copy.deepcopy(report)
    summary = sharing_summary(report)
    zh, en = render_markdown(report), render_markdown(report, lang="en")
    assert zh.startswith("# RAG 源撤回审计报告")
    assert en.startswith("# RAG Source-Withdrawal Audit Report")
    assert "No generation model was enabled" in en
    assert "## Full-rebuild comparison" in en
    for ref in report["withdrawn_refs"]:
        assert ref in zh and ref in en
    assert "星桥项目" in zh and "星桥项目" in en
    assert report == original
    assert sharing_summary(report) == summary
    assert render_markdown(report, lang="unknown") == zh
    assert workbench.get_report(report["report_id"]) == original


def test_cli_report_and_summary_json_are_language_invariant(tmp_path, capsys):
    database = tmp_path / "evidence.sqlite"
    workbench = Workbench(database)
    key = workbench.ingest(demo_bundle())
    report = workbench.replay(key, DEMO_WITHDRAWALS)
    for format_name in ("json", "summary"):
        outputs = []
        for lang in ("en", "zh"):
            assert main(["--db", str(database), "--lang", lang, "report", report["report_id"], "--format", format_name]) == 0
            outputs.append(capsys.readouterr().out)
        assert outputs[0] == outputs[1]
    for lang, heading in (("en", "# RAG Source-Withdrawal Audit Report"), ("zh", "# RAG 源撤回审计报告")):
        assert main(["report", report["report_id"], "--db", str(database), "--lang", lang, "--format", "markdown"]) == 0
        assert capsys.readouterr().out.startswith(heading)


def test_cli_demo_identity_does_not_depend_on_language(tmp_path, capsys):
    outputs = []
    for lang in ("en", "zh"):
        output = tmp_path / (lang + ".json")
        assert main(["--lang", lang, "demo", "--db", str(tmp_path / (lang + ".sqlite")), "--output", str(output)]) == 0
        capsys.readouterr()
        outputs.append(json.loads(output.read_text(encoding="utf-8")))
    assert outputs[0] == outputs[1]
    assert "lang" not in outputs[0] and "locale" not in outputs[0]
