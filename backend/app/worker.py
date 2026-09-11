"""In-process job queue and worker.

Render's free tier has no background worker service, so generation runs inside
the web process. That constraint turned out to be worth leaning into rather
than merely tolerating:

* The queue is a Postgres table claimed with ``FOR UPDATE SKIP LOCKED``, so
  there is no Redis in the stack and no second thing that can be down.
* Progress events are rows, so a client that reconnects mid-run replays what
  it missed instead of watching a blank bar. With an in-memory bus you get
  neither replay nor durability.
* Stale claims are reclaimable. A free instance can be shut down mid-job at
  any time, so a job whose lock has aged past a threshold is returned to the
  queue on the next startup rather than being lost.

The pipeline itself is synchronous and CPU-bound in places (LaTeX compilation),
so it runs in a thread rather than blocking the event loop and starving the
HTTP handlers sharing the same process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .agent.graph import run_pipeline
from .agent.report import build_change_report, build_gap_report
from .config import Settings, get_settings
from .db.models import Generation, GenerationEvent, GenerationGap, Job, utcnow
from .db.session import get_sessionmaker
from .storage import get_storage

logger = logging.getLogger(__name__)

__all__ = ["enqueue", "record_event", "Worker", "STAGE_PROGRESS"]

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# Rough progress weights so the UI bar advances monotonically. The compile
# stages are wide because that is where a slow run actually spends its time.
STAGE_PROGRESS: dict[str, int] = {
    "queued": 0,
    "parse_resume": 5,
    "parse_resume.done": 10,
    "parse_job": 15,
    "parse_job.done": 25,
    "gap_fill": 28,
    "gap_fill.done": 32,
    "coverage": 35,
    "coverage.done": 50,
    "plan": 55,
    "plan.done": 70,
    "apply": 72,
    "apply.done": 78,
    "compile": 82,
    "compile.done": 90,
    "compile_fix": 84,
    "trim": 86,
    "deliver": 97,
    "completed": 100,
}

STAGE_LABELS: dict[str, str] = {
    "parse_resume": "Parsing your resume",
    "parse_resume.done": "Resume parsed",
    "parse_job": "Reading the job posting",
    "parse_job.done": "Job posting understood",
    "gap_fill": "Posting is vague, re-reading it",
    "coverage": "Matching your experience to the requirements",
    "coverage.done": "Coverage assessed",
    "plan": "Planning edits",
    "plan.done": "Edit plan ready",
    "apply": "Applying edits",
    "apply.done": "Edits applied and validated",
    "compile": "Compiling PDF",
    "compile.done": "PDF compiled",
    "compile_fix": "Fixing a compile error",
    "trim": "Trimming to fit one page",
    "deliver": "Finishing up",
    "completed": "Done",
}


async def enqueue(session: AsyncSession, generation_id: str) -> Job:
    job = Job(generation_id=generation_id, state="pending")
    session.add(job)
    await session.commit()
    return job


async def record_event(
    session: AsyncSession,
    generation_id: str,
    stage: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one progress row."""
    result = await session.execute(
        select(GenerationEvent.seq)
        .where(GenerationEvent.generation_id == generation_id)
        .order_by(GenerationEvent.seq.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none() or 0
    session.add(
        GenerationEvent(
            generation_id=generation_id,
            seq=last + 1,
            stage=stage,
            message=STAGE_LABELS.get(stage, stage.replace(".", " ").replace("_", " ")),
            percent=STAGE_PROGRESS.get(stage, 0),
            detail=detail or {},
        )
    )
    await session.commit()


class Worker:
    """Polls the jobs table and runs generations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # -- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        await self._reclaim_stale()
        self._task = asyncio.create_task(self._loop(), name="forge-worker")
        logger.info("worker %s started", WORKER_ID)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("worker %s stopped", WORKER_ID)

    # -- queue ---------------------------------------------------------

    async def _reclaim_stale(self) -> None:
        """Return jobs abandoned by a killed instance to the queue.

        Free-tier instances are shut down without warning, so this is a normal
        path rather than a disaster path.
        """
        cutoff = utcnow() - timedelta(seconds=self.settings.job_stale_after_seconds)
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(Job)
                .where(Job.state == "running", Job.locked_at < cutoff)
                .values(state="pending", locked_at=None, locked_by="")
                .returning(Job.id)
            )
            reclaimed = list(result.scalars())
            await session.commit()
        if reclaimed:
            logger.warning("reclaimed %d stale job(s)", len(reclaimed))

    async def _claim(self, session: AsyncSession) -> Job | None:
        """Claim one pending job, skipping rows another worker holds."""
        dialect = session.bind.dialect.name if session.bind else "postgresql"
        statement = select(Job).where(Job.state == "pending").order_by(Job.created_at)
        if dialect == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        statement = statement.limit(1)

        job = (await session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None
        job.state = "running"
        job.locked_at = utcnow()
        job.locked_by = WORKER_ID
        job.attempts += 1
        await session.commit()
        return job

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                async with get_sessionmaker()() as session:
                    job = await self._claim(session)
                if job is None:
                    await asyncio.sleep(self.settings.job_poll_interval_seconds)
                    continue
                await self._run_job(job.id, job.generation_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker loop error")
                await asyncio.sleep(self.settings.job_poll_interval_seconds)

    # -- execution -----------------------------------------------------

    async def _run_job(self, job_id: str, generation_id: str) -> None:
        async with get_sessionmaker()() as session:
            generation = await session.get(Generation, generation_id)
            if generation is None or generation.is_deleted:
                await self._finish(session, job_id, "cancelled", "generation removed")
                return

            resume_source = generation.tex_source
            job_text = ""
            if generation.job_description_id:
                from .db.models import JobDescription

                jd = await session.get(JobDescription, generation.job_description_id)
                job_text = jd.raw_text if jd else ""

            generation.status = "running"
            await session.commit()
            await record_event(session, generation_id, "parse_resume")

        loop = asyncio.get_running_loop()
        try:
            # The pipeline blocks on subprocess compiles; keep it off the loop.
            result = await loop.run_in_executor(
                None, lambda: run_pipeline(resume_source, job_text)
            )
        except Exception as exc:
            logger.exception("generation %s failed", generation_id)
            async with get_sessionmaker()() as session:
                generation = await session.get(Generation, generation_id)
                if generation:
                    generation.status = "failed"
                    generation.error = str(exc)[:2000]
                    await session.commit()
                await record_event(
                    session, generation_id, "failed", {"error": str(exc)[:500]}
                )
                await self._finish(session, job_id, "failed", str(exc))
            return

        await self._persist(job_id, generation_id, result)

    async def _persist(self, job_id: str, generation_id: str, result: Any) -> None:
        change = build_change_report(result.apply_result, result.plan.strategy)
        gaps = build_gap_report(result.job_spec, result.coverage)

        pdf_key = ""
        if result.pdf and self.settings.storage_configured:
            try:
                storage = get_storage()
                pdf_key = f"generations/{generation_id}.pdf"
                storage.put_pdf(pdf_key, result.pdf)
            except Exception:
                # A missing PDF must not lose the tailored .tex, which is the
                # part that cannot be regenerated for free.
                logger.exception("pdf upload failed for %s", generation_id)
                pdf_key = ""

        async with get_sessionmaker()() as session:
            for event in result.events:
                stage = event.get("stage", "")
                detail = {k: v for k, v in event.items() if k != "stage"}
                await record_event(session, generation_id, stage, detail)

            generation = await session.get(Generation, generation_id)
            if generation is None:
                await self._finish(session, job_id, "cancelled", "generation removed")
                return

            generation.status = "completed"
            generation.tex_source = result.source
            generation.pdf_key = pdf_key
            generation.page_count = result.page_count
            generation.company = result.job_spec.company or generation.company
            generation.role = result.job_spec.role or generation.role
            generation.coverage = result.coverage.model_dump()
            generation.change_report = change.to_dict()
            generation.gap_report = gaps
            generation.coverage_score = gaps["coverage_score"]

            # Normalise gaps into their own rows so the cross-application
            # roadmap is a GROUP BY rather than a scan over JSON blobs.
            for skill in gaps["missing_required"]:
                session.add(
                    GenerationGap(
                        generation_id=generation_id,
                        user_id=generation.user_id,
                        skill=skill,
                        normalized_skill=skill.strip().lower()[:200],
                        kind="required",
                        company=generation.company,
                    )
                )
            for skill in gaps["missing_preferred"]:
                session.add(
                    GenerationGap(
                        generation_id=generation_id,
                        user_id=generation.user_id,
                        skill=skill,
                        normalized_skill=skill.strip().lower()[:200],
                        kind="preferred",
                        company=generation.company,
                    )
                )

            await session.commit()
            await record_event(session, generation_id, "completed")
            await self._finish(session, job_id, "completed", "")

    async def _finish(
        self, session: AsyncSession, job_id: str, state: str, error: str
    ) -> None:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.state = state
        job.last_error = error[:2000]
        job.locked_at = None
        await session.commit()
