import uuid

from tests.conftest import ADMIN, response, text_block, tool_block

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


def test_chat_runs_tool_loop(client, fake_claude):
    fake_claude.responses = [
        response(tool_block("load_skill", {"name": "fees-and-payments"}), stop_reason="tool_use"),
        response(
            tool_block(
                "read_skill_file", {"skill": "fees-and-payments", "filename": "fees.yaml"}, "tu_2"
            ),
            stop_reason="tool_use",
        ),
        response(text_block("Grade 7 tuition is 2,900 USD per year.")),
    ]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "Fees for grade 7?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"].startswith("Grade 7 tuition") and body["lang"] == "en"

    # Tool results went back to Claude in a single user message, with the real file content.
    last_call = fake_claude.calls[-1]["messages"]
    results = last_call[-1]["content"]
    assert results[0]["type"] == "tool_result" and "2900" in results[0]["content"]

    # The request used a frozen system prompt + the model settings.
    first = fake_claude.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert "admissions:" in first["system"][0]["text"]
    assert "Fees for grade 7?" not in first["system"][0]["text"]


def test_conversation_memory_is_per_session(client, fake_claude):
    sid = SID()
    fake_claude.responses = [response(text_block("Hi!")), response(text_block("Again!"))]
    client.post("/api/chat", json={"session_id": sid, "message": "hello"})
    client.post("/api/chat", json={"session_id": sid, "message": "and again"})
    assert len(fake_claude.calls[1]["messages"]) == 3  # user, assistant, user

    fake_claude.responses = [response(text_block("New"))]
    client.post("/api/chat", json={"session_id": SID(), "message": "other person"})
    assert len(fake_claude.calls[2]["messages"]) == 1


def test_refusal_returns_safe_reply(client, fake_claude):
    fake_claude.responses = [response(stop_reason="refusal")]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "something bad"})
    assert r.status_code == 200 and "Sorry" in r.json()["reply"]


def test_khmer_reply_lang(client, fake_claude):
    fake_claude.responses = [response(text_block("សាលាបើកម៉ោង ៧:៣០ ព្រឹក។"))]
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


def test_admin_approve_flow(client, fake_claude):
    q = f"When is sports day {uuid.uuid4().hex[:6]}?"
    fake_claude.responses = [
        response(
            tool_block("log_unanswered", {"question": q, "lang": "en", "suggested_skill": ""}),
            stop_reason="tool_use",
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
