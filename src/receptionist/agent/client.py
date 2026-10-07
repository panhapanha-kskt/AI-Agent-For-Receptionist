"""The receptionist agent: Claude + a tool-use loop + per-session conversation memory.

A manual loop (instead of the SDK's beta tool runner) is used so each tool call
gets a per-request DB session and an audit-log entry.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from receptionist.agent import guardrails
from receptionist.agent.prompts import build_system_prompt
from receptionist.agent.tools import ToolContext, run_tool, tool_definitions
from receptionist.config import Settings
from receptionist.security import audit
from receptionist.skills.loader import SkillRegistry

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
SORRY = {
    "en": "Sorry, I can't help with that right now. Would you like me to take a message for "
    "the front office?",
    "km": "សូមអភ័យទោស ខ្ញុំមិនអាចជួយរឿងនេះបានទេ។ តើអ្នកចង់ទុកសារសម្រាប់ការិយាល័យខាងមុខទេ?",
}


@dataclass
class AgentReply:
    text: str
    lang: str
    session_reset: bool = False


class SessionStore:
    """In-memory conversation history, with expiry. Use Redis or a DB for multiple workers."""

    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 1000):
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._data: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._expire()
            entry = self._data.get(session_id)
            return list(entry[1]) if entry else []

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        with self._lock:
            self._data[session_id] = (time.monotonic(), messages)
            if len(self._data) > self.max_sessions:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for key in [k for k, (t, _) in self._data.items() if t < cutoff]:
            del self._data[key]


class ReceptionistAgent:
    def __init__(self, settings: Settings, skills: SkillRegistry, client: Any | None = None):
        self.settings = settings
        self.skills = skills
        self.sessions = SessionStore()
        self._client = client

    @property
    def client(self):
        # Created on first use, so the app can start (and be tested) without a key.
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key.get_secret_value() or None
            )
        return self._client

    def _request(self, messages: list[dict[str, Any]]):
        system = build_system_prompt(self.settings.school_name, self.skills.index())
        return self.client.beta.messages.create(
            model=self.settings.claude_model,
            max_tokens=self.settings.claude_max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tool_definitions(self.skills),
            messages=messages,
            output_config={"effort": self.settings.claude_effort},
            # If a safety classifier declines, the server retries on a fallback model.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def reply(self, db: Session, session_id: str, user_text: str, lang_hint: str | None = None):
        text = guardrails.clean_input(user_text, self.settings.max_text_chars)
        lang = lang_hint or guardrails.detect_lang(text)

        history = self.sessions.get(session_id)
        reset = len(history) >= self.settings.max_history_turns * 2
        if reset:
            history = []  # start fresh instead of editing history (keeps it append-only)

        # Work on a copy; only save it if the whole turn succeeds.
        messages = [*history, {"role": "user", "content": text}]
        ctx = ToolContext(db=db, skills=self.skills, session_id=session_id)

        for _ in range(self.settings.agent_max_tool_rounds):
            response = self._request(messages)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                audit.record(db, "agent", "refusal", {"session": session_id}, session_id)
                # A refused turn should not stay in the history.
                self.sessions.clear(session_id)
                return AgentReply(SORRY[lang], lang, session_reset=True)

            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                break

            results = []
            for block in tool_uses:
                output, is_error = run_tool(ctx, block.name, block.input)
                audit.record(
                    db,
                    "agent",
                    f"tool:{block.name}",
                    {"input": block.input, "error": is_error},
                    session_id,
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    }
                )
            # All results go back in ONE user message.
            messages.append({"role": "user", "content": results})
        else:
            logger.warning("session %s hit the tool-round limit", session_id)
            return AgentReply(SORRY[lang], lang, session_reset=reset)

        reply_text = "".join(b.text for b in response.content if b.type == "text")
        reply_text = guardrails.clean_output(reply_text) or SORRY[lang]
        self.sessions.save(session_id, messages)
        return AgentReply(reply_text, guardrails.detect_lang(reply_text), session_reset=reset)
