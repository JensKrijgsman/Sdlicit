"""Expansion stage service — delegates to the Orchestrator.

Pipeline: ADR Agent (full sweep) → Requirement Agent (RAG check)
          → ToM Agent (final verdict, sees all prior outputs)

The backend reads ADR files from disk using project_dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sdlicit.helpers.project_files import read_adr, read_all_adrs_except

from .models import AgentReview, ExpandRequest, ExpandResponse

if TYPE_CHECKING:
    from sdlicit.orchestrator import Orchestrator


async def expand_adr(
    request: ExpandRequest,
    orchestrator: "Orchestrator",
) -> ExpandResponse:
    """Run the full expansion pipeline over a completed ADR."""
    project_dir = Path(request.project_dir)
    adr_content = read_adr(project_dir, request.adr_filename)
    prior_adrs = read_all_adrs_except(project_dir, request.adr_filename)

    data = await orchestrator.expand_adr(
        adr_content=adr_content,
        prior_adrs=prior_adrs,
    )

    reviews = [
        AgentReview(
            agent_name=r.get("agent", ""),
            summary=r.get("summary", ""),
            suggestions=r.get("suggestions", []),
        )
        for r in data.get("reviews", [])
    ]

    return ExpandResponse(
        reviews=reviews,
        tom_verdict=data.get("tom_verdict", ""),
    )
