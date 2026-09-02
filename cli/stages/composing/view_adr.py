"""Composing stage — view / inspect a MADR file with Rich formatting."""

from __future__ import annotations

import re

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from shared.files import adr_dir, list_adr_files, read_adr
from shared.parsers import parse_madr_content

console = Console()

# Status badge colours
_STATUS_STYLE: dict[str, str] = {
    "accepted": "bold green",
    "proposed": "bold yellow",
    "deprecated": "bold red",
    "superseded": "bold magenta",
}


def _bullet_lines(raw_lines: list[str]) -> list[str]:
    """Extract non-empty, non-header lines (strip leading * / - markup)."""
    items = []
    for line in raw_lines:
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("##")
            and not stripped.startswith("###")
        ):
            items.append(re.sub(r"^[*\-]\s*", "", stripped))
    return items


def _render_adr(content: str, filename: str) -> None:
    """Render a MADR file with Rich panels and tables."""
    data = parse_madr_content(content)
    sections: dict[str, list[str]] = data["sections"]  # type: ignore[assignment]

    status = str(data["status"])
    style = _STATUS_STYLE.get(status.lower(), "bold white")
    status_badge = Text(
        f" {status.upper()} ",
        style=f"on {style.replace('bold ', '')}" if "bold" in style else style,
    )

    # Header panel
    header = Text()
    header.append(str(data["title"]), style="bold white")
    header.append("\n")
    header.append_text(status_badge)
    if data["date"]:
        header.append(f"   {data['date']}", style="dim")
    console.print(
        Panel(
            header,
            title=f"[dim]{filename}[/dim]",
            border_style="bright_blue",
            padding=(1, 2),
        )
    )

    # Decision Drivers
    if "Decision Drivers" in sections:
        items = _bullet_lines(sections["Decision Drivers"])
        if items:
            t = Table.grid(padding=(0, 2))
            t.add_column(style="dim cyan")
            t.add_column()
            for i, item in enumerate(items, 1):
                t.add_row(f"{i}.", item)
            console.print(
                Panel(t, title="Decision Drivers", border_style="cyan", padding=(0, 1))
            )

    # Context
    for heading in ("Context and Problem Statement", "Context"):
        if heading in sections:
            body = "\n".join(
                ln for ln in sections[heading] if ln.strip() and not ln.startswith("#")
            )
            if body.strip():
                console.print(
                    Panel(
                        body.strip(),
                        title="Context & Problem Statement",
                        border_style="blue",
                        padding=(1, 2),
                    )
                )
            break

    # Considered Options
    for heading in ("Considered Options", "Options"):
        if heading in sections:
            items = _bullet_lines(sections[heading])
            if items:
                cols = Columns(
                    [Panel(item, border_style="dim") for item in items],
                    equal=False,
                    expand=True,
                )
                console.print(Rule("[dim]Considered Options[/dim]"))
                console.print(cols)
            break

    # Decision Outcome
    for heading in ("Decision Outcome", "Decision"):
        if heading in sections:
            body_lines = [
                ln
                for ln in sections[heading]
                if ln.strip() and not ln.startswith("##") and not ln.startswith("###")
            ]
            body = "\n".join(body_lines).strip()
            if body:
                console.print(
                    Panel(
                        body,
                        title="[bold green]Decision Outcome[/bold green]",
                        border_style="green",
                        padding=(1, 2),
                    )
                )
            break

    # Consequences — split good / bad  (handles both * and - prefix)
    for heading in ("Consequences",):
        if heading in sections:
            good: list[str] = []
            bad: list[str] = []
            for line in sections[heading]:
                s = line.strip()
                if not s:
                    continue
                m = re.match(r"[*\-]\s+Good,\s+because\s+(.*)", s, re.I)
                if m:
                    good.append(m.group(1).strip())
                    continue
                m = re.match(r"[*\-]\s+Bad,\s+because\s+(.*)", s, re.I)
                if m:
                    bad.append(m.group(1).strip())
            rows: list[tuple[str, str]] = []
            for g in good:
                rows.append(("[green]+[/green]", g))
            for b in bad:
                rows.append(("[red]-[/red]", b))
            if rows:
                t = Table.grid(padding=(0, 1))
                t.add_column(width=2)
                t.add_column()
                for sign, text in rows:
                    t.add_row(sign, text)
                console.print(
                    Panel(
                        t, title="Consequences", border_style="yellow", padding=(0, 1)
                    )
                )
            break

    console.print()


def action_view_adr(working_dir: str) -> None:
    """Pick an ADR from the list and render it."""
    files = list_adr_files(working_dir)
    if not files:
        console.print(
            Panel(
                f"No ADRs found in [bold]{adr_dir(working_dir)}[/bold]",
                title="View ADR",
                border_style="yellow",
            )
        )
        return

    # Show numbered list
    console.print(Rule("[bold]Select an ADR to view[/bold]"))
    for idx, f in enumerate(files, 1):
        console.print(
            f"  [dim]{idx}.[/dim] [cyan]{f['filename']}[/cyan]  {f.get('title') or ''}"
        )

    choices = [str(i) for i in range(1, len(files) + 1)]
    choice = Prompt.ask("Enter number", choices=choices)
    selected = files[int(choice) - 1]

    try:
        content = read_adr(working_dir, str(selected["filename"]))
    except OSError as e:
        console.print(f"[red]Error reading file:[/red] {e}")
        return

    _render_adr(content, str(selected["filename"]))
