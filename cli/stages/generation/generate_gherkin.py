"""Generation stage — generate Gherkin scenarios with Socratic engagement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.syntax import Syntax

from shared.files import (
    latest_srs,
    load_personas,
    load_stories,
    write_gherkin,
    save_artifact_via_backend,
)
from shared.review import prompt_review
from shared.socratic import run_socratic_loop

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_generate_gherkin(client: "SdlicitClient", working_dir: str) -> None:
    """Generate Gherkin scenarios with Socratic engagement; save to artifacts/gherkin/."""
    console.print(Rule("[bold]Generate Gherkin Scenarios[/bold]"))

    personas = load_personas(working_dir)
    if not personas:
        console.print(
            "[yellow]No personas.json found.[/yellow]  "
            "Run [bold]Generate personas[/bold] first, or enter one manually."
        )
        name = Prompt.ask("  Persona name", default="user")
        role = Prompt.ask("  Persona role", default="user")
        personas = [{"name": name, "role": role, "goals": [], "frustrations": []}]
    else:
        console.print(
            f"  Using existing personas ({len(personas)}): "
            + ", ".join(p.get("name", "?") for p in personas[:5])
        )

    # Pick the persona to focus this feature file on
    if len(personas) == 1:
        persona = personas[0]
    else:
        names = [p.get("name", f"persona_{i}") for i, p in enumerate(personas)]
        for i, n in enumerate(names):
            console.print(f"  [bold]{i + 1}[/bold]. {n}")
        idx_str = Prompt.ask(
            "Generate Gherkin for which persona? (number)",
            choices=[str(i + 1) for i in range(len(names))],
            default="1",
        )
        persona = personas[int(idx_str) - 1]

    stories = load_stories(working_dir)
    srs_path = latest_srs(working_dir)
    requirements = ""
    if stories:
        requirements = "\n".join(
            f"- {s.get('story_id', '?')}: {s.get('statement', '')}" for s in stories
        )
        console.print(f"  Using {len(stories)} user story/stories as requirements.")
    elif srs_path is not None:
        try:
            requirements = srs_path.read_text(encoding="utf-8")
            console.print(f"  Using SRS: [dim]{srs_path.name}[/dim]")
        except OSError:
            requirements = ""
    if not requirements.strip():
        requirements = Prompt.ask("Requirements / acceptance criteria")

    gherkin = ""
    extra_notes = ""
    while True:
        effective_reqs = requirements + (
            f"\n\n[user notes]\n{extra_notes}" if extra_notes else ""
        )

        def _call(clarifications: list[dict[str, Any]]) -> dict[str, Any]:
            return client.generate_gherkin(
                project_dir=working_dir,
                personas=[persona],
                requirements=effective_reqs,
                clarifications=clarifications,
            )

        try:
            data = run_socratic_loop(_call, status_message="Generating Gherkin…")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

        gherkin = data.get("gherkin", "") or gherkin
        if not gherkin:
            console.print(
                Panel("(No Gherkin generated)", title="Raw Output", border_style="dim")
            )
            return

        syntax = Syntax(gherkin, "gherkin", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title="Generated Gherkin", border_style="green"))

        outcome = prompt_review(
            artifact_label="Gherkin feature",
            current_content=gherkin,
            suffix=".feature",
        )
        if outcome.action == "skip":
            console.print("[dim]Discarded.[/dim]")
            return
        if outcome.action == "regenerate":
            extra_notes = outcome.notes or extra_notes
            continue
        if outcome.action == "edit":
            gherkin = outcome.content

        slug = persona.get("name", "scenarios")
        out_path = write_gherkin(working_dir, slug, gherkin)
        console.print(f"[green]✓[/green] Saved [bold]{out_path}[/bold]")
        # Backend-canonical save for traceability
        save_artifact_via_backend(
            client,
            "bdd",
            {"persona": persona.get("name", ""), "gherkin": gherkin},
            working_dir=working_dir,
        )
        return
