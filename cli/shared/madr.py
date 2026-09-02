"""MADR 4.0.0 template rendering — CLI-side only.

Three template variants:
  minimal  — status+date frontmatter; Context, Considered Options,
              Decision Outcome, Consequences
  full     — YAML frontmatter + all optional sections
              (Decision Drivers, Pros and Cons, Confirmation, More Information)
  bare     — empty shell with all sections as placeholders
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date_type
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class AdrFields:
    title: str
    status: str = "proposed"
    date: str = ""
    template: str = "minimal"
    context: str = ""
    options_considered: list[str] = field(default_factory=list)
    chosen_option: str = ""
    decision: str = ""
    consequences_positive: list[str] = field(default_factory=list)
    consequences_negative: list[str] = field(default_factory=list)
    decision_drivers: list[str] = field(default_factory=list)
    decision_makers: str = ""
    consulted: str = ""
    informed: str = ""
    # Traceability fields
    adr_id: str = ""
    supersedes: str = ""
    implements: list[str] = field(default_factory=list)
    tested_by: list[str] = field(default_factory=list)


class ComposeResponse(NamedTuple):
    filename: str
    content: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _next_sequence(existing_files: list[str]) -> int:
    max_num = 0
    for name in existing_files:
        m = re.match(r"^(\d+)-", name)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return max_num + 1


def _today() -> str:
    return str(_date_type.today())


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_minimal(f: AdrFields) -> str:
    lines: list[str] = []
    lines.append("---")
    if f.adr_id:
        lines.append(f"id: {f.adr_id}")
    lines.append(f"status: {f.status}")
    lines.append(f"date: {f.date or _today()}")
    if f.supersedes:
        lines.append(f"supersedes: {f.supersedes}")
    if f.implements:
        lines.append(f"implements: [{', '.join(f.implements)}]")
    if f.tested_by:
        lines.append(f"tested_by: [{', '.join(f.tested_by)}]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {f.title}")
    lines.append("")
    lines.append("## Context and Problem Statement")
    lines.append("")
    lines.append(f.context or "")
    lines.append("")
    if f.options_considered:
        lines.append("## Considered Options")
        lines.append("")
        for opt in f.options_considered:
            lines.append(f"* {opt}")
        lines.append("")
    lines.append("## Decision Outcome")
    lines.append("")
    chosen = f.chosen_option or (
        f.options_considered[0] if f.options_considered else ""
    )
    justification = f.decision or ""
    if chosen:
        lines.append(f'Chosen option: "{chosen}", because {justification}')
    else:
        lines.append(justification)
    lines.append("")
    if f.consequences_positive or f.consequences_negative:
        lines.append("### Consequences")
        lines.append("")
        for c in f.consequences_positive:
            lines.append(f"* Good, because {c}")
        for c in f.consequences_negative:
            lines.append(f"* Bad, because {c}")
        lines.append("")
    return "\n".join(lines)


def _render_full(f: AdrFields) -> str:
    lines: list[str] = []
    lines.append("---")
    if f.adr_id:
        lines.append(f"id: {f.adr_id}")
    lines.append(f"status: {f.status}")
    lines.append(f"date: {f.date or _today()}")
    if f.supersedes:
        lines.append(f"supersedes: {f.supersedes}")
    if f.implements:
        lines.append(f"implements: [{', '.join(f.implements)}]")
    if f.tested_by:
        lines.append(f"tested_by: [{', '.join(f.tested_by)}]")
    lines.append(f"decision-makers: {f.decision_makers or ''}")
    lines.append(f"consulted: {f.consulted or ''}")
    lines.append(f"informed: {f.informed or ''}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {f.title}")
    lines.append("")
    lines.append("## Context and Problem Statement")
    lines.append("")
    lines.append(f.context or "")
    lines.append("")
    if f.decision_drivers:
        lines.append("## Decision Drivers")
        lines.append("")
        for d in f.decision_drivers:
            lines.append(f"* {d}")
        lines.append("")
    if f.options_considered:
        lines.append("## Considered Options")
        lines.append("")
        for opt in f.options_considered:
            lines.append(f"* {opt}")
        lines.append("")
    lines.append("## Decision Outcome")
    lines.append("")
    chosen = f.chosen_option or (
        f.options_considered[0] if f.options_considered else ""
    )
    justification = f.decision or ""
    if chosen:
        lines.append(f'Chosen option: "{chosen}", because {justification}')
    else:
        lines.append(justification)
    lines.append("")
    if f.consequences_positive or f.consequences_negative:
        lines.append("### Consequences")
        lines.append("")
        for c in f.consequences_positive:
            lines.append(f"* Good, because {c}")
        for c in f.consequences_negative:
            lines.append(f"* Bad, because {c}")
        lines.append("")
    lines.append("### Confirmation")
    lines.append("")
    lines.append(
        "<!-- Describe how compliance with this decision can be confirmed. -->"
    )
    lines.append("")
    if f.options_considered:
        lines.append("## Pros and Cons of the Options")
        lines.append("")
        for opt in f.options_considered:
            lines.append(f"### {opt}")
            lines.append("")
            lines.append("* Good, because")
            lines.append("* Neutral, because")
            lines.append("* Bad, because")
            lines.append("")
    lines.append("## More Information")
    lines.append("")
    return "\n".join(lines)


def _render_bare(f: AdrFields) -> str:
    return (
        "---\n"
        "id:\n"
        "status:\n"
        "date:\n"
        "supersedes:\n"
        "implements: []\n"
        "tested_by: []\n"
        "decision-makers:\n"
        "consulted:\n"
        "informed:\n"
        "---\n\n"
        f"# {f.title}\n\n"
        "## Context and Problem Statement\n\n\n\n"
        "## Decision Drivers\n\n"
        "* <!-- decision driver -->\n\n"
        "## Considered Options\n\n"
        "* <!-- option -->\n\n"
        "## Decision Outcome\n\n"
        'Chosen option: "", because\n\n'
        "### Consequences\n\n"
        "* Good, because\n"
        "* Bad, because\n\n"
        "### Confirmation\n\n\n\n"
        "## Pros and Cons of the Options\n\n"
        "### <!-- title of option -->\n\n"
        "* Good, because\n"
        "* Neutral, because\n"
        "* Bad, because\n\n"
        "## More Information\n\n"
    )


_RENDERERS = {
    "minimal": _render_minimal,
    "full": _render_full,
    "bare": _render_bare,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_madr(fields: AdrFields, existing_files: list[str]) -> ComposeResponse:
    """Render MADR 4.0.0 markdown. Pure function — no file I/O."""
    seq = _next_sequence(existing_files)
    num = str(seq).zfill(4)
    filename = f"{num}-{_slugify(fields.title)}.md"
    # Auto-assign ADR ID from sequence number if not set
    if not fields.adr_id:
        fields.adr_id = f"ADR-{num}"
    renderer = _RENDERERS.get(fields.template, _render_minimal)
    content = renderer(fields)
    return ComposeResponse(filename=filename, content=content)
