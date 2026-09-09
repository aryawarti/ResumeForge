"""Concept resolution for the technology guard.

A naive guard compares the words in the original bullet with the words in the
rewrite and rejects anything new. That guard is unusable, because it rejects
the exact edit this product exists to make: the posting says "microservices",
the resume says "distributed backend services", and aligning the two is a free
win that costs nothing in honesty.

So the guard compares *concepts*. Every surface form resolves to a canonical
concept, and only a new **concept** counts as a hallucination. Restating a
concept you already claimed is rephrasing; asserting one you never claimed is
invention. That distinction is the whole design.

The clusters below are deliberately tight. A cluster may only contain terms a
careful reader would agree are the same claim worded differently. Merely
*related* terms stay separate -- "containers" does not imply Kubernetes, and
lumping them would open exactly the hole the guard exists to close.
"""

from __future__ import annotations

import re

__all__ = ["CONCEPT_FORMS", "FORM_TO_CONCEPT", "PHRASES", "resolve_concept"]


# canonical concept -> surface forms that mean the same claim
CONCEPT_FORMS: dict[str, tuple[str, ...]] = {
    # --- architecture --------------------------------------------------
    "microservices": (
        "microservices",
        "microservice",
        "micro-services",
        "micro services",
        "distributed services",
        "distributed backend services",
        "distributed service architecture",
        "service oriented architecture",
        "service-oriented architecture",
        "soa",
    ),
    "monolith": ("monolith", "monolithic", "monolithic application"),
    "eventdriven": (
        "event driven",
        "event-driven",
        "eventdriven",
        "event driven architecture",
        "publish subscribe",
        "pub/sub",
        "pubsub",
    ),
    "serverless": ("serverless", "function as a service", "faas"),
    "rest": ("rest", "restful", "rest api", "restful api", "rest apis"),
    # --- delivery ------------------------------------------------------
    "ci": ("ci", "continuous integration"),
    "cd": ("cd", "continuous deployment", "continuous delivery"),
    "cicd": ("ci/cd", "cicd", "ci cd"),
    "iac": (
        "iac",
        "infrastructure as code",
        "infrastructure-as-code",
    ),
    # --- data ----------------------------------------------------------
    "postgres": ("postgres", "postgresql", "psql", "postgres database"),
    "etl": ("etl", "extract transform load", "elt", "data pipeline", "data pipelines"),
    "datawarehouse": ("data warehouse", "data warehousing", "datawarehouse"),
    "streaming": ("stream processing", "streaming", "real time processing"),
    # --- ml ------------------------------------------------------------
    "machinelearning": ("machine learning", "ml", "statistical learning"),
    "deeplearning": ("deep learning", "neural networks", "neural network"),
    "nlp": ("nlp", "natural language processing"),
    "llm": ("llm", "llms", "large language model", "large language models"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "cv": ("computer vision", "image recognition"),
    # --- practice ------------------------------------------------------
    "testing": (
        "automated testing",
        "test automation",
        "unit testing",
        "unit tests",
        "integration testing",
    ),
    "observability": (
        "observability",
        "monitoring",
        "instrumentation",
        "telemetry",
    ),
    "oncall": ("on-call", "on call", "oncall", "incident response"),
    "codereview": ("code review", "code reviews", "peer review"),
    "agile": ("agile", "scrum", "sprint planning"),
    "accessibility": ("accessibility", "a11y", "wcag"),
    "performance": (
        "performance optimization",
        "performance tuning",
        "latency optimization",
    ),
    # --- aliases where one product has many spellings ------------------
    "kubernetes": ("kubernetes", "k8s", "kube"),
    "javascript": ("javascript", "js", "ecmascript"),
    "typescript": ("typescript", "ts"),
    "nodejs": ("node", "nodejs", "node.js"),
    "go": ("go", "golang"),
    "githubactions": ("github actions", "githubactions", "gha"),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud", "google cloud platform"),
    "azure": ("azure", "microsoft azure"),
}


def _build_form_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for concept, forms in CONCEPT_FORMS.items():
        for form in forms:
            index[form] = concept
    return index


FORM_TO_CONCEPT: dict[str, str] = _build_form_index()

# Multi-word forms, longest first so "distributed backend services" wins over
# "distributed services" and neither is shadowed by a single-word match.
PHRASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((form, concept) for form, concept in FORM_TO_CONCEPT.items() if " " in form),
        key=lambda pair: -len(pair[0]),
    )
)

_PHRASE_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"\b" + re.escape(form) + r"\b", re.IGNORECASE), concept)
    for form, concept in PHRASES
)


def match_phrases(text: str) -> tuple[set[str], str]:
    """Find multi-word concept forms and blank them out of the text.

    Returns the concepts found and the text with those phrases replaced by
    spaces, so the single-token pass that follows does not re-read their
    component words.
    """
    found: set[str] = set()
    remaining = text
    for pattern, concept in _PHRASE_RES:
        def _blank(match: re.Match[str]) -> str:
            return " " * len(match.group(0))

        new_text, count = pattern.subn(_blank, remaining)
        if count:
            found.add(concept)
            remaining = new_text
    return found, remaining


def resolve_concept(token: str) -> str:
    """Map a single surface token to its canonical concept.

    Unknown tokens resolve to themselves, so a technology absent from every
    list is still tracked -- it simply forms its own single-member concept.
    """
    lowered = token.lower().strip(".,;:!?()[]{}\"'")
    return FORM_TO_CONCEPT.get(lowered, lowered)
