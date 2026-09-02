"""Traceability subsystem — unified trace service, graph store, and conflict detection.

Provides three levels of trace analysis (controlled via ``trace_check_mode`` config):
  - ``structural`` — ID cross-reference validation (fast, no LLM)
  - ``semantic`` — TF-IDF + Jaccard + NMF topic alignment (requires scikit-learn)
  - ``full`` — structural + semantic + graph-based conflict detection (may use LLM)

Public API::

    from sdlicit.expansion_stage.traceability import TraceService, TraceGraph

    service = TraceService(config)
    result = service.analyse(workspace)             # uses config.trace_check_mode
    result = service.analyse(workspace, mode="full")  # explicit override
"""

from sdlicit.expansion_stage.traceability.trace_dag import TraceEdge, TraceGraph, TraceNode
from sdlicit.expansion_stage.traceability.trace_service import TraceCoverage, TraceService

__all__ = ["TraceService", "TraceCoverage", "TraceGraph", "TraceEdge", "TraceNode"]
