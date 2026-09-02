"""CLI action: Query the knowledge base interactively."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def _show_kb_sources(client: "SdlicitClient", working_dir: str) -> None:
    """Display ingested files and their status before querying."""
    try:
        scan = client.scan_documents(project_dir=working_dir)
    except Exception:
        return  # non-critical — just skip the source display

    docs = scan.get("documents", [])
    if not docs:
        return

    lines: list[Text] = []
    for doc in docs:
        path = doc["relative_path"]
        status = doc.get("ingestion_status", "none")
        if status == "complete":
            line = Text(f"  ✓  {path}")
            line.stylize("green")
        elif status == "partial":
            line = Text(f"  ◐  {path}  [partial]")
            line.stylize("yellow")
        else:
            line = Text(f"  ○  {path}  [not ingested]")
            line.stylize("dim")
        lines.append(line)

    if lines:
        from rich.console import Group

        console.print(
            Panel(
                Group(*lines),
                title="[bold]Knowledge Base Sources[/bold]",
                border_style="dim",
            )
        )


def action_query_kb(client: "SdlicitClient", working_dir: str = "") -> None:
    """Interactive knowledge base query loop with store-aware routing."""
    # Check if RAG is enabled on the server by doing a probe query
    try:
        probe = client.query_kb("", mode="hybrid")
        if not probe.get("rag_enabled", True):
            console.print(
                "[yellow]Knowledge base is disabled on the server (enable_rag=false in config).[/yellow]"
            )
            return
    except Exception:
        pass  # Proceed anyway — the real query will show errors

    # Show ingested sources
    _show_kb_sources(client, working_dir)

    console.print(
        Panel(
            "[bold]Knowledge Base Query[/bold]\n\n"
            "Ask questions against the RAG knowledge corpus.\n\n"
            "[bold]Stores:[/bold]\n"
            "  • [cyan]knowledge[/cyan] — Reference docs (ISO standards, PDFs)\n"
            "  • [cyan]artifacts[/cyan] — Project artifacts (SOW, ADR, etc.)\n"
            "  • [cyan]all[/cyan]       — Search across both (full graph)\n\n"
            "[bold]Modes:[/bold] naive, local, global, hybrid, mix\n\n"
            "[dim]Options: 'probe on/off' to toggle graph probing.[/dim]\n"
            "[dim]Type 'back' to return to the menu.[/dim]",
            border_style="bright_cyan",
        )
    )

    probe_enabled = False

    while True:
        console.print()
        query = Prompt.ask("[bold]Query[/bold]")
        if query.lower() in ("back", "q", "quit", "exit"):
            break

        # Toggle probe mode
        if query.lower() in ("probe on", "probe off"):
            probe_enabled = query.lower() == "probe on"
            state = "enabled" if probe_enabled else "disabled"
            console.print(f"[dim]Graph probing {state}.[/dim]")
            continue

        store = Prompt.ask(
            "[dim]Store[/dim]",
            default="all",
            choices=["knowledge", "artifacts", "all"],
        )

        mode = Prompt.ask(
            "[dim]Retrieval mode[/dim]",
            default="hybrid",
            choices=["naive", "local", "global", "hybrid", "mix"],
        )

        with console.status("[bold]Querying knowledge base…[/bold]"):
            data = client.query_rag(
                query,
                store=store,
                mode=mode,
                probe_first=probe_enabled,
            )

        results = data.get("results", [])
        if not results:
            if probe_enabled and data.get("probed", False):
                console.print(
                    "[yellow]Graph probe found no relevant entities. "
                    "Try with probe off or a different store.[/yellow]"
                )
            else:
                console.print("[yellow]No results found.[/yellow]")
            continue

        for i, chunk in enumerate(results, 1):
            text = chunk.get("text", "")
            source = chunk.get("source", "unknown")
            relevance = chunk.get("relevance", 0.0)
            chunk_store = chunk.get("store", "all")
            chunk_mode = chunk.get("mode", mode)
            console.print(
                Panel(
                    text,
                    title=(
                        f"Result {i} — {source} "
                        f"(relevance: {relevance:.2f}, "
                        f"store: {chunk_store}, mode: {chunk_mode})"
                    ),
                    border_style="dim",
                )
            )
