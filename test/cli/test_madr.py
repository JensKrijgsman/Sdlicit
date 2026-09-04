"""Tests for MADR template rendering (cli/shared/madr.py)."""

from __future__ import annotations

from shared.madr import AdrFields, render_madr


def test_render_madr_assigns_sequence_and_slug_from_existing_files():
    fields = AdrFields(title="Use PostgreSQL for storage")
    resp = render_madr(fields, existing_files=["0001-first.md", "0002-second.md"])

    assert resp.filename == "0003-use-postgresql-for-storage.md"
    assert fields.adr_id == "ADR-0003"  # auto-assigned from the sequence number


def test_render_madr_starts_sequence_at_one_when_no_existing_files():
    fields = AdrFields(title="Adopt event sourcing")
    resp = render_madr(fields, existing_files=[])
    assert resp.filename == "0001-adopt-event-sourcing.md"
    assert fields.adr_id == "ADR-0001"


def test_render_madr_keeps_explicit_adr_id():
    fields = AdrFields(title="X", adr_id="ADR-0099")
    render_madr(fields, existing_files=[])
    assert fields.adr_id == "ADR-0099"  # not overwritten by the sequence number


def test_minimal_template_includes_frontmatter_and_sections():
    fields = AdrFields(
        title="Choose primary datastore",
        template="minimal",
        context="We need durable storage.",
        options_considered=["Postgres", "SQLite"],
        chosen_option="Postgres",
        decision="it scales with our concurrency needs.",
        consequences_positive=["strong consistency"],
        consequences_negative=["ops overhead"],
        implements=["REQ-01", "REQ-02"],
        tested_by=["BDD-01"],
    )
    resp = render_madr(fields, existing_files=[])
    content = resp.content

    assert "implements: [REQ-01, REQ-02]" in content
    assert "tested_by: [BDD-01]" in content
    assert "## Considered Options" in content
    assert "* Postgres" in content
    assert 'Chosen option: "Postgres", because it scales with our concurrency needs.' in content
    assert "* Good, because strong consistency" in content
    assert "* Bad, because ops overhead" in content
    # minimal never renders decision-drivers/confirmation/pros-and-cons sections
    assert "## Decision Drivers" not in content
    assert "### Confirmation" not in content


def test_full_template_adds_decision_makers_and_pros_and_cons():
    fields = AdrFields(
        title="Choose primary datastore",
        template="full",
        options_considered=["Postgres"],
        decision_makers="Alice, Bob",
    )
    content = render_madr(fields, existing_files=[]).content

    assert "decision-makers: Alice, Bob" in content
    assert "### Confirmation" in content
    assert "## Pros and Cons of the Options" in content
    assert "### Postgres" in content


def test_bare_template_is_an_empty_placeholder_shell():
    fields = AdrFields(title="Untitled decision", template="bare")
    content = render_madr(fields, existing_files=[]).content

    assert "# Untitled decision" in content
    assert 'Chosen option: "", because' in content
    assert "id:\n" in content  # frontmatter keys present but empty


def test_unknown_template_falls_back_to_minimal():
    fields = AdrFields(title="X", template="does-not-exist")
    content = render_madr(fields, existing_files=[]).content
    assert "## Context and Problem Statement" in content
    assert "### Confirmation" not in content  # minimal, not full


def test_slugify_strips_punctuation_and_collapses_separators():
    fields = AdrFields(title="Use Redis: caching & pub/sub!!")
    resp = render_madr(fields, existing_files=[])
    assert resp.filename == "0001-use-redis-caching-pub-sub.md"


def test_sequence_ignores_files_without_a_leading_number():
    fields = AdrFields(title="Next")
    resp = render_madr(fields, existing_files=["README.md", "0007-something.md"])
    assert resp.filename.startswith("0008-")
