"""CLI: 0 complete success, 1 failed/incomplete checks, 2 invalid input or operation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from .schema import MAX_INPUT_BYTES, ValidationError


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _destination(path: str | None, force: bool) -> None:
    if path and Path(path).exists() and not force:
        raise ValidationError(f"Output already exists: {path}; use --force to replace it")


def _protect_evidence_output(args) -> None:
    output = getattr(args, "output", None)
    if not output:
        return
    target = Path(output).resolve()
    database = Path(args.db).resolve()
    protected = [database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))]
    input_name = getattr(args, "input", None)
    if input_name and Path(input_name).is_file():
        protected.append(Path(input_name).resolve())
    for source in protected:
        same_name = os.path.normcase(str(target)) == os.path.normcase(str(source))
        same_file = target.exists() and source.exists() and target.samefile(source)
        if same_name or same_file:
            raise ValidationError("Output must not overwrite the evidence database, its sidecars, or the input evidence")


def _emit(value, output: str | None = None, force: bool = False, text: bool = False) -> None:
    content = value if text else _json(value)
    if output:
        _destination(output, force)
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w" if force else "x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        print(_json({"output": str(target.resolve())}), end="")
    else:
        print(content, end="" if content.endswith("\n") else "\n")


def _read(path: str) -> bytes:
    target = Path(path)
    if target.stat().st_size > MAX_INPUT_BYTES:
        raise ValidationError("Input exceeds 25 MiB")
    return target.read_bytes()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag-audit", description="离线 RAG 证据审计与来源撤回验证")
    default_db = str(Path(os.environ.get("RAG_AUDIT_HOME", ".rag-audit")) / "evidence.sqlite")
    parser.add_argument("--db", default=default_db, help="SQLite evidence store")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def output_args(command):
        command.add_argument("--output", "-o")
        command.add_argument("--force", action="store_true")

    demo = sub.add_parser("demo", parents=[common], help="创建可回放的内置案件")
    output_args(demo)
    inspect = sub.add_parser("inspect", parents=[common], help="审计 JSON / M4-M5 JSONL，不写数据库")
    inspect.add_argument("input")
    output_args(inspect)
    ingest = sub.add_parser("import", parents=[common], help="不可覆盖地导入证据")
    ingest.add_argument("input")
    ingest.add_argument("--label", default="")
    listing = sub.add_parser("list", parents=[common], help="列出本地案件")
    listing.add_argument("--reports", action="store_true")
    replay = sub.add_parser("replay", parents=[common], help="从文件或已存 bundle_id 实际撤回并回放")
    replay.add_argument("input")
    replay.add_argument("--withdraw", action="append", required=True, help="source_id@revision，可重复指定")
    replay.add_argument("--query", action="append", help="替换回放查询集")
    output_args(replay)
    report = sub.add_parser("report", parents=[common], help="导出已存干预报告")
    report.add_argument("report_id")
    report.add_argument("--format", choices=["json", "markdown", "summary"], default="json")
    output_args(report)
    export = sub.add_parser("export", parents=[common], help="导出完整证据包，包含原文")
    export.add_argument("bundle_id")
    output_args(export)
    verify = sub.add_parser("verify", parents=[common], help="验证存储对象及事件链")
    verify.add_argument("--expected-checkpoint")
    output_args(verify)
    bench = sub.add_parser("benchmark", parents=[common], help="运行自编受控验收案例")
    output_args(bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _protect_evidence_output(args)
        _destination(getattr(args, "output", None), getattr(args, "force", False))
        from .audit import audit_bundle
        from .importers import load_input
        from .service import Workbench

        if args.command == "inspect":
            audit = audit_bundle(load_input(_read(args.input), args.input))
            _emit(audit, args.output, args.force)
            return 0 if audit["passed"] else 1
        if args.command == "benchmark":
            from .evaluation import benchmark
            result = benchmark()
            _emit(result, args.output, args.force)
            return 0 if result["acceptance"]["passed"] else 1
        app = Workbench(args.db)
        if args.command == "demo":
            from .demo import demo_bundle
            bundle = demo_bundle()
            key = app.ingest(bundle, "内置：重复来源与正常查询")
            _emit(app.get_bundle(key) if args.output else {"bundle_id": key, "db": str(app.store.path)}, args.output, args.force)
        elif args.command == "import":
            key = app.ingest(load_input(_read(args.input), args.input), args.label or Path(args.input).name)
            audit = app.audit(key)
            _emit({"bundle_id": key, "audit": audit})
            return 0 if audit["passed"] else 1
        elif args.command == "list":
            _emit(app.list_reports() if args.reports else app.list_bundles())
        elif args.command == "replay":
            if Path(args.input).is_file():
                key = app.ingest(load_input(_read(args.input), args.input), Path(args.input).name)
            else:
                key = args.input
            result = app.replay(key, args.withdraw, args.query)
            _emit(result, args.output, args.force)
            return 0 if result["validation"]["equivalent_to_full_rebuild"] else 1
        elif args.command == "report":
            result = app.get_report(args.report_id)
            if args.format == "markdown":
                from .report import render_markdown
                _emit(render_markdown(result), args.output, args.force, text=True)
            elif args.format == "summary":
                from .report import sharing_summary
                _emit(sharing_summary(result), args.output, args.force)
            else:
                _emit(result, args.output, args.force)
        elif args.command == "export":
            _emit(app.get_bundle(args.bundle_id), args.output, args.force)
        elif args.command == "verify":
            result = app.verify_ledger(args.expected_checkpoint)
            _emit(result, args.output, args.force)
            return 0 if result["valid"] else 1
        return 0
    except (ValidationError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError) as error:
        print(_json({"error": str(error)}), file=sys.stderr, end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
