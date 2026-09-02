"""Generation stage — standalone Gherkin validation command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from shared.files import list_gherkin_files

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()


def action_validate_gherkin(client: SdlicitClient, working_dir: str) -> None:
    """Validate Gherkin syntax of existing feature files or pasted text."""
    console.print(Rule("[bold]Validate Gherkin Syntax[/bold]"))

    # Check for existing feature files
    files = list_gherkin_files(working_dir)

    if files:
        console.print("[bold]Existing feature files:[/bold]")
        for i, f in enumerate(files, 1):
            console.print(f"  [bold]{i}[/bold]. {f.name}")
        console.print(f"  [bold]{len(files) + 1}[/bold]. Paste new Gherkin text")
        console.print()

        choices = [str(i) for i in range(1, len(files) + 2)]
        idx_str = Prompt.ask("Choose file or paste new", choices=choices, default="1")
        idx = int(idx_str)

        if idx <= len(files):
            try:
                gherkin_text = files[idx - 1].read_text(encoding="utf-8")
                console.print(f"  Validating: [dim]{files[idx - 1].name}[/dim]")
            except OSError as exc:
                console.print(f"[red]Could not read file:[/red] {exc}")
                return
        else:
            gherkin_text = _read_multiline("Paste Gherkin text")
    else:
        gherkin_text = _read_multiline("Paste Gherkin text")

    if not gherkin_text.strip():
        console.print("[yellow]No input provided — cancelled.[/yellow]")
        return

    # Show preview
    syntax = Syntax(gherkin_text[:2000], "gherkin", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="Input", border_style="dim"))

    # Call validation endpoint
    with console.status("[bold]Validating…[/bold]"):
        try:
            result = client.validate_gherkin(gherkin_text)
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return

    # Display results
    valid = result.get("valid", False)
    feature_name = result.get("feature_name", "")
    scenario_count = result.get("scenario_count", 0)
    issues = result.get("issues", [])

    console.print()
    if valid:
        console.print(
            Panel(
                f"[green]✓ Valid Gherkin[/green]\n\n"
                f"Feature: [bold]{feature_name}[/bold]\n"
                f"Scenarios: {scenario_count}",
                border_style="green",
            )
        )
    else:
        table = Table(title="Validation Issues", show_lines=True)
        table.add_column("#", justify="right", style="dim")
        table.add_column("Issue", style="red")
        for i, issue in enumerate(issues, 1):
            table.add_row(str(i), issue)
        console.print(table)
        console.print(
            f"\n[red]✗ Invalid Gherkin[/red]  "
            f"({len(issues)} issue{'s' if len(issues) != 1 else ''})"
        )


def _read_multiline(prompt: str) -> str:
    console.print(f"\n[bold]{prompt}[/bold] (empty line to finish):")
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
