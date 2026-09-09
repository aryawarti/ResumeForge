"""Structured representation of a job posting and its coverage.

The coverage matrix is the analytical core of the product. Splitting each
requirement into four states matters because they call for three different
responses and only one of them can be safely automated:

* ``covered_prominent`` -- already working; leave it alone.
* ``covered_buried`` -- the claim exists but is read last. Reorder. No factual
  risk at all, which is why the planner reaches for this first.
* ``covered_rephrased`` -- the claim exists in different words. Rewrite, under
  the guards. This is the only state where wording changes are justified.
* ``absent`` -- genuinely missing. This is *not* automatable. It goes to the
  gaps report, and the honest answer is that the resume should not claim it.

Conflating the last two is exactly how competing tools end up hallucinating.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Requirement",
    "JobSpec",
    "CoverageItem",
    "CoverageMatrix",
    "GapReport",
    "COVERAGE_STATES",
]

COVERAGE_STATES = (
    "covered_prominent",
    "covered_buried",
    "covered_rephrased",
    "absent",
)


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="The requirement as the posting states it.")
    kind: Literal["required", "preferred"] = Field(
        description=(
            "Whether the posting treats this as a hard requirement or a "
            "nice-to-have. Read the framing: 'must have' and 'requires' are "
            "required; 'bonus', 'plus', 'ideally' are preferred."
        )
    )
    category: Literal[
        "technology", "experience", "domain", "education", "responsibility", "soft"
    ]
    keywords: list[str] = Field(
        description=(
            "The posting's own words for this requirement, verbatim. These "
            "drive vocabulary alignment, so copy the posting's phrasing "
            "rather than normalising it."
        )
    )


class JobSpec(BaseModel):
    """Everything extracted from one posting."""

    model_config = ConfigDict(extra="forbid")

    company: str = Field(description="Hiring company, or 'Unknown' if not stated.")
    role: str = Field(description="Job title as posted.")
    seniority: Literal[
        "intern", "junior", "mid", "senior", "staff", "principal", "manager", "unknown"
    ]
    domain: str = Field(
        description="Business domain, e.g. fintech, healthcare, developer tooling."
    )
    requirements: list[Requirement]
    vocabulary: list[str] = Field(
        description=(
            "Distinctive terms the posting uses repeatedly, beyond the "
            "requirements themselves. These are the words an ATS keyword "
            "filter is most likely keyed to."
        )
    )
    is_underspecified: bool = Field(
        description=(
            "True if the posting is too vague to tailor against -- fewer than "
            "roughly four concrete requirements, or nothing but generic "
            "culture language."
        )
    )
    underspecified_reason: str = Field(
        default="",
        description="If underspecified, what specifically is missing.",
    )


class CoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(description="The requirement text being assessed.")
    status: Literal[
        "covered_prominent", "covered_buried", "covered_rephrased", "absent"
    ]
    evidence_bullet_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of bullets that support this requirement. Required for every "
            "status except 'absent', which must have none."
        ),
    )
    resume_phrasing: str = Field(
        default="",
        description=(
            "For 'covered_rephrased', the wording the resume currently uses, "
            "so the vocabulary mismatch is visible in the report."
        ),
    )
    note: str = Field(description="One sentence explaining the assessment.")


class CoverageMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CoverageItem]

    def by_status(self, status: str) -> list[CoverageItem]:
        return [item for item in self.items if item.status == status]

    @property
    def absent(self) -> list[CoverageItem]:
        return self.by_status("absent")

    def score(self) -> float:
        """Fraction of requirements the resume covers in some form."""
        if not self.items:
            return 0.0
        covered = sum(1 for item in self.items if item.status != "absent")
        return covered / len(self.items)


class GapReport(BaseModel):
    """What the posting wanted that the resume genuinely does not have."""

    model_config = ConfigDict(extra="forbid")

    missing_required: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    assessment: str = Field(
        default="",
        description="Honest read on whether the gaps are disqualifying.",
    )
