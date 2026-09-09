"""The typed resume document tree.

Nodes carry spans into the original source, never detached copies of text.
An edit therefore always knows exactly which characters it is allowed to
touch, and anything the parser did not recognise simply has no node pointing
at it -- which is what "unrecognised content is preserved verbatim" means in
practice.

Identifiers are content-derived so they are stable across regenerations of the
same base resume: reordering bullets does not renumber them, which matters
because an edit plan references bullets by ID and may be reviewed weeks later.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from .scanner import Span

__all__ = [
    "Bullet",
    "Entry",
    "Section",
    "ResumeTree",
    "slugify",
    "content_id",
]

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_TEX_COMMAND = re.compile(r"\\[a-zA-Z]+\s*")
_TEX_BRACES = re.compile(r"[{}]")
_WHITESPACE = re.compile(r"\s+")


def slugify(text: str, limit: int = 24) -> str:
    """Lowercase alphanumeric slug, used for the human-readable part of IDs."""
    plain = strip_tex(text).lower()
    slug = _SLUG_STRIP.sub("-", plain).strip("-")
    return slug[:limit] or "untitled"


def strip_tex(text: str) -> str:
    """Best-effort plain text from a LaTeX fragment.

    Used for slugs, hashing and guard analysis -- never for rendering, so a
    lossy result is acceptable here in a way it never is on the output path.
    """
    out = _TEX_COMMAND.sub(" ", text)
    out = _TEX_BRACES.sub("", out)
    out = out.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_")
    out = out.replace("\\#", "#").replace("\\$", "$").replace("~", " ")
    return _WHITESPACE.sub(" ", out).strip()


def content_id(prefix: str, text: str, *, scope: str = "", length: int = 8) -> str:
    """Deterministic ID derived from normalised content."""
    basis = f"{scope}\x00{_WHITESPACE.sub(' ', strip_tex(text).lower()).strip()}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class Bullet:
    """One achievement line -- the unit the agent is allowed to rewrite."""

    id: str
    inner: Span
    """The editable text argument only, excluding the surrounding macro."""
    outer: Span
    """The full macro invocation, used as the unit of reordering."""
    text: str
    """Raw LaTeX of the bullet body, exactly as written."""
    kind: str = "item"

    @property
    def plain(self) -> str:
        return strip_tex(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "inner": [self.inner.start, self.inner.end],
            "outer": [self.outer.start, self.outer.end],
            "text": self.text,
            "kind": self.kind,
        }


@dataclass(slots=True)
class Entry:
    """A role, degree or project: a header plus the bullets beneath it."""

    id: str
    command: str
    label: str
    fields: tuple[str, ...]
    header: Span
    span: Span
    bullets: list[Bullet] = field(default_factory=list)
    bullet_region: Span | None = None
    """Span covering the first through last bullet, the reorderable region."""
    separators: tuple[str, ...] = ()
    """Text observed between consecutive bullets, preserved on reorder."""

    def bullet_by_id(self, bullet_id: str) -> Bullet | None:
        for bullet in self.bullets:
            if bullet.id == bullet_id:
                return bullet
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "label": self.label,
            "fields": list(self.fields),
            "header": [self.header.start, self.header.end],
            "span": [self.span.start, self.span.end],
            "bullets": [b.to_dict() for b in self.bullets],
            "bullet_region": (
                [self.bullet_region.start, self.bullet_region.end]
                if self.bullet_region
                else None
            ),
            "separators": list(self.separators),
        }


@dataclass(slots=True)
class Section:
    """A top-level resume section."""

    id: str
    title: str
    title_span: Span
    header_span: Span
    span: Span
    body: Span
    entries: list[Entry] = field(default_factory=list)
    loose_bullets: list[Bullet] = field(default_factory=list)
    """Bullets directly under the section, with no intervening entry header."""
    is_skills: bool = False

    def all_bullets(self) -> Iterator[Bullet]:
        yield from self.loose_bullets
        for entry in self.entries:
            yield from entry.bullets

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "title_span": [self.title_span.start, self.title_span.end],
            "header_span": [self.header_span.start, self.header_span.end],
            "span": [self.span.start, self.span.end],
            "body": [self.body.start, self.body.end],
            "entries": [e.to_dict() for e in self.entries],
            "loose_bullets": [b.to_dict() for b in self.loose_bullets],
            "is_skills": self.is_skills,
        }


@dataclass(slots=True)
class ResumeTree:
    """A parsed resume: the immutable source plus everything we recognised."""

    source: str
    profile: str
    sections: list[Section] = field(default_factory=list)
    preamble: Span | None = None
    section_region: Span | None = None
    """Span from the first to the last section, the reorderable region."""
    section_separators: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    # -- lookup ----------------------------------------------------------

    def section_by_id(self, section_id: str) -> Section | None:
        for section in self.sections:
            if section.id == section_id:
                return section
        return None

    def entry_by_id(self, entry_id: str) -> Entry | None:
        for section in self.sections:
            for entry in section.entries:
                if entry.id == entry_id:
                    return entry
        return None

    def bullet_by_id(self, bullet_id: str) -> Bullet | None:
        for bullet in self.all_bullets():
            if bullet.id == bullet_id:
                return bullet
        return None

    def owner_of(self, bullet_id: str) -> Entry | Section | None:
        """Return the entry or section that directly contains ``bullet_id``."""
        for section in self.sections:
            for bullet in section.loose_bullets:
                if bullet.id == bullet_id:
                    return section
            for entry in section.entries:
                if entry.bullet_by_id(bullet_id):
                    return entry
        return None

    def all_bullets(self) -> Iterator[Bullet]:
        for section in self.sections:
            yield from section.all_bullets()

    def all_entries(self) -> Iterator[Entry]:
        for section in self.sections:
            yield from section.entries

    # -- diagnostics -----------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "sections": len(self.sections),
            "entries": sum(len(s.entries) for s in self.sections),
            "bullets": sum(1 for _ in self.all_bullets()),
        }

    def summary(self) -> str:
        s = self.stats()
        return (
            f"{s['sections']} sections, {s['entries']} entries, "
            f"{s['bullets']} bullets (profile: {self.profile})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "sections": [s.to_dict() for s in self.sections],
            "preamble": (
                [self.preamble.start, self.preamble.end] if self.preamble else None
            ),
            "section_region": (
                [self.section_region.start, self.section_region.end]
                if self.section_region
                else None
            ),
            "section_separators": list(self.section_separators),
            "warnings": list(self.warnings),
            "stats": self.stats(),
        }
