"""HTTP routes.

Three things here are worth reading closely.

Resume upload validates before it stores. The file is parsed and compiled, and
the response reports what was found -- "4 sections, 4 entries, 10 bullets" --
so a mis-parse is visible immediately rather than discovered later in a
tailored document. A resume that does not compile is still stored, flagged,
because the user may want to fix it rather than lose it.

Deletion is soft and independent. Deleting a base resume never touches the
generations made from it, and vice versa. Each generation holds its own .tex
snapshot precisely so that guarantee is structural rather than a promise.

The event stream replays. Progress rows are durable, so a client passing
Last-Event-ID gets what it missed rather than a blank bar -- which matters on
an instance that can be restarted mid-generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from selectolax.parser import HTMLParser
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agent.ops import EditPlan
from ..auth import (
    create_access_token,
    create_sse_ticket,
    current_user,
    hash_password,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from ..config import Settings, get_settings
from ..db.models import (
    BaseResume,
    Generation,
    GenerationEvent,
    GenerationGap,
    JobDescription,
    User,
)
from ..db.session import get_session, live
from ..latex.compiler import get_compiler
from ..latex.parser import ParseError, parse_resume
from ..storage import StorageError, get_storage
from ..worker import enqueue, record_event
from .schemas import (
    GapRoadmap,
    GapRoadmapItem,
    GenerateRequest,
    GenerationDetail,
    GenerationOut,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResumeDetail,
    ResumeOut,
    ResumeUploadRequest,
    TokenPair,
    UserOut,
)

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
resume_router = APIRouter(prefix="/api/resumes", tags=["resumes"])
generation_router = APIRouter(prefix="/api/generations", tags=["generations"])
gaps_router = APIRouter(prefix="/api/gaps", tags=["gaps"])


# --- auth -----------------------------------------------------------------


@auth_router.post("/register", response_model=TokenPair, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    existing = await session.execute(
        select(User).where(User.email == payload.email.lower())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="that email is already registered")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    session.add(user)
    await session.commit()

    refresh = await issue_refresh_token(
        session, user.id, request.headers.get("user-agent", "")
    )
    return TokenPair(
        access_token=create_access_token(user.id, settings), refresh_token=refresh
    )


@auth_router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    result = await session.execute(
        select(User).where(User.email == payload.email.lower())
    )
    user = result.scalar_one_or_none()
    # Same message either way, so the endpoint does not confirm which emails
    # have accounts.
    if user is None or user.is_deleted or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="incorrect email or password")

    refresh = await issue_refresh_token(
        session, user.id, request.headers.get("user-agent", "")
    )
    return TokenPair(
        access_token=create_access_token(user.id, settings), refresh_token=refresh
    )


@auth_router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    user, replacement = await rotate_refresh_token(
        session, payload.refresh_token, request.headers.get("user-agent", "")
    )
    return TokenPair(
        access_token=create_access_token(user.id, settings),
        refresh_token=replacement,
    )


@auth_router.post("/logout", status_code=204)
async def logout(
    payload: RefreshRequest, session: AsyncSession = Depends(get_session)
) -> Response:
    await revoke_refresh_token(session, payload.refresh_token)
    return Response(status_code=204)


@auth_router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user


# --- resumes --------------------------------------------------------------


@resume_router.post("", response_model=ResumeDetail, status_code=201)
async def upload_resume(
    payload: ResumeUploadRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ResumeDetail:
    try:
        tree = parse_resume(payload.tex_source, payload.profile)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=f"could not parse: {exc}") from exc

    # Validate on upload rather than at generation time: a template we cannot
    # compile should be caught now, not sixty seconds into a tailoring run.
    loop = asyncio.get_running_loop()
    compiler = get_compiler(settings.compile_backend, binary=settings.tectonic_binary)
    compile_result = await loop.run_in_executor(
        None,
        lambda: compiler.compile(
            payload.tex_source, timeout=settings.compile_timeout_seconds
        ),
    )

    resume = BaseResume(
        user_id=user.id,
        name=payload.name,
        tex_source=payload.tex_source,
        template_profile=tree.profile,
        parse_tree=tree.to_dict(),
        page_count=compile_result.page_count,
        compiles=compile_result.ok,
        parse_warnings=list(tree.warnings),
    )
    session.add(resume)
    await session.commit()

    warnings = list(tree.warnings)
    if not compile_result.ok:
        warnings.append(
            "this resume did not compile: " + "; ".join(compile_result.errors[:3])
        )

    return ResumeDetail(
        id=resume.id,
        name=resume.name,
        template_profile=resume.template_profile,
        page_count=resume.page_count,
        compiles=resume.compiles,
        created_at=resume.created_at,
        tex_source=resume.tex_source,
        stats=tree.stats(),
        warnings=warnings,
        sections=[
            {
                "id": s.id,
                "title": s.title.strip(),
                "entries": len(s.entries),
                "bullets": sum(1 for _ in s.all_bullets()),
                "is_skills": s.is_skills,
            }
            for s in tree.sections
        ],
    )


@resume_router.get("", response_model=list[ResumeOut])
async def list_resumes(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BaseResume]:
    result = await session.execute(
        live(BaseResume)
        .where(BaseResume.user_id == user.id)
        .order_by(BaseResume.created_at.desc())
    )
    return list(result.scalars())


@resume_router.get("/{resume_id}", response_model=ResumeDetail)
async def get_resume(
    resume_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeDetail:
    resume = await _owned_resume(session, resume_id, user.id)
    tree_dict = resume.parse_tree or {}
    return ResumeDetail(
        id=resume.id,
        name=resume.name,
        template_profile=resume.template_profile,
        page_count=resume.page_count,
        compiles=resume.compiles,
        created_at=resume.created_at,
        tex_source=resume.tex_source,
        stats=tree_dict.get("stats", {}),
        warnings=resume.parse_warnings or [],
        sections=[
            {
                "id": s["id"],
                "title": s["title"].strip(),
                "entries": len(s.get("entries", [])),
                "bullets": sum(
                    len(e.get("bullets", [])) for e in s.get("entries", [])
                )
                + len(s.get("loose_bullets", [])),
                "is_skills": s.get("is_skills", False),
            }
            for s in tree_dict.get("sections", [])
        ],
    )


@resume_router.delete("/{resume_id}", status_code=204)
async def delete_resume(
    resume_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a base resume.

    Generations made from it are deliberately untouched: each holds its own
    .tex snapshot and remains downloadable.
    """
    resume = await _owned_resume(session, resume_id, user.id)
    resume.soft_delete()
    await session.commit()
    return Response(status_code=204)


# --- generations ----------------------------------------------------------


async def _fetch_job_text(url: str) -> str:
    """Fetch and flatten a posting from a URL."""
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": "ResumeForge/0.1"}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    tree = HTMLParser(response.text)
    for tag in tree.css("script, style, nav, header, footer, svg"):
        tag.decompose()
    body = tree.body
    text = body.text(separator="\n", strip=True) if body else ""
    return "\n".join(line for line in text.splitlines() if line.strip())


@generation_router.post("", response_model=GenerationOut, status_code=202)
async def create_generation(
    payload: GenerateRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Generation:
    resume = await _owned_resume(session, payload.base_resume_id, user.id)

    job_text = payload.job_text.strip()
    if not job_text and payload.job_url:
        try:
            job_text = await _fetch_job_text(payload.job_url)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"could not read that job URL ({exc}); paste the text instead",
            ) from exc
    if len(job_text) < 80:
        raise HTTPException(
            status_code=422,
            detail="job description is too short to tailor against",
        )

    description = JobDescription(
        user_id=user.id,
        company=payload.company,
        role=payload.role,
        source_url=payload.job_url,
        raw_text=job_text,
    )
    session.add(description)
    await session.flush()

    # Versions accumulate; a regeneration never overwrites its predecessor.
    existing = await session.execute(
        select(func.count())
        .select_from(Generation)
        .where(
            Generation.user_id == user.id,
            Generation.base_resume_id == resume.id,
            Generation.company == payload.company,
            Generation.role == payload.role,
        )
    )
    version_no = (existing.scalar_one() or 0) + 1

    generation = Generation(
        user_id=user.id,
        base_resume_id=resume.id,
        job_description_id=description.id,
        base_resume_name=resume.name,
        company=payload.company,
        role=payload.role,
        version_no=version_no,
        status="queued",
        tex_source=resume.tex_source,
    )
    session.add(generation)
    await session.commit()

    await record_event(session, generation.id, "queued")
    await enqueue(session, generation.id)
    return generation


@generation_router.get("", response_model=list[GenerationOut])
async def list_generations(
    company: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Generation]:
    statement = live(Generation).where(Generation.user_id == user.id)
    if company:
        statement = statement.where(Generation.company == company)
    result = await session.execute(statement.order_by(Generation.created_at.desc()))
    return list(result.scalars())


@generation_router.get("/{generation_id}", response_model=GenerationDetail)
async def get_generation(
    generation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> GenerationDetail:
    generation = await _owned_generation(session, generation_id, user.id)

    pdf_url = None
    if generation.pdf_key:
        try:
            pdf_url = get_storage().presigned_url(
                generation.pdf_key,
                filename=f"{generation.company or 'resume'}-v{generation.version_no}.pdf",
            )
        except StorageError:
            logger.warning("could not sign pdf url for %s", generation_id)

    return GenerationDetail(
        id=generation.id,
        status=generation.status,
        company=generation.company,
        role=generation.role,
        version_no=generation.version_no,
        base_resume_name=generation.base_resume_name,
        page_count=generation.page_count,
        coverage_score=generation.coverage_score,
        created_at=generation.created_at,
        tex_source=generation.tex_source,
        error=generation.error,
        pdf_url=pdf_url,
        coverage=generation.coverage or {},
        change_report=generation.change_report or {},
        gap_report=generation.gap_report or {},
    )


@generation_router.post("/{generation_id}/stream-ticket")
async def stream_ticket(
    generation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Mint a short-lived ticket for the EventSource connection.

    EventSource cannot send an Authorization header, so the stream is
    authorised by a ticket scoped to this one generation.
    """
    await _owned_generation(session, generation_id, user.id)
    return {"ticket": create_sse_ticket(user.id, generation_id, settings)}


@generation_router.get("/{generation_id}/events")
async def generation_events(
    generation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EventSourceResponse:
    from ..auth import sse_user

    user = await sse_user(request, generation_id, session, settings)
    await _owned_generation(session, generation_id, user.id)

    # Durable events mean a reconnect replays rather than restarting blind.
    last_id = int(request.headers.get("last-event-id") or 0)

    async def stream() -> AsyncIterator[dict[str, Any]]:
        cursor = last_id
        while True:
            if await request.is_disconnected():
                break
            async with get_sessionmaker_local()() as read_session:
                result = await read_session.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.generation_id == generation_id,
                        GenerationEvent.seq > cursor,
                    )
                    .order_by(GenerationEvent.seq)
                )
                events = list(result.scalars())

                for event in events:
                    cursor = event.seq
                    yield {
                        "id": str(event.seq),
                        "event": "progress",
                        "data": json.dumps(
                            {
                                "stage": event.stage,
                                "message": event.message,
                                "percent": event.percent,
                                "detail": event.detail,
                            }
                        ),
                    }

                generation = await read_session.get(Generation, generation_id)
                if generation and generation.status in {"completed", "failed"}:
                    yield {
                        "event": "done",
                        "data": json.dumps(
                            {"status": generation.status, "error": generation.error}
                        ),
                    }
                    break
            await asyncio.sleep(1.0)

    return EventSourceResponse(stream())


def get_sessionmaker_local():
    from ..db.session import get_sessionmaker

    return get_sessionmaker()


@generation_router.delete("/{generation_id}", status_code=204)
async def delete_generation(
    generation_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete one generated version. The base resume is untouched."""
    generation = await _owned_generation(session, generation_id, user.id)
    generation.soft_delete()
    await session.commit()
    return Response(status_code=204)


# --- gaps -----------------------------------------------------------------


@gaps_router.get("/roadmap", response_model=GapRoadmap)
async def gap_roadmap(
    limit: int = 25,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> GapRoadmap:
    """Aggregate gaps across every application.

    This is the payoff for normalising gaps into their own table: after
    fifteen applications it can tell you that eleven wanted Kafka and you have
    none -- a learning roadmap derived from the market you are actually
    applying into.
    """
    total = await session.execute(
        select(func.count())
        .select_from(Generation)
        .where(Generation.user_id == user.id, Generation.deleted_at.is_(None))
    )

    result = await session.execute(
        select(
            GenerationGap.normalized_skill,
            func.min(GenerationGap.skill).label("display"),
            func.count().label("occurrences"),
            func.sum(
                case((GenerationGap.kind == "required", 1), else_=0)
            ).label("required_count"),
        )
        .where(GenerationGap.user_id == user.id)
        .group_by(GenerationGap.normalized_skill)
        .order_by(func.count().desc())
        .limit(limit)
    )

    items: list[GapRoadmapItem] = []
    for row in result:
        companies = await session.execute(
            select(func.distinct(GenerationGap.company)).where(
                GenerationGap.user_id == user.id,
                GenerationGap.normalized_skill == row.normalized_skill,
            )
        )
        required = int(row.required_count or 0)
        items.append(
            GapRoadmapItem(
                skill=row.display,
                occurrences=int(row.occurrences),
                required_count=required,
                preferred_count=int(row.occurrences) - required,
                companies=[c for c in companies.scalars() if c],
            )
        )

    return GapRoadmap(total_applications=int(total.scalar_one() or 0), items=items)


# --- helpers --------------------------------------------------------------


async def _owned_resume(
    session: AsyncSession, resume_id: str, user_id: str
) -> BaseResume:
    result = await session.execute(
        live(BaseResume).where(
            BaseResume.id == resume_id, BaseResume.user_id == user_id
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=404, detail="resume not found")
    return resume


async def _owned_generation(
    session: AsyncSession, generation_id: str, user_id: str
) -> Generation:
    result = await session.execute(
        live(Generation).where(
            Generation.id == generation_id, Generation.user_id == user_id
        )
    )
    generation = result.scalar_one_or_none()
    if generation is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return generation
