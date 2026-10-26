"""Regenerate only the named public examples using self-authored sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from rag_audit.demo import DEMO_WITHDRAWALS, demo_bundle
from rag_audit.evaluation import benchmark
from rag_audit.report import render_markdown, sharing_summary
from rag_audit.service import Workbench


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Replace these generated example files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / "examples"
    names = ["demo-bundle.json", "joint-withdrawal-report.json", "joint-withdrawal-report.md",
             "sharing-summary.json", "controlled-evaluation.json", "history-m4.jsonl", "history-m5.jsonl"]
    if not args.force and any((target / name).exists() for name in names):
        raise SystemExit("Generated examples already exist; use --force to regenerate the named files")
    with TemporaryDirectory(prefix="rag-audit-examples-") as temp:
        workbench = Workbench(Path(temp) / "evidence.sqlite")
        bundle_id = workbench.ingest(demo_bundle(), "public controlled example")
        bundle = workbench.get_bundle(bundle_id)
        report = workbench.replay(bundle_id, DEMO_WITHDRAWALS)
        controlled = benchmark()
        if not controlled["acceptance"]["passed"] or not report["validation"]["equivalent_to_full_rebuild"]:
            raise SystemExit("Controlled acceptance failed; examples were not written")
        outputs = {"demo-bundle.json": bundle, "joint-withdrawal-report.json": report,
                   "sharing-summary.json": sharing_summary(report), "controlled-evaluation.json": controlled}
        target.mkdir(parents=True, exist_ok=True)
        for name, value in outputs.items():
            (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (target / "joint-withdrawal-report.md").write_text(render_markdown(report), encoding="utf-8")
        for kind in ("m4", "m5"):
            fixture = root / "tests" / "fixtures" / f"{kind}_history.jsonl"
            if not fixture.exists():
                # The fixtures may use a hyphenated name; select the one matching the format.
                candidates = sorted((root / "tests" / "fixtures").glob(f"*{kind}*.jsonl"))
                if len(candidates) != 1:
                    raise SystemExit(f"Expected exactly one public {kind} fixture")
                fixture = candidates[0]
            (target / f"history-{kind}.jsonl").write_bytes(fixture.read_bytes())
    print(json.dumps({"files": names, "controlled_acceptance": controlled["acceptance"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
