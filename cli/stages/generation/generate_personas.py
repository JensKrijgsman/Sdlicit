"""Generation stage — generate user personas with Socratic engagement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from shared.files import (
    latest_srs,
    list_adr_files,
    personas_md_path,
    write_personas,
    save_artifact_via_backend,
)
from shared.review import prompt_review
from shared.socratic import run_socratic_loop

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def _render_personas(personas: list[dict]) -> None:
    table = Table(title="Generated Personas", show_lines=True)
    table.add_column("Name", style="cyan")
    table.add_column("Role", style="green")
    table.add_column("Goals")
    table.add_column("Frustrations", style="red")
    for p in personas:
        table.add_row(
            p.get("name", ""),
            p.get("role", ""),
            "\n".join(f"• {g}" for g in p.get("goals", [])),
            "\n".join(f"• {f}" for f in p.get("frustrations", [])),
        )
    console.print(table)


def action_generate_personas(client: "SdlicitClient", working_dir: str) -> None:
    """Generate personas from ADRs + (optional) latest SRS, with Socratic engagement."""
    console.print(Rule("[bold]Generate Personas[/bold]"))
    files = list_adr_files(working_dir)
    if files:
        console.print(f"  Found {len(files)} ADR(s) — using as context.")
    else:
        console.print(
            "  [yellow]No ADRs found[/yellow] — generation will rely on SRS only."
        )

    srs_path = latest_srs(working_dir)
    srs_content = ""
    if srs_path is not None:
        try:
            srs_content = srs_path.read_text(encoding="utf-8")
            console.print(f"  Using SRS: [dim]{srs_path.name}[/dim]")
        except OSError:
            srs_content = ""

    personas: list[dict] = []
    extra_notes = ""
    while True:
        effective_srs = srs_content + (
            f"\n\n[user notes]\n{extra_notes}" if extra_notes else ""
        )

        def _call(clarifications: list[dict[str, Any]]) -> dict[str, Any]:
            return client.generate_personas(
                project_dir=working_dir,
                srs_content=effective_srs,
                clarifications=clarifications,
            )

        try:
            data = run_socratic_loop(_call, status_message="Generating personas…")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

        personas = data.get("personas") or []
        if not personas:
            console.print(
                Panel(
                    data.get("raw_suggestion", "") or "(No personas generated)",
                    title="Raw Output",
                    border_style="dim",
                )
            )
            return

        _render_personas(personas)

        outcome = prompt_review(
            artifact_label="personas",
            current_content=_personas_to_md(personas),
            suffix=".md",
        )
        if outcome.action == "skip":
            console.print("[dim]Discarded.[/dim]")
            return
        if outcome.action == "regenerate":
            extra_notes = outcome.notes or extra_notes
            continue
        # accept or edit → save
        if outcome.action == "edit":
            console.print(
                "[yellow]Manual edit applied — markdown only; structured JSON keeps original AI output.[/yellow]"
            )
            json_path, md_path = write_personas(working_dir, personas)
            personas_md_path(working_dir).write_text(outcome.content, encoding="utf-8")
            console.print(
                f"[green]✓[/green] Saved [bold]{json_path}[/bold] + [bold]{md_path}[/bold]"
            )
            _ingest_personas(client, md_path)
            return

        json_path, md_path = write_personas(working_dir, personas)
        console.print(
            f"[green]✓[/green] Saved [bold]{json_path}[/bold] + [bold]{md_path}[/bold]"
        )
        # Backend-canonical save for traceability
        save_artifact_via_backend(
            client, "personas", {"personas": personas}, working_dir=working_dir
        )
        _ingest_personas(client, md_path)
        return


def _ingest_personas(client: "SdlicitClient", md_path) -> None:
    """Best-effort auto-ingest of personas markdown into the KB."""
    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError:
        return
    resp = client.ingest_artifact(
        text=content, artifact_type="personas", name="main", replace=True
    )
    if resp.get("chunks"):
        console.print(f"[dim]✓ Ingested into KB ({resp['chunks']} chunks)[/dim]")


def _personas_to_md(personas: list[dict]) -> str:
    lines = ["# Personas", ""]
    for p in personas:
        lines.append(f"## {p.get('name', '?')} — {p.get('role', '?')}")
        if p.get("goals"):
            lines.append("**Goals:**")
            lines.extend(f"- {g}" for g in p["goals"])
        if p.get("frustrations"):
            lines.append("**Frustrations:**")
            lines.extend(f"- {f}" for f in p["frustrations"])
        lines.append("")
    return "\n".join(lines)


# Re-export Markdown to avoid lint complaint about unused import in some contexts
_ = Markdown
