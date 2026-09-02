"""CLI action: Trigger knowledge base ingestion.

The backend reads files directly from the project directory — the CLI
only triggers the scan/ingest and renders progress from SSE events.

Color-coded ingestion status:
  - Green:  already fully ingested
  - Yellow: partially uploaded
  - White:  not yet ingested
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _size_str(size_bytes: int) -> str:
    if size_bytes > 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def _show_doc_table(
    docs: list[dict[str, Any]],
    selected: set[int] | None = None,
) -> None:
    """Print the document table with color-coded ingestion status.

    If *selected* is provided, mark toggled rows with ● in orange.
    """
    table = Table(title="Documents Found", show_lines=False)
    table.add_column("#", style="bold", width=4)
    table.add_column("Sel", width=3)
    table.add_column("File")
    table.add_column("Size", justify="right")
    table.add_column("Status")

    for i, doc in enumerate(docs):
        status = doc.get("ingestion_status", "none")
        path = doc["relative_path"]
        size = _size_str(doc["size_bytes"])

        if status == "complete":
            status_str = "[green]✓ ingested[/green]"
            file_style = "green dim"
            sel_mark = "[green dim]✓[/green dim]"
        elif status == "partial":
            status_str = "[yellow]◐ partial[/yellow]"
            file_style = "yellow"
            sel_mark = (
                "[bright_red bold]●[/bright_red bold]"
                if selected is not None and i in selected
                else "[dim]○[/dim]"
            )
        else:
            status_str = "[dim]— new[/dim]"
            file_style = ""
            sel_mark = (
                "[bright_red bold]●[/bright_red bold]"
                if selected is not None and i in selected
                else "[dim]○[/dim]"
            )

        if selected is not None:
            num_style = "bold" if i in selected else "dim"
        else:
            num_style = "bold"

        file_cell = f"[{file_style}]{path}[/{file_style}]" if file_style else path
        table.add_row(
            f"[{num_style}]{i + 1}[/{num_style}]",
            sel_mark,
            file_cell,
            size,
            status_str,
        )

    console.print(table)


def _clear() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def _select_some(docs: list[dict[str, Any]]) -> list[str] | None:
    """Prompt-based file selector: type numbers to toggle, 'done' to proceed."""
    # Only non-complete files are selectable
    selectable = {
        i for i, d in enumerate(docs) if d.get("ingestion_status") != "complete"
    }
    selected: set[int] = set()  # start with nothing selected

    def _redraw() -> None:
        _clear()
        count = len(selected)
        _show_doc_table(docs, selected)
        console.print(f"\n  [dim]{count} file(s) selected[/dim]")
        console.print(
            "\n[dim]Type a file number to toggle it. "
            "Type [bold]done[/bold] to start ingestion, [bold]all[/bold] to select all, "
            "[bold]none[/bold] to deselect all, or [bold]q[/bold] to cancel.[/dim]\n"
        )

    _redraw()

    while True:
        answer = Prompt.ask("[bold]Toggle / action[/bold]").strip().lower()

        if answer in ("done", "d"):
            return [docs[i]["relative_path"] for i in sorted(selected)]
        if answer in ("q", "quit", "back"):
            return None
        if answer == "all":
            selected = set(selectable)
        elif answer == "none":
            selected.clear()
        else:
            # Try to parse as number(s)
            for part in answer.replace(",", " ").split():
                try:
                    idx = int(part) - 1
                except ValueError:
                    console.print(f"  [red]'{part}' is not a valid number[/red]")
                    continue
                if idx not in selectable:
                    if 0 <= idx < len(docs):
                        console.print(
                            f"  [dim]#{idx + 1} is already fully ingested[/dim]"
                        )
                    else:
                        console.print(f"  [red]#{idx + 1} is out of range[/red]")
                    continue
                selected ^= {idx}  # toggle

        _redraw()


# ---------------------------------------------------------------------------
# Main action
# ---------------------------------------------------------------------------


def action_ingest_kb(client: SdlicitClient, working_dir: str) -> None:
    """Trigger backend to scan and ingest documents from working_dir."""
    # 1. Check KB status
    try:
        status = client.kb_status(project_dir=working_dir)
    except Exception as exc:
        console.print(f"[red]Cannot reach KB status endpoint: {exc}[/red]")
        return

    if not status.get("rag_enabled", False):
        console.print(
            "[yellow]Knowledge base is disabled on the server "
            "(enable_rag=false in config).[/yellow]"
        )
        return

    # 2. Ask backend to scan for documents (includes ingestion status)
    with console.status("[bold]Scanning project for documents…[/bold]"):
        try:
            scan = client.scan_documents(project_dir=working_dir)
        except Exception as exc:
            console.print(f"[red]Scan failed: {exc}[/red]")
            return

    docs = scan.get("documents", [])
    if not docs:
        console.print(
            Panel(
                "No documents (.pdf, .md, .txt) found in the project.\n\n"
                "Place ISO standards or reference documents under:\n"
                f"  {working_dir}/.sdlicit/knowledge/\n"
                f"  {working_dir}/knowledge/",
                title="No Documents Found",
                border_style="yellow",
            )
        )
        return

    # 3. Show documents and ask what to do
    _show_doc_table(docs)

    selectable = [d for d in docs if d.get("ingestion_status") != "complete"]
    if not selectable:
        console.print("\n[green]All documents are already ingested.[/green]")
        return

    console.print()
    choice = Prompt.ask(
        "[bold]Insert[/bold] \[a]ll / \[s]ome / \[q]uit",
        choices=["a", "s", "q"],
        default="a",
    )

    if choice == "q":
        console.print("[dim]Cancelled.[/dim]")
        return

    if choice == "a":
        selected_files = [
            d["relative_path"] for d in docs if d.get("ingestion_status") != "complete"
        ]
    else:
        selected_files = _select_some(docs)

    if selected_files is None:
        console.print("[dim]Cancelled.[/dim]")
        return
    if not selected_files:
        console.print("[yellow]No files selected for ingestion.[/yellow]")
        return

    console.print(
        f"\n[bold]Ingesting {len(selected_files)} file(s) into LightRAG…[/bold]"
    )
    console.print(
        "[dim]The backend extracts text, chunks, and builds the knowledge graph.[/dim]\n"
    )

    # 4. Stream SSE events from the ingest endpoint
    total_chunks = 0
    ingested = 0
    errors: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("chunks"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Starting…", total=None)

        with client.ingest_kb(
            project_dir=working_dir, selected_files=selected_files
        ) as response:
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                etype = event.get("type")

                if etype == "start":
                    total_chunks = event["total_chunks"]
                    progress.update(task, total=total_chunks, description="Ingesting")

                elif etype == "progress":
                    progress.update(
                        task,
                        completed=event["current"],
                        description=f"[cyan]{event['source_name']}[/cyan]",
                    )
                    if event.get("ok"):
                        ingested += 1
                    else:
                        errors.append(event.get("source_name", "?"))

                elif etype == "done":
                    ingested = event.get("ingested", ingested)
                    total_chunks = event.get("total_chunks", total_chunks)
                    errors = event.get("errors", errors)
                    progress.update(task, completed=total_chunks, description="Done")

                elif etype == "error":
                    console.print(f"[red]{event['message']}[/red]")
                    return

    console.print()
    if ingested > 0:
        console.print(
            f"[green]✓ Successfully ingested {ingested} / {total_chunks} chunk(s)[/green]"
        )
    if errors:
        console.print(f"[yellow]⚠ {len(errors)} error(s):[/yellow]")
        for err in errors[:10]:
            console.print(f"  [yellow]⚠ {err}[/yellow]")
        if len(errors) > 10:
            console.print(f"  [dim]… and {len(errors) - 10} more[/dim]")
    console.print()
