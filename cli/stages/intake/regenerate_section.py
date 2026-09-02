"""Intake stage — regenerate a single SOW section with user feedback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from shared.files import latest_sow

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()

_SECTIONS = [
    "project_name",
    "problem_statement",
    "stakeholders",
    "functional_requirements",
    "non_functional_requirements",
    "constraints",
    "out_of_scope",
    "open_questions",
]


def action_regenerate_sow_section(client: SdlicitClient, working_dir: str) -> None:
    """Regenerate a single section of an existing SOW with user feedback."""
    console.print(Rule("[bold]Regenerate SOW Section[/bold]"))

    sow_path = latest_sow(working_dir)
    if sow_path is None:
        console.print(
            Panel(
                "No SOW found in [bold].sdlicit/artifacts/[/bold].\n"
                "Run [bold]Create SOW from brief[/bold] first.",
                border_style="yellow",
            )
        )
        return

    try:
        sow_content = sow_path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Could not read SOW:[/red] {exc}")
        return

    console.print(f"  Source: [dim]{sow_path.name}[/dim]")
    console.print()

    # Show available sections
    console.print("[bold]Available sections:[/bold]")
    for i, s in enumerate(_SECTIONS, 1):
        console.print(f"  [bold]{i}[/bold]. {s.replace('_', ' ').title()}")
    console.print()

    idx_str = Prompt.ask(
        "Section to regenerate",
        choices=[str(i) for i in range(1, len(_SECTIONS) + 1)],
    )
    section_name = _SECTIONS[int(idx_str) - 1]

    user_feedback = Prompt.ask(
        f"Feedback for [bold]{section_name.replace('_', ' ')}[/bold] "
        "(what to change/improve)",
        default="",
    )

    # Extract the raw brief (first line typically)
    raw_brief = sow_content

    with console.status(f"[bold]Regenerating {section_name}…[/bold]"):
        try:
            result = client.regenerate_sow_section(
                raw_brief=raw_brief,
                section_name=section_name,
                prior_sections=sow_content,
                user_feedback=user_feedback,
                current_content=sow_content,
            )
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

    new_content = result.get("content", "")
    if not new_content:
        console.print("[yellow]No content returned for this section.[/yellow]")
        return

    console.print()
    console.print(
        Panel(
            Markdown(new_content),
            title=f"Regenerated: {section_name.replace('_', ' ').title()}",
            border_style="green",
        )
    )

    # Show Socratic probe if present
    probe = result.get("socratic_probe")
    if probe and probe.get("question"):
        console.print(
            Panel(
                f"[bold magenta]Socratic Probe[/bold magenta] ({probe.get('style', '')})\n\n"
                f"{probe.get('question', '')}",
                border_style="magenta",
            )
        )

    # Ask if user wants to replace the section in the SOW
    from rich.prompt import Confirm

    if Confirm.ask("\nReplace this section in the SOW file?", default=True):
        # Replace section in content
        updated = _replace_section(sow_content, section_name, new_content)
        sow_path.write_text(updated, encoding="utf-8")
        console.print(f"[green]✓[/green] Updated [bold]{sow_path}[/bold]")
    else:
        console.print("[dim]Section not replaced.[/dim]")


def _replace_section(sow_content: str, section_name: str, new_content: str) -> str:
    """Replace a section in a SOW markdown file.

    Sections are identified by ``## <Title>`` headings.
    """
    import re

    heading = section_name.replace("_", " ").title()
    # Match from "## <heading>" to the next "## " or end of string
    pattern = rf"(## {re.escape(heading)}\s*\n)(.*?)(?=\n## |\Z)"
    replacement = rf"\g<1>{new_content}\n"
    updated, count = re.subn(pattern, replacement, sow_content, count=1, flags=re.DOTALL)
    if count == 0:
        # Section heading not found — append at end
        updated = sow_content.rstrip() + f"\n\n## {heading}\n\n{new_content}\n"
    return updated
