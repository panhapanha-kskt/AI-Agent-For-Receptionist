"""Phase 3: fast model for routed questions, approved-answer matching, workflows."""

import uuid

from receptionist.agent.client import NO_MATCH_NOTE
from receptionist.config import get_settings
from receptionist.knowledge import service as knowledge
from receptionist.metrics.models import TurnMetric
from tests.conftest import overloaded, response, text_block

SID = lambda: str(uuid.uuid4())  # noqa: E731


def ask(client, message, sid=None):
    return client.post("/api/chat", json={"session_id": sid or SID(), "message": message})


def test_routed_question_uses_fast_model(client, fake_llm, db):
    fake_llm.responses = [response(text_block("2,900 USD per year."))]
    r = ask(client, "How much is the tuition for grade 7?")
    assert fake_llm.calls[0]["model"] == get_settings().gemini_model_fast
    assert db.get(TurnMetric, r.json()["turn_id"]).model == get_settings().gemini_model_fast


def test_fast_model_failure_hands_over_to_main_model(client, fake_llm, db):
    # Any error from the fast model (even a 400, e.g. an unsupported option) → main model.
    fake_llm.responses = [overloaded(400), response(text_block("2,900 USD per year."))]
    r = ask(client, "How much is the tuition for grade 7?")
    assert r.status_code == 200
    settings = get_settings()
    assert [c["model"] for c in fake_llm.calls] == [
        settings.gemini_model_fast,
        settings.gemini_model,
    ]
    turn = db.get(TurnMetric, r.json()["turn_id"])
    assert turn.retries == 1 and turn.model == settings.gemini_model


def test_unrouted_question_uses_main_model_with_no_match_note(client, fake_llm):
    fake_llm.responses = [response(text_block("I don't have that information."))]
    ask(client, "Do you have a swimming pool?")
    assert fake_llm.calls[0]["model"] == get_settings().gemini_model
    texts = [p.text for p in fake_llm.calls[0]["contents"][-1].parts]
    assert NO_MATCH_NOTE in texts and texts[-1] == "Do you have a swimming pool?"


def test_approved_answer_is_attached(client, fake_llm, db):
    marker = uuid.uuid4().hex[:6]
    knowledge.create_entry(
        db,
        "campus-and-contact",
        f"Is there a rooftop garden {marker}?",
        "Yes, open at lunchtime.",
        "en",
    )
    fake_llm.responses = [response(text_block("Yes, at lunchtime."))]
    r = ask(client, f"Is there a rooftop garden {marker}?")
    texts = [p.text for p in fake_llm.calls[0]["contents"][-1].parts]
    assert any('skill="approved-answer"' in t and "open at lunchtime" in t for t in texts)
    assert NO_MATCH_NOTE not in texts
    assert fake_llm.calls[0]["model"] == get_settings().gemini_model_fast
    assert db.get(TurnMetric, r.json()["turn_id"]).path == "knowledge"


def test_retired_approved_answer_is_not_used(client, fake_llm, db):
    marker = uuid.uuid4().hex[:6]
    entry = knowledge.create_entry(
        db, "general-faq", f"Old question {marker}?", "Old answer.", "en"
    )
    knowledge.deactivate(db, entry.id)
    fake_llm.responses = [response(text_block("OK"))]
    ask(client, f"Old question {marker}?")
    texts = [p.text for p in fake_llm.calls[0]["contents"][-1].parts]
    assert not any("Old answer." in t for t in texts)


def test_workflow_skill_uses_main_model(client, fake_llm):
    # admissions has a workflow.yaml (multi-step booking): the main model handles it.
    fake_llm.responses = [response(text_block("Which day suits you?"))]
    ask(client, "When can I book a campus tour?")
    assert fake_llm.calls[0]["model"] == get_settings().gemini_model
    reference = fake_llm.calls[0]["contents"][-1].parts[0].text
    assert "book_campus_tour" in reference


def test_quota_error_puts_model_in_cooldown(client, fake_llm, app):
    settings = get_settings()
    app.state.agent._cooldown_until.clear()
    # Unrouted question → main model first; it is out of quota (429) → fallback answers.
    fake_llm.responses = [overloaded(429), response(text_block("Hi from fallback"))]
    ask(client, "Do you have a swimming pool?")
    assert [c["model"] for c in fake_llm.calls] == [
        settings.gemini_model,
        settings.gemini_fallback_model,
    ]
    # Next question: the main model is skipped instead of failing again.
    fake_llm.calls.clear()
    fake_llm.responses = [response(text_block("Hi again"))]
    ask(client, "Do you have a library card?")
    assert [c["model"] for c in fake_llm.calls] == [settings.gemini_fallback_model]
    app.state.agent._cooldown_until.clear()
