"""CLI action: Create SOW from raw brief text."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from shared.files import next_sow_filename, save_artifact_via_backend, write_sow
from shared.review import prompt_review
from shared.socratic import run_socratic_loop

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def _read_multiline(prompt: str) -> str:
    console.print()
    console.print(f"[bold]{prompt}[/bold] (empty line to finish):")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "" and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _render_progress_table(sections: list[dict[str, Any]]) -> Table:
    """Build a Rich table showing section generation progress."""
    table = Table(title="SOW Generation Progress", show_lines=True)
    table.add_column("Section", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Socratic", justify="center")
    table.add_column("KB", justify="center")

    for s in sections:
        status_map = {
            "pending": "[dim]○ Pending[/dim]",
            "generating": "[yellow]◉ Generating…[/yellow]",
            "complete": "[green]✓ Done[/green]",
        }
        status = status_map.get(s["status"], s["status"])

        socratic = ""
        if s.get("probe"):
            socratic = f"[magenta]⚡ {s['probe']['style']}[/magenta]"
        elif s.get("needs_socratic"):
            socratic = "[yellow]⚠ Flagged[/yellow]"

        kb = ""
        if s.get("kb_grounded") is True:
            kb = "[green]✓[/green]"
        elif s.get("kb_grounded") is False:
            kb = "[red]⚠ Ungrounded[/red]"

        table.add_row(s["heading"], status, socratic, kb)

    return table


def action_create_sow(client: SdlicitClient, working_dir: str) -> None:
    """Interactively create a SOW from a raw project brief."""
    console.print(
        Panel(
            "[bold]Create Statement of Work[/bold]\n\n"
            "Paste your raw project brief, meeting notes, or brain dump below.\n"
            "The SOW Agent will extract a structured Statement of Work\n"
            "with stakeholders, requirements, and open questions.\n\n"
            "[dim]Tip: paste multi-line text, then press Enter on an empty line to submit.[/dim]",
            border_style="bright_cyan",
        )
    )

    raw_text = _read_multiline("Enter your brief")
    if not raw_text:
        console.print("[yellow]No input provided — cancelled.[/yellow]")
        return

    console.print(f"\n[dim]Brief length: {len(raw_text)} characters[/dim]")

    sow_markdown = ""
    extra_notes = ""
    while True:
        brief = raw_text + (f"\n\n[user notes]\n{extra_notes}" if extra_notes else "")

        # Try streaming endpoint first
        sow_markdown = _stream_sow(client, brief) or ""

        if not sow_markdown:
            # Fallback to synchronous endpoint
            def _call(
                clarifications: list[dict[str, Any]], _brief: str = brief
            ) -> dict[str, Any]:
                return client.create_sow(_brief, clarifications=clarifications)

            data = run_socratic_loop(
                _call, status_message="SOW Agent is analysing your brief…"
            )
            sow_markdown = data.get("sow_markdown", "") or sow_markdown

        if not sow_markdown:
            console.print("[yellow]Agent returned empty result.[/yellow]")
            return

        console.print()
        console.print(
            Panel(Markdown(sow_markdown), title="Generated SOW", border_style="green")
        )

        outcome = prompt_review(
            artifact_label="SOW",
            current_content=sow_markdown,
            suffix=".md",
        )
        if outcome.action == "skip":
            console.print("[dim]Discarded.[/dim]")
            return
        if outcome.action == "regenerate":
            extra_notes = outcome.notes or extra_notes
            continue
        if outcome.action == "edit":
            sow_markdown = outcome.content
        # accept or edit → fall through to save

        if Confirm.ask("\nSave this SOW?", default=True):
            title_hint = sow_markdown.split("\n", 1)[0].strip("# ").strip()
            filename = next_sow_filename(working_dir, title_hint)
            out_path = write_sow(working_dir, filename, sow_markdown)
            console.print(f"[green]✓[/green] Saved to [bold]{out_path}[/bold]")
            # Backend-canonical save for traceability
            save_artifact_via_backend(
                client, "sow", {"markdown": sow_markdown, "title": title_hint},
                working_dir=working_dir,
            )
        return


def _stream_sow(client: SdlicitClient, brief: str) -> str | None:
    """Stream SOW generation section-by-section with Rich Live display.

    Returns the full markdown on success, or None on failure/fallback.
    """
    section_order = [
        ("project_name", "Project Name"),
        ("problem_statement", "Problem Statement"),
        ("stakeholders", "Stakeholders"),
        ("functional_requirements", "Functional Requirements"),
        ("non_functional_requirements", "Non-Functional Requirements"),
        ("constraints", "Constraints"),
        ("out_of_scope", "Out of Scope"),
        ("open_questions", "Open Questions"),
    ]

    sections: list[dict[str, Any]] = [
        {
            "key": k,
            "heading": h,
            "status": "pending",
            "content": "",
            "needs_socratic": False,
            "probe": None,
            "kb_grounded": None,
        }
        for k, h in section_order
    ]
    full_markdown = ""

    try:
        with (
            Live(
                _render_progress_table(sections), console=console, refresh_per_second=4
            ) as live,
            client.create_sow_stream(brief) as events,
        ):
            for event in events:
                ev_type = event.get("event")
                section_key = event.get("section")
                idx = next(
                    (i for i, s in enumerate(sections) if s["key"] == section_key), -1
                )

                if ev_type == "section_start" and idx >= 0:
                    sections[idx]["status"] = "generating"
                    live.update(_render_progress_table(sections))

                elif ev_type == "section_complete" and idx >= 0:
                    sections[idx]["status"] = "complete"
                    sections[idx]["content"] = event.get("content", "")
                    sections[idx]["needs_socratic"] = event.get("needs_socratic", False)
                    live.update(_render_progress_table(sections))

                    # Show the section content immediately
                    md_text = event.get("markdown", "")
                    if md_text:
                        console.print(Panel(Markdown(md_text), border_style="dim"))

                elif ev_type == "socratic_probe":
                    probe = event.get("probe", {})
                    if idx >= 0:
                        sections[idx]["probe"] = probe
                        live.update(_render_progress_table(sections))
                    if probe.get("question"):
                        console.print(
                            Panel(
                                f"[bold magenta]Socratic Probe[/bold magenta] ({probe.get('style', '')})\n\n"
                                f"{probe.get('question', '')}",
                                border_style="magenta",
                                title=f"Section: {event.get('section', '')}",
                            )
                        )

                elif ev_type == "kb_verification" and idx >= 0:
                    sections[idx]["kb_grounded"] = event.get("grounded")
                    live.update(_render_progress_table(sections))

                elif ev_type == "complete":
                    full_markdown = event.get("full_markdown", "")

        return full_markdown or None

    except Exception as exc:
        console.print(
            f"[dim]Streaming unavailable ({exc}), falling back to synchronous…[/dim]"
        )
        return None
