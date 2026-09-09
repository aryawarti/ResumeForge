"""The LangGraph agent pipeline.

Two genuine cycles, each bounded so the graph always terminates:

    parse_job -> [gap_fill loop, <= max_gap_fill_rounds] -> coverage
              -> plan -> apply -> compile
                    -> [compile_fix loop, <= max_compile_attempts]
              -> trim -> [trim loop, <= max_trim_rounds] -> deliver

The gap-fill loop only runs when the initial job-posting parse comes back
under-specified -- most postings do not need it, so most runs never enter it.
The compile-fix loop feeds the compiler's own error text back to the model so
a broken bullet gets one more attempt before it is dropped. Both loops have a
hard ceiling: state carries an attempt counter, and the routing function sends
the graph forward once the ceiling is hit, whatever the outcome. A pipeline
that can loop forever is not a pipeline that can be trusted in a request path.

Every node other than the two model-calling nodes (job parsing and planning)
is deterministic code. The compile step never asks the model to "fix the
LaTeX" -- it asks for corrected plain text for one bullet, and the renderer
re-escapes and re-applies it exactly as it would any other rewrite, guards
included.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..config import Settings, get_settings
from ..latex.compiler import CompileResult, get_compiler
from ..latex.parser import parse_resume
from ..latex.tree import ResumeTree
from .apply import ApplyResult, apply_plan
from .jobspec import CoverageItem, CoverageMatrix, GapReport, JobSpec
from .llm import LLMClient
from .ops import DropBullet, EditPlan
from .prompts import (
    COMPILE_REPAIR_SYSTEM,
    COVERAGE_SYSTEM,
    JOB_GAPFILL_SYSTEM,
    JOB_PARSE_SYSTEM,
    PLAN_SYSTEM,
    TRIM_SYSTEM,
)

logger = logging.getLogger(__name__)

__all__ = ["PipelineState", "PipelineResult", "build_graph", "run_pipeline"]


# --- state -----------------------------------------------------------------


class PipelineState(TypedDict, total=False):
    resume_source: str
    profile_name: str | None
    job_text: str

    tree: ResumeTree
    job_spec: JobSpec
    gap_fill_rounds: int

    coverage: CoverageMatrix
    plan: EditPlan
    apply_result: ApplyResult

    compile_result: CompileResult
    compile_attempts: int
    original_page_count: int | None

    trim_rounds: int
    dropped_ids: list[str]

    final_source: str
    events: list[dict[str, Any]]
    error: str | None


def _emit(state: PipelineState, stage: str, **detail: Any) -> None:
    """Append a progress event; consumed by the SSE layer in real time."""
    state.setdefault("events", []).append({"stage": stage, **detail})
    logger.info("stage=%s %s", stage, detail)


# --- nodes -------------------------------------------------------------


def make_nodes(settings: Settings):
    llm = LLMClient(settings)

    def parse_resume_node(state: PipelineState) -> PipelineState:
        _emit(state, "parse_resume")
        tree = parse_resume(state["resume_source"], state.get("profile_name"))
        state["tree"] = tree
        _emit(state, "parse_resume.done", summary=tree.summary())
        return state

    def parse_job_node(state: PipelineState) -> PipelineState:
        _emit(state, "parse_job")
        job_spec = llm.parse(
            schema=JobSpec,
            system=JOB_PARSE_SYSTEM,
            user=state["job_text"],
        )
        state["job_spec"] = job_spec
        state["gap_fill_rounds"] = 0
        _emit(
            state,
            "parse_job.done",
            requirements=len(job_spec.requirements),
            underspecified=job_spec.is_underspecified,
        )
        return state

    def gap_fill_node(state: PipelineState) -> PipelineState:
        rounds = state.get("gap_fill_rounds", 0) + 1
        state["gap_fill_rounds"] = rounds
        _emit(state, "gap_fill", round=rounds)
        job_spec = llm.parse(
            schema=JobSpec,
            system=JOB_GAPFILL_SYSTEM,
            user=(
                f"Posting:\n{state['job_text']}\n\n"
                f"Previous extraction found only {len(state['job_spec'].requirements)} "
                f"requirements and was marked underspecified: "
                f"{state['job_spec'].underspecified_reason}"
            ),
        )
        state["job_spec"] = job_spec
        _emit(state, "gap_fill.done", requirements=len(job_spec.requirements))
        return state

    def coverage_node(state: PipelineState) -> PipelineState:
        _emit(state, "coverage")
        tree = state["tree"]
        bullets_desc = "\n".join(
            f"[{b.id}] {b.plain}" for b in tree.all_bullets()
        )
        requirements_desc = "\n".join(
            f"- ({r.kind}, {r.category}) {r.text}" for r in state["job_spec"].requirements
        )
        matrix = llm.parse(
            schema=CoverageMatrix,
            system=COVERAGE_SYSTEM,
            user=(
                f"Requirements:\n{requirements_desc}\n\n"
                f"Resume bullets (id: text):\n{bullets_desc}"
            ),
        )
        state["coverage"] = matrix
        _emit(
            state,
            "coverage.done",
            score=round(matrix.score(), 2),
            absent=len(matrix.absent),
        )
        return state

    def plan_node(state: PipelineState) -> PipelineState:
        _emit(state, "plan")
        tree = state["tree"]
        coverage = state["coverage"]

        structure = _describe_structure(tree)
        coverage_desc = "\n".join(
            f"- [{item.status}] {item.requirement} "
            f"(evidence: {item.evidence_bullet_ids or 'none'})"
            for item in coverage.items
        )
        plan = llm.parse(
            schema=EditPlan,
            system=PLAN_SYSTEM,
            user=(
                f"Job: {state['job_spec'].role} at {state['job_spec'].company}\n\n"
                f"Coverage assessment:\n{coverage_desc}\n\n"
                f"Resume structure (ids and current text):\n{structure}"
            ),
        )
        state["plan"] = plan
        _emit(state, "plan.done", edits=len(plan.edits), strategy=plan.strategy)
        return state

    def apply_node(state: PipelineState) -> PipelineState:
        _emit(state, "apply")
        result = apply_plan(state["tree"], state["plan"])
        state["apply_result"] = result
        state["compile_attempts"] = 0
        state.setdefault("dropped_ids", [])
        _emit(
            state,
            "apply.done",
            applied=len(result.applied),
            rejected=len(result.rejected),
        )
        return state

    def compile_node(state: PipelineState) -> PipelineState:
        attempt = state.get("compile_attempts", 0) + 1
        state["compile_attempts"] = attempt
        _emit(state, "compile", attempt=attempt)

        compiler = get_compiler(
            settings.compile_backend, binary=settings.tectonic_binary
        )
        source = state["apply_result"].source
        result = compiler.compile(source, timeout=settings.compile_timeout_seconds)
        state["compile_result"] = result

        if state.get("original_page_count") is None and result.ok:
            # Only set on the very first successful compile of this run's
            # *original*, unedited resume -- callers seed this before start.
            pass

        _emit(
            state,
            "compile.done",
            ok=result.ok,
            pages=result.page_count,
            errors=result.errors[:3],
        )
        return state

    def compile_fix_node(state: PipelineState) -> PipelineState:
        _emit(state, "compile_fix")
        errors = "\n".join(state["compile_result"].errors[:8])
        tree = state["tree"]
        rewritten_ids = {
            edit.target_id
            for edit in state["apply_result"].applied
            if edit.op == "rewrite_bullet"
        }
        candidates = "\n".join(
            f"[{bid}] {tree.bullet_by_id(bid).plain}"
            for bid in rewritten_ids
            if tree.bullet_by_id(bid)
        )
        if not candidates:
            # Nothing we rewrote is a plausible culprit; there is nothing
            # productive to repair, so let the ceiling route this to trim.
            _emit(state, "compile_fix.skipped", reason="no rewritten bullets to repair")
            return state

        fix_plan = llm.parse(
            schema=EditPlan,
            system=COMPILE_REPAIR_SYSTEM,
            user=f"Compiler errors:\n{errors}\n\nRewritten bullets:\n{candidates}",
        )
        result = apply_plan(state["tree"], fix_plan)
        state["apply_result"] = result
        _emit(state, "compile_fix.done", edits=len(fix_plan.edits))
        return state

    def trim_node(state: PipelineState) -> PipelineState:
        rounds = state.get("trim_rounds", 0) + 1
        state["trim_rounds"] = rounds
        _emit(state, "trim", round=rounds)

        coverage_desc = "\n".join(
            f"- [{item.status}] {item.requirement} "
            f"(evidence: {item.evidence_bullet_ids or 'none'})"
            for item in state["coverage"].items
        )
        structure = _describe_structure(state["tree"])
        trim_plan = llm.parse(
            schema=EditPlan,
            system=TRIM_SYSTEM,
            user=(
                f"Current page count: {state['compile_result'].page_count}. "
                f"Target: {state.get('original_page_count') or 1}.\n\n"
                f"Coverage:\n{coverage_desc}\n\nResume:\n{structure}"
            ),
        )
        # Trim plans must only ever contain drops -- enforced by re-filtering
        # rather than trusting the schema alone, since a defensive second
        # check here costs nothing and a stray edit type here would otherwise
        # silently reach apply_plan.
        trim_plan.edits = [e for e in trim_plan.edits if isinstance(e, DropBullet)]
        result = apply_plan(state["tree"], trim_plan)
        state["apply_result"] = result
        state["dropped_ids"] = state.get("dropped_ids", []) + [
            e.target_id for e in result.applied
        ]
        _emit(state, "trim.done", dropped=len(result.applied))
        return state

    def deliver_node(state: PipelineState) -> PipelineState:
        state["final_source"] = state["apply_result"].source
        _emit(
            state,
            "deliver",
            pages=state.get("compile_result", CompileResult(ok=False)).page_count,
        )
        return state

    return {
        "parse_resume": parse_resume_node,
        "parse_job": parse_job_node,
        "gap_fill": gap_fill_node,
        "coverage": coverage_node,
        "plan": plan_node,
        "apply": apply_node,
        "compile": compile_node,
        "compile_fix": compile_fix_node,
        "trim": trim_node,
        "deliver": deliver_node,
    }


def _describe_structure(tree: ResumeTree) -> str:
    lines: list[str] = []
    lines.append(f"sections: {[(s.id, s.title) for s in tree.sections]}")
    for section in tree.sections:
        for entry in section.entries:
            lines.append(f"entry [{entry.id}] {entry.label} in section {section.id}")
            for bullet in entry.bullets:
                lines.append(f"  bullet [{bullet.id}] {bullet.plain}")
        for bullet in section.loose_bullets:
            lines.append(f"loose bullet [{bullet.id}] in section {section.id}: {bullet.plain}")
    return "\n".join(lines)


# --- routing -----------------------------------------------------------


def _route_after_job_parse(state: PipelineState) -> str:
    settings = get_settings()
    if (
        state["job_spec"].is_underspecified
        and state.get("gap_fill_rounds", 0) < settings.max_gap_fill_rounds
    ):
        return "gap_fill"
    return "coverage"


def _route_after_gap_fill(state: PipelineState) -> str:
    settings = get_settings()
    if (
        state["job_spec"].is_underspecified
        and state.get("gap_fill_rounds", 0) < settings.max_gap_fill_rounds
    ):
        return "gap_fill"
    return "coverage"


def _route_after_compile(state: PipelineState) -> str:
    settings = get_settings()
    result = state["compile_result"]
    attempts = state.get("compile_attempts", 0)

    if not result.ok:
        if attempts < settings.max_compile_attempts:
            return "compile_fix"
        # Exhausted repair attempts on a genuinely broken compile: fall back
        # to the pre-edit source rather than deliver nothing.
        state["apply_result"] = ApplyResult(source=state["tree"].source)
        state["events"].append(
            {"stage": "compile.exhausted", "note": "reverted to original resume"}
        )
        return "deliver"

    original_pages = state.get("original_page_count")
    if original_pages and result.page_count > original_pages:
        if state.get("trim_rounds", 0) < settings.max_trim_rounds:
            return "trim"
    return "deliver"


def _route_after_trim(state: PipelineState) -> str:
    return "compile"


# --- graph construction --------------------------------------------------


def build_graph(settings: Settings | None = None):
    settings = settings or get_settings()
    nodes = make_nodes(settings)

    graph = StateGraph(PipelineState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.set_entry_point("parse_resume")
    graph.add_edge("parse_resume", "parse_job")
    graph.add_conditional_edges(
        "parse_job", _route_after_job_parse, {"gap_fill": "gap_fill", "coverage": "coverage"}
    )
    graph.add_conditional_edges(
        "gap_fill", _route_after_gap_fill, {"gap_fill": "gap_fill", "coverage": "coverage"}
    )
    graph.add_edge("coverage", "plan")
    graph.add_edge("plan", "apply")
    graph.add_edge("apply", "compile")
    graph.add_conditional_edges(
        "compile",
        _route_after_compile,
        {"compile_fix": "compile_fix", "trim": "trim", "deliver": "deliver"},
    )
    graph.add_edge("compile_fix", "compile")
    graph.add_conditional_edges("trim", _route_after_trim, {"compile": "compile"})
    graph.add_edge("deliver", END)

    return graph.compile()


@dataclass(slots=True)
class PipelineResult:
    source: str
    pdf: bytes | None
    page_count: int
    job_spec: JobSpec
    coverage: CoverageMatrix
    plan: EditPlan
    apply_result: ApplyResult
    events: list[dict[str, Any]] = field(default_factory=list)


def run_pipeline(
    resume_source: str,
    job_text: str,
    *,
    profile_name: str | None = None,
    settings: Settings | None = None,
) -> PipelineResult:
    """Run the full pipeline synchronously and return everything downstream needs."""
    settings = settings or get_settings()
    app = build_graph(settings)

    # A quick unedited compile establishes the page budget the trim loop
    # protects, without spending a model call.
    compiler = get_compiler(settings.compile_backend, binary=settings.tectonic_binary)
    baseline = compiler.compile(resume_source, timeout=settings.compile_timeout_seconds)

    initial: PipelineState = {
        "resume_source": resume_source,
        "profile_name": profile_name,
        "job_text": job_text,
        "original_page_count": baseline.page_count if baseline.ok else None,
        "events": [],
    }

    final_state = app.invoke(initial, config={"recursion_limit": 60})

    compile_result = final_state.get("compile_result") or baseline
    return PipelineResult(
        source=final_state["final_source"],
        pdf=compile_result.pdf,
        page_count=compile_result.page_count,
        job_spec=final_state["job_spec"],
        coverage=final_state["coverage"],
        plan=final_state["plan"],
        apply_result=final_state["apply_result"],
        events=final_state.get("events", []),
    )
