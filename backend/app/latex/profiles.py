"""Declarative recognition rules for resume templates.

The parser is template-agnostic: it knows how to find sections, entries and
bullets *given a profile*, but the mapping from LaTeX macros to those concepts
lives entirely in data. Supporting a new template is a new ``TemplateProfile``
entry, not a code change.

Detection is scored rather than first-match, so a document using Jake's macros
inside an otherwise generic layout still resolves to the specific profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["TemplateProfile", "PROFILES", "detect_profile", "get_profile"]


@dataclass(frozen=True, slots=True)
class EntryRule:
    """How to read one flavour of entry header (a role, degree or project)."""

    command: str
    arg_count: int
    label_args: tuple[int, ...] = (0,)
    """Which argument indices carry the human-readable title, in priority order."""


@dataclass(frozen=True, slots=True)
class TemplateProfile:
    name: str
    description: str

    section_commands: tuple[str, ...] = ("section",)
    section_arg_count: int = 1
    section_optional_arg: bool = False

    entry_rules: tuple[EntryRule, ...] = ()

    bullet_commands: tuple[str, ...] = ()
    """Commands whose sole argument is bullet text, e.g. a resume item macro."""

    bullet_list_environments: tuple[str, ...] = ("itemize",)
    """Environments in which a bare item marker introduces a bullet."""

    bullet_list_commands: tuple[str, ...] = ()
    """Paired start/end macros that stand in for a list environment."""

    signature_commands: tuple[str, ...] = ()
    """Macros whose presence strongly indicates this template."""

    signature_classes: tuple[str, ...] = ()
    """Document classes that indicate this template."""

    skills_section_hints: tuple[str, ...] = (
        "skill",
        "technolog",
        "technical",
        "languages",
        "tools",
        "competenc",
        "proficienc",
    )

    def all_entry_commands(self) -> tuple[str, ...]:
        return tuple(rule.command for rule in self.entry_rules)

    def entry_arg_counts(self) -> dict[str, int]:
        return {rule.command: rule.arg_count for rule in self.entry_rules}

    def rule_for(self, command: str) -> EntryRule | None:
        for rule in self.entry_rules:
            if rule.command == command:
                return rule
        return None


# --- Jake Gutierrez's template -------------------------------------------
# By a wide margin the most common LaTeX resume in circulation, and the one
# most likely to be uploaded first.
JAKES = TemplateProfile(
    name="jakes",
    description="Jake Gutierrez resume template (resumeSubheading / resumeItem)",
    section_commands=("section",),
    entry_rules=(
        EntryRule("resumeSubheading", 4, label_args=(0, 2)),
        EntryRule("resumeProjectHeading", 2, label_args=(0,)),
        EntryRule("resumeSubSubheading", 2, label_args=(0,)),
    ),
    bullet_commands=("resumeItem",),
    bullet_list_environments=("itemize",),
    bullet_list_commands=("resumeItemListStart", "resumeItemListEnd"),
    signature_commands=(
        "resumeSubheading",
        "resumeItem",
        "resumeItemListStart",
        "resumeSubHeadingListStart",
    ),
)

# --- moderncv -------------------------------------------------------------
MODERNCV = TemplateProfile(
    name="moderncv",
    description="moderncv class (cventry / cvitem)",
    section_commands=("section", "subsection"),
    entry_rules=(
        EntryRule("cventry", 6, label_args=(1, 2)),
        EntryRule("cvitem", 2, label_args=(0,)),
    ),
    bullet_commands=(),
    bullet_list_environments=("itemize",),
    signature_commands=("cventry", "cvitem", "cvlistitem"),
    signature_classes=("moderncv",),
)

# --- AltaCV ---------------------------------------------------------------
ALTACV = TemplateProfile(
    name="altacv",
    description="AltaCV class (cvevent / cvsection)",
    section_commands=("cvsection", "section"),
    entry_rules=(
        EntryRule("cvevent", 4, label_args=(0, 1)),
        EntryRule("cvachievement", 3, label_args=(1,)),
    ),
    bullet_commands=(),
    bullet_list_environments=("itemize",),
    signature_commands=("cvevent", "cvsection", "cvachievement"),
    signature_classes=("altacv",),
)

# --- Generic fallback -----------------------------------------------------
# Handles hand-rolled resumes: plain sections, itemize lists, bare items.
# Deliberately last and deliberately permissive.
GENERIC = TemplateProfile(
    name="generic",
    description="Plain sectioning commands with itemize lists",
    section_commands=("section", "subsection", "section*", "subsection*"),
    section_optional_arg=True,
    entry_rules=(
        EntryRule("subsection", 1, label_args=(0,)),
        EntryRule("textbf", 1, label_args=(0,)),
    ),
    bullet_commands=(),
    bullet_list_environments=("itemize", "enumerate", "description"),
    signature_commands=(),
)


PROFILES: tuple[TemplateProfile, ...] = (JAKES, MODERNCV, ALTACV, GENERIC)


def get_profile(name: str) -> TemplateProfile:
    for profile in PROFILES:
        if profile.name == name:
            return profile
    raise KeyError(f"unknown template profile: {name!r}")


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    profile: TemplateProfile
    score: int
    evidence: tuple[str, ...]


def score_profile(source: str, profile: TemplateProfile) -> ProfileMatch:
    """Score how strongly ``source`` looks like ``profile``."""
    evidence: list[str] = []
    score = 0

    for cls in profile.signature_classes:
        if f"documentclass" in source and cls in source:
            score += 8
            evidence.append(f"documentclass mentions {cls}")

    for command in profile.signature_commands:
        # A macro counts as evidence only where it is *used*, not merely
        # defined, so a template's own \newcommand block does not self-match.
        uses = source.count("\\" + command)
        definitions = source.count("\\newcommand{\\" + command) + source.count(
            "\\renewcommand{\\" + command
        )
        if uses - definitions > 0:
            score += 4
            evidence.append(f"uses \\{command}")

    for command in profile.entry_rules:
        if "\\" + command.command in source:
            score += 1

    if profile.name == "generic" and "\\section" in source:
        score += 1
        evidence.append("has sectioning commands")

    return ProfileMatch(profile=profile, score=score, evidence=tuple(evidence))


def detect_profile(source: str) -> ProfileMatch:
    """Pick the best-fitting profile for ``source``.

    Returns the generic profile when nothing scores, which keeps upload
    working for templates we have never seen -- the parse may find fewer
    structures, and the upload flow surfaces that count to the user for
    confirmation rather than failing outright.
    """
    matches = [score_profile(source, profile) for profile in PROFILES]
    matches.sort(key=lambda m: (m.score, m.profile.name != "generic"), reverse=True)
    best = matches[0]
    if best.score == 0:
        return ProfileMatch(profile=GENERIC, score=0, evidence=("no template signature found",))
    return best
