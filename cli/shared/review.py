"""Accept / Regenerate / Edit / Skip review prompt — shared across stages.

Drives the post-generation comprehension loop: the user is shown the
artifact and must explicitly choose what to do with it. Editing opens
``$EDITOR`` (default ``vim``) on a temp file.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.prompt import Prompt

console = Console()


ReviewAction = Literal["accept", "regenerate", "edit", "skip"]


@dataclass
class ReviewOutcome:
    action: ReviewAction
    content: str = ""
    notes: str = ""


def prompt_review(
    *,
    artifact_label: str,
    current_content: str,
    suffix: str = ".md",
) -> ReviewOutcome:
    """Ask the user what to do with a generated artifact.

    Returns:
        ``ReviewOutcome(action="accept", content=current_content)``
        ``ReviewOutcome(action="regenerate", notes=<str>)``
        ``ReviewOutcome(action="edit", content=<edited>)``
        ``ReviewOutcome(action="skip")``
    """
    console.print()
    console.print(
        f"[bold]How would you like to proceed with the {artifact_label}?[/bold]"
    )
    console.print("  [bold][a][/bold]  Accept and save")
    console.print("  [bold][r][/bold]  Regenerate (with optional notes)")
    console.print("  [bold][e][/bold]  Edit manually in $EDITOR")
    console.print("  [bold][s][/bold]  Skip — do not save")

    choice = Prompt.ask(
        "[bold]Choice[/bold]",
        choices=["a", "r", "e", "s"],
        default="a",
    )

    if choice == "a":
        return ReviewOutcome(action="accept", content=current_content)

    if choice == "r":
        notes = Prompt.ask(
            "[dim]Notes for regeneration (optional)[/dim]",
            default="",
        )
        return ReviewOutcome(action="regenerate", notes=notes.strip())

    if choice == "e":
        edited = _edit_in_external_editor(current_content, suffix=suffix)
        if edited is None:
            console.print("[yellow]Edit cancelled — keeping original.[/yellow]")
            return ReviewOutcome(action="accept", content=current_content)
        return ReviewOutcome(action="edit", content=edited)

    return ReviewOutcome(action="skip")


def _edit_in_external_editor(content: str, *, suffix: str = ".md") -> str | None:
    """Open ``$EDITOR`` (or ``vim``) on a temp file pre-loaded with ``content``."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vim"
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=suffix, delete=False, encoding="utf-8"
        ) as tf:
            tf.write(content)
            tmp_path = tf.name
        try:
            subprocess.call([editor, tmp_path])
            with open(tmp_path, encoding="utf-8") as f:
                return f.read()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
    except (OSError, FileNotFoundError) as exc:
        console.print(f"[red]Could not launch editor:[/red] {exc}")
        return None
