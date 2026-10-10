"""The receptionist agent: Gemini + a function-calling loop + per-session memory.

Design notes:
- Async + streaming: text is yielded as soon as Gemini produces it, so the caller sees
  the first words while the rest is still being generated.
- Automatic function calling is disabled; tools run here so every call gets input
  validation and an audit-log entry.
- Resilience: the SDK retries 408/429/5xx with exponential backoff + jitter, each request
  has a timeout, and if the main model is still overloaded we switch to a fallback model
  (only before any text has been shown, so the caller never sees a half-answer twice).
"""

import asyncio
import logging
import re
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy.orm import Session

from receptionist.agent import guardrails, memory
from receptionist.agent.prompts import build_system_prompt
from receptionist.agent.tools import ToolContext, run_tool, tool_definitions
from receptionist.config import Settings
from receptionist.database import SessionLocal
from receptionist.learning import service as learning
from receptionist.metrics.service import TurnStats, ms_since, now
from receptionist.router.embeddings import GeminiEmbedder
from receptionist.router.knowledge import KnowledgeMatcher
from receptionist.router.service import SkillRouter
from receptionist.security import audit
from receptionist.skills.loader import SkillRegistry

logger = logging.getLogger(__name__)

# Gemini stopped because of its safety filters.
BLOCKED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
}
# Worth trying the fallback model for these (after the SDK's own retries).
OVERLOADED = {408, 429, 500, 502, 503, 504}
QUOTA_COOLDOWN_SECONDS = 60
SORRY = {
    "en": "Sorry, I can't help with that right now. Would you like me to take a message for "
    "the front office?",
    "km": "សូមអភ័យទោស ខ្ញុំមិនអាចជួយរឿងនេះបានទេ។ តើអ្នកចង់ទុកសារសម្រាប់ការិយាល័យខាងមុខទេ?",
}
THINKING_LEVELS = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}


class AgentNotConfigured(RuntimeError):
    pass


@dataclass
class AgentReply:
    text: str
    lang: str
    session_reset: bool = False
    stats: TurnStats | None = None
    question: str = ""


@dataclass
class TurnRecord:
    """A recent answer, kept so the caller can rate it (thumbs up/down)."""

    question: str
    answer: str
    skill: str | None
    lang: str


MAX_RECENT_TURNS = 20


@dataclass
class SessionState:
    """What we remember about one conversation (in memory only, expires after 30 min)."""

    contents: list[types.Content] = field(default_factory=list)
    # Skills whose full content is already in `contents` (no need to send it again).
    skills_in_context: set[str] = field(default_factory=set)
    last_skill: str | None = None
    turns: int = 0  # caller messages since the last summary
    summary: str | None = None  # condensed earlier conversation (see agent/memory.py)
    summary_sent: bool = True
    recent_turns: dict[int, TurnRecord] = field(default_factory=dict)

    def copy(self) -> "SessionState":
        return SessionState(
            list(self.contents),
            set(self.skills_in_context),
            self.last_skill,
            self.turns,
            self.summary,
            self.summary_sent,
            dict(self.recent_turns),
        )


class SessionStore:
    """In-memory conversation state, with expiry. Use Redis or a DB for multiple workers."""

    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 1000):
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._data: dict[str, tuple[float, SessionState]] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState:
        """A copy of the session's state (changes are only kept if save() is called)."""
        with self._lock:
            self._expire()
            entry = self._data.get(session_id)
            return entry[1].copy() if entry else SessionState()

    def save(self, session_id: str, state: SessionState) -> None:
        with self._lock:
            self._data[session_id] = (time.monotonic(), state)
            if len(self._data) > self.max_sessions:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)

    def remember_turn(self, session_id: str, turn_id: int, record: TurnRecord) -> None:
        with self._lock:
            entry = self._data.get(session_id)
            if entry is None:
                return
            turns = entry[1].recent_turns
            turns[turn_id] = record
            while len(turns) > MAX_RECENT_TURNS:
                turns.pop(next(iter(turns)))

    def recent_turn(self, session_id: str, turn_id: int) -> TurnRecord | None:
        with self._lock:
            entry = self._data.get(session_id)
            return entry[1].recent_turns.get(turn_id) if entry else None

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for key in [k for k, (t, _) in self._data.items() if t < cutoff]:
            del self._data[key]


_FAKE_REFERENCE = re.compile(r"</?\s*school_reference", re.IGNORECASE)


def caller_text(text: str) -> str:
    """Neutralise anything in the caller's message that imitates our reference tag."""
    return _FAKE_REFERENCE.sub("(reference", text)


NO_MATCH_NOTE = (
    "[Server note: automatic matching found no skill or approved answer for the next "
    "message. If none of the listed skills clearly covers it, don't search further: call "
    "log_unanswered and offer to take a message.]"
)


def _learned_examples() -> dict[str, list[str]]:
    db = SessionLocal()
    try:
        return learning.routing_examples(db)
    finally:
        db.close()


def reference_block(skill: str, content: str) -> str:
    return f'<school_reference skill="{skill}">\n{content}\n</school_reference>'


@dataclass
class _Round:
    """Everything one streamed model call produced."""

    content: types.Content | None
    finish_reason: Any = None
    block_reason: Any = None
    usage: Any = None

    @property
    def function_calls(self) -> list[types.FunctionCall]:
        if self.content is None or not self.content.parts:
            return []
        return [p.function_call for p in self.content.parts if p.function_call]


class ReceptionistAgent:
    def __init__(self, settings: Settings, skills: SkillRegistry, client: Any | None = None):
        self.settings = settings
        self.skills = skills
        self.sessions = SessionStore()
        self._client = client
        self.router = None
        self.knowledge = None
        # Circuit breaker: model -> time until which it is skipped (after a 429 quota error).
        self._cooldown_until: dict[str, float] = {}
        # Background summarisation tasks (kept referenced so they aren't garbage-collected).
        self._background: set[asyncio.Task] = set()
        if settings.router_enabled:
            embedder = None
            if settings.router_embeddings:
                embedder = GeminiEmbedder(lambda: self.client, settings.embedding_model)
            self.router = SkillRouter(
                skills,
                embedder=embedder,
                threshold=settings.router_threshold,
                margin=settings.router_margin,
                lexical_threshold=settings.router_lexical_threshold,
                extra_examples=_learned_examples,
            )
            self.knowledge = KnowledgeMatcher(
                self.router, settings.knowledge_threshold, settings.knowledge_lexical_threshold
            )

    @property
    def client(self):
        # Created on first use, so the app can start (and be tested) without a key.
        if self._client is None:
            key = self.settings.gemini_api_key.get_secret_value()
            if not key:
                raise AgentNotConfigured("GEMINI_API_KEY is not set")
            self._client = genai.Client(
                api_key=key,
                vertexai=self.settings.gemini_vertexai,
                http_options=types.HttpOptions(
                    timeout=self.settings.llm_timeout_ms,
                    retry_options=types.HttpRetryOptions(
                        attempts=self.settings.llm_retry_attempts,
                        initial_delay=0.5,
                        max_delay=4.0,
                        # Not 429: a used-up quota won't recover in seconds, so we switch to
                        # the fallback model (separate quota) immediately instead.
                        http_status_codes=[408, 500, 502, 503, 504],
                    ),
                ),
            )
        return self._client

    def _config(self) -> types.GenerateContentConfig:
        # Deterministic for a given skill set, so Gemini can reuse the cached prefix.
        system = build_system_prompt(self.settings.school_name, self.skills.index())
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=t["parameters"],
            )
            for t in tool_definitions(self.skills)
        ]
        level = THINKING_LEVELS.get(self.settings.thinking_level.strip().lower())
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)],
            # We run tools ourselves (validation + audit), never the SDK.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=self.settings.llm_max_output_tokens,
            temperature=0.3,
            thinking_config=types.ThinkingConfig(thinking_level=level) if level else None,
        )

    async def _stream_round(
        self, model: str, contents: list[types.Content], config, stats: TurnStats, emitted: list
    ) -> AsyncIterator[str | _Round]:
        """One streamed model call. Yields text pieces, then the finished _Round."""
        parts: list[types.Part] = []
        result = _Round(content=None)
        started = now()
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            )
            async for chunk in stream:
                feedback = chunk.prompt_feedback
                if feedback is not None and feedback.block_reason:
                    result.block_reason = feedback.block_reason
                if chunk.usage_metadata is not None:
                    result.usage = chunk.usage_metadata  # cumulative; keep the last one
                if not chunk.candidates:
                    continue
                candidate = chunk.candidates[0]
                if candidate.finish_reason:
                    result.finish_reason = candidate.finish_reason
                if candidate.content is None or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    parts.append(part)  # kept as-is: preserves thought signatures
                    if part.text and not part.thought:
                        stats.mark_first_text()
                        emitted.append(part.text)
                        yield part.text
        finally:
            stats.llm_ms += ms_since(started)
            stats.llm_calls += 1
        stats.add_usage(result.usage)
        if parts:
            result.content = types.Content(role="model", parts=parts)
        yield result

    def _models(self, fast_first: bool) -> list[str]:
        """Models to try, in order, for this turn (skipping any in quota cooldown)."""
        s = self.settings
        models = [s.gemini_model_fast] if fast_first and s.gemini_model_fast else []
        models.append(s.gemini_model)
        if s.gemini_fallback_model and s.gemini_fallback_model not in models:
            models.append(s.gemini_fallback_model)
        now_ = time.monotonic()
        available = [m for m in models if self._cooldown_until.get(m, 0) <= now_]
        return available or models[-1:]  # never end up with nothing to try

    def _cool_down(self, model: str) -> None:
        """After a 429 (quota used up), skip this model for a while instead of hitting it,
        failing and falling back on every single call."""
        self._cooldown_until[model] = time.monotonic() + QUOTA_COOLDOWN_SECONDS
        logger.warning("%s is out of quota; skipping it for %ds", model, QUOTA_COOLDOWN_SECONDS)

    async def _round(self, models: list[str], contents, config, stats: TurnStats, emitted: list):
        """One model call, moving down `models` if a model fails before showing any text:
        - the fast model hands over to the main model on any error;
        - the main model hands over to the fallback model only when overloaded (429/5xx)."""
        for i, model in enumerate(models):
            stats.model = model
            try:
                async for item in self._stream_round(model, contents, config, stats, emitted):
                    yield item
                return
            except (genai_errors.APIError, httpx.TimeoutException) as exc:
                code = getattr(exc, "code", 408)
                is_last = i == len(models) - 1
                is_fast = model == self.settings.gemini_model_fast and i == 0
                if code == 429:
                    self._cool_down(model)
                if emitted or is_last or not (is_fast or code in OVERLOADED):
                    raise
                logger.warning("%s failed (%s %s); trying %s", model, code, exc, models[i + 1])
                stats.retries += 1

    async def _blocked(self, db, session_id: str, lang: str, reason: str, stats: TurnStats):
        await asyncio.to_thread(
            audit.record, db, "agent", "blocked", {"reason": reason}, session_id
        )
        # A blocked turn should not stay in the history.
        self.sessions.clear(session_id)
        stats.outcome = "blocked"
        return AgentReply(SORRY[lang], lang, session_reset=True, stats=stats)

    async def stream(
        self,
        db: Session,
        session_id: str,
        user_text: str,
        lang_hint: str | None = None,
        stats: TurnStats | None = None,
    ) -> AsyncIterator[str | AgentReply]:
        """Yield reply text as it is generated; the last item is the final AgentReply."""
        stats = stats or TurnStats(session_id=session_id)
        stats.session_id = session_id
        stats.model = self.settings.gemini_model
        text = guardrails.clean_input(user_text, self.settings.max_text_chars)
        lang = lang_hint or guardrails.detect_lang(text)

        # Work on a copy; it is only saved if the whole turn succeeds.
        state = self.sessions.get(session_id)
        reset = len(state.contents) >= self.settings.max_history_turns * 2
        if reset:
            state = SessionState()  # start fresh instead of editing history

        user_parts, workflow, routed_skill = [], False, None
        loaded_skills: list[str] = []
        logged_unanswered = False
        if self.router is not None:
            started = now()
            route = await self.router.route(text, previous_skill=state.last_skill)
            if route.skill:
                routed_skill = route.skill
                stats.path, stats.skill = "routed", route.skill
                state.last_skill = route.skill
                skill = self.skills.get(route.skill)
                workflow = skill is not None and "workflow.yaml" in skill.files
                if route.skill not in state.skills_in_context:
                    bundle = self.skills.bundle(route.skill, self.settings.router_bundle_max_bytes)
                    user_parts.append(
                        types.Part.from_text(text=reference_block(route.skill, bundle))
                    )
                    state.skills_in_context.add(route.skill)
            # Staff-approved answers (from the review queue) are checked on the server too.
            approved = await self.knowledge.match(db, text) if self.knowledge else None
            if approved is not None:
                entry, score = approved
                stats.path = "knowledge"
                approved_text = f"Question: {entry.question}\nAnswer: {entry.answer}"
                user_parts.append(
                    types.Part.from_text(text=reference_block("approved-answer", approved_text))
                )
            stats.route_ms = ms_since(started)
            logger.info(
                "route %s score=%.2f via %s; approved answer: %s",
                route.skill, route.score, route.method,
                f"#{approved[0].id} ({approved[1]:.2f})" if approved else "none",
            )  # fmt: skip
            if not route.skill and approved is None:
                user_parts.append(types.Part.from_text(text=NO_MATCH_NOTE))
        if state.summary and not state.summary_sent:
            user_parts.insert(0, types.Part.from_text(text=memory.summary_block(state.summary)))
            state.summary_sent = True
        user_parts.append(types.Part.from_text(text=caller_text(text)))
        contents = state.contents
        contents.append(types.Content(role="user", parts=user_parts))
        # Answer already attached and no multi-step workflow: the fast model is enough.
        models = self._models(fast_first=stats.path in ("routed", "knowledge") and not workflow)
        ctx = ToolContext(db=db, skills=self.skills, session_id=session_id)
        config = self._config()
        emitted: list[str] = []

        for _ in range(self.settings.agent_max_tool_rounds):
            result: _Round | None = None
            async for item in self._round(models, contents, config, stats, emitted):
                if isinstance(item, _Round):
                    result = item
                else:
                    yield item

            if result.block_reason or result.finish_reason in BLOCKED:
                reason = str(result.block_reason or result.finish_reason)
                yield await self._blocked(db, session_id, lang, reason, stats)
                return

            if result.content is None:
                logger.warning("empty response, finish_reason=%s", result.finish_reason)
                stats.outcome = "empty"
                yield AgentReply(SORRY[lang], lang, session_reset=reset, stats=stats)
                return

            # Append the model turn unchanged (keeps Gemini's thought signatures intact).
            contents.append(result.content)

            calls = result.function_calls
            if not calls:
                break

            parts = []
            for call in calls:
                args = dict(call.args or {})
                started = now()
                # Tools use the (sync) database; run them off the event loop.
                output, is_error = await asyncio.to_thread(run_tool, ctx, call.name or "", args)
                stats.tool_ms += ms_since(started)
                if call.name == "log_unanswered":
                    logged_unanswered = True
                if call.name == "load_skill" and not is_error:
                    loaded_skills.append(args.get("name"))
                    stats.skill = args.get("name")
                    state.skills_in_context.add(stats.skill)
                    state.last_skill = stats.skill
                await asyncio.to_thread(
                    audit.record,
                    db,
                    "agent",
                    f"tool:{call.name}",
                    {"input": args, "error": is_error},
                    session_id,
                )
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call.id,
                            name=call.name,
                            response={"error": output} if is_error else {"result": output},
                        )
                    )
                )
            # All results go back in ONE turn.
            contents.append(types.Content(role="user", parts=parts))
        else:
            logger.warning("session %s hit the tool-round limit", session_id)
            stats.outcome = "tool_limit"
            yield AgentReply(SORRY[lang], lang, session_reset=reset, stats=stats)
            return

        reply_text = guardrails.clean_output("".join(emitted)) or SORRY[lang]
        # Learn from router misses: the model found the right skill itself.
        if self.router is not None and loaded_skills and not logged_unanswered:
            learned = loaded_skills[-1]
            if learned != routed_skill:
                added = await asyncio.to_thread(learning.add_routing_example, db, learned, text)
                if added is not None:
                    self.router.examples_changed()
                    logger.info("learned routing example for %s", learned)
        state.turns += 1
        self.sessions.save(session_id, state)
        every = self.settings.summarize_after_turns
        if every and state.turns >= every:
            # After the reply is delivered, so the caller never waits for it.
            task = asyncio.create_task(self._summarize(session_id, state.turns))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        yield AgentReply(
            reply_text,
            guardrails.detect_lang(reply_text),
            session_reset=reset,
            stats=stats,
            question=text,
        )

    async def _summarize(self, session_id: str, turns_seen: int) -> None:
        """Condense the conversation so far into a short summary (runs in the background)."""
        state = self.sessions.get(session_id)
        if state.turns != turns_seen or not state.contents:
            return  # the caller has moved on; try again after their next message
        prompt = memory.SUMMARY_PROMPT.format(
            transcript=memory.transcript(state.contents, state.summary)
        )
        try:
            models = self._models(fast_first=True)
            result = await self.client.aio.models.generate_content(
                model=models[0], contents=prompt, config=memory.summary_config()
            )
            summary = (result.text or "").strip()
        except Exception:
            logger.exception("could not summarise session %s", session_id)
            return
        if not summary:
            return
        latest = self.sessions.get(session_id)
        if latest.turns != turns_seen:
            return  # a new message arrived meanwhile: don't overwrite it
        self.sessions.save(
            session_id,
            SessionState(last_skill=latest.last_skill, summary=summary, summary_sent=False),
        )
        logger.info("session %s summarised after %d messages", session_id, turns_seen)

    async def draft_answer(self, db: Session, question: str) -> str:
        """Draft an answer for STAFF to review (review queue), from skills/approved answers only.

        Never shown to callers directly: staff edit and approve it first.
        """
        parts = []
        if self.router is not None:
            route = await self.router.route(question)
            if route.skill:
                bundle = self.skills.bundle(route.skill, self.settings.router_bundle_max_bytes)
                parts.append(types.Part.from_text(text=reference_block(route.skill, bundle)))
            approved = await self.knowledge.match(db, question) if self.knowledge else None
            if approved is not None:
                entry, _ = approved
                approved_text = f"Question: {entry.question}\nAnswer: {entry.answer}"
                parts.append(
                    types.Part.from_text(text=reference_block("approved-answer", approved_text))
                )
        if not parts:
            return ""
        parts.append(types.Part.from_text(text=memory.DRAFT_PROMPT.format(question=question)))
        config = types.GenerateContentConfig(
            max_output_tokens=600,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )
        last_error = None
        for model in self._models(fast_first=True):
            try:
                result = await self.client.aio.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                return (result.text or "").strip()
            except (genai_errors.APIError, httpx.TimeoutException) as exc:
                last_error = exc
        raise last_error

    async def reply(self, db: Session, session_id: str, user_text: str, **kwargs) -> AgentReply:
        """Non-streaming convenience wrapper around stream()."""
        final = None
        async for item in self.stream(db, session_id, user_text, **kwargs):
            if isinstance(item, AgentReply):
                final = item
        return final
