"""pdfTeX compatibility shim for the XeTeX-based Tectonic backend.

Tectonic runs XeTeX, which does not implement pdfTeX's primitives. That
matters more than it sounds, because the most widely used LaTeX resume
template in circulation calls ``\\pdfglyphtounicode`` -- via
``\\input{glyphtounicode}`` -- specifically to make its output ATS-parsable.
Without a shim, the single most likely upload fails to compile.

The shim is applied **only to the temporary copy handed to the compiler**. The
stored and delivered ``.tex`` is never modified, for two reasons: the
round-trip guarantee says we hand back the user's own file, and their file is
correct as written -- it targets pdflatex, where these primitives exist. Our
compile is a preview and a validation step, not a rewrite.

Every definition is guarded, so a real primitive is never shadowed and the
shim becomes inert the moment it runs under an engine that has them.
"""

from __future__ import annotations

import re

from .scanner import build_mask, find_command, read_arguments

__all__ = ["PDFTEX_SHIM", "needs_shim", "apply_shim"]

PDFTEX_SHIM = r"""
%%% ResumeForge: pdfTeX compatibility shim (compile-time only, not saved).
%%% Every definition is guarded and becomes inert under pdflatex.
\makeatletter
\ifdefined\pdfglyphtounicode\else\providecommand\pdfglyphtounicode[2]{}\fi
\ifdefined\pdfgentounicode\else\newcount\pdfgentounicode\fi
\ifdefined\pdfcompresslevel\else\newcount\pdfcompresslevel\fi
\ifdefined\pdfobjcompresslevel\else\newcount\pdfobjcompresslevel\fi
\ifdefined\pdfminorversion\else\newcount\pdfminorversion\fi
\ifdefined\pdfsuppresswarningpagegroup\else\newcount\pdfsuppresswarningpagegroup\fi
\ifdefined\pdfinfo\else\providecommand\pdfinfo[1]{}\fi
\ifdefined\pdfcatalog\else\providecommand\pdfcatalog[1]{}\fi
\ifdefined\pdfstringdef\else\providecommand\pdfstringdef[2]{}\fi
\makeatother
%%% end shim
"""

# Primitives that appear in real-world resume templates and are pdfTeX-only.
_PDFTEX_MARKERS = re.compile(
    r"\\(?:pdfglyphtounicode|pdfgentounicode|pdfcompresslevel"
    r"|pdfobjcompresslevel|pdfminorversion|pdfinfo|pdfcatalog"
    r"|input\s*\{\s*glyphtounicode\s*\})"
)


def needs_shim(source: str) -> bool:
    """True if ``source`` uses a pdfTeX primitive XeTeX lacks."""
    return bool(_PDFTEX_MARKERS.search(source))


def apply_shim(source: str) -> str:
    """Return ``source`` with the shim inserted after the document class.

    Placement matters: the shim must precede ``\\input{glyphtounicode}``, which
    templates put in the preamble, but must follow ``\\documentclass``, since
    nothing may come between the class declaration and the start of the file.
    """
    if not needs_shim(source):
        return source

    mask = build_mask(source)
    offset = find_command(source, "documentclass", 0, mask)
    if offset == -1:
        return PDFTEX_SHIM + source

    _, after = read_arguments(
        source, offset + len("\\documentclass"), 1, mask, optional_first=True
    )
    return source[:after] + "\n" + PDFTEX_SHIM + source[after:]
