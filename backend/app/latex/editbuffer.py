"""Span-replacement rendering.

The round-trip guarantee lives here. A document is never rebuilt from a parsed
representation; it is the original source with a set of non-overlapping span
replacements applied. Two consequences fall out for free:

* An empty edit set renders the source unchanged, character for character.
* An edit that turns out to be a no-op (a reorder into the existing order,
  a rewrite to identical text) is dropped, so it cannot perturb bytes it
  should not touch.

Overlapping edits are a programming error and raise rather than silently
producing a mangled document.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .scanner import Span

__all__ = ["Replacement", "EditBuffer"]


@dataclass(frozen=True, slots=True)
class Replacement:
    """Replace the characters under ``span`` with ``text``."""

    span: Span
    text: str
    origin: str = ""
    """Free-form provenance label, surfaced in diagnostics and the diff report."""


@dataclass
class EditBuffer:
    """Accumulates span replacements against an immutable source string."""

    source: str
    _edits: list[Replacement] = field(default_factory=list)

    def replace(self, span: Span, text: str, origin: str = "") -> bool:
        """Queue a replacement. Returns False if it was a no-op and dropped."""
        if span.end > len(self.source):
            raise ValueError(f"span {span} exceeds source length {len(self.source)}")
        if span.text(self.source) == text:
            return False
        for existing in self._edits:
            if existing.span.overlaps(span):
                raise ValueError(
                    f"edit {origin or span} overlaps "
                    f"{existing.origin or existing.span}"
                )
        self._edits.append(Replacement(span=span, text=text, origin=origin))
        return True

    def __len__(self) -> int:
        return len(self._edits)

    @property
    def edits(self) -> tuple[Replacement, ...]:
        return tuple(sorted(self._edits, key=lambda r: r.span.start))

    def render(self) -> str:
        """Apply every queued replacement and return the resulting source."""
        if not self._edits:
            # The guarantee, made explicit: zero edits means zero change.
            return self.source
        out: list[str] = []
        cursor = 0
        for edit in self.edits:
            out.append(self.source[cursor : edit.span.start])
            out.append(edit.text)
            cursor = edit.span.end
        out.append(self.source[cursor:])
        return "".join(out)
