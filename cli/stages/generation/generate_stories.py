"""Generation stage — generate user stories with Socratic engagement."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from shared.files import (
    latest_srs,
    list_adr_files,
    load_personas,
    save_artifact_via_backend,
    write_stories,
)
from shared.review import prompt_review
from shared.socratic import run_socratic_loop

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def _render_stories(stories: list[dict]) -> None:
    table = Table(title="Generated User Stories", show_lines=True)
    table.add_column("ID", style="cyan")
    table.add_column("Persona", style="green")
    table.add_column("Statement")
    table.add_column("Refs", style="dim")
    for s in stories:
        table.add_row(
            s.get("story_id", ""),
            s.get("persona_id", ""),
            s.get("statement", ""),
            ", ".join(s.get("requirement_ids", [])),
        )
    console.print(table)


def _stories_to_md(stories: list[dict]) -> str:
    lines = ["# User Stories", ""]
    for s in stories:
        sid = s.get("story_id", "?")
        pid = s.get("persona_id", "?")
        refs = ", ".join(s.get("requirement_ids", []))
        lines.append(
            f"- **{sid}** _(persona: {pid}; req: {refs})_ — {s.get('statement', '')}"
        )
    return "\n".join(lines)


def action_generate_stories(client: SdlicitClient, working_dir: str) -> None:
    files = list_adr_files(working_dir)
    if not files:
        console.print(
            Panel(
                "No ADRs found. Create ADRs first (or proceed with SRS-only context).",
                title="Generate User Stories",
                border_style="yellow",
            )
        )

    console.print(Rule("[bold]Generate User Stories[/bold]"))

    personas = load_personas(working_dir)
    if personas:
        console.print(
            f"  Using existing personas ({len(personas)}): "
            + ", ".join(p.get("name", "?") for p in personas[:5])
        )
        personas_json = json.dumps(personas)
    else:
        console.print("[yellow]No personas.json found.[/yellow]")
        personas_json = Prompt.ask("Personas JSON (paste or [] to skip)", default="[]")

    srs_path = latest_srs(working_dir)
    if srs_path is not None:
        try:
            requirements_text = srs_path.read_text(encoding="utf-8")
            console.print(f"  Using SRS: [dim]{srs_path.name}[/dim]")
        except OSError:
            requirements_text = ""
    else:
        requirements_text = Prompt.ask(
            "Requirements (paste or [] to skip)", default="[]"
        )

    stories: list[dict] = []
    extra_notes = ""
    while True:
        effective_reqs = requirements_text + (
            f"\n\n[user notes]\n{extra_notes}" if extra_notes else ""
        )

        def _call(
            clarifications: list[dict[str, Any]], _reqs: str = effective_reqs
        ) -> dict[str, Any]:
            return client.generate_stories(
                project_dir=working_dir,
                personas=personas_json,
                requirements=_reqs,
                clarifications=clarifications,
            )

        try:
            data = run_socratic_loop(_call, status_message="Generating user stories…")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

        stories = data.get("stories") or []
        if not stories:
            console.print(
                Panel(
                    data.get("raw_suggestion", "") or "(No stories generated)",
                    title="Raw Output",
                    border_style="dim",
                )
            )
            return

        _render_stories(stories)

        outcome = prompt_review(
            artifact_label="user stories",
            current_content=_stories_to_md(stories),
            suffix=".md",
        )
        if outcome.action == "skip":
            console.print("[dim]Discarded.[/dim]")
            return
        if outcome.action == "regenerate":
            extra_notes = outcome.notes or extra_notes
            continue

        json_path, md_path = write_stories(working_dir, stories)
        if outcome.action == "edit":
            md_path.write_text(outcome.content, encoding="utf-8")
        console.print(
            f"[green]✓[/green] Saved [bold]{json_path}[/bold] + [bold]{md_path}[/bold]"
        )
        # Backend-canonical save for traceability
        save_artifact_via_backend(
            client, "stories", {"stories": stories}, working_dir=working_dir
        )
        try:
            content = md_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if content:
            resp = client.ingest_artifact(
                text=content, artifact_type="stories", name="main", replace=True
            )
            if resp.get("chunks"):
                console.print(
                    f"[dim]✓ Ingested into KB ({resp['chunks']} chunks)[/dim]"
                )
        return
