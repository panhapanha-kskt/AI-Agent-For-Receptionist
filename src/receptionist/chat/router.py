"""Public endpoints: text chat (plain + streaming), transcription, and voice messages."""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.sse import EventSourceResponse, format_sse_event
from google.genai import errors as genai_errors
from sqlalchemy.orm import Session

from receptionist.agent import guardrails
from receptionist.agent.client import AgentNotConfigured, AgentReply, TurnRecord
from receptionist.agent.guardrails import InputRejected
from receptionist.chat.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    TranscriptResponse,
)
from receptionist.config import get_settings
from receptionist.database import SessionLocal, get_db
from receptionist.learning import service as learning
from receptionist.metrics import service as metrics
from receptionist.metrics.service import TurnStats, ms_since, now
from receptionist.security.rate_limit import limiter
from receptionist.voice.stt import AudioTooLong, STTUnavailable, Transcript
from receptionist.voice.validators import AudioRejected, validate_audio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])
BUSY = "Assistant is busy, please try again."


def _rate() -> str:
    return get_settings().rate_limit


def _api_failure(exc: Exception, stats: TurnStats) -> None:
    """Log the real reason server-side; callers only ever see BUSY."""
    if isinstance(exc, genai_errors.APIError):
        logger.error("Gemini API error %s: %s", exc.code, exc.message)
        stats.outcome = f"api_{exc.code}"
    elif isinstance(exc, AgentNotConfigured):
        logger.error("LLM not configured: %s", exc)
        stats.outcome = "not_configured"
    else:
        logger.exception("LLM request failed")
        stats.outcome = "error"


async def _finish(request: Request, db: Session, result: AgentReply, stats: TurnStats) -> dict:
    """TTS + metrics for a finished reply; returns the response fields."""
    agent = request.app.state.agent
    started = now()
    audio = await asyncio.to_thread(request.app.state.tts.synthesize, result.text, result.lang)
    if audio:
        stats.tts_ms = ms_since(started)
    stats.finish()
    turn_id = await asyncio.to_thread(metrics.record, db, stats)
    if turn_id is not None and stats.session_id and stats.outcome == "answered":
        # Remember the answer briefly so the caller can rate it.
        agent.sessions.remember_turn(
            stats.session_id,
            turn_id,
            TurnRecord(result.question, result.text, stats.skill, result.lang),
        )
    return {
        "reply": result.text,
        "lang": result.lang,
        "audio_base64": base64.b64encode(audio).decode() if audio else None,
        "session_reset": result.session_reset,
        "turn_id": turn_id,
    }


async def _answer(
    request: Request, db: Session, session_id: UUID, text: str, lang: str | None, stats: TurnStats
) -> ChatResponse:
    try:
        result = await request.app.state.agent.reply(
            db, str(session_id), text, lang_hint=lang, stats=stats
        )
    except InputRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _api_failure(exc, stats)
        await asyncio.to_thread(metrics.record, db, stats)
        raise HTTPException(status_code=503, detail=BUSY) from None
    return ChatResponse(**await _finish(request, db, result, stats))


async def _transcribe(request: Request, audio: UploadFile, lang: str) -> tuple[Transcript, int]:
    settings = get_settings()
    try:
        data, _fmt = await asyncio.to_thread(
            validate_audio, audio.file, audio.content_type, settings.max_audio_bytes
        )
        started = now()
        transcript = await asyncio.to_thread(
            request.app.state.stt.transcribe, data, None if lang == "auto" else lang
        )
        stt_ms = ms_since(started)
    except (AudioRejected, AudioTooLong) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except STTUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        logger.exception("Could not decode audio")
        raise HTTPException(status_code=422, detail="Could not read the audio file.") from None
    finally:
        await audio.close()
    if not transcript.text:
        raise HTTPException(status_code=422, detail="No speech was detected.")
    return transcript, stt_ms


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(_rate)
async def chat(request: Request, body: ChatRequest, db: Session = Depends(get_db)):
    stats = TurnStats(channel="text")
    return await _answer(request, db, body.session_id, body.message, body.lang, stats)


def _sse(event: str, data: dict) -> bytes:
    return format_sse_event(event=event, data_str=json.dumps(data, ensure_ascii=False))


# Returns EventSourceResponse directly (not response_class=...), so FastAPI does not
# treat this rate-limited handler as a generator endpoint.
@router.post("/chat/stream")
@limiter.limit(_rate)
async def chat_stream(request: Request, body: ChatRequest):
    """Server-Sent Events: `delta` {text} ... then `done` {reply, lang, turn_id, ...} or
    `error` {detail}. The model call is cancelled if the browser disconnects."""
    try:
        guardrails.clean_input(body.message, get_settings().max_text_chars)
    except InputRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def events() -> AsyncIterator[bytes]:
        stats = TurnStats(channel="stream")
        # Own session: it must live as long as the stream, not just the request handler.
        db = SessionLocal()
        stream = request.app.state.agent.stream(
            db, str(body.session_id), body.message, lang_hint=body.lang, stats=stats
        )
        try:
            async for item in stream:
                if isinstance(item, AgentReply):
                    yield _sse("done", await _finish(request, db, item, stats))
                    return
                if await request.is_disconnected():
                    stats.outcome = "disconnected"
                    await asyncio.to_thread(metrics.record, db, stats)
                    return  # closing the generator cancels the upstream Gemini stream
                yield _sse("delta", {"text": item})
        except Exception as exc:
            _api_failure(exc, stats)
            await asyncio.to_thread(metrics.record, db, stats)
            yield _sse("error", {"detail": BUSY})
        finally:
            await stream.aclose()
            db.close()

    return EventSourceResponse(
        events(),
        # Ask reverse proxies (nginx) not to buffer the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/transcribe", response_model=TranscriptResponse)
@limiter.limit(_rate)
async def transcribe(
    request: Request,
    lang: Literal["auto", "en", "km"] = Form("auto"),
    audio: UploadFile = File(...),
):
    """Voice message → text. The page then sends the text to /api/chat/stream."""
    transcript, stt_ms = await _transcribe(request, audio, lang)
    logger.info(
        "transcribed %.1fs of audio in %dms (%s)", transcript.duration, stt_ms, transcript.lang
    )
    return TranscriptResponse(transcript=transcript.text, lang=transcript.lang, stt_ms=stt_ms)


@router.post("/voice", response_model=ChatResponse)
@limiter.limit(_rate)
async def voice(
    request: Request,
    session_id: UUID = Form(...),
    lang: Literal["auto", "en", "km"] = Form("auto"),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Voice message → full reply in one request (for API clients; the web page streams)."""
    stats = TurnStats(channel="voice")
    transcript, stats.stt_ms = await _transcribe(request, audio, lang)
    response = await _answer(request, db, session_id, transcript.text, transcript.lang, stats)
    response.transcript = transcript.text
    return response


@router.post("/feedback", status_code=201)
@limiter.limit(_rate)
async def feedback(request: Request, body: FeedbackRequest, db: Session = Depends(get_db)):
    """A caller's 👍/👎 on one answer. Only answers given in this caller's own session
    (in the last 30 minutes) can be rated, and each only once."""
    agent = request.app.state.agent
    turn = agent.sessions.recent_turn(str(body.session_id), body.turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="This answer can no longer be rated.")
    saved = await asyncio.to_thread(
        learning.record_feedback,
        db,
        body.turn_id,
        body.rating,
        turn.question,
        turn.answer,
        turn.skill,
        turn.lang,
        body.comment,
    )
    if saved is None:
        raise HTTPException(status_code=409, detail="Already rated.")
    return {"ok": True}
