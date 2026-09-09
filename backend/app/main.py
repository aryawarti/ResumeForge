"""FastAPI application entry point.

The worker starts inside this process. Render's free tier has no background
worker service, so the alternative would be a paid plan for what one asyncio
task handles fine at this scale.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import auth_router, gaps_router, generation_router, resume_router
from .config import get_settings
from .db.session import create_all
from .storage import get_storage
from .worker import Worker

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if settings.environment == "development":
        # Alembic owns the schema in production; this keeps local setup to a
        # single command.
        await create_all()

    try:
        get_storage().ensure_bucket()
    except Exception:
        # Storage being down should not stop the API from serving; PDF
        # delivery degrades, tailoring still works.
        logger.warning("object storage unavailable at startup", exc_info=True)

    worker = Worker(settings)
    await worker.start()
    app.state.worker = worker
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(
    title="ResumeForge",
    description="AI resume tailoring with structural formatting preservation",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Last-Event-ID"],
)

app.include_router(auth_router)
app.include_router(resume_router)
app.include_router(generation_router)
app.include_router(gaps_router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict[str, object]:
    from .latex.compiler import get_compiler

    settings = get_settings()
    try:
        backend = get_compiler(settings.compile_backend, binary=settings.tectonic_binary)
        compiler = {"available": backend.available(), "backend": backend.name}
    except Exception as exc:
        compiler = {"available": False, "error": str(exc)}

    return {
        "status": "ok",
        "environment": settings.environment,
        "model": settings.model,
        "compiler": compiler,
        "llm_configured": bool(settings.anthropic_api_key),
    }
