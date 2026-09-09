"""LaTeX escaping for model-authored text.

The agent is asked for plain prose and never for LaTeX, and escaping happens
here, in code, on the way in. That ordering matters: it makes an entire class
of compile failure structurally impossible rather than something the
compile-fix loop has to discover and repair. A model that writes "reduced cost
by 40%" cannot break the build, because the percent sign is escaped before it
ever reaches the file.

The inverse direction is equally important. Bullets are shown to the model as
plain text, so it never sees markup it might feel invited to imitate.
"""

from __future__ import annotations

import re

__all__ = ["escape_tex", "contains_markup", "is_plain_prose"]

# Order matters: the backslash must be replaced first or it would corrupt
# every escape sequence introduced after it.
_ESCAPES: tuple[tuple[str, str], ...] = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)

_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_MATH_RE = re.compile(r"(?<!\\)\$")


def escape_tex(text: str) -> str:
    """Escape plain prose so it is safe to place in a LaTeX document."""
    out = text
    for raw, replacement in _ESCAPES:
        out = out.replace(raw, replacement)
    # Collapse the whitespace a model sometimes emits around punctuation.
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()


def contains_markup(text: str) -> bool:
    """True if ``text`` carries LaTeX commands or math mode.

    Bullets that contain markup are reorder-only. Rewriting them would mean
    asking the model to reproduce formatting, which is precisely the
    responsibility this architecture keeps away from the model.
    """
    return bool(_COMMAND_RE.search(text) or _MATH_RE.search(text))


def is_plain_prose(text: str) -> bool:
    return not contains_markup(text)
