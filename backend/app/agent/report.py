"""Delivery artefacts: the change report and the gaps report.

Two audiences, two documents.

The change report answers "what did you do to my resume, and why". Every entry
carries the agent's stated reason, and -- crucially -- the *rejected* edits
appear alongside the applied ones. A tool that silently discards a third of
its own plan is not trustworthy; showing the rejection and its cause is what
makes the guard visible rather than merely present.

The gaps report answers "what did this job want that I do not have". It is
deliberately not softened. An honest gap list is the most useful thing the
system produces, because aggregated across applications it becomes a learning
roadmap derived from the market rather than from a blog post.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from .apply import ApplyResult
from .jobspec import CoverageMatrix, JobSpec

__all__ = ["BulletDiff", "ChangeReport", "build_change_report", "build_gap_report"]


@dataclass(slots=True)
class BulletDiff:
    """One bullet's before/after, with word-level segments for the UI."""

    bullet_id: str
    op: str
    reason: str
    before: str
    after: str
    segments: list[dict[str, str]] = field(default_factory=list)
    applied: bool = True
    rejection: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bullet_id": self.bullet_id,
            "op": self.op,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "segments": self.segments,
            "applied": self.applied,
            "rejection": self.rejection,
        }


def word_diff(before: str, after: str) -> list[dict[str, str]]:
    """Word-level diff segments, for side-by-side rendering in the UI.

    Word granularity rather than character granularity: a rewrite that swaps
    "distributed backend services" for "microservices" should read as one
    substitution, not a scatter of single-letter changes.
    """
    before_words = before.split()
    after_words = after.split()
    matcher = difflib.SequenceMatcher(None, before_words, after_words, autojunk=False)

    segments: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append({"type": "equal", "text": " ".join(before_words[i1:i2])})
        elif tag == "delete":
            segments.append({"type": "removed", "text": " ".join(before_words[i1:i2])})
        elif tag == "insert":
            segments.append({"type": "added", "text": " ".join(after_words[j1:j2])})
        else:
            segments.append({"type": "removed", "text": " ".join(before_words[i1:i2])})
            segments.append({"type": "added", "text": " ".join(after_words[j1:j2])})
    return segments


@dataclass(slots=True)
class ChangeReport:
    strategy: str
    diffs: list[BulletDiff] = field(default_factory=list)
    reorderings: list[dict[str, Any]] = field(default_factory=list)
    rejected_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "diffs": [d.to_dict() for d in self.diffs],
            "reorderings": self.reorderings,
            "rejected_count": self.rejected_count,
        }


def build_change_report(result: ApplyResult, strategy: str = "") -> ChangeReport:
    """Turn an apply result into the bullet-by-bullet report shown to the user."""
    report = ChangeReport(strategy=strategy, rejected_count=len(result.rejected))

    for edit in result.applied:
        if edit.op == "rewrite_bullet":
            report.diffs.append(
                BulletDiff(
                    bullet_id=edit.target_id,
                    op=edit.op,
                    reason=edit.reason,
                    before=edit.before,
                    after=edit.after,
                    segments=word_diff(edit.before, edit.after),
                    applied=True,
                )
            )
        elif edit.op == "drop_bullet":
            report.diffs.append(
                BulletDiff(
                    bullet_id=edit.target_id,
                    op=edit.op,
                    reason=edit.reason,
                    before=edit.before,
                    after="",
                    segments=[{"type": "removed", "text": edit.before}],
                    applied=True,
                )
            )
        else:
            report.reorderings.append(
                {
                    "op": edit.op,
                    "target_id": edit.target_id,
                    "reason": edit.reason,
                    "detail": edit.detail,
                    "before": edit.before,
                    "after": edit.after,
                }
            )

    # Rejections are part of the report, not a hidden implementation detail.
    for rejected in result.rejected:
        report.diffs.append(
            BulletDiff(
                bullet_id=rejected.target_id,
                op=rejected.op,
                reason=rejected.reason,
                before="",
                after=rejected.proposed,
                segments=[],
                applied=False,
                rejection=rejected.rejection,
            )
        )

    return report


def build_gap_report(
    job_spec: JobSpec, coverage: CoverageMatrix
) -> dict[str, Any]:
    """List what the posting wanted that the resume genuinely lacks."""
    required_texts = {
        r.text for r in job_spec.requirements if r.kind == "required"
    }

    missing_required: list[str] = []
    missing_preferred: list[str] = []
    for item in coverage.absent:
        if item.requirement in required_texts:
            missing_required.append(item.requirement)
        else:
            missing_preferred.append(item.requirement)

    total = len(coverage.items) or 1
    return {
        "company": job_spec.company,
        "role": job_spec.role,
        "seniority": job_spec.seniority,
        "domain": job_spec.domain,
        "coverage_score": round(coverage.score(), 3),
        "missing_required": missing_required,
        "missing_preferred": missing_preferred,
        "counts": {
            "total_requirements": len(coverage.items),
            "prominent": len(coverage.by_status("covered_prominent")),
            "buried": len(coverage.by_status("covered_buried")),
            "rephrased": len(coverage.by_status("covered_rephrased")),
            "absent": len(coverage.absent),
        },
        "assessment": (
            f"{len(missing_required)} of {len(required_texts)} required items are "
            f"not evidenced in this resume."
            if required_texts
            else "No hard requirements were identified in this posting."
        ),
    }
