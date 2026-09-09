"""Lossless scanning primitives for LaTeX source.

Every function here works in terms of character offsets into an immutable
source string. Nothing is ever re-serialised from a parsed representation,
which is what makes ResumeForge's round-trip guarantee structural rather than
best-effort: if a node is not edited, its original bytes are emitted verbatim.

The scanner is deliberately *not* a LaTeX interpreter. It understands just
enough surface syntax -- escapes, comments, balanced groups, environments --
to locate editable regions and to leave everything else alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = [
    "Span",
    "CommandCall",
    "Environment",
    "build_mask",
    "match_group",
    "find_command",
    "iter_commands",
    "read_arguments",
    "find_environments",
]

# Characters that, when preceded by a backslash, form an escaped literal
# rather than a control word. These are inert for brace-matching purposes.
_ESCAPABLE = set("\\{}$&#^_%~ ")


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open character range [start, end) into a source string."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"inverted span: {self.start} > {self.end}")

    def __len__(self) -> int:
        return self.end - self.start

    def text(self, source: str) -> str:
        return source[self.start : self.end]

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Span") -> bool:
        return self.start <= other.start and other.end <= self.end


def build_mask(source: str) -> bytearray:
    """Classify every character as active code (1) or inert (0).

    Inert means the character must not be interpreted as LaTeX syntax:

    * the body of a ``%`` comment, up to but not including its newline
    * both characters of an escaped literal such as ``\\%``, ``\\{``, ``\\_``

    Brace matching and command detection consult this mask, so an escaped
    brace never opens a group and a ``}`` inside a comment never closes one.
    """
    mask = bytearray(b"\x01") * len(source)
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            nxt = source[i + 1]
            if nxt in _ESCAPABLE:
                # Escaped literal: neutralise the pair.
                mask[i] = 0
                mask[i + 1] = 0
                i += 2
                continue
            if nxt.isalpha():
                # Control word: the backslash and its letters stay active so
                # command detection can see them; they contain no braces.
                i += 1
                while i < n and source[i].isalpha():
                    i += 1
                continue
            # Control symbol with a non-escapable char.
            mask[i] = 0
            mask[i + 1] = 0
            i += 2
            continue
        if ch == "%":
            # Comment runs to end of line; the newline itself stays active.
            mask[i] = 0
            i += 1
            while i < n and source[i] != "\n":
                mask[i] = 0
                i += 1
            continue
        i += 1
    return mask


def match_group(source: str, open_index: int, mask: Sequence[int] | None = None) -> int:
    """Return the index of the brace closing the group opened at ``open_index``.

    Raises ``ValueError`` if the group is unbalanced, which for our purposes
    means the file is not something we should be editing.
    """
    if source[open_index] != "{":
        raise ValueError(
            f"expected an opening brace at offset {open_index}, "
            f"found {source[open_index]!r}"
        )
    if mask is None:
        mask = build_mask(source)
    depth = 0
    for i in range(open_index, len(source)):
        if not mask[i]:
            continue
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced group opened at offset {open_index}")


def _skip_ws(source: str, i: int, mask: Sequence[int]) -> int:
    """Advance past whitespace and comments, which may separate arguments."""
    n = len(source)
    while i < n:
        if source[i].isspace():
            i += 1
        elif source[i] == "%" and not mask[i]:
            while i < n and source[i] != "\n":
                i += 1
        else:
            break
    return i


@dataclass(frozen=True, slots=True)
class CommandCall:
    """A located command invocation and the spans of its arguments."""

    name: str
    span: Span
    """Covers the control word plus every argument that was read."""
    args: tuple[Span, ...]
    """Inner spans -- the content *between* each pair of braces, exclusive."""

    def arg_text(self, source: str, index: int) -> str:
        return self.args[index].text(source)


def read_arguments(
    source: str,
    after: int,
    count: int,
    mask: Sequence[int],
    *,
    optional_first: bool = False,
) -> tuple[list[Span], int]:
    """Read up to ``count`` brace groups starting at ``after``.

    Returns the inner spans and the offset just past the final closing brace.
    Stops early if fewer groups are present, which keeps malformed input
    recoverable rather than fatal.
    """
    spans: list[Span] = []
    i = after
    n = len(source)

    if optional_first:
        j = _skip_ws(source, i, mask)
        if j < n and source[j] == "[" and mask[j]:
            depth = 0
            k = j
            while k < n:
                if mask[k]:
                    if source[k] == "[":
                        depth += 1
                    elif source[k] == "]":
                        depth -= 1
                        if depth == 0:
                            break
                k += 1
            if k < n:
                i = k + 1

    for _ in range(count):
        j = _skip_ws(source, i, mask)
        if j >= n or source[j] != "{" or not mask[j]:
            break
        close = match_group(source, j, mask)
        spans.append(Span(j + 1, close))
        i = close + 1
    return spans, i


def find_command(
    source: str,
    name: str,
    start: int = 0,
    mask: Sequence[int] | None = None,
) -> int:
    """Find the next invocation of ``name`` at or after ``start``, or -1.

    Matches whole control words only, so searching for ``section`` does not
    match inside a longer command such as ``sectionrule``.
    """
    if mask is None:
        mask = build_mask(source)
    needle = "\\" + name
    n = len(source)
    i = start
    while True:
        i = source.find(needle, i)
        if i == -1:
            return -1
        end = i + len(needle)
        # Reject a longer control word, or a backslash that is itself inert.
        if (end < n and source[end].isalpha()) or not mask[i]:
            i = end
            continue
        return i


def iter_commands(
    source: str,
    names: Sequence[str],
    arg_counts: dict[str, int],
    mask: Sequence[int] | None = None,
    *,
    region: Span | None = None,
) -> Iterator[CommandCall]:
    """Yield every invocation of any command in ``names``, in source order."""
    if mask is None:
        mask = build_mask(source)
    lo = region.start if region else 0
    hi = region.end if region else len(source)

    found: list[tuple[int, str]] = []
    for name in names:
        i = lo
        while True:
            i = find_command(source, name, i, mask)
            if i == -1 or i >= hi:
                break
            found.append((i, name))
            i += len(name) + 1
    found.sort()

    for offset, name in found:
        after = offset + len(name) + 1
        args, end = read_arguments(source, after, arg_counts.get(name, 0), mask)
        yield CommandCall(name=name, span=Span(offset, end), args=tuple(args))


@dataclass(frozen=True, slots=True)
class Environment:
    """A located ``begin``/``end`` environment pair."""

    name: str
    span: Span
    """Covers the whole environment including both delimiters."""
    body: Span
    """Content strictly between the opening and closing delimiters."""
    depth: int


def find_environments(
    source: str,
    name: str,
    mask: Sequence[int] | None = None,
    *,
    region: Span | None = None,
) -> list[Environment]:
    """Locate every ``name`` environment, handling nesting correctly."""
    if mask is None:
        mask = build_mask(source)
    lo = region.start if region else 0
    hi = region.end if region else len(source)

    opens: list[tuple[int, int]] = []  # (begin_offset, body_start)
    result: list[Environment] = []
    i = lo
    while i < hi:
        b = find_command(source, "begin", i, mask)
        e = find_command(source, "end", i, mask)
        candidates = [x for x in (b, e) if x != -1 and x < hi]
        if not candidates:
            break
        at = min(candidates)
        kind = "begin" if at == b else "end"
        args, after = read_arguments(source, at + len(kind) + 1, 1, mask)
        if not args or args[0].text(source).strip() != name:
            i = at + len(kind) + 1
            continue
        if kind == "begin":
            opens.append((at, after))
        elif opens:
            begin_offset, body_start = opens.pop()
            result.append(
                Environment(
                    name=name,
                    span=Span(begin_offset, after),
                    body=Span(body_start, at),
                    depth=len(opens),
                )
            )
        i = after
    result.sort(key=lambda env: env.span.start)
    return result
