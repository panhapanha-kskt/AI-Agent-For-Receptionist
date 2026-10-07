import json
import uuid

import pytest

from receptionist.agent.tools import ToolContext, run_tool, tool_definitions
from receptionist.knowledge import service as knowledge
from receptionist.learning import service as learning
from receptionist.messages import service as messages
from receptionist.skills.loader import SkillRegistry
from tests.conftest import ROOT


@pytest.fixture
def ctx(db):
    reg = SkillRegistry(ROOT / "skills")
    reg.reload()
    return ToolContext(db=db, skills=reg, session_id=str(uuid.uuid4()))


def test_definitions_are_strict_and_closed(ctx):
    for tool in tool_definitions(ctx.skills):
        assert tool["strict"] is True
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_load_skill_and_read_file(ctx):
    out, err = run_tool(ctx, "load_skill", {"name": "fees-and-payments"})
    assert not err and "fees.yaml" in out
    out, err = run_tool(
        ctx, "read_skill_file", {"skill": "fees-and-payments", "filename": "fees.yaml"}
    )
    assert not err and "tuition_per_year" in out


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("load_skill", {"name": "../../.env"}),
        ("read_skill_file", {"skill": "admissions", "filename": "../../.env"}),
        (
            "take_message",
            {"caller_name": "A", "contact": "1", "for_office": "hackers", "message": "x"},
        ),
        (
            "take_message",
            {"caller_name": "A", "contact": "1", "for_office": "finance", "message": "x" * 5000},
        ),
        ("log_unanswered", {"question": "q", "lang": "fr", "suggested_skill": ""}),
        ("delete_everything", {}),
        ("load_skill", "not-a-dict"),
    ],
)
def test_bad_tool_input_is_an_error_not_an_exception(ctx, name, data):
    out, err = run_tool(ctx, name, data)
    assert err is True
    assert out


def test_take_message_saves(ctx, db):
    out, err = run_tool(
        ctx,
        "take_message",
        {
            "caller_name": "Dara",
            "contact": "012 345 678",
            "for_office": "finance",
            "message": "Receipt please",
        },
    )
    assert not err and "reference #" in out
    assert any(m.caller_name == "Dara" for m in messages.list_messages(db))


def test_learning_loop_end_to_end(ctx, db):
    question = f"Do you have a swimming pool {uuid.uuid4().hex[:6]}?"
    # 1. Agent can't answer -> logs it.
    run_tool(ctx, "log_unanswered", {"question": question, "lang": "en", "suggested_skill": ""})
    item = next(q for q in learning.list_pending(db) if q.question == question)

    # Asking again groups into the same item.
    run_tool(ctx, "log_unanswered", {"question": question, "lang": "en", "suggested_skill": ""})
    db.refresh(item)
    assert item.times_asked == 2

    # 2. Not in knowledge yet.
    out, _ = run_tool(ctx, "search_knowledge", {"query": question, "skill": ""})
    assert "swimming" not in out.lower() or "No approved answer" in out

    # 3. Staff approve an answer.
    learning.approve(db, item.id, "Yes, a 25m pool, open to Grade 3+.", "campus-and-contact")

    # 4. Now the agent finds it.
    out, err = run_tool(
        ctx, "search_knowledge", {"query": "Is there a swimming pool?", "skill": ""}
    )
    assert not err
    assert "25m pool" in json.loads(out)[0]["answer"]


def test_knowledge_versioning(db):
    e1 = knowledge.create_entry(db, "admissions", "Open day date?", "June 1", "en")
    e2 = knowledge.update_entry(db, e1.id, "June 8")
    db.refresh(e1)
    assert not e1.active and e2.active and e2.version == 2 and e2.replaces_id == e1.id


def test_khmer_search_matches_without_spaces(db):
    knowledge.create_entry(db, "campus-and-contact", "សាលាបើកម៉ោងប៉ុន្មាន", "ម៉ោង ៧:៣០ ព្រឹក", "km")
    hits = knowledge.search(db, "តើសាលាបើកម៉ោងប៉ុន្មាន?")
    assert hits and hits[0][1].lang == "km"
