"""Tests for TraceService's structural analysis and implements detection.

Only exercises paths that need no LLM and no live KB: structural mode
(pure filesystem + regex) and detect_implements (TF-IDF/Jaccard over
local text, no network).
"""

from __future__ import annotations

from sdlicit.expansion_stage.traceability.trace_service import TraceService


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_workspace(tmp_path):
    # _extract_docs_requirements (used by detect_implements) only recognises
    # a requirement as its own "## REQ-ID" header block, or a "- [REQ-ID]"
    # bullet, not a bare inline mention, so the SRS fixture must use one of
    # those two forms, not free running prose.
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "srs.md",
        "# SRS\n\n"
        "## REQ-AUTH-01\n\nUsers must be able to log in.\n\n"
        "## REQ-AUTH-02\n\nUsers must be able to log out.\n",
    )
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001-jwt.md",
        "---\nid: ADR-0001\nimplements: [REQ-AUTH-01]\n---\n\n# ADR-0001: Use JWT\n",
    )
    return tmp_path


def test_structural_analysis_runs_without_an_llm_or_kb(tmp_path):
    workspace = _seed_workspace(tmp_path)
    service = TraceService(default_mode="structural")
    result = service.analyse(workspace)
    assert result.mode == "structural"


def test_structural_analysis_finds_the_declared_requirements(tmp_path):
    workspace = _seed_workspace(tmp_path)
    service = TraceService(default_mode="structural")
    reqs = service._parse_requirements(workspace)
    assert "REQ-AUTH-01" in reqs
    assert "REQ-AUTH-02" in reqs


def test_analyse_artifact_returns_empty_coverage_for_unknown_id(tmp_path):
    workspace = _seed_workspace(tmp_path)
    service = TraceService(default_mode="structural")
    coverage = service.analyse_artifact("ADR-9999-does-not-exist", workspace)
    assert coverage.artifact_id == "ADR-9999-does-not-exist"
    assert coverage.artifact_type == "unknown"


def test_detect_implements_finds_explicit_req_id_mentions(tmp_path):
    workspace = _seed_workspace(tmp_path)
    service = TraceService(default_mode="structural")
    text = "This ADR is about REQ-AUTH-02 and how sessions end."
    matched = service.detect_implements(text, workspace)
    assert "REQ-AUTH-02" in matched


def test_detect_implements_returns_empty_when_srs_has_no_requirements(tmp_path):
    _write(tmp_path / ".sdlicit" / "artifacts" / "srs.md", "# SRS\n\nNo requirements yet.\n")
    service = TraceService(default_mode="structural")
    matched = service.detect_implements("Some ADR text about logging in.", tmp_path)
    assert matched == []


def test_detect_implements_returns_empty_without_an_srs_file(tmp_path):
    service = TraceService(default_mode="structural")
    matched = service.detect_implements("Some ADR text.", tmp_path)
    assert matched == []
