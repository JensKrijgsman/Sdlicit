"""Composing stage — list ADRs action."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from shared.files import adr_dir, list_adr_files

console = Console()


def action_list_adrs(working_dir: str) -> None:
    """Scan .sdlicit/adr/ locally and render a table of existing ADRs."""
    files = list_adr_files(working_dir)
    path_str = str(adr_dir(working_dir))

    if not files:
        console.print(
            Panel(
                f"No ADRs found in [bold]{path_str}[/bold]\n\n"
                "Use [bold]Create new ADR[/bold] to get started.",
                title="ADR List",
                border_style="yellow",
            )
        )
        return

    table = Table(title=f"ADRs  ·  {path_str}", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Filename", style="cyan")
    table.add_column("Title")
    for idx, f in enumerate(files, 1):
        table.add_row(str(idx), f["filename"], f.get("title") or "—")
    console.print(table)
