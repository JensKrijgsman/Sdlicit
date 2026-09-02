"""Expansion stage CLI plugin.

Exposes ``menu_entries`` — a list of (key, label, description, action_fn) tuples
that ``cli_client.py`` plugs into the main menu dynamically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .expand_adr import action_expand_adr
from .ingest_kb import action_ingest_kb
from .kb_manage import action_kb_manage
from .query_kb import action_query_kb
from .traceability import action_traceability_dashboard

if TYPE_CHECKING:
    from api_client import SdlicitClient


def menu_entries(
    client: "SdlicitClient", working_dir: str
) -> list[tuple[str, str, str, object]]:
    return [
        (
            "expand_adr",
            "Review artifact (multi-agent)",
            "ADR + Requirement + ToM agents jointly review an ADR",
            lambda: action_expand_adr(client, working_dir),
        ),
        (
            "ingest_kb",
            "Ingest documents into KB",
            "Scan project for PDFs/markdown and index into LightRAG",
            lambda: action_ingest_kb(client, working_dir),
        ),
        (
            "query_kb",
            "Query knowledge base",
            "Ask a question against the LightRAG knowledge corpus",
            lambda: action_query_kb(client, working_dir),
        ),
        (
            "kb_manage",
            "Manage knowledge base",
            "View artifact ingestion status, delete chunks, locate sources",
            lambda: action_kb_manage(client, working_dir),
        ),
        (
            "traceability",
            "Traceability dashboard",
            "View coverage metrics, graph nodes, and link integrity",
            lambda: action_traceability_dashboard(client, working_dir),
        ),
    ]
