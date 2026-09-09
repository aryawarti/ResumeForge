"""Deterministic validation of proposed rewrites.

No model runs in this module. Every check is code comparing a rewritten bullet
against its original, and every failure is a hard rejection of that single
operation -- never a silent repair, and never a reason to abandon the run.
Rejected edits are recorded with their reason and surfaced in the diff report,
so a user can see what the agent wanted to do and why it was not allowed to.

Three guards, addressing three distinct ways a rewrite can become a lie:

* the technology guard stops the resume gaining a skill
* the numeric guard stops it gaining a metric
* the scope guard stops it gaining authority

The scope guard is the one most tools omit, and it is the one that catches
"led a team of 5".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..latex.tree import strip_tex
from .concepts import match_phrases, resolve_concept
from .lexicon import (
    SCOPE_PATTERNS,
    SCOPE_TERMS,
    STOPWORD_CAPS,
    TECH_ALIASES,
    TECHNOLOGIES,
)

__all__ = [
    "GuardResult",
    "extract_technologies",
    "extract_numbers",
    "extract_scope_claims",
    "check_rewrite",
    "LENGTH_TOLERANCE",
]

# The length limits are deliberately asymmetric, because growth and shrinkage
# are different risks. Line-wrapping in a one-page resume is unforgiving, so a
# bullet that grows 40% can push the document onto a second page even though
# every other guard passed. Shrinkage cannot overflow anything -- abbreviating
# "continuous integration" to "CI" is a legitimate 30% cut -- so it is bounded
# much more loosely, and only to catch a rewrite that has discarded substance
# rather than condensed it. Numbers lost along the way are reported separately
# via GuardResult.dropped_numbers.
MAX_GROWTH = 0.25
MAX_SHRINK = 0.50

# Retained as the growth bound under its original name for callers and tests
# that refer to a single tolerance.
LENGTH_TOLERANCE = MAX_GROWTH

_SCOPE_RES = tuple(re.compile(p, re.IGNORECASE) for p in SCOPE_PATTERNS)

# Numbers, including 40%, 3x, $1.2M, 1,200, 850ms, p99.
_NUMBER_RE = re.compile(
    r"""
    (?P<value>
        \$?\d[\d,]*(?:\.\d+)?
    )
    (?P<unit>
        \s*%|x\b|k\b|m\b|bn?\b|
        \s*(?:ms|s|sec|secs|seconds|min|mins|minutes|hours?|hrs?|days?|weeks?|months?|years?)\b|
        \s*(?:gb|mb|kb|tb|qps|rps|req/s|rpm)\b
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]*")

# Canonical concepts are always tracked even when the concept name itself is
# absent from the raw technology lists.
from .concepts import CONCEPT_FORMS as _CONCEPT_FORMS  # noqa: E402

_KNOWN_CONCEPTS: frozenset[str] = frozenset(_CONCEPT_FORMS)


def _normalise_tech(token: str) -> str:
    lowered = token.lower().strip(".,;:!?()[]{}\"'")
    lowered = TECH_ALIASES.get(lowered, lowered)
    return resolve_concept(lowered)


def _split_composite(core: str) -> list[str]:
    """Split a slashed compound into its parts.

    ``CI/CD`` must resolve to the two concepts it actually asserts, otherwise
    it reads as a single unknown technology and a bullet that already said
    "CI" appears to have gained something. Genuine product names containing a
    slash are not split, because they resolve as a whole first.
    """
    if "/" not in core or _normalise_tech(core) in TECHNOLOGIES:
        return [core]
    parts = [p for p in core.split("/") if p]
    return parts if len(parts) > 1 else [core]


def extract_technologies(text: str) -> set[str]:
    """Extract the set of technology *concepts* a bullet claims.

    Multi-word forms are resolved first and blanked out, then single tokens
    are matched against the lexicon, then shape heuristics catch products no
    lexicon knows: internal capitals (PyTorch), acronyms (GRPC), dotted names
    (Node.js) and hyphenated lowercase packages (scikit-learn).

    Everything returned is a canonical concept, so two spellings of the same
    claim compare equal and only a genuinely new claim looks new.
    """
    plain = strip_tex(text)
    found, plain = match_phrases(plain)

    for match in _TOKEN_RE.finditer(plain):
        raw = match.group(0)
        core = raw.strip(".,;:!?()[]{}\"'")
        if len(core) < 2:
            continue

        for part in _split_composite(core):
            token = _normalise_tech(part)
            if not token or token in STOPWORD_CAPS:
                continue

            if token in TECHNOLOGIES or token in _KNOWN_CONCEPTS:
                found.add(token)
                continue

            # Acronym: three or more capitals, e.g. GRPC, SAML, ETL.
            if part.isupper() and len(part) >= 3:
                found.add(token)
                continue

            # Internal capital after a lowercase, e.g. PyTorch, TensorFlow.
            if re.search(r"[a-z][A-Z]", part):
                found.add(token)
                continue

            # Dotted product name, e.g. Node.js, ASP.NET.
            if "." in part and not part.endswith("."):
                found.add(token)
                continue

            # Hyphenated lowercase package, e.g. scikit-learn, socket-io.
            if "-" in part and part.islower() and len(part) > 4:
                found.add(token)
                continue

    return found


def _normalise_number(value: str, unit: str | None) -> str:
    digits = value.replace(",", "").replace("$", "").lstrip("0") or "0"
    if digits.endswith("."):
        digits = digits[:-1]
    suffix = (unit or "").strip().lower().replace(" ", "")
    return f"{digits}{suffix}"


def extract_numbers(text: str) -> set[str]:
    """Extract every quantitative claim, normalised for comparison."""
    plain = strip_tex(text)
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(plain):
        found.add(_normalise_number(match.group("value"), match.group("unit")))
    return found


def extract_scope_claims(text: str) -> set[str]:
    """Extract assertions of authority, ownership or team leadership."""
    plain = strip_tex(text).lower()
    found: set[str] = set()

    for match in re.finditer(r"[a-z][a-z-]*", plain):
        token = match.group(0)
        if token in SCOPE_TERMS:
            found.add(token)

    for pattern in _SCOPE_RES:
        for match in pattern.finditer(plain):
            found.add(match.group(0).strip())

    return found


@dataclass(slots=True)
class GuardResult:
    """Outcome of validating one proposed rewrite."""

    ok: bool
    violations: list[str] = field(default_factory=list)
    added_technologies: set[str] = field(default_factory=set)
    added_numbers: set[str] = field(default_factory=set)
    added_scope: set[str] = field(default_factory=set)
    dropped_numbers: set[str] = field(default_factory=set)
    length_ratio: float = 1.0

    @property
    def summary(self) -> str:
        return "; ".join(self.violations) if self.violations else "passed all guards"


def check_rewrite(original: str, rewritten: str) -> GuardResult:
    """Validate a proposed bullet rewrite against its original.

    Strict per-bullet semantics: the comparison is against *this bullet only*,
    not the rest of the resume. Surfacing a technology from the skills list
    into a job bullet would imply you used it on that job, which is a claim
    the original bullet never made.
    """
    result = GuardResult(ok=True)

    old_tech = extract_technologies(original)
    new_tech = extract_technologies(rewritten)
    added_tech = new_tech - old_tech
    if added_tech:
        result.ok = False
        result.added_technologies = added_tech
        result.violations.append(
            "introduces technology not in the original bullet: "
            + ", ".join(sorted(added_tech))
        )

    old_nums = extract_numbers(original)
    new_nums = extract_numbers(rewritten)
    added_nums = new_nums - old_nums
    if added_nums:
        result.ok = False
        result.added_numbers = added_nums
        result.violations.append(
            "introduces figures not in the original bullet: "
            + ", ".join(sorted(added_nums))
        )
    result.dropped_numbers = old_nums - new_nums

    old_scope = extract_scope_claims(original)
    new_scope = extract_scope_claims(rewritten)
    added_scope = new_scope - old_scope
    if added_scope:
        result.ok = False
        result.added_scope = added_scope
        result.violations.append(
            "escalates scope of responsibility: " + ", ".join(sorted(added_scope))
        )

    old_len = max(len(strip_tex(original)), 1)
    new_len = len(strip_tex(rewritten))
    result.length_ratio = new_len / old_len
    if result.length_ratio - 1.0 > MAX_GROWTH:
        result.ok = False
        result.violations.append(
            f"length grows by {(result.length_ratio - 1.0) * 100:+.0f}% "
            f"(limit +{MAX_GROWTH * 100:.0f}%), risking page overflow"
        )
    elif 1.0 - result.length_ratio > MAX_SHRINK:
        result.ok = False
        result.violations.append(
            f"length shrinks by {(1.0 - result.length_ratio) * 100:.0f}% "
            f"(limit {MAX_SHRINK * 100:.0f}%), suggesting the rewrite dropped "
            f"substance rather than condensing it"
        )

    if not strip_tex(rewritten):
        result.ok = False
        result.violations.append("rewrite is empty after stripping markup")

    return result
