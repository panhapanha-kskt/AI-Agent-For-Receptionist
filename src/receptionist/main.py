"""App factory. Run with: uvicorn receptionist.main:app --reload"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from receptionist.admin.router import router as admin_router
from receptionist.agent.client import ReceptionistAgent
from receptionist.chat.router import router as chat_router
from receptionist.config import Settings, get_settings
from receptionist.database import init_db
from receptionist.security.headers import SecurityHeadersMiddleware
from receptionist.security.rate_limit import limiter
from receptionist.skills.loader import SkillRegistry
from receptionist.voice.stt import WhisperSTT
from receptionist.voice.tts import make_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("receptionist")


def create_app(settings: Settings | None = None, agent_client=None) -> FastAPI:
    settings = settings or get_settings()

    async def _warm_up_speech(app: FastAPI) -> None:
        # Load Whisper in the background so startup isn't blocked and the first voice
        # message doesn't pay the load (or first-time download) cost.
        try:
            await asyncio.to_thread(app.state.stt.warm_up)
            if settings.stt_enabled:
                logger.info("speech-to-text models loaded")
        except Exception:
            logger.exception("could not pre-load speech-to-text models")

    async def _warm_up_router(app: FastAPI) -> None:
        # Embed the skills once at startup so the first caller doesn't wait for it.
        router = app.state.agent.router
        if router is None:
            return
        try:
            await router.ensure_index()
        except Exception:
            logger.exception("could not build the skill router index")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        tasks = [
            asyncio.create_task(_warm_up_speech(app)),
            asyncio.create_task(_warm_up_router(app)),
        ]
        yield
        for task in tasks:
            task.cancel()

    app = FastAPI(
        title="AI Receptionist",
        lifespan=lifespan,
        # Interactive API docs only outside production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    skills = SkillRegistry(settings.skills_dir)
    skills.reload()
    for err in skills.errors:
        logger.warning("Skill not loaded: %s", err)
    logger.info("Loaded skills: %s", ", ".join(skills.names()) or "(none)")

    app.state.skills = skills
    app.state.agent = ReceptionistAgent(settings, skills, client=agent_client)
    app.state.stt = WhisperSTT(settings)
    app.state.tts = make_tts(settings)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Admin-Key"],
    )

    app.include_router(chat_router)
    app.include_router(admin_router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "skills": len(skills.names())}

    web = settings.web_dir
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(web / "admin.html")

    return app


app = create_app()
