"""FastAPI application entry point.

The worker starts inside this process. Render's free tier has no background
worker service, so the alternative would be a paid plan for what one asyncio
task handles fine at this scale.
"""

from __future__ import annotations

import asyncio
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


async def _prepare_storage() -> None:
    """Create the PDF bucket if it is reachable, without blocking startup."""
    if not get_settings().storage_configured:
        logger.info("object storage not configured; PDF download is off")
        return
    try:
        await asyncio.to_thread(get_storage().ensure_bucket)
    except Exception as exc:
        # Tailoring still works; it is PDF delivery that degrades. Log the
        # cause plainly rather than a traceback -- "MinIO is not running" is
        # the usual answer and does not need a stack.
        logger.warning(
            "object storage unavailable, PDF delivery will fail until it "
            "returns (%s: %s)",
            type(exc).__name__,
            exc,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    local_sqlite = settings.database_url.startswith("sqlite")
    if settings.environment == "development" and local_sqlite:
        # A local SQLite file is created on the spot, so development is one
        # command. Postgres, local or hosted, is only ever changed by
        # `alembic upgrade head`: create_all on a Postgres database leaves
        # tables Alembic does not know it owns, and the first migration then
        # fails on them. tests/test_migrations.py keeps both schemas identical.
        await create_all()

    # Off the critical path deliberately. This is a blocking network call, and
    # when the endpoint is unreachable it costs a connect timeout per retry --
    # which is time the API spends refusing connections rather than serving
    # them. Locally that reads as "the backend is broken" when in fact only
    # MinIO is missing, so nothing here is allowed to delay startup.
    storage_task = asyncio.create_task(_prepare_storage())

    worker = Worker(settings)
    await worker.start()
    app.state.worker = worker
    try:
        yield
    finally:
        await worker.stop()
        # The bucket probe may still be waiting on a connect timeout.
        storage_task.cancel()


app = FastAPI(
    title="ResumeForge",
    description="AI resume tailoring with structural formatting preservation",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    # Blank in a dashboard means "not set", not "match the empty string".
    allow_origin_regex=get_settings().cors_origin_regex or None,
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
        "model": (
            settings.groq_model
            if settings.llm_provider == "groq"
            else settings.model
        ),
        "compiler": compiler,
        "llm_provider": settings.llm_provider,
        "llm_configured": bool(
            settings.groq_api_key
            if settings.llm_provider == "groq"
            else settings.anthropic_api_key
        ),
        "storage_configured": settings.storage_configured,
    }
