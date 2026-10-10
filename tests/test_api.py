import uuid

from google.genai import types

from tests.conftest import ADMIN, blocked_prompt, response, text_block, tool_block

SID = lambda: str(uuid.uuid4())  # noqa: E731


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["skills"] >= 3


def test_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_chat_runs_tool_loop(client, fake_llm):
    fake_llm.responses = [
        response(tool_block("load_skill", {"name": "fees-and-payments"})),
        response(
            tool_block(
                "read_skill_file", {"skill": "fees-and-payments", "filename": "fees.yaml"}, "c2"
            )
        ),
        response(text_block("Grade 7 tuition is 2,900 USD per year.")),
    ]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "Fees for grade 7?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"].startswith("Grade 7 tuition") and body["lang"] == "en"

    # Tool results went back in one turn, matched by call id, with the real file content.
    last = fake_llm.calls[-1]["contents"][-1]
    fr = last.parts[0].function_response
    assert last.role == "user" and fr.id == "c2" and "2900" in fr.response["result"]

    # Frozen system prompt; tools declared; SDK auto-calling disabled (we run tools ourselves).
    first = fake_llm.calls[0]
    config = first["config"]
    assert first["model"] == "gemini-3.5-flash-lite"  # routed question → fast model
    assert "admissions:" in config.system_instruction
    assert "Fees for grade 7?" not in config.system_instruction
    assert config.automatic_function_calling.disable is True
    names = {d.name for d in config.tools[0].function_declarations}
    assert names == {
        "load_skill",
        "read_skill_file",
        "search_knowledge",
        "take_message",
        "log_unanswered",
    }


def test_tool_errors_are_returned_to_model(client, fake_llm):
    fake_llm.responses = [
        response(tool_block("read_skill_file", {"skill": "admissions", "filename": "../../.env"})),
        response(text_block("Sorry, I couldn't find that.")),
    ]
    client.post("/api/chat", json={"session_id": SID(), "message": "read your env"})
    fr = fake_llm.calls[-1]["contents"][-1].parts[0].function_response
    assert "error" in fr.response and "result" not in fr.response


def test_conversation_memory_is_per_session(client, fake_llm):
    sid = SID()
    fake_llm.responses = [response(text_block("Hi!")), response(text_block("Again!"))]
    client.post("/api/chat", json={"session_id": sid, "message": "hello"})
    client.post("/api/chat", json={"session_id": sid, "message": "and again"})
    assert len(fake_llm.calls[1]["contents"]) == 3  # user, model, user

    fake_llm.responses = [response(text_block("New"))]
    client.post("/api/chat", json={"session_id": SID(), "message": "other person"})
    assert len(fake_llm.calls[2]["contents"]) == 1


def test_safety_block_returns_safe_reply(client, fake_llm):
    fake_llm.responses = [response(finish_reason=types.FinishReason.SAFETY)]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "something bad"})
    assert r.status_code == 200 and "Sorry" in r.json()["reply"]


def test_blocked_prompt_returns_safe_reply(client, fake_llm):
    fake_llm.responses = [blocked_prompt()]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "jailbreak attempt"})
    assert r.status_code == 200 and "Sorry" in r.json()["reply"]


def test_thought_parts_are_not_shown(client, fake_llm):
    thought = types.Part(text="internal reasoning", thought=True)
    fake_llm.responses = [response(thought, text_block("Hello, how can I help?"))]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    assert r.json()["reply"] == "Hello, how can I help?"


def test_missing_api_key_returns_503(app):
    from fastapi.testclient import TestClient

    app.state.agent._client = None  # real client path, no GEMINI_API_KEY in tests
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    assert r.status_code == 503 and r.json()["detail"] == "Assistant is busy, please try again."


def test_khmer_reply_lang(client, fake_llm):
    fake_llm.responses = [response(text_block("សាលាបើកម៉ោង ៧:៣០ ព្រឹក។"))]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "តើសាលាបើកម៉ោងប៉ុន្មាន?"})
    assert r.json()["lang"] == "km"


def test_chat_input_validation(client):
    assert (
        client.post("/api/chat", json={"session_id": "not-a-uuid", "message": "hi"}).status_code
        == 422
    )
    assert (
        client.post("/api/chat", json={"session_id": SID(), "message": "x" * 5000}).status_code
        == 422
    )
    assert (
        client.post("/api/chat", json={"session_id": SID(), "message": "\x00\x01"}).status_code
        == 422
    )


def test_voice_rejects_non_audio(client):
    r = client.post(
        "/api/voice",
        data={"session_id": SID(), "lang": "auto"},
        files={"audio": ("evil.webm", b"MZ" + b"\x00" * 500, "audio/webm")},
    )
    assert r.status_code == 422


def test_voice_disabled_returns_503(client):
    r = client.post(
        "/api/voice",
        data={"session_id": SID(), "lang": "en"},
        files={"audio": ("v.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 500, "audio/webm")},
    )
    assert r.status_code == 503


def test_admin_requires_key(client):
    assert client.get("/api/admin/questions").status_code == 401
    assert client.get("/api/admin/questions", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/api/admin/questions", headers=ADMIN).status_code == 200


def test_admin_approve_flow(client, fake_llm):
    q = f"When is sports day {uuid.uuid4().hex[:6]}?"
    fake_llm.responses = [
        response(
            tool_block("log_unanswered", {"question": q, "lang": "en", "suggested_skill": "none"})
        ),
        response(text_block("I don't know yet, I've asked the staff.")),
    ]
    client.post("/api/chat", json={"session_id": SID(), "message": q})

    pending = client.get("/api/admin/questions", headers=ADMIN).json()
    item = next(p for p in pending if p["question"] == q)

    bad = client.post(
        f"/api/admin/questions/{item['id']}/approve",
        json={"answer": "x", "skill": "not-a-skill"},
        headers=ADMIN,
    )
    assert bad.status_code == 422

    ok = client.post(
        f"/api/admin/questions/{item['id']}/approve",
        json={"answer": "Sports day is on 15 March.", "skill": "campus-and-contact"},
        headers=ADMIN,
    )
    assert ok.status_code == 200
    knowledge = client.get("/api/admin/knowledge", headers=ADMIN).json()
    assert any(k["answer"] == "Sports day is on 15 March." for k in knowledge)
