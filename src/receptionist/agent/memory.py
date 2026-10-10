"""Rolling conversation summaries ("compaction") for long chats.

Instead of wiping a conversation after N turns, older turns are condensed into a short
summary that is carried forward. This keeps requests small (fast, cheap) while the agent
still remembers what the caller wants. Personal details are masked before summarising,
and summaries live only in memory with the rest of the session (never stored).
"""

from google.genai import types

from receptionist.security.redact import redact

SUMMARY_PROMPT = """\
Summarise this conversation between a school receptionist (AI) and a caller, so the \
receptionist can continue it. At most 120 words, plain text, in English. Include:
- who the caller is if they said (parent, student, visitor) and their preferred language;
- what they want, and any program, grade or date they mentioned;
- what has already been answered (with the exact facts given);
- anything still open or promised (e.g. a message taken for an office).
Do not include phone numbers, email addresses or other personal identifiers.

Conversation:
{transcript}
"""
MAX_TRANSCRIPT_CHARS = 12000

DRAFT_PROMPT = """\
You are helping school staff answer a question that callers asked. Using ONLY the school \
reference blocks above, draft a short, friendly answer (1-4 sentences) that staff can \
review and edit. If the references do not contain the answer, or the needed detail is \
marked TODO, reply exactly: NOT FOUND IN SKILLS - please write the answer.

Question: {question}
"""


def _is_internal(text: str) -> bool:
    """School references and server notes are context, not conversation."""
    stripped = text.lstrip()
    return stripped.startswith(("<school_reference", "[Server note", "<conversation_summary"))


def transcript(contents: list[types.Content], previous_summary: str | None = None) -> str:
    lines = []
    if previous_summary:
        lines.append(f"(Earlier summary) {previous_summary}")
    for content in contents:
        for part in content.parts or []:
            if part.text and not part.thought and not _is_internal(part.text):
                speaker = "Caller" if content.role == "user" else "Receptionist"
                lines.append(f"{speaker}: {part.text.strip()}")
            elif part.function_call and part.function_call.name == "take_message":
                lines.append("Receptionist: (saved a message for staff)")
    # Mask personal data; keep the most recent part if it is very long.
    return redact("\n".join(lines))[-MAX_TRANSCRIPT_CHARS:]


def summary_block(summary: str) -> str:
    return f"<conversation_summary>\n{summary}\n</conversation_summary>"


def summary_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        max_output_tokens=400,
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    )
