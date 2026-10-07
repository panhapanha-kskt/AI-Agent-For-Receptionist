from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str
    lang: str
    audio_base64: str | None = None
    session_reset: bool = False
    transcript: str | None = None
