"""CLI: 0 complete success, 1 failed/incomplete checks, 2 invalid input or operation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from .schema import MAX_INPUT_BYTES, ValidationError
from .cli_i18n import cli_text


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _destination(path: str | None, force: bool, lang: str = "zh") -> None:
    if path and Path(path).exists() and not force:
        raise ValidationError(cli_text(lang, "exists", path=path))


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
            raise ValidationError(cli_text(args.lang, "protected"))


def _emit(value, output: str | None = None, force: bool = False, text: bool = False, lang: str = "zh") -> None:
    content = value if text else _json(value)
    if output:
        _destination(output, force, lang)
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w" if force else "x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        print(_json({"output": str(target.resolve())}), end="")
    else:
        print(content, end="" if content.endswith("\n") else "\n")


def _read(path: str, lang: str = "zh") -> bytes:
    target = Path(path)
    if target.stat().st_size > MAX_INPUT_BYTES:
        raise ValidationError(cli_text(lang, "too_large"))
    return target.read_bytes()


class LocalizedParser(argparse.ArgumentParser):
    """Localize owned help and error prefixes without global gettext changes."""
    def __init__(self, *args, lang: str = "zh", add_help: bool = True, **kwargs):
        self.lang = lang
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = cli_text(lang, "positional")
        self._optionals.title = cli_text(lang, "options")
        if add_help:
            self.add_argument("-h", "--help", action="help", help=cli_text(lang, "help"))

    def _get_formatter(self):
        lang = self.lang
        class Formatter(argparse.HelpFormatter):
            def add_usage(self, usage, actions, groups, prefix=None):
                super().add_usage(usage, actions, groups, cli_text(lang, "usage") if prefix is None else prefix)
        return Formatter(self.prog)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, cli_text(self.lang, "parse_error", prog=self.prog, message=message))


def _requested_lang(argv: list[str]) -> str:
    """Choose help language before argparse handles --help at either position."""
    requested = "zh"
    for index, token in enumerate(argv):
        if token == "--":
            break
        if token == "--lang" and index + 1 < len(argv):
            requested = argv[index + 1]
        elif token.startswith("--lang="):
            requested = token.partition("=")[2]
    return "en" if requested == "en" else "zh"


def _parser(lang: str = "zh") -> argparse.ArgumentParser:
    message = lambda key: cli_text(lang, key)
    parser = LocalizedParser(prog="rag-audit", description=message("description"), lang=lang)
    default_db = str(Path(os.environ.get("RAG_AUDIT_HOME", ".rag-audit")) / "evidence.sqlite")
    parser.add_argument("--db", default=default_db, help=message("db"))
    parser.add_argument("--lang", choices=["en", "zh"], default=lang, help=message("lang"))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help=message("db"))
    common.add_argument("--lang", choices=["en", "zh"], default=argparse.SUPPRESS, help=message("lang"))
    sub = parser.add_subparsers(dest="command", required=True, title=message("commands"),
                               parser_class=lambda **kwargs: LocalizedParser(lang=lang, **kwargs))

    def output_args(command):
        command.add_argument("--output", "-o", help=message("output"))
        command.add_argument("--force", action="store_true", help=message("force"))

    def command(name):
        return sub.add_parser(name, parents=[common], help=message(name), description=message(name))

    demo = command("demo")
    output_args(demo)
    inspect = command("inspect")
    inspect.add_argument("input", help=message("input"))
    output_args(inspect)
    ingest = command("import")
    ingest.add_argument("input", help=message("input"))
    ingest.add_argument("--label", default="", help=message("label"))
    listing = command("list")
    listing.add_argument("--reports", action="store_true", help=message("reports"))
    replay = command("replay")
    replay.add_argument("input", help=message("input"))
    replay.add_argument("--withdraw", action="append", required=True, help=message("withdraw"))
    replay.add_argument("--query", action="append", help=message("query"))
    output_args(replay)
    report = command("report")
    report.add_argument("report_id", help=message("report_id"))
    report.add_argument("--format", choices=["json", "markdown", "summary"], default="json", help=message("format"))
    output_args(report)
    export = command("export")
    export.add_argument("bundle_id", help=message("bundle_id"))
    output_args(export)
    verify = command("verify")
    verify.add_argument("--expected-checkpoint", help=message("checkpoint"))
    output_args(verify)
    bench = command("benchmark")
    output_args(bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser(_requested_lang(argv)).parse_args(argv)
    try:
        _protect_evidence_output(args)
        _destination(getattr(args, "output", None), getattr(args, "force", False), args.lang)
        from .audit import audit_bundle
        from .importers import load_input
        from .service import Workbench

        if args.command == "inspect":
            audit = audit_bundle(load_input(_read(args.input, args.lang), args.input))
            _emit(audit, args.output, args.force, lang=args.lang)
            return 0 if audit["passed"] else 1
        if args.command == "benchmark":
            from .evaluation import benchmark
            result = benchmark()
            _emit(result, args.output, args.force, lang=args.lang)
            return 0 if result["acceptance"]["passed"] else 1
        app = Workbench(args.db)
        if args.command == "demo":
            from .demo import demo_bundle
            bundle = demo_bundle()
            key = app.ingest(bundle, "Built-in: duplicate sources and normal queries")
            _emit(app.get_bundle(key) if args.output else {"bundle_id": key, "db": str(app.store.path)}, args.output, args.force, lang=args.lang)
        elif args.command == "import":
            key = app.ingest(load_input(_read(args.input, args.lang), args.input), args.label or Path(args.input).name)
            audit = app.audit(key)
            _emit({"bundle_id": key, "audit": audit}, lang=args.lang)
            return 0 if audit["passed"] else 1
        elif args.command == "list":
            _emit(app.list_reports() if args.reports else app.list_bundles(), lang=args.lang)
        elif args.command == "replay":
            if Path(args.input).is_file():
                key = app.ingest(load_input(_read(args.input, args.lang), args.input), Path(args.input).name)
            else:
                key = args.input
            result = app.replay(key, args.withdraw, args.query)
            _emit(result, args.output, args.force, lang=args.lang)
            return 0 if result["validation"]["equivalent_to_full_rebuild"] else 1
        elif args.command == "report":
            result = app.get_report(args.report_id)
            if args.format == "markdown":
                from .report import render_markdown
                _emit(render_markdown(result, lang=args.lang), args.output, args.force, text=True, lang=args.lang)
            elif args.format == "summary":
                from .report import sharing_summary
                _emit(sharing_summary(result), args.output, args.force, lang=args.lang)
            else:
                _emit(result, args.output, args.force, lang=args.lang)
        elif args.command == "export":
            _emit(app.get_bundle(args.bundle_id), args.output, args.force, lang=args.lang)
        elif args.command == "verify":
            result = app.verify_ledger(args.expected_checkpoint)
            _emit(result, args.output, args.force, lang=args.lang)
            return 0 if result["valid"] else 1
        return 0
    except (ValidationError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError) as error:
        print(_json({"error": cli_text(args.lang, "error", message=error)}), file=sys.stderr, end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
