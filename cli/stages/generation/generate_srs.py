"""Generation stage — generate SRS from the latest SOW with Socratic engagement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from shared.files import latest_sow, save_artifact_via_backend, write_srs
from shared.review import prompt_review
from shared.socratic import run_socratic_loop

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_generate_srs(client: SdlicitClient, working_dir: str) -> None:
    """Generate a structured SRS document from the latest SOW."""
    console.print(Rule("[bold]Generate SRS (from SOW)[/bold]"))

    sow_path = latest_sow(working_dir)
    if sow_path is None:
        console.print(
            Panel(
                "No SOW found in [bold].sdlicit/artifacts/[/bold].\n"
                "Run [bold]Create SOW from brief[/bold] first.",
                title="Generate SRS",
                border_style="yellow",
            )
        )
        return

    try:
        sow_content = sow_path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Could not read SOW:[/red] {exc}")
        return
    console.print(f"  Source SOW: [dim]{sow_path.name}[/dim]\n")

    srs_md = ""
    extra_notes = ""
    while True:
        effective_sow = sow_content + (
            f"\n\n[user notes]\n{extra_notes}" if extra_notes else ""
        )

        def _call(
            clarifications: list[dict[str, Any]], _sow: str = effective_sow
        ) -> dict[str, Any]:
            return client.generate_srs(
                project_dir=working_dir,
                sow_content=_sow,
                clarifications=clarifications,
            )

        try:
            data = run_socratic_loop(_call, status_message="Generating SRS…")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

        srs_md = data.get("srs_markdown", "") or srs_md
        if not srs_md:
            console.print(
                Panel(
                    data.get("raw_suggestion", "") or "(No SRS generated)",
                    title="Raw Output",
                    border_style="dim",
                )
            )
            return

        console.print()
        console.print(
            Panel(Markdown(srs_md), title="Generated SRS", border_style="green")
        )

        outcome = prompt_review(
            artifact_label="SRS",
            current_content=srs_md,
            suffix=".md",
        )
        if outcome.action == "skip":
            console.print("[dim]Discarded.[/dim]")
            return
        if outcome.action == "regenerate":
            extra_notes = outcome.notes or extra_notes
            continue
        if outcome.action == "edit":
            srs_md = outcome.content

        out_path = write_srs(working_dir, srs_md, slug="requirements")
        console.print(f"[green]✓[/green] Saved [bold]{out_path}[/bold]")

        # Try backend-canonical save for consistent naming/traceability
        save_artifact_via_backend(
            client, "srs", {"markdown": srs_md}, working_dir=working_dir
        )

        ingest_resp = client.ingest_artifact(
            text=srs_md, artifact_type="srs", name=out_path.stem, replace=True
        )
        if ingest_resp.get("chunks"):
            console.print(
                f"[dim]✓ Ingested into KB ({ingest_resp['chunks']} chunks)[/dim]"
            )
        return
