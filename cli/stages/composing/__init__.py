"""Composing stage CLI plugin.

Exposes ``menu_entries`` — a list of (key, label, description, action_fn) tuples
that ``cli_client.py`` plugs into the main menu dynamically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .create_adr import action_create_adr
from .list_adrs import action_list_adrs
from .suggest_directions import action_suggest_adr_directions
from .view_adr import action_view_adr

if TYPE_CHECKING:
    from api_client import SdlicitClient


def menu_entries(
    client: SdlicitClient, working_dir: str
) -> list[tuple[str, str, str, object]]:
    """Return (key, label, description, callable) tuples for the main menu."""
    return [
        (
            "create_adr",
            "Create new ADR",
            "Step-by-step ADR creation wizard (MADR 4.0.0) with AI review",
            lambda: action_create_adr(client, working_dir),
        ),
        (
            "suggest_adr_directions",
            "Suggest ADR directions",
            "AI ideation pass — which ADRs should you write next? (no solutions proposed)",
            lambda: action_suggest_adr_directions(client, working_dir),
        ),
        (
            "list_adrs",
            "List artifacts",
            "Show all ADRs in the project",
            lambda: action_list_adrs(working_dir),
        ),
        (
            "view_adr",
            "View / inspect an ADR",
            "Read and inspect a specific ADR with parsed sections",
            lambda: action_view_adr(working_dir),
        ),
    ]
