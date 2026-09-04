"""Tests for canonical artifact naming and path resolution."""

from __future__ import annotations

import pytest

from sdlicit.helpers.artifact_naming import (
    resolve_adr_meta,
    resolve_artifact_meta,
    resolve_bdd_meta,
    resolve_personas_meta,
    resolve_sow_meta,
    resolve_srs_meta,
    resolve_stories_meta,
)


def test_sow_meta_is_a_fixed_single_file():
    meta = resolve_sow_meta()
    assert meta.tag == "SOW"
    assert meta.filename == "sow.md"
    assert meta.relative_path == "sow.md"
    assert meta.artifact_type == "sow"


def test_srs_personas_stories_are_fixed_single_files():
    assert resolve_srs_meta().relative_path == "srs.md"
    assert resolve_personas_meta().relative_path == "personas.md"
    assert resolve_stories_meta().relative_path == "stories.md"


def test_adr_meta_starts_at_one_with_no_existing_dir(tmp_path):
    meta = resolve_adr_meta(tmp_path, "Use React for the frontend")
    assert meta.tag == "ADR-0001"
    assert meta.filename == "ADR-0001-use-react-for-the-frontend.md"
    assert meta.relative_path == "adr/ADR-0001-use-react-for-the-frontend.md"


def test_adr_meta_continues_the_existing_sequence(tmp_path):
    adr_dir = tmp_path / ".sdlicit" / "artifacts" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-0001-first.md").write_text("x", encoding="utf-8")
    (adr_dir / "ADR-0003-third.md").write_text("x", encoding="utf-8")

    meta = resolve_adr_meta(tmp_path, "Fourth decision")
    assert meta.tag == "ADR-0004"


def test_adr_meta_ignores_non_matching_filenames(tmp_path):
    adr_dir = tmp_path / ".sdlicit" / "artifacts" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-0002-second.md").write_text("x", encoding="utf-8")
    (adr_dir / "notes.md").write_text("x", encoding="utf-8")
    (adr_dir / "README.md").write_text("x", encoding="utf-8")

    meta = resolve_adr_meta(tmp_path, "Third decision")
    assert meta.tag == "ADR-0003"


def test_bdd_meta_slugifies_persona_name():
    meta = resolve_bdd_meta("Busy Research Coordinator")
    assert meta.filename == "busy-research-coordinator.feature"
    assert meta.relative_path == "bdd/busy-research-coordinator.feature"
    assert meta.tag == "BDD-busy-research-coordinator"


def test_resolve_artifact_meta_dispatches_by_type(tmp_path):
    assert resolve_artifact_meta("sow").artifact_type == "sow"
    assert resolve_artifact_meta("srs").artifact_type == "srs"
    assert resolve_artifact_meta("personas").artifact_type == "personas"
    assert resolve_artifact_meta("stories").artifact_type == "stories"
    assert resolve_artifact_meta("bdd", persona_name="Alex").artifact_type == "bdd"
    adr = resolve_artifact_meta("adr", project_dir=tmp_path, title="Pick a database")
    assert adr.artifact_type == "adr"


def test_resolve_artifact_meta_adr_without_project_dir_raises():
    with pytest.raises(ValueError, match="project_dir"):
        resolve_artifact_meta("adr", title="Missing project dir")


def test_resolve_artifact_meta_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown artifact type"):
        resolve_artifact_meta("not-a-real-type")  # type: ignore[arg-type]
