"""Tests for TraceGraph, built from artifact frontmatter on disk."""

from __future__ import annotations

from sdlicit.expansion_stage.traceability.trace_dag import TraceGraph


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_from_project_with_no_artifacts_dir_returns_empty_graph(tmp_path):
    graph = TraceGraph.from_project(tmp_path)
    assert graph.nodes == {}
    assert graph.edges == []


def test_from_project_builds_nodes_from_frontmatter(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001-use-jwt.md",
        "---\nid: ADR-0001\nstatus: accepted\n---\n\n# ADR-0001: Use JWT\n",
    )
    graph = TraceGraph.from_project(tmp_path)
    assert "ADR-0001" in graph.nodes
    node = graph.nodes["ADR-0001"]
    assert node.title == "ADR-0001: Use JWT"
    assert node.status == "accepted"
    assert node.artifact_type == "adr"


def test_from_project_builds_implements_supersedes_tested_by_edges(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0002.md",
        "---\nid: ADR-0002\nimplements: [REQ-AUTH-01]\nsupersedes: ADR-0001\n"
        "tested_by: [BDD-login]\n---\n\n# ADR-0002\n",
    )
    graph = TraceGraph.from_project(tmp_path)
    edge_types = {(e.source, e.target, e.edge_type) for e in graph.edges}
    assert ("ADR-0002", "REQ-AUTH-01", "IMPLEMENTS") in edge_types
    assert ("ADR-0002", "ADR-0001", "SUPERSEDES") in edge_types
    assert ("ADR-0002", "BDD-login", "TESTED_BY") in edge_types


def test_from_project_adds_bdd_feature_nodes_with_extracted_title(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "bdd" / "login.feature",
        "Feature: User login\n\n  Scenario: Successful login\n",
    )
    graph = TraceGraph.from_project(tmp_path)
    assert "login" in graph.nodes
    assert graph.nodes["login"].title == "User login"
    assert graph.nodes["login"].artifact_type == "bdd"


def test_from_project_feature_without_a_title_falls_back_to_filename(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "bdd" / "untitled.feature",
        "Scenario: something\n",
    )
    graph = TraceGraph.from_project(tmp_path)
    assert graph.nodes["untitled"].title == "untitled"


def test_downstream_and_upstream_and_impacted(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001.md",
        "---\nid: ADR-0001\nimplements: [REQ-01]\n---\n\n# ADR-0001\n",
    )
    graph = TraceGraph.from_project(tmp_path)

    downstream = graph.get_downstream("ADR-0001")
    assert len(downstream) == 1
    assert downstream[0].target == "REQ-01"

    upstream = graph.get_upstream("REQ-01")
    assert len(upstream) == 1
    assert upstream[0].source == "ADR-0001"

    # get_impacted walks backward: it answers "if REQ-01 changes, what
    # points at it and might now be stale", not "what does ADR-0001 point to".
    impacted = graph.get_impacted("REQ-01")
    assert "ADR-0001" in impacted
    assert graph.get_impacted("ADR-0001") == []  # nothing points at ADR-0001 here


def test_get_downstream_of_unknown_node_is_empty(tmp_path):
    graph = TraceGraph.from_project(tmp_path)
    assert graph.get_downstream("does-not-exist") == []
    assert graph.get_upstream("does-not-exist") == []


class _StubKB:
    def __init__(self, edges):
        self._edges = edges

    def extract_graph_edges(self):
        return self._edges


def test_merge_lightrag_edges_matches_only_exact_case_insensitive_names(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001.md",
        "---\nid: ADR-0001\n---\n\n# ADR-0001: Use JWT\n",
    )
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0002.md",
        "---\nid: ADR-0002\n---\n\n# ADR-0002: Use refresh tokens\n",
    )
    kb = _StubKB(
        [
            # Exact id match, case insensitive — should be added.
            {"source": "adr-0001", "target": "ADR-0002", "type": "TRACES_TO"},
            # No known node matches this name — should be dropped silently.
            {"source": "Some Unrelated Thing", "target": "ADR-0002", "type": "TRACES_TO"},
        ]
    )
    graph = TraceGraph.from_project_and_kb(tmp_path, kb=kb)
    edge_types = {(e.source, e.target, e.edge_type) for e in graph.edges}
    assert ("ADR-0001", "ADR-0002", "TRACES_TO") in edge_types
    assert len(graph.edges) == 1


def test_merge_lightrag_edges_deduplicates_against_frontmatter_edges(tmp_path):
    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001.md",
        "---\nid: ADR-0001\nimplements: [REQ-01]\n---\n\n# ADR-0001\n",
    )
    kb = _StubKB([{"source": "ADR-0001", "target": "REQ-01", "type": "IMPLEMENTS"}])
    graph = TraceGraph.from_project_and_kb(tmp_path, kb=kb)
    matching = [
        e
        for e in graph.edges
        if e.source == "ADR-0001" and e.target == "REQ-01" and e.edge_type == "IMPLEMENTS"
    ]
    assert len(matching) == 1  # not duplicated


def test_merge_lightrag_edges_handles_extraction_failure_gracefully(tmp_path):
    class _BrokenKB:
        def extract_graph_edges(self):
            raise RuntimeError("graph unavailable")

    _write(
        tmp_path / ".sdlicit" / "artifacts" / "adr" / "ADR-0001.md",
        "---\nid: ADR-0001\n---\n\n# ADR-0001\n",
    )
    graph = TraceGraph.from_project_and_kb(tmp_path, kb=_BrokenKB())
    assert "ADR-0001" in graph.nodes  # frontmatter parsing still succeeded
