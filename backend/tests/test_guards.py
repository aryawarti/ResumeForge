"""Validation guard behaviour.

These tests encode the product's central promise in both directions. The
rejection cases are the reason anyone would trust the output; the acceptance
cases are the reason the tool is worth running at all. A guard that only ever
rejected would be perfectly safe and perfectly useless, so both halves matter.
"""

from __future__ import annotations

import pytest

from app.agent.guards import (
    check_rewrite,
    extract_numbers,
    extract_scope_claims,
    extract_technologies,
)
from app.latex.escape import contains_markup, escape_tex

# --- rejections: the failure modes that get people caught -----------------

HALLUCINATIONS = [
    pytest.param(
        "Rewrote the order-ingestion service in Go, cutting p99 latency to 120ms",
        "Rewrote the order-ingestion service in Go on Kubernetes, cutting p99 to 120ms",
        "kubernetes",
        id="adds-a-technology",
    ),
    pytest.param(
        "Built an internal reporting tool in Python replacing a manual process",
        "Led a team of 5 to build an internal reporting tool in Python",
        "team of 5",
        id="adds-team-leadership",
    ),
    pytest.param(
        "Added Redis caching to the pricing API, dropping read volume by 60%",
        "Added Redis caching to the pricing API, dropping read volume by 85%",
        "85%",
        id="inflates-a-metric",
    ),
    pytest.param(
        "Implemented a React dashboard used by 1,200 daily operators",
        "Owned the React dashboard used by 1,200 daily operators",
        "owned",
        id="claims-ownership",
    ),
    pytest.param(
        "Contributed to the payments service redesign",
        "Architected the payments service redesign",
        "architected",
        id="claims-architecture",
    ),
    pytest.param(
        "Wrote integration tests for the billing module",
        "Wrote integration tests for the billing module using Playwright",
        "playwright",
        id="adds-a-tool",
    ),
    pytest.param(
        "Improved API response times",
        "Improved API response times by 45%",
        "45%",
        id="invents-a-figure",
    ),
]


@pytest.mark.parametrize("original,rewritten,expected", HALLUCINATIONS)
def test_hallucinations_are_rejected(
    original: str, rewritten: str, expected: str
) -> None:
    result = check_rewrite(original, rewritten)
    assert not result.ok, f"guard allowed: {rewritten}"
    assert expected in result.summary.lower()


# --- acceptances: the edits the product exists to make --------------------

LEGITIMATE = [
    pytest.param(
        "Migrated 40+ distributed backend services from a shared Postgres instance "
        "to per-service schemas",
        "Migrated 40+ microservices from a shared Postgres instance to per-service "
        "schemas",
        id="spec-example-vocabulary-alignment",
    ),
    pytest.param(
        "Designed a lock-free job queue backed by Postgres advisory locks",
        "Designed a lock-free job queue backed by PostgreSQL advisory locks",
        id="spelling-variant",
    ),
    pytest.param(
        "Rewrote the ingestion service in Go, cutting p99 latency to 120ms",
        "Rewrote the ingestion service in Golang, reducing p99 latency to 120ms",
        id="language-alias",
    ),
    pytest.param(
        "Built continuous integration tooling that cut release time in half",
        "Built CI tooling that cut release time in half",
        id="abbreviation",
    ),
    pytest.param(
        "Applied machine learning to rank search results",
        "Applied ML to rank search results",
        id="ml-abbreviation",
    ),
]


@pytest.mark.parametrize("original,rewritten", LEGITIMATE)
def test_legitimate_rephrasing_is_allowed(original: str, rewritten: str) -> None:
    result = check_rewrite(original, rewritten)
    assert result.ok, f"guard wrongly rejected: {result.summary}"


# --- extraction primitives ------------------------------------------------


def test_concept_resolution_collapses_synonyms() -> None:
    a = extract_technologies("built distributed backend services")
    b = extract_technologies("built microservices")
    assert "microservices" in a and "microservices" in b


def test_composite_token_is_split() -> None:
    """CI/CD asserts two concepts, not one unknown technology."""
    assert extract_technologies("built a CI/CD pipeline") >= {"cicd"}
    assert "ci" in extract_technologies("built continuous integration tooling")


def test_unknown_technologies_are_still_detected() -> None:
    """A product absent from every list must still be tracked by shape."""
    found = extract_technologies("Deployed with FlibbertyGibbet and ZORPQL")
    assert "flibbertygibbet" in found
    assert "zorpql" in found


def test_sentence_initial_capitals_are_not_technologies() -> None:
    assert extract_technologies("Built a reporting tool") == set()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("reduced latency by 40%", {"40%"}),
        ("from 850ms to 120ms", {"850ms", "120ms"}),
        ("saved $48,000 annually", {"48000"}),
        ("processing 2.4B rows", {"2.4b"}),
        ("3x throughput", {"3x"}),
        ("1,200 daily operators", {"1200"}),
    ],
)
def test_number_extraction(text: str, expected: set[str]) -> None:
    assert extract_numbers(text) >= expected


def test_number_formatting_variants_compare_equal() -> None:
    """Reformatting a figure is not inventing one."""
    assert check_rewrite("served 1,200 users", "served 1200 users").ok


def test_scope_claims_detected() -> None:
    assert "led" in extract_scope_claims("Led the migration")
    assert "team of 5" in extract_scope_claims("Managed a team of 5 engineers")
    assert extract_scope_claims("Built a dashboard") == set()


def test_dropping_a_number_is_allowed_but_recorded() -> None:
    result = check_rewrite(
        "Cut latency by 40% across the fleet", "Cut latency across the fleet"
    )
    assert result.ok
    assert "40%" in result.dropped_numbers


def test_excessive_length_growth_is_rejected() -> None:
    result = check_rewrite(
        "Built a dashboard",
        "Built a comprehensive operational dashboard for the whole organisation "
        "with many additional descriptive words appended to it",
    )
    assert not result.ok
    assert "length" in result.summary


def test_empty_rewrite_is_rejected() -> None:
    assert not check_rewrite("Built a dashboard", "   ").ok


# --- escaping -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("reduced cost by 40%", r"reduced cost by 40\%"),
        ("R&D spend", r"R\&D spend"),
        ("table_names", r"table\_names"),
        ("saved $48,000", r"saved \$48,000"),
        ("100# of load", r"100\# of load"),
    ],
)
def test_escaping_makes_prose_compile_safe(raw: str, expected: str) -> None:
    assert escape_tex(raw) == expected


def test_markup_detection_gates_rewrites() -> None:
    assert contains_markup(r"\textbf{Tessellate} $|$ \emph{Python}")
    assert not contains_markup("Built an internal reporting tool in Python")


def test_length_guard_is_asymmetric() -> None:
    """Growth risks page overflow; shrinkage does not, so limits differ."""
    grew = check_rewrite(
        "Built a dashboard for operators",
        "Built a comprehensive operational dashboard for the operations team",
    )
    assert not grew.ok and "grows" in grew.summary

    shrank = check_rewrite(
        "Built continuous integration tooling that cut release time in half",
        "Built CI tooling that cut release time in half",
    )
    assert shrank.ok

    gutted = check_rewrite(
        "Built continuous integration tooling that cut release time in half "
        "across every service in the platform",
        "Did CI work",
    )
    assert not gutted.ok and "shrinks" in gutted.summary
