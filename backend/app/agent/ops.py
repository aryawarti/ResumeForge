"""The edit operation vocabulary.

This module is the first and strongest anti-hallucination control in the
system, and it works by omission: there is no operation that creates a bullet,
adds a skill, or introduces free text anywhere in the document. The agent can
only reorder what exists, rewrite the interior of an existing bullet, or drop
something. "Add Kubernetes because the posting asked for it" is not a rejected
operation -- it is an operation that cannot be expressed.

These models are handed to the Claude API as a structured output schema, so
the constraint is enforced during generation rather than checked afterwards.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ReorderSections",
    "ReorderEntries",
    "ReorderBullets",
    "RewriteBullet",
    "DropBullet",
    "EditOp",
    "EditPlan",
]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        description=(
            "Why this edit helps for this specific job posting. Cite the "
            "requirement it addresses. This is shown verbatim to the user."
        ),
        min_length=1,
    )


class ReorderSections(_Op):
    """Promote whole sections, e.g. move Projects above Education."""

    op: Literal["reorder_sections"] = "reorder_sections"
    order: list[str] = Field(
        description=(
            "Every section id, in the desired new order. Must be a permutation "
            "of the existing section ids -- no additions, no omissions."
        )
    )


class ReorderEntries(_Op):
    """Reorder roles or projects within one section."""

    op: Literal["reorder_entries"] = "reorder_entries"
    section_id: str
    order: list[str] = Field(
        description="Every entry id in this section, in the desired new order."
    )


class ReorderBullets(_Op):
    """Reorder the bullets under one entry.

    The highest-value, lowest-risk operation available: it changes what a
    reviewer reads first without altering a single claim.
    """

    op: Literal["reorder_bullets"] = "reorder_bullets"
    entry_id: str
    order: list[str] = Field(
        description="Every bullet id under this entry, in the desired new order."
    )


class RewriteBullet(_Op):
    """Rephrase one bullet, without changing what it claims.

    Legitimate use is vocabulary alignment: the posting says "microservices"
    and the bullet says "distributed backend services". Every rewrite is put
    through the technology, numeric and scope guards before it is applied.
    """

    op: Literal["rewrite_bullet"] = "rewrite_bullet"
    bullet_id: str
    new_text: str = Field(
        description=(
            "Replacement text as plain prose. Do not include LaTeX commands or "
            "escape sequences; escaping is handled by the renderer. You may "
            "only rephrase what the original already claims -- you may not "
            "introduce a technology, a metric, or a scope of responsibility "
            "that is not already present in the original bullet."
        ),
        min_length=1,
    )


class DropBullet(_Op):
    """Remove a bullet. Reserved for page-overflow recovery."""

    op: Literal["drop_bullet"] = "drop_bullet"
    bullet_id: str


EditOp = Annotated[
    Union[
        ReorderSections,
        ReorderEntries,
        ReorderBullets,
        RewriteBullet,
        DropBullet,
    ],
    Field(discriminator="op"),
]


class EditPlan(BaseModel):
    """A complete tailoring plan for one resume against one job posting."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(
        description=(
            "Two or three sentences on the overall approach: what this posting "
            "rewards, and how the resume is being re-pointed at it."
        )
    )
    edits: list[EditOp] = Field(
        description=(
            "Operations in application order. Prefer reordering over rewriting: "
            "reordering carries no factual risk. Rewrite only where the posting "
            "uses different vocabulary for something the resume already claims."
        )
    )
