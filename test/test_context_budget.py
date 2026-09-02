"""Tests for bounded, ranked prior artifact context assembly."""

from __future__ import annotations

from sdlicit.helpers.context_budget import (
    assemble_context,
    context_budget_tokens,
    parse_adr_records,
    rank_prior_adrs,
)


def _adr(id_: str, title: str, implements: list[str] | None = None, body: str = "") -> str:
    impl = ", ".join(implements or [])
    filler = body or ("Lorem ipsum dolor sit amet. " * 20)
    return (
        f"---\nid: {id_}\nstatus: accepted\nimplements: [{impl}]\ntested_by: []\n---\n\n"
        f"# {id_}: {title}\n\n"
        f"## Context\n{filler}\n\n"
        f"## Decision\nDecided to {title.lower()}.\n\n"
        f"## Alternatives Considered\n- Option A\n- Option B\n\n"
        f"## Consequences\nPositive:\n- Works well.\n"
    )


def test_parse_adr_records_extracts_id_title_implements() -> None:
    content = _adr("ADR-0003", "Use Gradle as build tool", implements=["REQ-FUNC-01", "REQ-FUNC-02"])
    [record] = parse_adr_records([content])
    assert record.id == "ADR-0003"
    assert record.title == "ADR-0003: Use Gradle as build tool"
    assert record.implements == ("REQ-FUNC-01", "REQ-FUNC-02")
    assert record.sequence == 3


def test_rank_prior_adrs_prefers_requirement_overlap() -> None:
    unrelated = _adr("ADR-0001", "Use SLF4J for logging", implements=["REQ-FUNC-09"])
    related = _adr("ADR-0002", "Use H2 as internal database", implements=["REQ-FUNC-05"])
    records = parse_adr_records([unrelated, related])

    topic = "Choose a database migration tool for REQ-FUNC-05 storage requirements"
    ranked = rank_prior_adrs(topic, records)

    assert ranked[0].id == "ADR-0002"


def test_rank_prior_adrs_breaks_ties_by_recency_not_file_order() -> None:
    same_body = "Identical context and decision text so word overlap scores tie exactly."
    older = _adr("ADR-0001", "Build tool choice", body=same_body)
    newer = _adr("ADR-0009", "Build tool choice", body=same_body)
    # Oldest-first input order, identical text on both -> recency must decide.
    records = parse_adr_records([older, newer])

    ranked = rank_prior_adrs("build tool choice", records)

    assert ranked[0].id == "ADR-0009"


def test_rank_prior_adrs_respects_limit() -> None:
    records = parse_adr_records([_adr(f"ADR-{i:04d}", f"Decision {i}") for i in range(1, 21)])
    ranked = rank_prior_adrs("decision", records, limit=5)
    assert len(ranked) == 5


def test_assemble_context_keeps_full_text_under_budget() -> None:
    records = parse_adr_records([_adr("ADR-0001", "Small one", body="Short context.")])
    text, dropped = assemble_context(records, token_budget=5000)
    assert dropped == 0
    assert "Short context." in text


def test_assemble_context_summarises_once_over_budget() -> None:
    big = [_adr(f"ADR-{i:04d}", f"Decision {i}") for i in range(1, 6)]
    records = parse_adr_records(big)
    # Budget fits roughly one full ADR, not five.
    text, _dropped = assemble_context(records, token_budget=200)
    assert text  # something was kept
    full_form_count = text.count("## Context")
    assert 0 <= full_form_count < len(records)  # not every record kept its full sections


def test_assemble_context_reports_dropped_count_never_silent() -> None:
    records = parse_adr_records([_adr(f"ADR-{i:04d}", f"Decision {i}") for i in range(1, 51)])
    text, dropped = assemble_context(records, token_budget=50)
    assert dropped > 0


def test_assemble_context_empty_input() -> None:
    text, dropped = assemble_context([], token_budget=1000)
    assert text == ""
    assert dropped == 0


def test_context_budget_tokens_floors_degenerate_values() -> None:
    assert context_budget_tokens(0, 0.4) == 500
    assert context_budget_tokens(8192, 0.4) == 3276
