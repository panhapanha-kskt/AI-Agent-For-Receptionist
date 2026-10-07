"""Tools the agent may call.

Every tool is single-purpose and least-privilege. The model's tool input is
untrusted, so each handler validates it again (types, enums, lengths) before acting.
The agent can READ skills and knowledge, but can only WRITE two things: a message
for staff, and an entry in the review queue. It can never change what it knows.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from receptionist.knowledge import service as knowledge
from receptionist.learning import service as learning
from receptionist.messages import service as messages
from receptionist.messages.models import OFFICES
from receptionist.skills.loader import SkillError, SkillRegistry

LANGS = ["en", "km"]


class ToolInputError(ValueError):
    pass


@dataclass
class ToolContext:
    db: Session
    skills: SkillRegistry
    session_id: str


def tool_definitions(skills: SkillRegistry) -> list[dict[str, Any]]:
    """JSON schemas sent to Claude. Skill names are an enum, so only real skills are valid."""
    names = skills.names() or ["none"]

    def tool(name: str, description: str, properties: dict) -> dict:
        return {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        }

    return [
        tool(
            "load_skill",
            "Load the full instructions for one skill. Call this before answering a question "
            "the skill covers.",
            {"name": {"type": "string", "enum": names}},
        ),
        tool(
            "read_skill_file",
            "Read one reference file listed by load_skill (e.g. fees.yaml) for exact details.",
            {
                "skill": {"type": "string", "enum": names},
                "filename": {"type": "string", "description": "File name exactly as listed."},
            },
        ),
        tool(
            "search_knowledge",
            "Search answers that school staff have approved. Use when no skill covers the "
            "question, or the skill does not have the detail.",
            {
                "query": {"type": "string", "description": "The caller's question."},
                "skill": {
                    "type": "string",
                    "enum": ["", *names],
                    "description": "Limit to one skill, or empty string for all.",
                },
            },
        ),
        tool(
            "take_message",
            "Save a message for school staff. Confirm the details with the caller first.",
            {
                "caller_name": {"type": "string"},
                "contact": {"type": "string", "description": "Phone number or email."},
                "for_office": {"type": "string", "enum": list(OFFICES)},
                "message": {"type": "string"},
            },
        ),
        tool(
            "log_unanswered",
            "Record a question you could not answer from skills or knowledge, so staff can add "
            "the answer. Rewrite it as a clear general question without personal details.",
            {
                "question": {"type": "string"},
                "lang": {"type": "string", "enum": LANGS},
                "suggested_skill": {
                    "type": "string",
                    "enum": ["", *names],
                    "description": "Best matching skill, or empty string if none fits.",
                },
            },
        ),
    ]


def _str(data: dict, key: str, max_len: int, required: bool = True) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ToolInputError(f"'{key}' must be a string")
    value = value.strip()
    if required and not value:
        raise ToolInputError(f"'{key}' is required")
    if len(value) > max_len:
        raise ToolInputError(f"'{key}' is too long (max {max_len})")
    return value


def _choice(data: dict, key: str, allowed: list[str] | tuple[str, ...]) -> str:
    value = data.get(key)
    if value not in allowed:
        raise ToolInputError(f"'{key}' must be one of {list(allowed)}")
    return value


def _load_skill(ctx: ToolContext, data: dict) -> str:
    return ctx.skills.load(_choice(data, "name", ctx.skills.names()))


def _read_skill_file(ctx: ToolContext, data: dict) -> str:
    skill = _choice(data, "skill", ctx.skills.names())
    return ctx.skills.read_file(skill, _str(data, "filename", 100))


def _search_knowledge(ctx: ToolContext, data: dict) -> str:
    query = _str(data, "query", 500)
    skill = _choice(data, "skill", ["", *ctx.skills.names()]) or None
    hits = knowledge.search(ctx.db, query, skill=skill)
    if not hits:
        return "No approved answer found."
    return json.dumps(
        [{"question": e.question, "answer": e.answer, "skill": e.skill} for _, e in hits],
        ensure_ascii=False,
    )


def _take_message(ctx: ToolContext, data: dict) -> str:
    msg = messages.create(
        ctx.db,
        caller_name=_str(data, "caller_name", 120),
        contact=_str(data, "contact", 120),
        for_office=_choice(data, "for_office", OFFICES),
        body=_str(data, "message", 2000),
        session_id=ctx.session_id,
    )
    return f"Message saved (reference #{msg.id}). Staff will follow up."


def _log_unanswered(ctx: ToolContext, data: dict) -> str:
    item = learning.log_unanswered(
        ctx.db,
        question=_str(data, "question", 1000),
        lang=_choice(data, "lang", LANGS),
        suggested_skill=_choice(data, "suggested_skill", ["", *ctx.skills.names()]) or None,
    )
    return f"Logged for staff review (#{item.id})."


HANDLERS = {
    "load_skill": _load_skill,
    "read_skill_file": _read_skill_file,
    "search_knowledge": _search_knowledge,
    "take_message": _take_message,
    "log_unanswered": _log_unanswered,
}


def run_tool(ctx: ToolContext, name: str, data: Any) -> tuple[str, bool]:
    """Execute a tool call. Returns (result_text, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool '{name}'.", True
    if not isinstance(data, dict):
        return "Tool input must be an object.", True
    try:
        return handler(ctx, data), False
    except (ToolInputError, SkillError) as exc:
        return f"Error: {exc}", True
