"""The round-trip guarantee.

If these tests fail, nothing else in the system can be trusted: every claim
about formatting preservation rests on the property that parsing and
re-rendering an unedited document returns the original characters exactly.
"""

from __future__ import annotations

import pathlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent.apply import apply_plan
from app.agent.ops import EditPlan, ReorderBullets, ReorderEntries, ReorderSections
from app.latex.editbuffer import EditBuffer
from app.latex.parser import parse_resume
from app.latex.scanner import Span, build_mask, find_command, match_group

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FIXTURE_FILES = sorted(FIXTURES.glob("*.tex"))


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(params=[p.name for p in FIXTURE_FILES])
def fixture_source(request) -> str:
    return load(request.param)


# --- the core guarantee ---------------------------------------------------


def test_zero_edits_is_byte_identical(fixture_source: str) -> None:
    tree = parse_resume(fixture_source)
    result = apply_plan(tree, EditPlan(strategy="no-op", edits=[]))
    assert result.source == fixture_source


def test_edit_buffer_with_no_edits_returns_source(fixture_source: str) -> None:
    assert EditBuffer(fixture_source).render() == fixture_source


def test_identity_bullet_reorder_is_byte_identical(fixture_source: str) -> None:
    tree = parse_resume(fixture_source)
    edits = [
        ReorderBullets(
            entry_id=entry.id,
            order=[b.id for b in entry.bullets],
            reason="identity",
        )
        for entry in tree.all_entries()
        if entry.bullets
    ]
    result = apply_plan(tree, EditPlan(strategy="identity", edits=edits))
    assert result.source == fixture_source


def test_identity_section_reorder_is_byte_identical(fixture_source: str) -> None:
    tree = parse_resume(fixture_source)
    if not tree.sections:
        pytest.skip("fixture has no sections")
    plan = EditPlan(
        strategy="identity",
        edits=[
            ReorderSections(order=[s.id for s in tree.sections], reason="identity")
        ],
    )
    assert apply_plan(tree, plan).source == fixture_source


def test_identity_entry_reorder_is_byte_identical(fixture_source: str) -> None:
    tree = parse_resume(fixture_source)
    edits = [
        ReorderEntries(
            section_id=section.id,
            order=[e.id for e in section.entries],
            reason="identity",
        )
        for section in tree.sections
        if section.entries
    ]
    if not edits:
        pytest.skip("fixture has no entries")
    assert apply_plan(tree, EditPlan(strategy="identity", edits=edits)).source == (
        fixture_source
    )


# --- reordering is a pure permutation ------------------------------------


def test_bullet_reorder_preserves_every_character(fixture_source: str) -> None:
    """A reorder may move text but must never add, drop or alter any of it."""
    tree = parse_resume(fixture_source)
    entry = next((e for e in tree.all_entries() if len(e.bullets) > 1), None)
    if entry is None:
        pytest.skip("fixture has no entry with multiple bullets")

    ids = [b.id for b in entry.bullets]
    reversed_ids = list(reversed(ids))
    result = apply_plan(
        tree,
        EditPlan(
            strategy="reverse",
            edits=[
                ReorderBullets(entry_id=entry.id, order=reversed_ids, reason="test")
            ],
        ),
    )
    assert result.source != fixture_source
    assert len(result.source) == len(fixture_source)
    assert sorted(result.source) == sorted(fixture_source)


def test_reorder_is_reversible(fixture_source: str) -> None:
    """Reordering and then reordering back returns the original document."""
    tree = parse_resume(fixture_source)
    entry = next((e for e in tree.all_entries() if len(e.bullets) > 1), None)
    if entry is None:
        pytest.skip("fixture has no entry with multiple bullets")

    ids = [b.id for b in entry.bullets]
    once = apply_plan(
        tree,
        EditPlan(
            strategy="reverse",
            edits=[
                ReorderBullets(
                    entry_id=entry.id, order=list(reversed(ids)), reason="t"
                )
            ],
        ),
    ).source

    tree2 = parse_resume(once)
    entry2 = next(e for e in tree2.all_entries() if e.label == entry.label)
    twice = apply_plan(
        tree2,
        EditPlan(
            strategy="reverse back",
            edits=[
                ReorderBullets(
                    entry_id=entry2.id,
                    order=list(reversed([b.id for b in entry2.bullets])),
                    reason="t",
                )
            ],
        ),
    ).source
    assert twice == fixture_source


@settings(max_examples=25, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_arbitrary_permutations_preserve_characters(seed: int) -> None:
    """Any permutation is character-preserving, not just the reversal."""
    import random

    source = load("jakes_resume.tex")
    tree = parse_resume(source)
    entry = next(e for e in tree.all_entries() if len(e.bullets) > 2)
    ids = [b.id for b in entry.bullets]

    rng = random.Random(seed)
    shuffled = ids[:]
    rng.shuffle(shuffled)

    result = apply_plan(
        tree,
        EditPlan(
            strategy="shuffle",
            edits=[ReorderBullets(entry_id=entry.id, order=shuffled, reason="t")],
        ),
    )
    assert sorted(result.source) == sorted(source)
    if shuffled == ids:
        assert result.source == source


# --- parse stability ------------------------------------------------------


def test_parse_is_idempotent(fixture_source: str) -> None:
    """Re-parsing an unedited document yields the same structure and ids."""
    first = parse_resume(fixture_source)
    second = parse_resume(fixture_source)
    assert [s.id for s in first.sections] == [s.id for s in second.sections]
    assert [b.id for b in first.all_bullets()] == [b.id for b in second.all_bullets()]


def test_ids_survive_reordering(fixture_source: str) -> None:
    """Bullet ids are content-derived, so a reorder must not renumber them."""
    tree = parse_resume(fixture_source)
    entry = next((e for e in tree.all_entries() if len(e.bullets) > 1), None)
    if entry is None:
        pytest.skip("fixture has no entry with multiple bullets")

    before = {b.id for b in entry.bullets}
    result = apply_plan(
        tree,
        EditPlan(
            strategy="reverse",
            edits=[
                ReorderBullets(
                    entry_id=entry.id,
                    order=list(reversed([b.id for b in entry.bullets])),
                    reason="t",
                )
            ],
        ),
    )
    reparsed = parse_resume(result.source)
    entry2 = next(e for e in reparsed.all_entries() if e.label == entry.label)
    assert {b.id for b in entry2.bullets} == before


# --- scanner invariants ---------------------------------------------------


def test_comments_are_inert(fixture_source: str) -> None:
    """Nothing inside a comment may ever be treated as structure."""
    mask = build_mask(fixture_source)
    for index, char in enumerate(fixture_source):
        if char == "%" and mask[index]:
            pytest.fail(f"unescaped comment marker at {index} left active")


def test_commented_section_is_not_parsed() -> None:
    source = load("adversarial.tex")
    tree = parse_resume(source)
    titles = [s.title for s in tree.sections]
    assert "This section is commented out and must never be parsed" not in titles


def test_escaped_braces_do_not_open_groups() -> None:
    source = load("adversarial.tex")
    tree = parse_resume(source)
    bullets = [b.plain for b in tree.all_bullets()]
    assert any("like this" in b for b in bullets), "escaped-brace bullet was lost"


def test_nested_list_stays_with_its_parent_bullet() -> None:
    """A nested list is part of its parent bullet, not a sibling of it."""
    source = load("adversarial.tex")
    tree = parse_resume(source)
    texts = [b.plain for b in tree.all_bullets()]
    assert not any(t.startswith("This nested item belongs") for t in texts)
    parent = [t for t in texts if "Reduced on-call pages" in t]
    assert parent, "parent bullet of the nested list was lost"


def test_macro_name_prefix_is_not_confused() -> None:
    """A command whose name prefixes another must not match the longer one."""
    source = load("adversarial.tex")
    mask = build_mask(source)
    offset = find_command(source, "resumeItem", 0, mask)
    while offset != -1:
        following = source[offset + len("\\resumeItem")]
        assert not following.isalpha(), "matched inside a longer command name"
        offset = find_command(source, "resumeItem", offset + 1, mask)


def test_match_group_handles_escaped_braces() -> None:
    source = r"\cmd{outer \{ escaped \} still one group}tail"
    mask = build_mask(source)
    open_index = source.index("{")
    close = match_group(source, open_index, mask)
    assert source[close + 1 :] == "tail"


def test_spans_never_exceed_source(fixture_source: str) -> None:
    tree = parse_resume(fixture_source)
    n = len(fixture_source)
    for section in tree.sections:
        assert 0 <= section.span.start <= section.span.end <= n
        for entry in section.entries:
            assert section.span.contains(entry.span)
            for bullet in entry.bullets:
                assert entry.span.contains(bullet.outer)
                assert bullet.outer.contains(bullet.inner)


# --- edit buffer safety ---------------------------------------------------


def test_overlapping_edits_are_refused() -> None:
    buffer = EditBuffer("hello world")
    assert buffer.replace(Span(0, 5), "howdy")
    with pytest.raises(ValueError, match="overlaps"):
        buffer.replace(Span(3, 8), "nope")


def test_noop_replacement_is_dropped() -> None:
    buffer = EditBuffer("hello world")
    assert buffer.replace(Span(0, 5), "hello") is False
    assert len(buffer) == 0
    assert buffer.render() == "hello world"


def test_edits_apply_in_offset_order() -> None:
    buffer = EditBuffer("aaa bbb ccc")
    buffer.replace(Span(8, 11), "ZZZ")
    buffer.replace(Span(0, 3), "XXX")
    assert buffer.render() == "XXX bbb ZZZ"
