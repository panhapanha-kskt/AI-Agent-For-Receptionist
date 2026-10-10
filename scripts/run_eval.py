"""Run the evaluation questions against the REAL Gemini API and report accuracy + latency.

Usage (from the project folder, with .venv active and GEMINI_API_KEY in .env):
    python scripts/run_eval.py
    python scripts/run_eval.py --only fee-g7-en,adm-documents --delay 2

It uses a throwaway database, so your real review queue and metrics are not touched.
Each question runs in a fresh conversation. A JSON report is written to reports/.
Run it before and after every change to prompts, skills or the agent, and compare.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Throwaway database: must be set BEFORE importing the app.
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'eval.db'}"
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from receptionist.agent.client import ReceptionistAgent  # noqa: E402
from receptionist.config import get_settings  # noqa: E402
from receptionist.database import SessionLocal, init_db  # noqa: E402
from receptionist.learning import service as learning  # noqa: E402
from receptionist.metrics.service import TurnStats  # noqa: E402
from receptionist.skills.loader import SkillRegistry  # noqa: E402

UNKNOWN_HINTS = [
    "message",
    "don't have",
    "do not have",
    "not sure",
    "don't know",
    "do not know",
    "not available",
    "staff",
    "សារ",
    "មិនមាន",
    "មិនដឹង",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def judge(case: dict, reply: str, logged_unknown: bool) -> tuple[bool, str]:
    text = _norm(reply)
    kind = case["kind"]
    if kind == "fact":
        hit = any(_norm(s) in text for s in case.get("expect_any", []))
        return hit, "" if hit else f"missing one of {case['expect_any']}"
    if kind == "unknown":
        ok = logged_unknown or any(h in text for h in UNKNOWN_HINTS)
        return ok, "" if ok else "may have invented an answer"
    if kind == "injection":
        bad = [s for s in case.get("expect_none", []) if _norm(s) in text]
        return not bad, f"leaked {bad}" if bad else ""
    return False, f"unknown kind {kind}"


def percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


async def evaluate(agent: ReceptionistAgent, cases: list[dict], delay: float) -> list[dict]:
    results = []
    for case in cases:
        db = SessionLocal()
        before = len(learning.list_pending(db))
        stats = TurnStats(channel="eval")
        error = ""
        try:
            reply = await agent.reply(db, str(uuid.uuid4()), case["question"], stats=stats)
            text = reply.text
        except Exception as exc:  # report and continue with the next question
            text, error = "", f"{type(exc).__name__}: {exc}"
        stats.finish()
        logged_unknown = len(learning.list_pending(db)) > before
        db.close()

        passed, why = (False, error) if error else judge(case, text, logged_unknown)
        expected_skill = case.get("expect_skill")
        route_ok = None if not expected_skill else stats.skill == expected_skill
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "lang": case["lang"],
                "passed": passed,
                "why": why,
                "skill": stats.skill,
                "expected_skill": expected_skill,
                "route_ok": route_ok,
                "path": stats.path,
                "llm_calls": stats.llm_calls,
                "retries": stats.retries,
                "ttft_ms": stats.ttft_ms,
                "total_ms": stats.total_ms,
                "tokens_in": stats.prompt_tokens,
                "tokens_cached": stats.cached_tokens,
                "tokens_out": stats.output_tokens + stats.thought_tokens,
                "reply": text[:300],
            }
        )
        mark = "PASS" if passed else "FAIL"
        print(
            f"{mark:4} {case['id']:<18} {stats.total_ms:>6}ms calls={stats.llm_calls} "
            f"path={stats.path:<9} skill={stats.skill} {why}"
        )
        await asyncio.sleep(delay)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(ROOT / "tests" / "evals" / "questions.yaml"))
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between questions")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.gemini_api_key.get_secret_value():
        print("GEMINI_API_KEY is not set in .env - nothing to evaluate.")
        return 2

    cases = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    init_db()
    skills = SkillRegistry(settings.skills_dir)
    skills.reload()
    agent = ReceptionistAgent(settings, skills)

    results = asyncio.run(evaluate(agent, cases, args.delay))

    totals = [r["total_ms"] for r in results]
    routed = [r for r in results if r["route_ok"] is not None]
    summary = {
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.gemini_model,
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "accuracy": round(sum(r["passed"] for r in results) / len(results), 3) if results else None,
        "skill_hit_rate": round(sum(r["route_ok"] for r in routed) / len(routed), 3)
        if routed
        else None,
        "avg_llm_calls": round(sum(r["llm_calls"] for r in results) / len(results), 2)
        if results
        else None,
        "p50_ms": percentile(totals, 50),
        "p95_ms": percentile(totals, 95),
    }
    print("\n" + json.dumps(summary, indent=2))

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"eval-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {out}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
