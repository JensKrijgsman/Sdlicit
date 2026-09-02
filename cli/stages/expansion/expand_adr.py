"""Expansion stage — multi-agent ADR review action.

Sends a completed ADR through the expansion pipeline:
ADR_Agent → Requirement_Agent → ToM_Agent

The backend reads ADR files directly — the CLI only sends the filename
and project_dir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from shared.files import list_adr_files

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_expand_adr(client: SdlicitClient, working_dir: str) -> None:
    """Pick an ADR and run it through the expansion pipeline."""
    files = list_adr_files(working_dir)
    if not files:
        console.print(
            Panel(
                "No ADRs found. Create one first with [bold]Create new ADR[/bold].",
                title="Expand ADR",
                border_style="yellow",
            )
        )
        return

    console.print(Rule("[bold]Select an ADR to expand[/bold]"))
    for idx, f in enumerate(files, 1):
        console.print(
            f"  [dim]{idx}.[/dim] [cyan]{f['filename']}[/cyan]  {f.get('title') or ''}"
        )

    choices = [str(i) for i in range(1, len(files) + 1)]
    choice = Prompt.ask("Enter number", choices=choices)
    selected = files[int(choice) - 1]

    console.print("\n[bold]Running expansion pipeline …[/bold]")
    try:
        data = client.expand_adr(
            adr_filename=str(selected["filename"]),
            project_dir=working_dir,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    # Display reviews
    reviews = data.get("reviews", [])
    if reviews:
        table = Table(title="Agent Reviews", show_lines=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Summary", style="white")
        table.add_column("Suggestions")
        for review in reviews:
            suggestions = "\n".join(f"• {s}" for s in review.get("suggestions", []))
            table.add_row(
                review.get("agent_name", ""),
                review.get("summary", ""),
                suggestions or "—",
            )
        console.print(table)

    # ToM verdict
    verdict = data.get("tom_verdict", "")
    if verdict:
        console.print(
            Panel(
                verdict,
                title="[bold magenta]ToM Verdict[/bold magenta]",
                border_style="magenta",
            )
        )

    console.print()
