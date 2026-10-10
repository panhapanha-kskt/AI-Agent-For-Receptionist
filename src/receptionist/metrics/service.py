"""Collect per-turn timings while a reply is produced, then store and summarise them."""

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from receptionist.database import utcnow
from receptionist.metrics.models import TurnMetric

logger = logging.getLogger("receptionist.metrics")


def now() -> float:
    return time.perf_counter()


def ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


@dataclass
class TurnStats:
    """Mutable timing record for one turn. Filled in by the agent and the endpoints."""

    session_id: str | None = None
    channel: str = "text"
    path: str = "tools"
    skill: str | None = None
    model: str | None = None
    outcome: str = "answered"
    llm_calls: int = 0
    retries: int = 0
    route_ms: int | None = None
    ttft_ms: int | None = None
    llm_ms: int = 0
    tool_ms: int = 0
    stt_ms: int | None = None
    tts_ms: int | None = None
    total_ms: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    started: float = field(default_factory=now, repr=False)

    def add_usage(self, usage) -> None:
        """Add Gemini `usage_metadata` (any field may be missing or None)."""
        if usage is None:
            return
        self.prompt_tokens += getattr(usage, "prompt_token_count", None) or 0
        self.cached_tokens += getattr(usage, "cached_content_token_count", None) or 0
        self.output_tokens += getattr(usage, "candidates_token_count", None) or 0
        self.thought_tokens += getattr(usage, "thoughts_token_count", None) or 0

    def mark_first_text(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = ms_since(self.started)

    def finish(self) -> None:
        self.total_ms = ms_since(self.started)


_FIELDS = [c for c in TurnMetric.__table__.columns.keys() if c not in ("id", "created_at")]


def record(db: Session, stats: TurnStats) -> int | None:
    """Store a turn's metrics. Never let a metrics failure break the reply."""
    if not stats.total_ms:
        stats.finish()
    logger.info(
        "turn path=%s skill=%s calls=%d retries=%d ttft=%sms llm=%dms tools=%dms total=%dms "
        "tokens in=%d cached=%d out=%d thought=%d outcome=%s",
        stats.path,
        stats.skill,
        stats.llm_calls,
        stats.retries,
        stats.ttft_ms,
        stats.llm_ms,
        stats.tool_ms,
        stats.total_ms,
        stats.prompt_tokens,
        stats.cached_tokens,
        stats.output_tokens,
        stats.thought_tokens,
        stats.outcome,
    )
    try:
        row = TurnMetric(**{k: getattr(stats, k) for k in _FIELDS})
        db.add(row)
        db.commit()
        return row.id
    except Exception:
        logger.exception("could not store turn metrics")
        db.rollback()
        return None


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def summary(db: Session, hours: int = 24, slowest: int = 10) -> dict:
    since = utcnow() - timedelta(hours=hours)
    rows = list(db.scalars(select(TurnMetric).where(TurnMetric.created_at >= since)))
    totals = [r.total_ms for r in rows]
    ttfts = [r.ttft_ms for r in rows if r.ttft_ms is not None]
    by_path: dict[str, int] = {}
    for r in rows:
        by_path[r.path] = by_path.get(r.path, 0) + 1
    prompt = sum(r.prompt_tokens for r in rows)
    return {
        "hours": hours,
        "turns": len(rows),
        "total_ms": {"p50": _percentile(totals, 50), "p95": _percentile(totals, 95)},
        "ttft_ms": {"p50": _percentile(ttfts, 50), "p95": _percentile(ttfts, 95)},
        "avg_llm_calls": round(sum(r.llm_calls for r in rows) / len(rows), 2) if rows else None,
        "by_path": by_path,
        "errors": sum(1 for r in rows if r.outcome not in ("answered",)),
        "cache_hit_ratio": round(sum(r.cached_tokens for r in rows) / prompt, 3)
        if prompt
        else None,
        "slowest": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "path": r.path,
                "skill": r.skill,
                "llm_calls": r.llm_calls,
                "ttft_ms": r.ttft_ms,
                "total_ms": r.total_ms,
                "outcome": r.outcome,
            }
            for r in sorted(rows, key=lambda r: r.total_ms, reverse=True)[:slowest]
        ],
    }
