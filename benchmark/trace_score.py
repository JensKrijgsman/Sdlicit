"""Structural/semantic traceability scoring for an existing project workspace.

Thin wrapper around the production TraceService — no scoring logic lives
here, this only picks it up from an SdlicitConfig and returns the result
as a plain dict so a CLI or a notebook can print/serialize it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from sdlicit.config import SdlicitConfig
from sdlicit.expansion_stage.traceability import TraceService


def score_traceability(
    workspace: Path,
    mode: Literal["structural", "semantic", "full"] = "structural",
    graph_source: Literal["frontmatter", "lightrag"] = "frontmatter",
) -> dict:
    """Score a project's artifact traceability.

    workspace must contain a .sdlicit/ directory (config.yaml + artifacts/).
    graph_source defaults to frontmatter so this runs with no knowledge base
    required — pass lightrag only if the workspace has one ingested.
    """
    config = SdlicitConfig.from_project(workspace)
    config.trace_check_mode = mode
    config.traceability_graph_source = graph_source

    trace_svc = TraceService.from_config(config, llm=None, kb=None)
    coverage = trace_svc.analyse(workspace, mode=mode)
    return coverage.to_dict()
