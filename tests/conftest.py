import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Configure a throwaway environment BEFORE the app modules are imported.
_tmp = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parents[1]
os.environ.update(
    DATABASE_URL=f"sqlite:///{Path(_tmp) / 'test.db'}",
    SKILLS_DIR=str(ROOT / "skills"),
    WEB_DIR=str(ROOT / "web"),
    ADMIN_API_KEY="test-admin-key",
    GEMINI_API_KEY="",
    RATE_LIMIT="1000/minute",
    # Deterministic keyword routing in tests (no embeddings API).
    ROUTER_EMBEDDINGS="false",
    STT_ENABLED="false",
    TTS_PROVIDER="browser",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402
from google.genai import types  # noqa: E402

from receptionist.database import SessionLocal, init_db  # noqa: E402

ADMIN = {"X-Admin-Key": "test-admin-key"}


def text_block(text):
    return types.Part.from_text(text=text)


def tool_block(name, data, call_id="call_1"):
    return types.Part(function_call=types.FunctionCall(name=name, args=data, id=call_id))


def response(*parts, finish_reason=types.FinishReason.STOP):
    """A real google-genai response object, as the API would return it."""
    content = types.Content(role="model", parts=list(parts)) if parts else None
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=content, finish_reason=finish_reason)]
    )


def blocked_prompt():
    return types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
    )


def overloaded(code=503):
    """A Gemini API error, as raised by the SDK after its own retries."""
    return genai_errors.ServerError(
        code, {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}}
    )


def split_text(resp, pieces=3):
    """Split a text response into several stream chunks, like the real API does."""
    text = "".join(p.text or "" for p in resp.candidates[0].content.parts)
    size = max(1, len(text) // pieces)
    chunks = [text[i : i + size] for i in range(0, len(text), size)]
    return [response(text_block(c)) for c in chunks]


class FakeGemini:
    """Stands in for genai.Client(); returns scripted responses in order.

    Each scripted item is a response (sent as one stream chunk), a list of responses
    (sent as several chunks), or an exception (raised when the call is made).
    """

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self.aio = SimpleNamespace(
            models=SimpleNamespace(
                generate_content_stream=self._stream, generate_content=self._agenerate
            )
        )

    def _next(self, model, contents, config):
        # Snapshot the contents: the agent keeps appending to the same list.
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _generate(self, *, model, contents, config):
        return self._next(model, contents, config)

    async def _agenerate(self, *, model, contents, config):
        return self._next(model, contents, config)

    async def _stream(self, *, model, contents, config):
        item = self._next(model, contents, config)
        chunks = item if isinstance(item, list) else [item]

        async def gen():
            for chunk in chunks:
                yield chunk

        return gen()


@pytest.fixture
def fake_llm():
    return FakeGemini()


@pytest.fixture
def app(fake_llm):
    from receptionist.main import create_app

    init_db()
    return create_app(agent_client=fake_llm)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
