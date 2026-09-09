"""Apply a validated edit plan to a parsed resume.

Every operation is executed by this module, deterministically, against spans.
The model's output reaches the document only as data that has already survived
:mod:`app.agent.guards`; nothing here consults a model, and nothing here can
introduce content that was not either in the original file or in an approved
rewrite.

Two properties are maintained deliberately:

* An operation that changes nothing produces no edit, so an identity reorder
  or a rewrite to identical text leaves the file byte-identical.
* Reordering reuses the *original* separators positionally, so indentation and
  blank-line structure are preserved exactly rather than regenerated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..latex.editbuffer import EditBuffer
from ..latex.escape import contains_markup, escape_tex
from ..latex.scanner import Span
from ..latex.tree import Bullet, Entry, ResumeTree, Section, strip_tex
from .guards import GuardResult, check_rewrite
from .ops import (
    DropBullet,
    EditPlan,
    ReorderBullets,
    ReorderEntries,
    ReorderSections,
    RewriteBullet,
)

__all__ = ["AppliedEdit", "RejectedEdit", "ApplyResult", "apply_plan"]


@dataclass(slots=True)
class AppliedEdit:
    op: str
    target_id: str
    reason: str
    before: str = ""
    after: str = ""
    detail: str = ""


@dataclass(slots=True)
class RejectedEdit:
    op: str
    target_id: str
    reason: str
    rejection: str
    proposed: str = ""
    guard: GuardResult | None = None


@dataclass(slots=True)
class ApplyResult:
    source: str
    applied: list[AppliedEdit] = field(default_factory=list)
    rejected: list[RejectedEdit] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    def summary(self) -> str:
        return f"{len(self.applied)} edits applied, {len(self.rejected)} rejected"


def _validate_permutation(
    proposed: Sequence[str], existing: Sequence[str]
) -> str | None:
    """Return an error message if ``proposed`` is not a permutation."""
    if len(proposed) != len(existing):
        return (
            f"order lists {len(proposed)} ids but the target has "
            f"{len(existing)}; reordering may not add or remove items"
        )
    if set(proposed) != set(existing):
        unknown = sorted(set(proposed) - set(existing))
        missing = sorted(set(existing) - set(proposed))
        parts = []
        if unknown:
            parts.append(f"unknown ids {unknown}")
        if missing:
            parts.append(f"omitted ids {missing}")
        return "; ".join(parts)
    return None


def _render_group(
    source: str,
    members: Sequence[Span],
    separators: Sequence[str],
    order: Sequence[int],
    overrides: dict[int, str] | None = None,
) -> str:
    """Rebuild a reorderable region under a new ordering.

    Separators are consumed positionally, not carried with their members, so
    the whitespace and indentation pattern of the region is exactly preserved
    however the members are permuted.
    """
    overrides = overrides or {}
    texts = [
        overrides.get(index, members[index].text(source)) for index in order
    ]
    if not texts:
        return ""
    seps = list(separators[: max(len(texts) - 1, 0)])
    while len(seps) < len(texts) - 1:
        seps.append(seps[-1] if seps else "\n")
    out = [texts[0]]
    for index, text in enumerate(texts[1:]):
        out.append(seps[index])
        out.append(text)
    return "".join(out)


@dataclass
class _BulletGroup:
    """Pending mutations for one reorderable set of bullets."""

    owner_id: str
    bullets: list[Bullet]
    region: Span | None
    separators: tuple[str, ...]
    order: list[int] = field(default_factory=list)
    dropped: set[int] = field(default_factory=set)
    rewrites: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.order:
            self.order = list(range(len(self.bullets)))

    @property
    def mutated(self) -> bool:
        identity = list(range(len(self.bullets)))
        return bool(self.rewrites) or self.dropped or self.order != identity


def _collect_groups(tree: ResumeTree) -> dict[str, _BulletGroup]:
    groups: dict[str, _BulletGroup] = {}
    for section in tree.sections:
        if section.loose_bullets:
            region = Span(
                section.loose_bullets[0].outer.start,
                section.loose_bullets[-1].outer.end,
            )
            seps = tuple(
                tree.source[
                    section.loose_bullets[i].outer.end : section.loose_bullets[
                        i + 1
                    ].outer.start
                ]
                for i in range(len(section.loose_bullets) - 1)
            )
            groups[section.id] = _BulletGroup(
                owner_id=section.id,
                bullets=list(section.loose_bullets),
                region=region,
                separators=seps,
            )
        for entry in section.entries:
            if entry.bullets:
                groups[entry.id] = _BulletGroup(
                    owner_id=entry.id,
                    bullets=list(entry.bullets),
                    region=entry.bullet_region,
                    separators=entry.separators,
                )
    return groups


def _group_of(groups: dict[str, _BulletGroup], bullet_id: str) -> _BulletGroup | None:
    for group in groups.values():
        for bullet in group.bullets:
            if bullet.id == bullet_id:
                return group
    return None


def apply_plan(tree: ResumeTree, plan: EditPlan) -> ApplyResult:
    """Validate and apply ``plan`` against ``tree``.

    Returns the rendered source together with a full account of what was
    applied and what was refused. A rejected operation never aborts the run --
    the remaining edits still land, and the rejection is reported.
    """
    source = tree.source
    buffer = EditBuffer(source)
    result = ApplyResult(source=source)
    groups = _collect_groups(tree)

    section_order: list[int] | None = None
    entry_orders: dict[str, list[int]] = {}

    for op in plan.edits:
        # -- section reordering ------------------------------------------
        if isinstance(op, ReorderSections):
            existing = [s.id for s in tree.sections]
            error = _validate_permutation(op.order, existing)
            if error:
                result.rejected.append(
                    RejectedEdit("reorder_sections", "", op.reason, error)
                )
                continue
            section_order = [existing.index(sid) for sid in op.order]

        # -- entry reordering --------------------------------------------
        elif isinstance(op, ReorderEntries):
            section = tree.section_by_id(op.section_id)
            if section is None:
                result.rejected.append(
                    RejectedEdit(
                        "reorder_entries",
                        op.section_id,
                        op.reason,
                        "no such section",
                    )
                )
                continue
            existing = [e.id for e in section.entries]
            error = _validate_permutation(op.order, existing)
            if error:
                result.rejected.append(
                    RejectedEdit("reorder_entries", op.section_id, op.reason, error)
                )
                continue
            entry_orders[section.id] = [existing.index(eid) for eid in op.order]

        # -- bullet reordering -------------------------------------------
        elif isinstance(op, ReorderBullets):
            group = groups.get(op.entry_id)
            if group is None:
                result.rejected.append(
                    RejectedEdit(
                        "reorder_bullets",
                        op.entry_id,
                        op.reason,
                        "no such entry, or it has no bullets",
                    )
                )
                continue
            existing = [b.id for b in group.bullets]
            error = _validate_permutation(op.order, existing)
            if error:
                result.rejected.append(
                    RejectedEdit("reorder_bullets", op.entry_id, op.reason, error)
                )
                continue
            group.order = [existing.index(bid) for bid in op.order]
            result.applied.append(
                AppliedEdit(
                    op="reorder_bullets",
                    target_id=op.entry_id,
                    reason=op.reason,
                    before=" | ".join(b.plain[:40] for b in group.bullets),
                    after=" | ".join(group.bullets[i].plain[:40] for i in group.order),
                    detail=f"{len(group.bullets)} bullets reordered",
                )
            )

        # -- bullet rewriting --------------------------------------------
        elif isinstance(op, RewriteBullet):
            bullet = tree.bullet_by_id(op.bullet_id)
            if bullet is None:
                result.rejected.append(
                    RejectedEdit(
                        "rewrite_bullet", op.bullet_id, op.reason, "no such bullet"
                    )
                )
                continue
            if contains_markup(bullet.text):
                result.rejected.append(
                    RejectedEdit(
                        "rewrite_bullet",
                        op.bullet_id,
                        op.reason,
                        "bullet contains LaTeX markup and is reorder-only; "
                        "rewriting it would require the model to reproduce "
                        "formatting",
                        proposed=op.new_text,
                    )
                )
                continue

            guard = check_rewrite(bullet.plain, op.new_text)
            if not guard.ok:
                result.rejected.append(
                    RejectedEdit(
                        "rewrite_bullet",
                        op.bullet_id,
                        op.reason,
                        guard.summary,
                        proposed=op.new_text,
                        guard=guard,
                    )
                )
                continue

            group = _group_of(groups, op.bullet_id)
            if group is None:
                continue
            index = [b.id for b in group.bullets].index(op.bullet_id)
            escaped = escape_tex(op.new_text)
            original_outer = bullet.outer.text(source)
            rel_start = bullet.inner.start - bullet.outer.start
            rel_end = bullet.inner.end - bullet.outer.start
            group.rewrites[index] = (
                original_outer[:rel_start] + escaped + original_outer[rel_end:]
            )
            result.applied.append(
                AppliedEdit(
                    op="rewrite_bullet",
                    target_id=op.bullet_id,
                    reason=op.reason,
                    before=bullet.plain,
                    after=op.new_text,
                    detail=f"length {guard.length_ratio:+.0%} of original".replace(
                        "+", ""
                    ),
                )
            )

        # -- bullet removal ----------------------------------------------
        elif isinstance(op, DropBullet):
            group = _group_of(groups, op.bullet_id)
            bullet = tree.bullet_by_id(op.bullet_id)
            if group is None or bullet is None:
                result.rejected.append(
                    RejectedEdit(
                        "drop_bullet", op.bullet_id, op.reason, "no such bullet"
                    )
                )
                continue
            index = [b.id for b in group.bullets].index(op.bullet_id)
            group.dropped.add(index)
            result.applied.append(
                AppliedEdit(
                    op="drop_bullet",
                    target_id=op.bullet_id,
                    reason=op.reason,
                    before=bullet.plain,
                    after="",
                    detail="removed to fit page budget",
                )
            )

    # -- emit span replacements -------------------------------------------
    # Bullet groups first, then entries, then sections. Each level rebuilds a
    # region that contains the level below, so a mutated inner region must be
    # folded into its parent rather than emitted separately.
    _emit(tree, buffer, groups, entry_orders, section_order)

    result.source = buffer.render()
    return result


def _group_text(source: str, group: _BulletGroup) -> str:
    order = [i for i in group.order if i not in group.dropped]
    return _render_group(
        source,
        [b.outer for b in group.bullets],
        group.separators,
        order,
        group.rewrites,
    )


def _emit(
    tree: ResumeTree,
    buffer: EditBuffer,
    groups: dict[str, _BulletGroup],
    entry_orders: dict[str, list[int]],
    section_order: list[int] | None,
) -> None:
    """Translate pending mutations into non-overlapping span replacements."""
    source = tree.source

    def entry_text(entry: Entry) -> str:
        """Render one entry, folding in any bullet mutation it contains."""
        text = entry.span.text(source)
        group = groups.get(entry.id)
        if group and group.mutated and group.region:
            rel_start = group.region.start - entry.span.start
            rel_end = group.region.end - entry.span.start
            text = text[:rel_start] + _group_text(source, group) + text[rel_end:]
        return text

    def section_text(section: Section) -> str:
        """Render one section, folding in entry reordering and bullets."""
        text = section.span.text(source)
        order = entry_orders.get(section.id)
        entries_mutated = any(
            groups.get(e.id) and groups[e.id].mutated for e in section.entries
        )
        loose = groups.get(section.id)

        if section.entries and (order is not None or entries_mutated):
            members = [e.span for e in section.entries]
            seps = tuple(
                source[members[i].end : members[i + 1].start]
                for i in range(len(members) - 1)
            )
            overrides = {
                i: entry_text(e)
                for i, e in enumerate(section.entries)
                if groups.get(e.id) and groups[e.id].mutated
            }
            region = Span(members[0].start, members[-1].end)
            rel_start = region.start - section.span.start
            rel_end = region.end - section.span.start
            rebuilt = _render_group(
                source,
                members,
                seps,
                order if order is not None else list(range(len(members))),
                overrides,
            )
            text = text[:rel_start] + rebuilt + text[rel_end:]

        if loose and loose.mutated and loose.region:
            rel_start = loose.region.start - section.span.start
            rel_end = loose.region.end - section.span.start
            text = text[:rel_start] + _group_text(source, loose) + text[rel_end:]
        return text

    if section_order is not None and tree.section_region:
        members = [s.span for s in tree.sections]
        seps = tuple(
            source[members[i].end : members[i + 1].start]
            for i in range(len(members) - 1)
        )
        overrides = {i: section_text(s) for i, s in enumerate(tree.sections)}
        region = Span(members[0].start, members[-1].end)
        buffer.replace(
            region,
            _render_group(source, members, seps, section_order, overrides),
            origin="reorder_sections",
        )
        return

    for section in tree.sections:
        order = entry_orders.get(section.id)
        entries_mutated = any(
            groups.get(e.id) and groups[e.id].mutated for e in section.entries
        )
        loose = groups.get(section.id)
        if order is not None or (entries_mutated and section.entries) or (
            loose and loose.mutated
        ):
            buffer.replace(
                section.span,
                section_text(section),
                origin=f"section:{section.id}",
            )
