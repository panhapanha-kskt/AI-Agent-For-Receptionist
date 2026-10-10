"""Phase 1: streaming replies, retries/fallback model, thinking level, speech warm-up."""

import json
import uuid

from google.genai import types

from receptionist.config import get_settings
from receptionist.metrics.models import TurnMetric
from receptionist.voice.stt import WhisperSTT
from tests.conftest import overloaded, response, split_text, text_block, tool_block

SID = lambda: str(uuid.uuid4())  # noqa: E731


def read_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


def stream(client, message, **extra):
    r = client.post("/api/chat/stream", json={"session_id": SID(), "message": message, **extra})
    return r, read_sse(r.text)


def test_stream_sends_deltas_then_done(client, fake_llm):
    fake_llm.responses = [
        response(tool_block("load_skill", {"name": "fees-and-payments"})),
        split_text(response(text_block("Grade 7 tuition is 2,900 USD per year.")), pieces=4),
    ]
    r, events = stream(client, "Fees for grade 7?")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert kinds.count("delta") >= 3 and kinds[-1] == "done"
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    done = events[-1][1]
    assert streamed == done["reply"] == "Grade 7 tuition is 2,900 USD per year."
    assert done["turn_id"] and done["lang"] == "en"


def test_stream_rejects_bad_input(client):
    r = client.post("/api/chat/stream", json={"session_id": SID(), "message": "\x00\x01"})
    assert r.status_code == 422


def test_stream_reports_error_event(client, fake_llm):
    fake_llm.responses = [overloaded(400)]  # not an overload: no fallback
    r, events = stream(client, "hello")
    assert events[-1][0] == "error"
    assert events[-1][1]["detail"] == "Assistant is busy, please try again."


def test_overload_switches_to_fallback_model(client, fake_llm, db):
    fake_llm.responses = [overloaded(503), response(text_block("Hello from the fallback."))]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    assert r.status_code == 200 and r.json()["reply"] == "Hello from the fallback."
    settings = get_settings()
    assert fake_llm.calls[0]["model"] == settings.gemini_model
    assert fake_llm.calls[1]["model"] == settings.gemini_fallback_model

    turn = db.get(TurnMetric, r.json()["turn_id"])
    assert turn.retries == 1 and turn.model == settings.gemini_fallback_model
    assert turn.outcome == "answered"


def test_both_models_overloaded_returns_503(client, fake_llm):
    fake_llm.responses = [overloaded(503), overloaded(503)]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    assert r.status_code == 503


def test_no_fallback_for_client_errors(client, fake_llm):
    fake_llm.responses = [overloaded(400)]
    r = client.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    assert r.status_code == 503 and len(fake_llm.calls) == 1


def test_thinking_level_is_low_by_default(client, fake_llm):
    fake_llm.responses = [response(text_block("Hi"))]
    client.post("/api/chat", json={"session_id": SID(), "message": "hi"})
    config = fake_llm.calls[0]["config"]
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW


def test_voice_language_is_passed_through(client, fake_llm):
    fake_llm.responses = [response(text_block("Hello"))]
    r, events = stream(client, "hello", lang="km")
    assert events[-1][0] == "done"


def test_transcribe_disabled_returns_503(client):
    r = client.post(
        "/api/transcribe",
        data={"lang": "auto"},
        files={"audio": ("v.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 500, "audio/webm")},
    )
    assert r.status_code == 503


def test_transcribe_rejects_fake_audio(client):
    r = client.post(
        "/api/transcribe",
        data={"lang": "auto"},
        files={"audio": ("evil.webm", b"MZ" + b"\x00" * 500, "audio/webm")},
    )
    assert r.status_code == 422


def test_stt_warm_up_is_noop_when_disabled():
    stt = WhisperSTT(get_settings())
    stt.warm_up()  # STT_ENABLED=false in tests: must not try to load models
    assert stt._models == {}
