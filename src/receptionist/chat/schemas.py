from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    # Language from speech-to-text, when the message came from a voice recording.
    lang: Literal["en", "km"] | None = None


class ChatResponse(BaseModel):
    reply: str
    lang: str
    audio_base64: str | None = None
    session_reset: bool = False
    transcript: str | None = None
    # Identifies this turn for 👍/👎 feedback.
    turn_id: int | None = None


class TranscriptResponse(BaseModel):
    transcript: str
    lang: str
    stt_ms: int


class FeedbackRequest(BaseModel):
    session_id: UUID
    turn_id: int = Field(gt=0)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)
