"""Guided flow plugin — option 0 in the main menu.

A scripted walk through the SDLC artifact pipeline:

    SOW  →  SRS (optional)  →  Personas  →  User Stories  →
    ADR(s) (loop)  →  Gherkin

Artifacts already on disk are detected and the flow resumes from the
first missing step, with the user given the chance to regenerate any
existing artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .guided_flow import action_guided_flow

if TYPE_CHECKING:
    from api_client import SdlicitClient


def menu_entries(
    client: SdlicitClient, working_dir: str
) -> list[tuple[str, str, str, object]]:
    return [
        (
            "guided",
            "Guided flow (recommended)",
            "Walk through SOW → SRS → Personas → Stories → ADR → Gherkin with Socratic engagement",
            lambda: action_guided_flow(client, working_dir),
        ),
    ]
