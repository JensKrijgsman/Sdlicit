"""Expansion stage tools — KB query and traceability tools.

These functions are designed to work in both modes:
1. Direct call from Python code (current pipeline pattern)
2. Wrapped as ``dspy.Tool(func)`` for agentic/ReAct mode

Each function has clear input/output types and a descriptive docstring
that DSPy uses as the tool description for the LLM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdlicit.expansion_stage.tools.kb_router import KBRouter
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase


async def query_knowledge_base(
    query: str,
    *,
    kb: KnowledgeBase | None = None,
    kb_router: KBRouter | None = None,
    mode: str = "hybrid",
) -> str:
    """Search the knowledge base for relevant context.

    Queries the project's knowledge base (LightRAG) for documents
    matching the query. Returns concatenated text chunks.

    Args:
        query: Natural language search query.
        kb: KnowledgeBase instance (optional if kb_router provided).
        kb_router: Store-aware router (preferred over raw KB).
        mode: Search mode — 'hybrid', 'local', or 'global'.

    Returns:
        Concatenated relevant text chunks separated by newlines.
    """
    if kb_router is not None:
        chunks = await kb_router.query(query, probe_first=True)
        return "\n\n".join(c.text for c in chunks if c.text)
    elif kb is not None:
        chunks = await kb.query(query, mode=mode)
        return "\n\n".join(c.text for c in chunks if c.text)
    return ""


def check_traceability(
    artifact_id: str,
    artifact_type: str,
    project_dir: str,
) -> str:
    """Check traceability links for a specific artifact.

    Validates that the artifact has proper upstream/downstream links
    in the traceability graph. Returns a summary of link status.

    Args:
        artifact_id: The artifact identifier (e.g. 'ADR-001', 'REQ-03').
        artifact_type: Type of artifact ('adr', 'sow', 'srs', 'bdd').
        project_dir: Path to the project root.

    Returns:
        Human-readable summary of traceability status for this artifact.
    """
    from pathlib import Path

    from sdlicit.expansion_stage.traceability.trace_dag import TraceGraph

    graph = TraceGraph.from_project(Path(project_dir))

    node = graph.nodes.get(artifact_id)
    if node is None:
        return f"Artifact {artifact_id} not found in traceability graph."

    upstream = graph.get_upstream(artifact_id)
    downstream = graph.get_downstream(artifact_id)

    lines = [f"Artifact: {artifact_id} ({artifact_type})"]
    lines.append(f"  Upstream links: {len(upstream)}")
    for edge in upstream:
        lines.append(f"    ← {edge.source} [{edge.edge_type}]")
    lines.append(f"  Downstream links: {len(downstream)}")
    for edge in downstream:
        lines.append(f"    → {edge.target} [{edge.edge_type}]")

    if not upstream and artifact_type != "sow":
        lines.append("  ⚠ WARNING: No upstream links (potential orphan)")

    return "\n".join(lines)
