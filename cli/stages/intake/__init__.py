"""Intake stage CLI plugin.

Provides the SOW (Statement of Work) creation from raw briefs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .create_sow import action_create_sow
from .regenerate_section import action_regenerate_sow_section

if TYPE_CHECKING:
    from api_client import SdlicitClient


def menu_entries(
    client: "SdlicitClient", working_dir: str
) -> list[tuple[str, str, str, object]]:
    return [
        (
            "create_sow",
            "Create SOW from brief",
            "Paste a raw brief / meeting notes → AI extracts a structured SOW",
            lambda: action_create_sow(client, working_dir),
        ),
        (
            "regen_sow_section",
            "Regenerate SOW section",
            "Re-generate a single section of the latest SOW with feedback",
            lambda: action_regenerate_sow_section(client, working_dir),
        ),
    ]
