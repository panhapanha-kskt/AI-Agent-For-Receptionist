"""Phase 5: caller feedback, learned routing examples, AI drafts, export to faq.md."""

import asyncio
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from receptionist.config import get_settings
from receptionist.knowledge import service as knowledge
from receptionist.learning import service as learning
from receptionist.learning.models import RoutingExample, UnansweredQuestion
from tests.conftest import ADMIN, ROOT, FakeGemini, response, text_block, tool_block

SID = lambda: str(uuid.uuid4())  # noqa: E731


def chat(client, sid, message):
    r = client.post("/api/chat", json={"session_id": sid, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def rate(client, sid, turn_id, rating="down", **extra):
    return client.post(
        "/api/feedback",
        json={"session_id": sid, "turn_id": turn_id, "rating": rating, **extra},
    )


# --- feedback ---


def test_thumbs_down_goes_to_review_queue_with_the_answer(client, fake_llm, db):
    sid, marker = SID(), uuid.uuid4().hex[:6]
    fake_llm.responses = [response(text_block(f"Wrong answer {marker}."))]
    turn = chat(client, sid, f"When does the library open {marker}?")
    assert rate(client, sid, turn["turn_id"], comment="that's wrong").status_code == 201

    item = next(q for q in learning.list_pending(db) if marker in q.question)
    assert item.source == "feedback" and f"Wrong answer {marker}." in item.previous_answer
    listed = client.get("/api/admin/questions", headers=ADMIN).json()
    assert any(q["id"] == item.id and q["source"] == "feedback" for q in listed)


def test_thumbs_up_is_recorded_but_not_queued(client, fake_llm, db):
    sid, marker = SID(), uuid.uuid4().hex[:6]
    fake_llm.responses = [response(text_block("Good answer."))]
    turn = chat(client, sid, f"Hello {marker}")
    assert rate(client, sid, turn["turn_id"], "up").status_code == 201
    assert not any(marker in q.question for q in learning.list_pending(db))
    metrics = client.get("/api/admin/metrics", headers=ADMIN).json()
    assert metrics["feedback"].get("up", 0) >= 1


def test_feedback_only_once_and_only_from_the_same_session(client, fake_llm):
    sid = SID()
    fake_llm.responses = [response(text_block("Answer."))]
    turn_id = chat(client, sid, "Hi")["turn_id"]
    assert rate(client, SID(), turn_id).status_code == 404  # someone else's turn
    assert rate(client, sid, 999_999).status_code == 404  # unknown turn
    assert rate(client, sid, turn_id, "up").status_code == 201
    assert rate(client, sid, turn_id, "down").status_code == 409  # already rated
    assert rate(client, sid, turn_id, "meh").status_code == 422


def test_feedback_list_requires_admin(client):
    assert client.get("/api/admin/feedback").status_code == 401
    assert client.get("/api/admin/feedback?rating=down", headers=ADMIN).status_code == 200


# --- learned routing examples ---


def test_router_miss_becomes_a_routing_example(client, fake_llm, app, db):
    question = f"Is there financial help for families {uuid.uuid4().hex[:6]}?"
    fake_llm.responses = [
        response(tool_block("load_skill", {"name": "scholarships"})),
        response(text_block("Yes, see our scholarships.")),
    ]
    chat(client, SID(), question)
    examples = learning.routing_examples(db)
    assert question in examples.get("scholarships", [])

    # Next time the router picks it directly (no tool round-trip).
    result = asyncio.run(app.state.agent.router.route(question))
    assert result.skill == "scholarships"


def test_no_example_learned_when_the_question_was_unanswered(client, fake_llm, db):
    question = f"Do you teach underwater basket weaving {uuid.uuid4().hex[:6]}?"
    fake_llm.responses = [
        response(tool_block("load_skill", {"name": "programs-and-courses"})),
        response(
            tool_block(
                "log_unanswered",
                {"question": question, "lang": "en", "suggested_skill": "none"},
                "c2",
            )
        ),
        response(text_block("I don't know, I've asked staff.")),
    ]
    chat(client, SID(), question)
    assert question not in learning.routing_examples(db).get("programs-and-courses", [])


def test_routing_examples_skip_duplicates_and_can_be_deleted(client, db):
    text = f"How do I apply for a bursary {uuid.uuid4().hex[:6]}"
    first = learning.add_routing_example(db, "scholarships", text)
    assert first is not None
    assert learning.add_routing_example(db, "scholarships", text + "?") is None  # near-duplicate
    example_id = first.id
    r = client.delete(f"/api/admin/routing-examples/{example_id}", headers=ADMIN)
    db.expire_all()  # the server deleted it in another session
    assert r.status_code == 204 and db.get(RoutingExample, example_id) is None


# --- AI drafts for staff ---


def test_draft_with_ai_uses_the_skill_content(client, fake_llm, db):
    item = learning.log_unanswered(db, f"Grade 7 tuition fee {uuid.uuid4().hex[:6]}?", "en")
    fake_llm.responses = [response(text_block("Grade 7 tuition is 2,900 USD per year."))]
    r = client.post(f"/api/admin/questions/{item.id}/draft", headers=ADMIN)
    assert r.status_code == 200 and r.json()["draft_answer"].startswith("Grade 7 tuition")
    prompt_parts = fake_llm.calls[-1]["contents"][0].parts
    assert prompt_parts[0].text.startswith('<school_reference skill="fees-and-payments">')


def test_draft_without_matching_content_skips_the_llm(client, fake_llm, db):
    item = learning.log_unanswered(db, f"zzqx wobble {uuid.uuid4().hex[:6]}", "en")
    r = client.post(f"/api/admin/questions/{item.id}/draft", headers=ADMIN)
    assert r.status_code == 200 and r.json()["draft_answer"].startswith("NOT FOUND")
    assert fake_llm.calls == []


def test_draft_requires_admin(client, db):
    item = db.query(UnansweredQuestion).first() or learning.log_unanswered(db, "x?", "en")
    assert client.post(f"/api/admin/questions/{item.id}/draft").status_code == 401


# --- export to faq.md (uses a temporary copy of the skills folder) ---


@pytest.fixture
def temp_skills_client(tmp_path):
    from receptionist.main import create_app

    skills_dir = tmp_path / "skills"
    shutil.copytree(ROOT / "skills", skills_dir)
    settings = get_settings().model_copy(update={"skills_dir": skills_dir})
    with TestClient(create_app(settings=settings, agent_client=FakeGemini())) as c:
        yield c, skills_dir


def test_export_moves_answer_into_skill_faq(temp_skills_client, db):
    client, skills_dir = temp_skills_client
    marker = uuid.uuid4().hex[:6]
    entry = knowledge.create_entry(
        db, "facilities", f"Is there a prayer room {marker}?", "Yes.\n# not a heading", "en"
    )
    r = client.post(f"/api/admin/knowledge/{entry.id}/export", headers=ADMIN)
    assert r.status_code == 200 and r.json()["file"] == "facilities/faq.md"

    faq = (skills_dir / "facilities" / "faq.md").read_text(encoding="utf-8")
    assert f"## Is there a prayer room {marker}?" in faq
    assert "\\# not a heading" in faq  # answer lines can't become new questions
    db.refresh(entry)
    assert entry.active is False  # the database copy is retired
    # The new FAQ question now teaches the router.
    skills = client.app.state.skills
    assert any(marker in q for q in skills.example_questions("facilities"))
    # The real skills folder was not touched.
    assert not (ROOT / "skills" / "facilities" / "faq.md").exists()


def test_export_unknown_entry_is_404(temp_skills_client):
    client, _ = temp_skills_client
    assert client.post("/api/admin/knowledge/999999/export", headers=ADMIN).status_code == 404
