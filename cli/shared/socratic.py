"""Generic Socratic-loop helper for the CLI.

Encapsulates the call → probe → answer → re-call pattern used by every
backend endpoint that returns a ``socratic_probe``. Extracted from
``stages/intake/create_sow.py`` so personas / stories / gherkin / SRS
can reuse it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

console = Console()

_STYLE_LABEL = {
    "assumption": "Hidden assumption",
    "contradiction": "Contradiction",
    "depth": "Deeper reflection",
    "perspective": "Unconsidered angle",
}


def _render_probe(probe: dict[str, Any]) -> None:
    style = probe.get("style", "depth")
    label = _STYLE_LABEL.get(style, "Reflection")
    turn = probe.get("turn", 1)
    max_turns = probe.get("max_turns", 7)

    # 1. Transparency events — dim line before the probe card
    transparency_events: list[str] = probe.get("transparency_events") or []
    if transparency_events:
        console.print()
        event_text = Text("  ·  ".join(transparency_events), style="dim")
        console.print(event_text)

    # 2. KB facts — separate panel with a distinct border, before the question
    kb_facts: str = probe.get("kb_facts") or ""
    if kb_facts.strip():
        console.print()
        console.print(
            Panel(
                kb_facts.strip(),
                title="[bold cyan]From the Knowledge Base[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    # 3. Probe question
    body = f"[bold italic]{probe.get('question', '')}[/bold italic]"

    console.print()
    console.print(
        Panel(
            body,
            title=f"[bold magenta]Socratic probe — {label} (turn {turn}/{max_turns})[/bold magenta]",
            border_style="magenta",
        )
    )


def _ask_probe_answer() -> str:
    return Prompt.ask(
        "[magenta]Your answer[/magenta] [dim](or /skip to proceed)[/dim]",
        default="",
    )


def run_socratic_loop(
    call: Callable[[list[dict[str, Any]]], dict[str, Any]],
    probe_key: str = "socratic_probe",
    *,
    status_message: str = "Working…",
) -> dict[str, Any]:
    """Repeatedly invoke ``call(clarifications)`` until no probe is returned.

    ``call`` receives the running list of clarifications and must return
    the raw response dict (already JSON-decoded).  This helper:

    1. Calls once with ``[]``.
    2. While the response carries a ``socratic_probe``, it:
       a. Prints any transparency events as dim text.
       b. Shows a KB facts panel if the probe carries facts.
       c. Shows the probe question panel.
       d. Prompts the user for an answer.
       e. Re-calls with the updated clarifications.
    3. Returns the final response (probe will be ``None``).

    Typing ``/skip`` (or empty answer) sends a dummy "skip" answer back
    so the resolution judge stops the loop.
    """
    clarifications: list[dict[str, Any]] = []
    last: dict[str, Any] = {}

    while True:
        with console.status(f"[bold]{status_message}[/bold]"):
            last = call(clarifications)

        probe = last.get(probe_key)
        if not probe:
            return last

        _render_probe(probe)
        answer = _ask_probe_answer().strip()
        if not answer or answer.lower() == "/skip":
            console.print("[dim]Skipping further Socratic probing.[/dim]")
            clarifications.append(
                {
                    "question": probe.get("question", ""),
                    "answer": "skip",
                    "turn": probe.get("turn", len(clarifications) + 1),
                }
            )
            with console.status("[bold]Finalising…[/bold]"):
                last = call(clarifications)
            return last

        clarifications.append(
            {
                "question": probe.get("question", ""),
                "answer": answer,
                "turn": probe.get("turn", len(clarifications) + 1),
            }
        )

