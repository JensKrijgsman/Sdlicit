"""Generation stage CLI plugin.

Exposes ``menu_entries`` — a list of (key, label, description, action_fn) tuples
that ``cli_client.py`` plugs into the main menu dynamically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .generate_gherkin import action_generate_gherkin
from .generate_personas import action_generate_personas
from .generate_srs import action_generate_srs
from .generate_stories import action_generate_stories
from .validate_gherkin import action_validate_gherkin

if TYPE_CHECKING:
    from api_client import SdlicitClient


def menu_entries(
    client: SdlicitClient, working_dir: str
) -> list[tuple[str, str, str, object]]:
    return [
        (
            "gen_srs",
            "Generate SRS (from SOW)",
            "Extract a structured Software Requirements Specification from the latest SOW",
            lambda: action_generate_srs(client, working_dir),
        ),
        (
            "gen_personas",
            "Generate personas",
            "Create user personas from ADRs and the latest SRS",
            lambda: action_generate_personas(client, working_dir),
        ),
        (
            "gen_stories",
            "Generate user stories",
            "Create user stories from personas + SRS requirements",
            lambda: action_generate_stories(client, working_dir),
        ),
        (
            "gen_gherkin",
            "Generate Gherkin scenarios",
            "Produce BDD .feature files from personas + stories",
            lambda: action_generate_gherkin(client, working_dir),
        ),
        (
            "validate_gherkin",
            "Validate Gherkin syntax",
            "Check syntax of existing .feature files or pasted text",
            lambda: action_validate_gherkin(client, working_dir),
        ),
    ]
