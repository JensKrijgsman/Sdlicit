"""Traceability graph store — builds a directed graph from artifact frontmatter.

The graph is built on-demand from the filesystem (.sdlicit/artifacts/).
Relationships are derived from YAML frontmatter fields:
  - implements: [REQ-01, REQ-02]  → ADR -[IMPLEMENTS]-> REQ
  - supersedes: ADR-001            → ADR -[SUPERSEDES]-> ADR
  - tested_by: [BDD-01]           → ADR -[TESTED_BY]-> BDD
  - traces_from / traces_to       → generic upstream/downstream

The graph is also enriched during ingestion: LightRAG's graph naturally
captures IMPLEMENTS/SUPERSEDES/TESTED_BY as entity relationships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sdlicit.helpers.parsers import parse_madr_content
from sdlicit.logging import get_logger

_log = get_logger("trace_graph")

EdgeType = Literal["IMPLEMENTS", "SUPERSEDES", "TESTED_BY", "TRACES_TO", "TRACES_FROM"]


@dataclass(frozen=True)
class TraceNode:
    """A node in the traceability graph."""

    id: str
    artifact_type: str  # sow, adr, srs, personas, stories, bdd
    title: str
    status: str
    file_path: str


@dataclass(frozen=True)
class TraceEdge:
    """A directed edge in the traceability graph."""

    source: str  # node id
    target: str  # node id
    edge_type: EdgeType


@dataclass
class TraceGraph:
    """In-memory traceability DAG built from artifact frontmatter.

    Provides graph traversal for impact analysis and UI rendering.
    """

    nodes: dict[str, TraceNode] = field(default_factory=dict)
    edges: list[TraceEdge] = field(default_factory=list)
    _adjacency: dict[str, list[TraceEdge]] = field(default_factory=dict)
    _reverse_adj: dict[str, list[TraceEdge]] = field(default_factory=dict)

    @classmethod
    def from_project(cls, project_dir: Path) -> TraceGraph:
        """Scan .sdlicit/artifacts/ and build the graph from frontmatter only."""
        artifacts_dir = project_dir / ".sdlicit" / "artifacts"
        graph = cls()
        if not artifacts_dir.exists():
            return graph

        for md_file in artifacts_dir.rglob("*.md"):
            graph._add_artifact_file(md_file, artifacts_dir)
        for feature_file in artifacts_dir.rglob("*.feature"):
            graph._add_feature_file(feature_file, artifacts_dir)

        graph._rebuild_adjacency()
        return graph

    @classmethod
    def from_project_and_kb(
        cls,
        project_dir: Path,
        kb: Any | None = None,
    ) -> TraceGraph:
        """Build the graph from frontmatter AND enrich with LightRAG edges.

        Uses two sources:
        1. YAML frontmatter from .sdlicit/artifacts/ (deterministic, always)
        2. LightRAG's entity-relation graph (if ``kb`` is provided)

        LightRAG edges are merged with frontmatter edges. Duplicates
        (same source-target-type triple) are deduplicated.

        Args:
            project_dir: Project root containing .sdlicit/artifacts/.
            kb: A :class:`KnowledgeBase` instance (optional). If provided,
                edges from LightRAG's graph are merged in.
        """
        graph = cls.from_project(project_dir)

        if kb is not None:
            graph._merge_lightrag_edges(kb)
            graph._rebuild_adjacency()

        return graph

    def _merge_lightrag_edges(self, kb: Any) -> None:
        """Merge edges extracted from LightRAG's NetworkX graph.

        Only adds edges that don't already exist (by source-target-type).
        LightRAG entity names are matched to known node IDs using
        case-insensitive substring matching.
        """
        try:
            lightrag_edges = kb.extract_graph_edges()
        except Exception as exc:
            _log.warning("Could not extract LightRAG edges: %s", exc)
            return

        if not lightrag_edges:
            return

        # Build lookup: existing edges as (source, target, type) set
        existing = {(e.source, e.target, e.edge_type) for e in self.edges}

        # Build node-name → id lookup (case-insensitive)
        name_to_id: dict[str, str] = {}
        for nid, node in self.nodes.items():
            name_to_id[nid.lower()] = nid
            # Also index by title words for fuzzy matching
            for word in node.title.lower().split():
                if len(word) > 3:
                    name_to_id[word] = nid

        added = 0
        for edge_data in lightrag_edges:
            source_raw = edge_data["source"].strip()
            target_raw = edge_data["target"].strip()
            edge_type = edge_data["type"]

            # Resolve to known node IDs
            source_id = self._resolve_node_id(source_raw, name_to_id)
            target_id = self._resolve_node_id(target_raw, name_to_id)

            if not source_id or not target_id:
                continue

            triple = (source_id, target_id, edge_type)
            if triple in existing:
                continue

            self.edges.append(
                TraceEdge(source=source_id, target=target_id, edge_type=edge_type)
            )
            existing.add(triple)
            added += 1

        if added:
            _log.info("Merged %d edges from LightRAG graph", added)

    @staticmethod
    def _resolve_node_id(raw_name: str, name_to_id: dict[str, str]) -> str | None:
        """Match a LightRAG entity name to a known node ID.

        Only exact case-insensitive match on node ID or full title.
        Substring matching is intentionally excluded to avoid false
        positives (e.g. "AUTH" matching "Authorization strategy").
        """
        lower = raw_name.strip().lower()
        # Exact match on lowercased node ID or title
        if lower in name_to_id:
            return name_to_id[lower]
        return None

    def _add_artifact_file(self, file_path: Path, base_dir: Path) -> None:
        """Parse an artifact markdown file and add its node + edges."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return

        parsed = parse_madr_content(content)
        rel_path = file_path.relative_to(base_dir)
        artifact_type = self._infer_type(file_path, rel_path)

        node_id = parsed.get("id") or file_path.stem
        title = parsed.get("title", "") or file_path.stem
        status = parsed.get("status", "draft")

        node = TraceNode(
            id=node_id,
            artifact_type=artifact_type,
            title=title,
            status=status,
            file_path=str(file_path),
        )
        self.nodes[node_id] = node

        # Parse edges from frontmatter
        supersedes = parsed.get("supersedes", "")
        if supersedes:
            self.edges.append(
                TraceEdge(source=node_id, target=supersedes, edge_type="SUPERSEDES")
            )

        implements = parsed.get("implements", []) or []
        if isinstance(implements, str):
            implements = [implements] if implements else []
        for req_id in implements:
            self.edges.append(
                TraceEdge(source=node_id, target=req_id, edge_type="IMPLEMENTS")
            )

        tested_by = parsed.get("tested_by", []) or []
        if isinstance(tested_by, str):
            tested_by = [tested_by] if tested_by else []
        for bdd_id in tested_by:
            self.edges.append(
                TraceEdge(source=node_id, target=bdd_id, edge_type="TESTED_BY")
            )

        # Generic traces
        traces_from = parsed.get("traces_from", "")
        if traces_from and isinstance(traces_from, str):
            for t in traces_from.split(","):
                t = t.strip()
                if t:
                    self.edges.append(
                        TraceEdge(source=t, target=node_id, edge_type="TRACES_TO")
                    )

        traces_to = parsed.get("traces_to", "")
        if traces_to and isinstance(traces_to, str):
            for t in traces_to.split(","):
                t = t.strip()
                if t:
                    self.edges.append(
                        TraceEdge(source=node_id, target=t, edge_type="TRACES_TO")
                        )

    def _add_feature_file(self, file_path: Path, base_dir: Path) -> None:
        """Add a BDD .feature file as a node."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return

        node_id = file_path.stem
        # Try to extract feature title
        title_match = re.search(r"^Feature:\s*(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else file_path.stem

        node = TraceNode(
            id=node_id,
            artifact_type="bdd",
            title=title,
            status="active",
            file_path=str(file_path),
        )
        self.nodes[node_id] = node

    def _rebuild_adjacency(self) -> None:
        """Build forward and reverse adjacency maps."""
        self._adjacency.clear()
        self._reverse_adj.clear()
        for edge in self.edges:
            self._adjacency.setdefault(edge.source, []).append(edge)
            self._reverse_adj.setdefault(edge.target, []).append(edge)

    def get_downstream(self, node_id: str) -> list[TraceEdge]:
        """Get all edges going OUT from this node."""
        return self._adjacency.get(node_id, [])

    def get_upstream(self, node_id: str) -> list[TraceEdge]:
        """Get all edges coming IN to this node (backlinks)."""
        return self._reverse_adj.get(node_id, [])

    def get_impacted(self, node_id: str) -> list[str]:
        """BFS: find all nodes that transitively depend on node_id.

        Used for impact analysis when an artifact changes.
        """
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            # Nodes that reference current as a target
            for edge in self._reverse_adj.get(current, []):
                if edge.source not in visited:
                    queue.append(edge.source)
        visited.discard(node_id)  # Don't include self
        return list(visited)

    def get_stale_nodes(self, changed_node_id: str) -> list[TraceNode]:
        """Get nodes that are impacted (potentially stale) due to a change."""
        impacted_ids = self.get_impacted(changed_node_id)
        return [self.nodes[nid] for nid in impacted_ids if nid in self.nodes]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict for the REST API."""
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.artifact_type,
                    "title": n.title,
                    "status": n.status,
                    "filePath": n.file_path,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "type": e.edge_type,
                }
                for e in self.edges
            ],
        }

    @staticmethod
    def _infer_type(file_path: Path, rel_path: Path) -> str:
        """Infer artifact type from directory structure."""
        parts = rel_path.parts
        if parts:
            first_dir = parts[0].lower()
            if "adr" in first_dir or "decision" in first_dir:
                return "adr"
            if "srs" in first_dir or "requirement" in first_dir:
                return "srs"
            if "sow" in first_dir:
                return "sow"
            if "persona" in first_dir:
                return "personas"
            if "stor" in first_dir:
                return "stories"
            if "bdd" in first_dir or "scenario" in first_dir or "gherkin" in first_dir:
                return "bdd"
        return "unknown"
