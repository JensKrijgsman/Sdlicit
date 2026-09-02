"""Expansion stage — traceability dashboard and coverage checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.tree import Tree
from shared.files import list_adr_files, read_adr

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_traceability_dashboard(client: SdlicitClient, working_dir: str) -> None:
    """Display the traceability graph and coverage metrics."""
    console.print(Rule("[bold]Traceability Dashboard[/bold]"))

    # 1. Fetch trace coverage
    with console.status("[bold]Computing trace coverage…[/bold]"):
        try:
            coverage = client.get_trace_coverage(mode="structural")
        except Exception as exc:
            console.print(f"[red]Error fetching coverage:[/red] {exc}")
            return

    # Summary metrics
    table = Table(title="Coverage Summary", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    pct = coverage.get("structural_coverage_pct", 0)
    colour = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    table.add_row("Structural Coverage", f"[{colour}]{pct:.1f}%[/{colour}]")
    table.add_row("Total Links", str(coverage.get("total_links", 0)))
    table.add_row("Valid Links", str(coverage.get("valid_links", 0)))
    table.add_row("Broken Links", str(coverage.get("broken_links_count", 0)))
    table.add_row("Has Conflicts", str(coverage.get("has_conflicts", False)))

    sem_pct = coverage.get("semantic_coverage_pct")
    if sem_pct is not None:
        colour = "green" if sem_pct >= 80 else "yellow" if sem_pct >= 50 else "red"
        table.add_row("Semantic Coverage", f"[{colour}]{sem_pct:.1f}%[/{colour}]")

    # Artifact counts
    counts = coverage.get("artifact_counts", {})
    if counts:
        table.add_section()
        for atype, count in sorted(counts.items()):
            table.add_row(f"  {atype}", str(count))

    console.print(table)
    console.print()

    # 2. Per-artifact breakdown
    artifacts = coverage.get("artifacts", [])
    if artifacts:
        art_table = Table(title="Per-Artifact Coverage", show_lines=True)
        art_table.add_column("ID", style="cyan")
        art_table.add_column("Type", style="dim")
        art_table.add_column("Outgoing", justify="right")
        art_table.add_column("Valid", justify="right")
        art_table.add_column("Broken", justify="right", style="red")
        art_table.add_column("Semantic", justify="right")

        for a in artifacts:
            sem = (
                f"{a.get('semantic_score', 0):.2f}"
                if a.get("semantic_score") is not None
                else "—"
            )
            art_table.add_row(
                a.get("artifact_id", ""),
                a.get("artifact_type", ""),
                str(a.get("outgoing_links", 0)),
                str(a.get("valid_links", 0)),
                str(a.get("broken_links", 0)),
                sem,
            )
        console.print(art_table)
        console.print()

    # 3. Graph issues
    issues = coverage.get("graph_issues", [])
    if issues:
        console.print(
            Panel(
                "\n".join(
                    f"[{_severity_colour(i.get('severity', 'warning'))}]"
                    f"• {i.get('message', '')}[/{_severity_colour(i.get('severity', 'warning'))}]"
                    for i in issues[:20]
                ),
                title="Graph Issues",
                border_style="yellow",
            )
        )
    elif not coverage.get("has_conflicts"):
        console.print("[green]✓ No graph issues detected[/green]")

    # 4. Conflict assessment
    assessment = coverage.get("conflict_assessment", "")
    if assessment:
        console.print(
            Panel(assessment, title="Conflict Assessment", border_style="red")
        )

    # 5. Fetch and render the graph tree
    console.print()
    with console.status("[bold]Fetching traceability graph…[/bold]"):
        try:
            graph = client.get_traceability_graph()
        except Exception as exc:
            console.print(f"[dim]Graph unavailable: {exc}[/dim]")
            return

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        console.print("[dim]No nodes in the traceability graph yet.[/dim]")
        return

    tree = Tree("[bold]Traceability Graph[/bold]")
    # Group nodes by type
    by_type: dict[str, list] = {}
    for n in nodes:
        by_type.setdefault(n.get("type", "unknown"), []).append(n)

    for ntype, nlist in sorted(by_type.items()):
        branch = tree.add(f"[bold cyan]{ntype}[/bold cyan] ({len(nlist)})")
        for n in nlist[:15]:  # cap at 15 per type for readability
            status = n.get("status", "")
            label = f"{n.get('id', '?')} — {n.get('title', '')}"
            if status:
                label += f" [{status}]"
            branch.add(label)
        if len(nlist) > 15:
            branch.add(f"[dim]… +{len(nlist) - 15} more[/dim]")

    console.print(tree)
    console.print(f"\n[dim]{len(nodes)} nodes, {len(edges)} edges[/dim]")


def action_check_artifact_traceability(client: SdlicitClient, working_dir: str) -> None:
    """Per-artifact link check: pick an ADR, validate its links, suggest implements."""
    console.print(Rule("[bold]Check Artifact Traceability[/bold]"))

    files = list_adr_files(working_dir)
    if not files:
        console.print("[yellow]No ADRs found — nothing to check.[/yellow]")
        return

    table = Table(title="ADRs", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Filename", style="cyan")
    table.add_column("Title")
    for idx, f in enumerate(files, 1):
        table.add_row(str(idx), f["filename"], f.get("title") or "—")
    console.print(table)

    choice = Prompt.ask(
        "Pick an ADR to check", choices=[str(i) for i in range(1, len(files) + 1)]
    )
    entry = files[int(choice) - 1]
    filename = entry["filename"]
    if not filename:
        return
    artifact_id = filename.replace(".md", "")
    content = read_adr(working_dir, filename)

    with console.status(f"[bold]Checking {artifact_id}…[/bold]"):
        try:
            result = client.check_traceability(
                artifact_id, artifact_content=content, project_dir=working_dir
            )
        except Exception as exc:
            console.print(f"[red]Error checking traceability:[/red] {exc}")
            return

    issues = result.get("issues", [])
    if issues:
        console.print(
            Panel(
                "\n".join(
                    f"[{_severity_colour(i.get('severity', 'warning'))}]"
                    f"• {i.get('message', '')}[/{_severity_colour(i.get('severity', 'warning'))}]"
                    for i in issues
                ),
                title=f"Issues — {artifact_id}",
                border_style="yellow",
            )
        )
    else:
        console.print(f"[green]✓ No link issues found for {artifact_id}[/green]")

    impacted = result.get("impacted_nodes", [])
    if impacted:
        console.print(f"[dim]Impacted if changed:[/dim] {', '.join(impacted)}")

    suggested = result.get("suggested_implements", [])
    if suggested:
        console.print(f"[bold]Suggested implements:[/bold] {', '.join(suggested)}")

    if result.get("has_conflicts"):
        assessment = result.get("coverage_assessment", "")
        console.print(Panel(assessment or "Conflicts detected.", title="Conflicts", border_style="red"))


def _severity_colour(severity: str) -> str:
    return {"error": "red", "warning": "yellow", "info": "dim"}.get(severity, "white")
