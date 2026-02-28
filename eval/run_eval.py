from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.run import StubLLMClient, run_pipeline
from app.validators import detect_language


def load_cases(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def run_eval(cases: list[dict]) -> dict:
    llm = StubLLMClient()
    results: list[dict] = []
    for case in cases:
        lang = case["lang"]
        if lang == "auto":
            lang = detect_language(case["input"])
        out = run_pipeline(
            text=case["input"],
            mode=case.get("mode", "support"),
            lang=lang,
            llm=llm,
            retry_on_fail=True,
        )
        results.append(
            {
                "id": case["id"],
                "ok": out.ok,
                "attempts": out.attempts,
                "errors": out.errors,
                "expect_ok": case.get("expect_ok", True),
            }
        )

    correctness = [item["ok"] == item["expect_ok"] for item in results]
    attempts = [item["attempts"] for item in results]
    return {
        "summary": {
            "cases": len(results),
            "expectation_match_rate": round(mean(correctness), 4) if correctness else 0.0,
            "success_rate": round(mean([item["ok"] for item in results]), 4) if results else 0.0,
            "avg_attempts": round(mean(attempts), 2) if attempts else 0.0,
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prompt eval over eval/cases.jsonl")
    parser.add_argument("--cases", default="eval/cases.jsonl", help="Path to JSONL dataset")
    parser.add_argument("--out-dir", default="eval/results", help="Directory to save eval results")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_eval(load_cases(cases_path))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"eval_report_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
