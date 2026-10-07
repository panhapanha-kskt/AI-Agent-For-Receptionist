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
    ANTHROPIC_API_KEY="",
    RATE_LIMIT="1000/minute",
    STT_ENABLED="false",
    TTS_PROVIDER="browser",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from receptionist.database import SessionLocal, init_db  # noqa: E402

ADMIN = {"X-Admin-Key": "test-admin-key"}


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, data, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=data, id=block_id)


def response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeClaude:
    """Stands in for anthropic.Anthropic(); returns scripted responses in order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Snapshot the messages: the agent keeps appending to the same list.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def fake_claude():
    return FakeClaude()


@pytest.fixture
def app(fake_claude):
    from receptionist.main import create_app

    init_db()
    return create_app(agent_client=fake_claude)


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
