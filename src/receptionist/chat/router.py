"""Public endpoints: text chat and voice messages."""

import base64
import logging
from typing import Literal
from uuid import UUID

import anthropic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from receptionist.agent.guardrails import InputRejected
from receptionist.chat.schemas import ChatRequest, ChatResponse
from receptionist.config import get_settings
from receptionist.database import get_db
from receptionist.security.rate_limit import limiter
from receptionist.voice.stt import AudioTooLong, STTUnavailable
from receptionist.voice.validators import AudioRejected, validate_audio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def _rate() -> str:
    return get_settings().rate_limit


def _answer(request: Request, db: Session, session_id: UUID, text: str, lang: str | None):
    agent = request.app.state.agent
    try:
        result = agent.reply(db, str(session_id), text, lang_hint=lang)
    except InputRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (anthropic.AnthropicError, TypeError):
        # API errors, or the SDK finding no credentials (TypeError).
        # Log server-side; never leak details to the caller.
        logger.exception("Claude API error")
        raise HTTPException(
            status_code=503, detail="Assistant is busy, please try again."
        ) from None

    audio = request.app.state.tts.synthesize(result.text, result.lang)
    return ChatResponse(
        reply=result.text,
        lang=result.lang,
        audio_base64=base64.b64encode(audio).decode() if audio else None,
        session_reset=result.session_reset,
    )


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(_rate)
def chat(request: Request, body: ChatRequest, db: Session = Depends(get_db)):
    return _answer(request, db, body.session_id, body.message, None)


@router.post("/voice", response_model=ChatResponse)
@limiter.limit(_rate)
def voice(
    request: Request,
    session_id: UUID = Form(...),
    lang: Literal["auto", "en", "km"] = Form("auto"),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    try:
        data, _fmt = validate_audio(audio.file, audio.content_type, settings.max_audio_bytes)
        transcript = request.app.state.stt.transcribe(data, None if lang == "auto" else lang)
    except (AudioRejected, AudioTooLong) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except STTUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        logger.exception("Could not decode audio")
        raise HTTPException(status_code=422, detail="Could not read the audio file.") from None
    finally:
        audio.file.close()

    if not transcript.text:
        raise HTTPException(status_code=422, detail="No speech was detected.")

    response = _answer(request, db, session_id, transcript.text, transcript.lang)
    response.transcript = transcript.text
    return response
