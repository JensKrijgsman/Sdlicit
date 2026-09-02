"""Guided flow — sequenced walkthrough of the full SDLC artifact pipeline.

Detects which artifacts are already present, shows the user a summary,
and then runs each missing step (or offers to regenerate completed ones).

Steps:
  1. SOW          — required first artifact
  2. SRS          — optional but recommended; from latest SOW
  3. Personas     — from ADRs + latest SRS
  4. User stories — from personas + SRS
  5. ADR(s)       — reuses the existing ADR wizard, looped
  6. Gherkin      — from personas + stories

Each generation step is Socratic-aware (handled in its action) so the
user is engaged with thought-provoking questions before accepting any
AI output. After each step the user can Accept / Regenerate / Edit / Skip.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.rule import Rule
from rich.table import Table
from shared.files import (
    list_adr_files,
    list_gherkin_files,
    list_sow_files,
    list_srs_files,
    load_personas,
    load_stories,
)

from stages.composing.create_adr import action_create_adr
from stages.generation.generate_gherkin import action_generate_gherkin
from stages.generation.generate_personas import action_generate_personas
from stages.generation.generate_srs import action_generate_srs
from stages.generation.generate_stories import action_generate_stories
from stages.intake.create_sow import action_create_sow

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


# ── Status detection ──────────────────────────────────────────────────────


@dataclass
class ArtifactStatus:
    sow: int
    srs: int
    personas: int
    stories: int
    adrs: int
    gherkin: int

    @classmethod
    def scan(cls, working_dir: str) -> ArtifactStatus:
        return cls(
            sow=len(list_sow_files(working_dir)),
            srs=len(list_srs_files(working_dir)),
            personas=len(load_personas(working_dir)),
            stories=len(load_stories(working_dir)),
            adrs=len(list_adr_files(working_dir)),
            gherkin=len(list_gherkin_files(working_dir)),
        )


def _render_status(status: ArtifactStatus) -> None:
    table = Table(title="Workspace artifacts", show_header=True, header_style="bold")
    table.add_column("Artifact", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Status")

    def row(name: str, count: int, optional: bool = False) -> None:
        if count > 0:
            table.add_row(name, str(count), "[green]✓ present[/green]")
        elif optional:
            table.add_row(name, "0", "[yellow]optional — missing[/yellow]")
        else:
            table.add_row(name, "0", "[red]missing[/red]")

    row("SOW", status.sow)
    row("SRS", status.srs, optional=True)
    row("Personas", status.personas)
    row("User stories", status.stories)
    row("ADR(s)", status.adrs)
    row("Gherkin features", status.gherkin)
    console.print(table)


# ── Step helpers ──────────────────────────────────────────────────────────


@dataclass
class _Step:
    key: str
    title: str
    optional: bool
    is_done: Callable[[ArtifactStatus], bool]
    run: Callable[[], None]


def _build_steps(client: SdlicitClient, working_dir: str) -> list[_Step]:
    return [
        _Step(
            key="sow",
            title="Statement of Work",
            optional=False,
            is_done=lambda s: s.sow > 0,
            run=lambda: action_create_sow(client, working_dir),
        ),
        _Step(
            key="srs",
            title="Software Requirements Specification",
            optional=True,
            is_done=lambda s: s.srs > 0,
            run=lambda: action_generate_srs(client, working_dir),
        ),
        _Step(
            key="personas",
            title="User personas",
            optional=False,
            is_done=lambda s: s.personas > 0,
            run=lambda: action_generate_personas(client, working_dir),
        ),
        _Step(
            key="stories",
            title="User stories",
            optional=False,
            is_done=lambda s: s.stories > 0,
            run=lambda: action_generate_stories(client, working_dir),
        ),
        _Step(
            key="adr",
            title="ADR(s)",
            optional=False,
            is_done=lambda s: s.adrs > 0,
            run=lambda: _adr_loop(client, working_dir),
        ),
        _Step(
            key="gherkin",
            title="Gherkin feature files",
            optional=False,
            is_done=lambda s: s.gherkin > 0,
            run=lambda: action_generate_gherkin(client, working_dir),
        ),
    ]


def _adr_loop(client: SdlicitClient, working_dir: str) -> None:
    """Allow the user to create N ADRs in a row."""
    while True:
        action_create_adr(client, working_dir)
        if not Confirm.ask("Create another ADR?", default=False):
            return


def _run_trace_check(client: SdlicitClient, step_key: str) -> None:
    """Run a lightweight traceability check after a stage completes.

    Shows a quick coverage summary and any issues detected.
    Non-blocking: failures are silently ignored.
    """
    try:
        coverage = client.get_trace_coverage(mode="structural")
    except Exception:
        return

    total = coverage.get("total_links", 0)
    valid = coverage.get("valid_links", 0)
    broken = coverage.get("broken_links_count", 0)
    pct = coverage.get("structural_coverage_pct", 0)

    if total == 0:
        return  # No links to check yet

    colour = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
    console.print(
        f"  [dim]Traceability:[/dim] [{colour}]{pct:.0f}% coverage[/{colour}] "
        f"({valid}/{total} links valid"
        f"{f', {broken} broken' if broken else ''})"
    )

    issues = coverage.get("graph_issues", [])
    if issues:
        for issue in issues[:3]:
            sev = issue.get("severity", "warning")
            col = "red" if sev == "error" else "yellow"
            console.print(f"    [{col}]⚠ {issue.get('message', '')}[/{col}]")
        if len(issues) > 3:
            console.print(f"    [dim]… +{len(issues) - 3} more issues[/dim]")


# ── Main entry ────────────────────────────────────────────────────────────


def action_guided_flow(client: SdlicitClient, working_dir: str) -> None:
    """Run the guided SDLC artifact flow."""
    console.print(
        Panel(
            "[bold]Guided flow[/bold]\n\n"
            "I'll walk you through the SDLC artifacts in order:\n"
            "  1. SOW   2. SRS (optional)   3. Personas   4. Stories\n"
            "  5. ADR(s)   6. Gherkin scenarios\n\n"
            "Each step uses Socratic questioning so you stay in control "
            "of every AI-generated artifact.",
            border_style="bright_blue",
        )
    )

    status = ArtifactStatus.scan(working_dir)
    _render_status(status)

    steps = _build_steps(client, working_dir)

    if all(step.is_done(status) or step.optional for step in steps):
        console.print(
            "\n[green]All required artifacts already exist.[/green]  "
            "You can still regenerate any of them below."
        )

    for step in steps:
        console.print()
        console.print(Rule(f"[bold]Step: {step.title}[/bold]"))

        status = ArtifactStatus.scan(working_dir)
        already = step.is_done(status)

        if already:
            console.print(f"  [green]✓ {step.title} already present.[/green]")
            if not Confirm.ask(
                f"Regenerate / add more {step.title.lower()}?", default=False
            ):
                continue
        elif step.optional:
            console.print(f"  [yellow]{step.title} is optional.[/yellow]")
            if not Confirm.ask(f"Generate {step.title} now?", default=True):
                continue
        else:
            if not Confirm.ask(
                f"Continue with [bold]{step.title}[/bold]?", default=True
            ):
                console.print("[yellow]Stopping guided flow.[/yellow]")
                return

        try:
            step.run()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            if not Confirm.ask("Continue with the next step?", default=False):
                return

        # Run traceability check between stages
        _run_trace_check(client, step.key)

    console.print()
    console.print(
        Panel(
            "[bold green]Guided flow complete![/bold green]\n\n"
            "You now have the full set of artifacts in [bold].sdlicit/artifacts/[/bold].\n"
            "From here you can:\n"
            "  • Add additional ADRs as new architectural decisions arise\n"
            "  • Re-run [bold]Expand ADR[/bold] to get cross-agent reviews\n"
            "  • Check [bold]Traceability dashboard[/bold] for coverage metrics\n"
            "  • Query the knowledge base for any of your decisions",
            border_style="green",
        )
    )

    # Final traceability summary
    console.print()
    _run_trace_check(client, "final")
