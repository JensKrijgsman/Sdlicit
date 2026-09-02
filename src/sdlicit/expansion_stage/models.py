"""Pydantic models for the expansion stage API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExpandRequest(BaseModel):
    """Submit a completed ADR for multi-agent review.

    The backend reads file contents from project_dir — no file data over HTTP.
    """

    adr_filename: str = Field(
        ..., description="Filename of the ADR to expand (in .sdlicit/artifacts/adr/)"
    )
    project_dir: str = Field(
        ..., description="Project root — backend reads ADR files from disk"
    )
    session_id: str = Field(
        default="", description="Session identifier for ToM tracking"
    )


class AgentReview(BaseModel):
    """One agent's review output."""

    agent_name: str
    summary: str
    suggestions: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ExpandResponse(BaseModel):
    """Aggregated expansion output — all agent reviews + ToM verdict."""

    reviews: list[AgentReview] = Field(default_factory=list)
    tom_verdict: str = Field(default="", description="ToM agent's final recommendation")
    enhanced_adr: str = Field(
        default="", description="Optionally rewritten ADR (if applicable)"
    )


class KBQueryRequest(BaseModel):
    """Query the knowledge base."""

    query: str = Field(..., description="Natural-language question")
    mode: str = Field(
        default="hybrid",
        description="Retrieval mode: naive, local, global, hybrid, mix",
    )


class KBChunk(BaseModel):
    """One retrieved chunk from the knowledge base."""

    text: str = ""
    source: str = ""
    relevance: float = 0.0


class KBQueryResponse(BaseModel):
    """Knowledge base query results."""

    results: list[KBChunk] = Field(default_factory=list)
    rag_enabled: bool = Field(
        default=True, description="Whether RAG is active on the server"
    )


class RAGQueryRequest(BaseModel):
    """Store-aware RAG query request."""

    query: str = Field(..., description="Natural-language question")
    store: str = Field(
        default="all",
        description="Store to search: 'knowledge', 'artifacts', or 'all'",
    )
    mode: str = Field(
        default="hybrid",
        description="Retrieval mode: naive, local, global, hybrid, mix",
    )
    probe_first: bool = Field(
        default=False,
        description="Check graph entities before full retrieval",
    )
    top_k: int = Field(default=5, description="Max number of results")


class RAGChunk(BaseModel):
    """One retrieved chunk from the store-aware RAG query."""

    text: str = ""
    source: str = ""
    relevance: float = 0.0
    mode: str = "hybrid"
    store: str = "all"


class RAGQueryResponse(BaseModel):
    """Store-aware RAG query results."""

    results: list[RAGChunk] = Field(default_factory=list)
    rag_enabled: bool = Field(
        default=True, description="Whether RAG is active on the server"
    )
    probed: bool = Field(default=False, description="Whether graph probe was used")
    store: str = Field(default="all", description="Store that was queried")


class KBIngestRequest(BaseModel):
    """Trigger knowledge base ingestion from the project directory.

    The backend reads files directly — no document contents sent over HTTP.
    """

    project_dir: str = Field(
        ...,
        description="Project root to scan for documents",
    )
    selected_files: list[str] | None = Field(
        default=None,
        description="Relative paths of files to ingest. If None, ingest all.",
    )


class KBIngestResponse(BaseModel):
    """Result of a knowledge base ingestion."""

    ingested: int = Field(default=0, description="Number of chunks ingested")
    total_chunks: int = Field(default=0, description="Total chunks found")
    errors: list[str] = Field(default_factory=list)


class KBScanResponse(BaseModel):
    """Result of scanning a project directory for ingestible documents."""

    documents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of found documents with path, size, suffix, ingestion_status",
    )
    total_files: int = 0


class KBStatusResponse(BaseModel):
    """Knowledge base status."""

    rag_enabled: bool = True
    has_data: bool = False
    working_dir: str = ""


class IngestArtifactRequest(BaseModel):
    """Auto-ingest a produced artifact into the KB."""

    text: str = Field(..., description="Full artifact content (markdown)")
    artifact_type: str = Field(
        ...,
        description="Type label: sow, adr, personas, stories, srs, ...",
    )
    name: str = Field(..., description="Artifact identifier")
    replace: bool = Field(
        default=False,
        description="If True, delete any existing chunks for this artifact first",
    )


class IngestArtifactResponse(BaseModel):
    chunks: int = 0
    removed: int = 0


class SupersedeADRRequest(BaseModel):
    """Mark an ADR as superseded in the KB and ingest its replacement."""

    old_adr_id: str
    new_adr_id: str
    new_text: str


class SupersedeADRResponse(BaseModel):
    removed: int = 0
    added: int = 0


class DeleteArtifactRequest(BaseModel):
    """Delete an artifact from the KB."""

    artifact_type: str = Field(..., description="Type: sow, adr, etc.")
    name: str = Field(..., description="Artifact identifier")


class DeleteArtifactResponse(BaseModel):
    removed: int = 0


class ArtifactKBStatusEntry(BaseModel):
    """KB ingestion status for a single artifact."""

    artifact_type: str
    name: str
    status: str = Field(
        default="none",
        description="none | ingested | partial | error",
    )
    chunks: int = 0


class ArtifactKBStatusResponse(BaseModel):
    """KB ingestion status for all known artifacts."""

    artifacts: list[ArtifactKBStatusEntry] = []


class LocateChunkRequest(BaseModel):
    """Locate a chunk's text within its source document (PDF or text)."""

    source_ref: str = Field(
        ...,
        description="Source reference from RAGChunk (e.g. knowledge/.sdlicit/knowledge/ieee830.pdf#4-1-scope)",
    )
    snippet: str = Field(..., description="Chunk text to search for in the document")
    project_dir: str = Field(
        default="", description="Project root to resolve relative paths"
    )


class LocateChunkResponse(BaseModel):
    """Result of locating a chunk in its source document."""

    found: bool = False
    page: int = Field(
        default=1, description="1-based page number where chunk text was found"
    )
    file_path: str = Field(default="", description="Absolute path to the source file")
    file_type: str = Field(default="", description="File type: pdf, md, txt, rst")
    anchor: str = Field(default="", description="Section anchor from the source ref")
    match_score: float = Field(
        default=0.0, description="Proportion of snippet text found on the page (0-1)"
    )


# --- Traceability Models ---


class TraceNodeResponse(BaseModel):
    """A node in the traceability graph."""

    id: str
    type: str
    title: str
    status: str
    filePath: str = Field(default="", alias="filePath")


class TraceEdgeResponse(BaseModel):
    """A directed edge in the traceability graph."""

    source: str
    target: str
    type: str


class TraceGraphResponse(BaseModel):
    """Full traceability graph for UI rendering."""

    nodes: list[TraceNodeResponse] = Field(default_factory=list)
    edges: list[TraceEdgeResponse] = Field(default_factory=list)


class TraceCheckRequest(BaseModel):
    """Request to check traceability for a specific artifact."""

    artifact_id: str = Field(..., description="ID of the artifact to check")
    artifact_content: str = Field(default="", description="Full text content for semantic matching")
    project_dir: str = Field(default="", description="Project root")


class TraceCheckResult(BaseModel):
    """A single traceability issue found by the agent."""

    severity: str = Field(default="warning", description="info, warning, error")
    message: str
    source_id: str = Field(default="")
    target_id: str = Field(default="")


class TraceCheckResponse(BaseModel):
    """Response from the traceability check endpoint."""

    issues: list[TraceCheckResult] = Field(default_factory=list)
    impacted_nodes: list[str] = Field(default_factory=list)
    suggested_implements: list[str] = Field(default_factory=list, description="Auto-detected requirement IDs this artifact addresses")


# --- Trace Coverage Models ---


class ArtifactCoverageResponse(BaseModel):
    """Trace coverage for one artifact."""

    artifact_id: str
    artifact_type: str
    outgoing_links: int = 0
    valid_links: int = 0
    broken_links: int = 0
    semantic_score: float | None = None
    covered_by: list[str] = Field(default_factory=list)


class TraceCoverageRequest(BaseModel):
    """Request trace coverage analysis."""

    mode: str = Field(
        default="",
        description="Override mode: structural, semantic, full. Empty = use config.",
    )


class TraceCoverageResponse(BaseModel):
    """Full trace coverage result for the workspace."""

    mode: str = "structural"
    total_links: int = 0
    valid_links: int = 0
    broken_links_count: int = 0
    structural_coverage_pct: float = 0.0
    semantic_coverage_pct: float | None = None
    mean_tfidf: float | None = None
    mean_jaccard: float | None = None
    mean_topic: float | None = None
    mean_combined: float | None = None
    per_requirement_scores: dict[str, float] = Field(default_factory=dict)
    artifacts: list[ArtifactCoverageResponse] = Field(default_factory=list)
    graph_issues: list[dict] = Field(default_factory=list)
    has_conflicts: bool = False
    conflict_assessment: str = ""
    artifact_counts: dict[str, int] = Field(default_factory=dict)
