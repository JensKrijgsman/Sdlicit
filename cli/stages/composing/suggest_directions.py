"""Composing stage — suggest ADR directions.

Pure ideation: given the project Brief (latest SOW), prior ADRs, and any
downstream artifacts (personas / stories / Gherkin), ask the ADR Agent
which ADR topics the user should consider writing next.  The agent
NEVER proposes solutions — only WHAT decisions need to be made and WHY.

Used both as a standalone menu action and as an optional auto-prompt
at the start of the create-ADR wizard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from shared.files import (
    gherkin_dir,
    latest_sow,
    personas_md_path,
    stories_md_path,
)

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()

_PRIORITY_STYLE = {
    "high": "bold red",
    "medium": "yellow",
    "low": "dim",
}


def _load_brief(working_dir: str) -> str:
    """Read the latest SOW markdown.  Returns empty string when none exists."""
    sow = latest_sow(working_dir)
    if sow is None or not sow.is_file():
        return ""
    try:
        return sow.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_downstream_artifacts(working_dir: str) -> str:
    """Concatenate personas + stories + Gherkin markdown (when present)."""
    parts: list[str] = []

    p_md = personas_md_path(working_dir)
    if p_md.is_file():
        try:
            parts.append("# Personas\n\n" + p_md.read_text(encoding="utf-8"))
        except OSError:
            pass

    s_md = stories_md_path(working_dir)
    if s_md.is_file():
        try:
            parts.append("# User Stories\n\n" + s_md.read_text(encoding="utf-8"))
        except OSError:
            pass

    g_dir = gherkin_dir(working_dir)
    if g_dir.is_dir():
        feature_chunks: list[str] = []
        for feat in sorted(g_dir.glob("*.feature")):
            try:
                feature_chunks.append(
                    f"## {feat.name}\n\n{feat.read_text(encoding='utf-8')}"
                )
            except OSError:
                continue
        if feature_chunks:
            parts.append("# Gherkin\n\n" + "\n\n".join(feature_chunks))

    return "\n\n---\n\n".join(parts)


def _render_directions(summary: str, directions: list[dict[str, Any]]) -> None:
    """Render a Rich table of suggested ADR directions."""
    if not directions:
        console.print(
            Panel(
                "[dim]No new ADR directions suggested. The agent thinks the "
                "decision space is well-covered.[/dim]",
                title="Suggested ADR directions",
                border_style="green",
            )
        )
        if summary:
            console.print(f"[dim]{summary}[/dim]")
        return

    tbl = Table(
        title="Suggested ADR directions",
        show_lines=True,
        title_style="bold",
        header_style="bold",
    )
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Priority", width=8)
    tbl.add_column("Proposed title", style="bold cyan")
    tbl.add_column("Why now")
    tbl.add_column("Gap closed", style="dim")

    for idx, d in enumerate(directions, 1):
        prio = (d.get("priority") or "medium").lower()
        prio_text = Text(prio.upper(), style=_PRIORITY_STYLE.get(prio, "yellow"))
        tbl.add_row(
            str(idx),
            prio_text,
            d.get("title", ""),
            d.get("rationale", ""),
            d.get("gap_filled", "") or "—",
        )

    console.print(tbl)
    if summary:
        console.print()
        console.print(Panel(summary, title="Coverage", border_style="dim"))


def fetch_and_render_directions(
    client: SdlicitClient,
    working_dir: str,
) -> list[dict[str, Any]]:
    """Run the suggest-directions backend call and render the result.

    Returns the raw direction dicts so callers (e.g. the create-ADR
    auto-trigger) can pipe a chosen title back into the wizard.
    """
    brief = _load_brief(working_dir)
    if not brief:
        console.print(
            Panel(
                "[yellow]No SOW found.[/yellow]\n\n"
                "Run [bold]Create SOW[/bold] (Intake) first — the suggestions "
                "are grounded in the Brief.",
                title="Suggest ADR directions",
                border_style="yellow",
            )
        )
        return []

    downstream = _load_downstream_artifacts(working_dir)

    with console.status(
        "[bold]Asking the ADR agent which decisions need to be made…[/bold]"
    ):
        try:
            resp = client.suggest_adr_directions(
                brief=brief,
                project_dir=working_dir,
                downstream_artifacts=downstream,
            )
        except Exception as exc:
            console.print(f"[red]✗  Failed to fetch suggestions:[/red] {exc}")
            return []

    directions: list[dict[str, Any]] = list(resp.get("directions", []))
    summary = str(resp.get("summary", ""))
    _render_directions(summary, directions)
    return directions


def action_suggest_adr_directions(client: SdlicitClient, working_dir: str) -> None:
    """Standalone menu action — render the ADR-direction table."""
    console.print(
        Panel(
            "Ideation pass — the ADR agent reads the Brief, prior ADRs and any "
            "downstream artifacts (personas / stories / Gherkin) and proposes "
            "WHICH decisions you should be writing ADRs for.\n\n"
            "[dim]No solutions are proposed — only the decision space.[/dim]",
            title="Suggest ADR directions",
            border_style="green",
        )
    )
    fetch_and_render_directions(client, working_dir)


def offer_directions_then_prompt_title(
    client: SdlicitClient, working_dir: str
) -> str | None:
    """Auto-trigger variant for the create-ADR wizard.

    Asks the user whether they want suggestions; if yes, fetches and
    renders them and lets the user pick one (its title becomes the
    prefilled ADR title) or proceed without picking.

    Returns the chosen title string, or ``None`` if the user declined
    suggestions or did not pick one.
    """
    want = Prompt.ask(
        "[bold]Want AI suggestions for which ADR to write?[/bold] " "[dim](y/N)[/dim]",
        choices=["y", "n", "yes", "no"],
        default="n",
        show_choices=False,
    )
    if want.lower() not in ("y", "yes"):
        return None

    directions = fetch_and_render_directions(client, working_dir)
    if not directions:
        return None

    choice = Prompt.ask(
        "\n[bold]Pick a number to use as the ADR title, or press Enter to "
        "skip and write your own[/bold]",
        default="",
    ).strip()
    if not choice:
        return None
    try:
        idx = int(choice)
    except ValueError:
        console.print("[dim]Not a number — proceeding without a prefilled title.[/dim]")
        return None
    if not (1 <= idx <= len(directions)):
        console.print("[dim]Out of range — proceeding without a prefilled title.[/dim]")
        return None

    picked = directions[idx - 1]
    title = str(picked.get("title", "")).strip()
    if title:
        console.print(f"[green]✓[/green] Using title: [bold]{title}[/bold]")
    return title or None
