"""Expansion stage — KB management commands (status, delete, locate)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_kb_manage(client: "SdlicitClient", working_dir: str) -> None:
    """Knowledge base management: status, delete artifacts, locate sources."""
    console.print(Rule("[bold]Knowledge Base Management[/bold]"))

    # Show KB status
    with console.status("[bold]Checking KB status…[/bold]"):
        try:
            status = client.kb_status(project_dir=working_dir)
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

    if not status.get("rag_enabled"):
        console.print(
            Panel(
                "[yellow]RAG is disabled on the server.[/yellow]\n"
                "Enable it in .sdlicit/config.yaml with: enable_rag: true",
                border_style="yellow",
            )
        )
        return

    console.print(
        f"[green]✓[/green] KB is enabled  "
        f"has_data={status.get('has_data', False)}  "
        f"[dim]{status.get('working_dir', '')}[/dim]"
    )
    console.print()

    # Fetch artifact KB status
    with console.status("[bold]Fetching artifact ingestion status…[/bold]"):
        try:
            art_status = client.get_artifact_kb_status()
        except Exception as exc:
            console.print(f"[yellow]Could not fetch artifact KB status:[/yellow] {exc}")
            art_status = {}

    artifacts = art_status.get("artifacts", [])
    if artifacts:
        table = Table(title="Artifacts in Knowledge Base", show_lines=True)
        table.add_column("#", justify="right", style="dim")
        table.add_column("Type", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Status", style="green")
        table.add_column("Chunks", justify="right")

        for i, a in enumerate(artifacts, 1):
            table.add_row(
                str(i),
                a.get("artifact_type", ""),
                a.get("name", ""),
                a.get("status", ""),
                str(a.get("chunks", 0)),
            )
        console.print(table)
    else:
        console.print("[dim]No artifacts indexed in the KB.[/dim]")

    console.print()

    # Sub-menu
    console.print("  [bold][1][/bold]  Delete artifact from KB")
    console.print("  [bold][2][/bold]  Locate KB chunk source")
    console.print("  [bold][3][/bold]  Return to main menu")
    console.print()

    choice = Prompt.ask("Action", choices=["1", "2", "3"], default="3")

    if choice == "1":
        _delete_artifact(client, artifacts)
    elif choice == "2":
        _locate_chunk(client, working_dir)


def _delete_artifact(
    client: "SdlicitClient", artifacts: list[dict]
) -> None:
    """Delete an artifact's chunks from the KB."""
    if not artifacts:
        console.print("[yellow]No artifacts to delete.[/yellow]")
        return

    idx_str = Prompt.ask(
        "Artifact # to delete",
        choices=[str(i) for i in range(1, len(artifacts) + 1)],
    )
    artifact = artifacts[int(idx_str) - 1]
    atype = artifact.get("artifact_type", "")
    name = artifact.get("name", "")

    console.print(
        f"  Deleting [bold]{atype}/{name}[/bold] from KB…"
    )
    try:
        resp = client.delete_from_kb(artifact_type=atype, name=name)
        removed = resp.get("removed", 0)
        console.print(f"[green]✓[/green] Removed {removed} chunk(s) from KB.")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")


def _locate_chunk(client: "SdlicitClient", working_dir: str) -> None:
    """Locate the source file/page of a KB chunk."""
    source_ref = Prompt.ask(
        "Source reference (e.g. knowledge/.sdlicit/knowledge/ieee830.pdf#4-1)"
    )
    snippet = Prompt.ask("Text snippet from the chunk (optional)", default="")

    with console.status("[bold]Locating…[/bold]"):
        try:
            result = client.locate_chunk(
                source_ref=source_ref,
                snippet=snippet,
                project_dir=working_dir,
            )
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

    if not result.get("found"):
        console.print("[yellow]Could not locate the source for this chunk.[/yellow]")
        return

    console.print(
        Panel(
            f"[bold]File:[/bold] {result.get('file_path', '')}\n"
            f"[bold]Type:[/bold] {result.get('file_type', '')}\n"
            f"[bold]Page:[/bold] {result.get('page', 1)}\n"
            f"[bold]Anchor:[/bold] {result.get('anchor', '')}\n"
            f"[bold]Match Score:[/bold] {result.get('match_score', 0):.2f}",
            title="Chunk Source Location",
            border_style="green",
        )
    )
