"""Tests for structure aware KB ingestion chunkers."""

from __future__ import annotations

from sdlicit.helpers.chunkers import (
    ADRChunker,
    MarkdownSectionChunker,
    StoryChunker,
    get_chunker,
)


def test_markdown_section_chunker_splits_on_h2():
    text = "# Title\nintro\n\n## First\nbody one\n\n## Second\nbody two\n"
    chunks = MarkdownSectionChunker(label="SOW").chunk(text, "sow.md")
    headings = [c.file_path.rsplit("-", 1)[-1] for c in chunks]
    assert len(chunks) == 3  # preamble + First + Second
    assert "first" in headings
    assert "second" in headings
    assert chunks[0].file_path.startswith("sow.md#")


def test_markdown_section_chunker_treats_headingless_text_as_one_preamble_chunk():
    # _split_on_h2 always returns at least a "preamble" part when the text
    # is non blank, even with zero "## " headings, so this is one chunk
    # labelled preamble, not a literal unlabelled passthrough.
    text = "Just a flat paragraph, no headings at all."
    chunks = MarkdownSectionChunker().chunk(text, "sow.md")
    assert len(chunks) == 1
    assert text in chunks[0].text
    assert chunks[0].file_path == "sow.md#1-preamble"


def test_markdown_section_chunker_falls_back_to_whole_document_when_blank():
    chunks = MarkdownSectionChunker().chunk("   \n\n  ", "sow.md")
    assert len(chunks) == 1
    assert chunks[0].file_path == "sow.md"


def test_adr_chunker_emits_a_meta_chunk_with_relationship_edges():
    text = (
        "---\n"
        "id: ADR-0002\n"
        "status: accepted\n"
        "implements: [REQ-AUTH-01]\n"
        "supersedes: ADR-0001\n"
        "tested_by: [BDD-login]\n"
        "---\n\n"
        "# ADR-0002: Use JWT\n\n"
        "## Context and Problem Statement\n\nNeed stateless sessions.\n"
    )
    chunks = ADRChunker().chunk(text, "adr/ADR-0002-use-jwt.md")
    meta_chunk = next(c for c in chunks if c.file_path.endswith("#0-meta"))
    assert "ADR-0002 IMPLEMENTS REQ-AUTH-01" in meta_chunk.text
    assert "ADR-0002 SUPERSEDES ADR-0001" in meta_chunk.text
    assert "ADR-0002 TESTED_BY BDD-login" in meta_chunk.text


def test_adr_chunker_emits_one_chunk_per_section():
    text = (
        "---\nid: ADR-0003\n---\n\n# ADR-0003: Pick a queue\n\n"
        "## Context and Problem Statement\n\nNeed async processing.\n\n"
        "## Decision Outcome\n\nUse a message queue.\n"
    )
    chunks = ADRChunker().chunk(text, "adr/ADR-0003.md")
    section_chunks = [c for c in chunks if not c.file_path.endswith("#0-meta")]
    assert len(section_chunks) == 2
    assert any("Need async processing" in c.text for c in section_chunks)
    assert any("Use a message queue" in c.text for c in section_chunks)


def test_adr_chunker_with_no_frontmatter_still_emits_a_meta_chunk_from_the_filename():
    # adr_id falls back to the filename stem, so a meta chunk (title + ID
    # line) is always produced, even with no frontmatter and no sections
    # at all. There is no real world input that reaches the raw text
    # passthrough branch, this pins that down rather than assuming it.
    text = "plain text with no frontmatter and no h2 sections"
    chunks = ADRChunker().chunk(text, "adr/ADR-0004.md")
    assert len(chunks) == 1
    assert chunks[0].file_path == "adr/ADR-0004.md#0-meta"
    assert "ID: ADR-0004" in chunks[0].text


def test_story_chunker_splits_on_story_id_headings():
    text = (
        "## US-1: Login\nAs a user I want to log in.\n\n"
        "## US-2: Logout\nAs a user I want to log out.\n"
    )
    chunks = StoryChunker().chunk(text, "stories.md")
    assert len(chunks) == 2
    assert any("US-1" in c.text for c in chunks)
    assert any("US-2" in c.text for c in chunks)


def test_story_chunker_falls_back_to_markdown_sections_without_story_ids():
    text = "## Onboarding\nSome onboarding story text.\n"
    chunks = StoryChunker().chunk(text, "stories.md")
    assert len(chunks) == 1
    assert "[Story / Onboarding]" in chunks[0].text


def test_get_chunker_returns_registered_chunker_for_known_types():
    assert isinstance(get_chunker("adr"), ADRChunker)
    assert isinstance(get_chunker("ADR"), ADRChunker)  # case insensitive


def test_get_chunker_falls_back_to_generic_for_unknown_types():
    chunker = get_chunker("something-nobody-registered")
    text = "## Section\nbody\n"
    chunks = chunker.chunk(text, "unknown.md")
    assert len(chunks) == 1
    assert chunks[0].file_path.startswith("unknown.md#")
