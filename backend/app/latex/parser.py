"""Build a :class:`ResumeTree` from LaTeX source.

The parser recognises structure; it never rewrites it. Anything it does not
understand is simply left without a node, and therefore untouchable by the
edit pipeline downstream. That asymmetry is intentional: a parse miss degrades
the quality of tailoring, but it can never corrupt a document.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace

from .profiles import TemplateProfile, detect_profile, get_profile
from .scanner import (
    Span,
    build_mask,
    find_command,
    find_environments,
    match_group,
    read_arguments,
)
from .tree import Bullet, Entry, ResumeTree, Section, content_id, slugify, strip_tex

__all__ = ["parse_resume", "ParseError"]


class ParseError(Exception):
    """Raised when the source is too malformed to reason about safely."""


def _trim_span(source: str, span: Span) -> Span:
    """Shrink a span to exclude leading and trailing whitespace."""
    start, end = span.start, span.end
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return Span(start, end)


def _skip_star(source: str, i: int) -> int:
    """Step over the star of a starred sectioning command."""
    while i < len(source) and source[i].isspace():
        i += 1
    return i + 1 if i < len(source) and source[i] == "*" else i


def _find_all(
    source: str, names: tuple[str, ...], mask: bytearray, region: Span
) -> list[tuple[int, str]]:
    """Locate every invocation of any command in ``names`` inside ``region``."""
    hits: list[tuple[int, str]] = []
    for name in names:
        i = region.start
        while True:
            i = find_command(source, name, i, mask)
            if i == -1 or i >= region.end:
                break
            hits.append((i, name))
            i += len(name) + 1
    hits.sort()
    return hits


def _group_region(
    source: str, outers: list[Span]
) -> tuple[Span | None, tuple[str, ...]]:
    """Compute the reorderable region and the separators between members."""
    if not outers:
        return None, ()
    region = Span(outers[0].start, outers[-1].end)
    separators = tuple(
        source[outers[i].end : outers[i + 1].start] for i in range(len(outers) - 1)
    )
    return region, separators


def _parse_macro_bullets(
    source: str,
    mask: bytearray,
    profile: TemplateProfile,
    region: Span,
    scope: str,
) -> list[Bullet]:
    """Parse bullets expressed as a single-argument macro."""
    bullets: list[Bullet] = []
    for offset, name in _find_all(source, profile.bullet_commands, mask, region):
        args, end = read_arguments(source, offset + len(name) + 1, 1, mask)
        if not args:
            continue
        inner = args[0]
        text = inner.text(source)
        bullets.append(
            Bullet(
                id=content_id("b", text, scope=scope),
                inner=inner,
                outer=Span(offset, end),
                text=text,
                kind=name,
            )
        )
    return bullets


def _parse_list_bullets(
    source: str,
    mask: bytearray,
    profile: TemplateProfile,
    region: Span,
    scope: str,
) -> list[Bullet]:
    """Parse bullets expressed as item markers inside a list environment."""
    bullets: list[Bullet] = []
    for env_name in profile.bullet_list_environments:
        envs = find_environments(source, env_name, mask, region=region)
        if not envs:
            continue
        # Only the outermost lists carry resume bullets; a nested list is part
        # of its parent bullet's text and must not be split out.
        outermost = [e for e in envs if e.depth == 0]
        nested = [e for e in envs if e.depth > 0]

        for env in outermost:
            item_offsets = [
                off for off, _ in _find_all(source, ("item",), mask, env.body)
            ]
            item_offsets = [
                off
                for off in item_offsets
                if not any(inner.body.start <= off < inner.body.end for inner in nested)
            ]
            for index, offset in enumerate(item_offsets):
                stop = (
                    item_offsets[index + 1]
                    if index + 1 < len(item_offsets)
                    else env.body.end
                )
                outer = _trim_span(source, Span(offset, stop))
                inner = _trim_span(source, Span(offset + len("\\item"), stop))
                if len(inner) == 0:
                    continue
                text = inner.text(source)
                bullets.append(
                    Bullet(
                        id=content_id("b", text, scope=scope),
                        inner=inner,
                        outer=outer,
                        text=text,
                        kind="item",
                    )
                )
    return bullets


def _parse_bullets(
    source: str,
    mask: bytearray,
    profile: TemplateProfile,
    region: Span,
    scope: str,
) -> tuple[list[Bullet], Span | None, tuple[str, ...]]:
    """Parse every bullet in ``region``, preferring macro form over list form."""
    bullets: list[Bullet] = []
    if profile.bullet_commands:
        bullets = _parse_macro_bullets(source, mask, profile, region, scope)
    if not bullets:
        bullets = _parse_list_bullets(source, mask, profile, region, scope)

    bullets.sort(key=lambda b: b.outer.start)
    bullets = _disambiguate(bullets)
    region_span, separators = _group_region(source, [b.outer for b in bullets])
    return bullets, region_span, separators


def _disambiguate(bullets: list[Bullet]) -> list[Bullet]:
    """Ensure IDs are unique when two bullets have identical text."""
    seen: dict[str, int] = {}
    for bullet in bullets:
        count = seen.get(bullet.id, 0)
        seen[bullet.id] = count + 1
        if count:
            bullet.id = f"{bullet.id}x{count}"
    return bullets


def _parse_entries(
    source: str,
    mask: bytearray,
    profile: TemplateProfile,
    section: Section,
) -> list[Entry]:
    """Parse entry headers and attach the bullets that follow each one."""
    commands = profile.all_entry_commands()
    if not commands:
        return []
    hits = _find_all(source, commands, mask, section.body)
    if not hits:
        return []

    entries: list[Entry] = []
    for index, (offset, name) in enumerate(hits):
        rule = profile.rule_for(name)
        assert rule is not None
        args, header_end = read_arguments(
            source, offset + len(name) + 1, rule.arg_count, mask
        )
        stop = hits[index + 1][0] if index + 1 < len(hits) else section.body.end

        field_texts = tuple(a.text(source) for a in args)
        label = ""
        for idx in rule.label_args:
            if idx < len(field_texts) and strip_tex(field_texts[idx]):
                label = strip_tex(field_texts[idx])
                break
        if not label:
            label = f"entry-{index + 1}"

        entry_id = content_id("e", label, scope=section.id)
        body_region = Span(header_end, stop)
        bullets, bullet_region, separators = _parse_bullets(
            source, mask, profile, body_region, entry_id
        )

        entries.append(
            Entry(
                id=entry_id,
                command=name,
                label=label,
                fields=field_texts,
                header=Span(offset, header_end),
                span=Span(offset, stop),
                bullets=bullets,
                bullet_region=bullet_region,
                separators=separators,
            )
        )

    # Unique-ify entry IDs the same way bullets are handled.
    seen: dict[str, int] = {}
    for entry in entries:
        count = seen.get(entry.id, 0)
        seen[entry.id] = count + 1
        if count:
            entry.id = f"{entry.id}x{count}"
    return entries


def _looks_like_skills(title: str, profile: TemplateProfile) -> bool:
    lowered = strip_tex(title).lower()
    return any(hint in lowered for hint in profile.skills_section_hints)


def parse_resume(source: str, profile_name: str | None = None) -> ResumeTree:
    """Parse ``source`` into a :class:`ResumeTree`.

    ``profile_name`` forces a specific template profile; when omitted the
    best-scoring profile is detected automatically.
    """
    if not source.strip():
        raise ParseError("source is empty")

    if profile_name:
        profile = get_profile(profile_name)
        evidence: tuple[str, ...] = (f"profile forced to {profile_name}",)
    else:
        match = detect_profile(source)
        profile = match.profile
        evidence = match.evidence

    mask = build_mask(source)
    warnings: list[str] = []

    # Body starts after \begin{document} when there is one.
    docs = find_environments(source, "document", mask)
    if docs:
        body = docs[0].body
        preamble = Span(0, docs[0].span.start)
    else:
        body = Span(0, len(source))
        preamble = None
        warnings.append("no document environment found; treating whole file as body")

    hits = _find_all(source, profile.section_commands, mask, body)
    # A starred variant resolves to the same offset as its unstarred form;
    # keep the longest command name at each offset and drop duplicates.
    deduped: dict[int, str] = {}
    for offset, name in hits:
        if offset not in deduped or len(name) > len(deduped[offset]):
            deduped[offset] = name
    hits = sorted(deduped.items())

    sections: list[Section] = []
    for index, (offset, name) in enumerate(hits):
        after = _skip_star(source, offset + len(name) + 1)
        args, header_end = read_arguments(
            source,
            after,
            profile.section_arg_count,
            mask,
            optional_first=profile.section_optional_arg,
        )
        if not args:
            warnings.append(f"section at offset {offset} has no title argument")
            continue
        stop = hits[index + 1][0] if index + 1 < len(hits) else body.end
        title_span = args[0]
        title = title_span.text(source)
        section = Section(
            id=content_id("s", title),
            title=title,
            title_span=title_span,
            header_span=Span(offset, header_end),
            span=Span(offset, stop),
            body=Span(header_end, stop),
            is_skills=_looks_like_skills(title, profile),
        )
        sections.append(section)

    seen: dict[str, int] = {}
    for section in sections:
        count = seen.get(section.id, 0)
        seen[section.id] = count + 1
        if count:
            section.id = f"{section.id}x{count}"

    for section in sections:
        section.entries = _parse_entries(source, mask, profile, section)
        if section.entries:
            covered_from = section.entries[0].span.start
            loose_region = Span(section.body.start, covered_from)
        else:
            loose_region = section.body
        loose, _, _ = _parse_bullets(source, mask, profile, loose_region, section.id)
        section.loose_bullets = loose

    section_region, section_separators = _group_region(
        source, [_trim_span(source, s.span) for s in sections]
    )

    if not sections:
        warnings.append(
            "no sections recognised; the template may need a dedicated profile"
        )

    tree = ResumeTree(
        source=source,
        profile=profile.name,
        sections=sections,
        preamble=preamble,
        section_region=section_region,
        section_separators=section_separators,
        warnings=tuple(warnings) + tuple(f"detection: {e}" for e in evidence),
    )
    return tree
