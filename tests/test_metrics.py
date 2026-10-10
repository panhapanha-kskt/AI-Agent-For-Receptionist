import uuid
from types import SimpleNamespace

import yaml

from receptionist.metrics import service as metrics
from receptionist.metrics.models import TurnMetric
from receptionist.metrics.service import TurnStats
from tests.conftest import ADMIN, ROOT, response, text_block, tool_block


def test_stats_add_usage_tolerates_missing_fields():
    s = TurnStats()
    s.add_usage(SimpleNamespace(prompt_token_count=100, cached_content_token_count=None))
    s.add_usage(None)
    assert s.prompt_tokens == 100 and s.cached_tokens == 0


def test_record_and_summary(db):
    for total in (100, 200, 300, 4000):
        metrics.record(db, TurnStats(total_ms=total, llm_calls=1, ttft_ms=50))
    out = metrics.summary(db)
    assert out["turns"] >= 4
    assert out["total_ms"]["p95"] >= 300
    assert out["slowest"][0]["total_ms"] >= 4000


def test_chat_records_turn_metrics(client, fake_llm, db):
    fake_llm.responses = [
        response(tool_block("load_skill", {"name": "fees-and-payments"})),
        response(text_block("Grade 7 is 2,900 USD.")),
    ]
    r = client.post("/api/chat", json={"session_id": str(uuid.uuid4()), "message": "fees?"})
    assert r.status_code == 200 and r.json()["turn_id"]

    turn = db.get(TurnMetric, r.json()["turn_id"])
    assert turn.llm_calls == 2 and turn.skill == "fees-and-payments"
    assert client.get("/api/admin/metrics", headers=ADMIN).json()["turns"] >= 1


def test_metrics_endpoint_requires_admin(client):
    assert client.get("/api/admin/metrics").status_code == 401


def test_eval_file_is_valid():
    cases = yaml.safe_load(
        (ROOT / "tests" / "evals" / "questions.yaml").read_text(encoding="utf-8")
    )
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        assert c["kind"] in {"fact", "unknown", "injection"}
        if c["kind"] == "fact":
            assert c["expect_any"]
        if c["kind"] == "injection":
            assert c["expect_none"]
