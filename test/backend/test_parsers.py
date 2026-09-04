"""Tests for MADR content parsing."""

from __future__ import annotations

from sdlicit.helpers.parsers import parse_madr_content, summarise_adr

MADR_SAMPLE = """---
id: ADR-0001
status: accepted
date: 2026-01-01
implements: [REQ-AUTH-01, REQ-AUTH-02]
tested_by: [BDD-login]
---

# ADR-0001: Use JWT for session tokens

## Context and Problem Statement

The API needs a stateless session mechanism.
It must work across multiple backend instances.

## Considered Options

- Server side sessions
- JWT

## Decision Outcome

Chosen option: JWT, because it is stateless.

### Consequences

Positive: no shared session store needed.

"""


def test_parses_frontmatter_scalars_and_lists():
    result = parse_madr_content(MADR_SAMPLE)
    assert result["id"] == "ADR-0001"
    assert result["status"] == "accepted"
    assert result["date"] == "2026-01-01"
    assert result["implements"] == ["REQ-AUTH-01", "REQ-AUTH-02"]
    assert result["tested_by"] == ["BDD-login"]


def test_extracts_title_from_first_h1():
    result = parse_madr_content(MADR_SAMPLE)
    assert result["title"] == "ADR-0001: Use JWT for session tokens"


def test_splits_h2_sections():
    result = parse_madr_content(MADR_SAMPLE)
    sections = result["sections"]
    assert "Context and Problem Statement" in sections
    assert "Considered Options" in sections
    assert "Decision Outcome" in sections
    assert any("stateless" in line for line in sections["Context and Problem Statement"])


def test_trailing_blank_lines_are_stripped_from_sections():
    result = parse_madr_content(MADR_SAMPLE)
    decision = result["sections"]["Decision Outcome"]
    assert decision[-1].strip() != ""


def test_h3_subsections_stay_inside_the_enclosing_h2():
    result = parse_madr_content(MADR_SAMPLE)
    decision = result["sections"]["Decision Outcome"]
    assert any("### Consequences" in line for line in decision)


def test_single_implements_string_is_normalised_to_a_list():
    content = "---\nimplements: REQ-01\n---\n\n# Title\n"
    result = parse_madr_content(content)
    assert result["implements"] == ["REQ-01"]


def test_no_frontmatter_still_parses_title_and_sections():
    content = "# Plain ADR\n\n## Context and Problem Statement\n\nSome context.\n"
    result = parse_madr_content(content)
    assert result["id"] == ""
    assert result["title"] == "Plain ADR"
    # The blank line right after the "## " heading is kept as part of the
    # section body (only trailing blanks are stripped, not leading ones).
    assert "Some context." in result["sections"]["Context and Problem Statement"]


def test_empty_content_returns_empty_defaults():
    result = parse_madr_content("")
    assert result["title"] == ""
    assert result["sections"] == {}
    assert result["implements"] == []


def test_summarise_adr_includes_status_title_context_and_outcome():
    summary = summarise_adr(MADR_SAMPLE)
    assert summary.startswith("[accepted] ADR-0001: Use JWT for session tokens:")
    assert "stateless session mechanism" in summary
    assert "JWT, because it is stateless" in summary


def test_summarise_adr_handles_missing_sections_gracefully():
    content = "---\nstatus: draft\n---\n\n# Untitled decision\n"
    summary = summarise_adr(content)
    assert summary == "[draft] Untitled decision:  → "
