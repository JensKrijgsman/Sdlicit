"""MADR content parsers — extract structured data from markdown.

These are used by agents and services to reason over existing ADRs
without requiring the frontend to send pre-parsed data.
"""

from __future__ import annotations

import re
from typing import Any


def _parse_yaml_value(raw: str) -> str | list[str]:
    """Parse a YAML value that may be a scalar or inline list.

    Handles: ``[REQ-01, REQ-02]`` → list, plain string → str.
    """
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        if not inner.strip():
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    return raw


def parse_madr_content(content: str) -> dict[str, Any]:
    """Parse MADR 4.0.0 markdown into a structured dict.

    Returns a dict with keys: id, title, status, date, supersedes,
    implements, tested_by, sections (dict of heading → list[str]).
    Handles both YAML frontmatter and legacy bullet-metadata formats.
    """
    lines = content.splitlines()
    result: dict[str, Any] = {
        "id": "",
        "title": "",
        "status": "",
        "date": "",
        "supersedes": "",
        "implements": [],
        "tested_by": [],
        "sections": {},
    }

    body_start = 0

    # YAML frontmatter
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body_start = i + 1
                break
            m = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
            if m:
                key = m.group(1)
                val = _parse_yaml_value(m.group(2))
                # Normalise known list fields
                if key in ("implements", "tested_by"):
                    if isinstance(val, str):
                        result[key] = [val] if val else []
                    else:
                        result[key] = val
                else:
                    result[key] = val if isinstance(val, str) else val

    # Title — first H1
    for line in lines[body_start:]:
        if line.startswith("# "):
            result["title"] = line[2:].strip()
            break

    # H2 sections
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[body_start:]:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    for v in sections.values():
        while v and not v[-1].strip():
            v.pop()

    result["sections"] = sections
    return result


def summarise_adr(content: str) -> str:
    """Return a compact one-paragraph summary of an ADR.

    Useful for stuffing prior ADRs into an agent's context window.
    """
    data = parse_madr_content(content)
    title = data.get("title", "Untitled")
    status = data.get("status", "unknown")
    sections = data.get("sections", {})

    context_lines = sections.get("Context and Problem Statement", [])
    context = " ".join(l.strip() for l in context_lines if l.strip())[:200]

    outcome_lines = sections.get("Decision Outcome", [])
    outcome = " ".join(
        l.strip() for l in outcome_lines if l.strip() and not l.startswith("#")
    )[:200]

    return f"[{status}] {title}: {context} → {outcome}"
