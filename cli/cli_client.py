"""Sdlicit Rich CLI — main entry point.

Pure presentation layer.  Talks to the Sdlicit backend exclusively
over REST via :class:`SdlicitClient`.  No direct imports from the
``sdlicit`` package — the CLI and backend are independently installable.

All file I/O lives in ``shared/files.py`` and ``shared/journal.py``.
The CLI is the **only** writer of session data under
``.sdlicit/sessions/``; the backend reads but never writes there.
"""

from __future__ import annotations

import atexit
import os
import signal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from api_client import SdlicitClient, DEFAULT_BASE_URL
from shared.journal import (
    Journal,
    detect_crashed_sessions,
    mark_session_crashed,
    read_index,
)
from stages.composing import menu_entries as composing_entries
from stages.expansion import menu_entries as expansion_entries
from stages.generation import menu_entries as generation_entries
from stages.guided import menu_entries as guided_entries
from stages.intake import menu_entries as intake_entries

console = Console()

DEFAULT_WORKING_DIR = str((Path(__file__).resolve().parent.parent / "test").resolve())


def _clear() -> None:
    """Clear the terminal for a 'screen' feel."""
    os.system("clear" if os.name != "nt" else "cls")


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _draw_header(
    server_config: dict[str, Any],
    working_dir: str,
    journal: Journal | None = None,
) -> None:
    """Draw the persistent application header with status info + token meter."""
    flags = []
    if not server_config.get("enable_rag", True):
        flags.append("[red]RAG off[/red]")
    if not server_config.get("enable_tom", True):
        flags.append("[red]ToM off[/red]")
    if not server_config.get("enable_socratic", True):
        flags.append("[red]Socratic off[/red]")
    flag_str = "  ".join(flags) if flags else "[green]all agents active[/green]"

    if journal is not None:
        totals = journal.totals
        ctx_window = server_config.get("model_context_window") or 0
        threshold_pct = server_config.get("compact_threshold_pct") or 0.4
        threshold = int(ctx_window * threshold_pct) if ctx_window else 0
        used = totals["total_tokens"]
        if threshold:
            pct = used / threshold if threshold else 0
            colour = "green" if pct < 0.6 else "yellow" if pct < 1.0 else "red"
            meter = (
                f"[{colour}]Σ {_format_tokens(used)} / "
                f"{_format_tokens(threshold)} tok[/{colour}]"
            )
        else:
            meter = f"Σ {_format_tokens(used)} tok"
        meter += f"  [dim]({totals['calls']} calls)[/dim]"
    else:
        meter = ""

    header = Table.grid(padding=(0, 2))
    header.add_column(justify="left")
    header.add_column(justify="right")
    header.add_row(
        "[bold bright_blue]Sdlicit[/bold bright_blue] — SDLC Artifact Helper",
        f"[dim]{server_config.get('model', 'unknown')}[/dim]",
    )
    header.add_row(f"[dim]Project:[/dim] {working_dir}", flag_str)
    if meter and journal is not None:
        header.add_row(f"[dim]Session:[/dim] {journal.session_id}", meter)
    console.print(Panel(header, border_style="bright_blue"))
    console.print()


# ── Per-action token panel + stats screen ─────────────────────────────


def _draw_action_usage(journal: Journal, before_totals: dict[str, Any]) -> None:
    """Show tokens consumed by the action that just ran (delta vs *before*)."""
    after = journal.totals
    delta_prompt = after["prompt_tokens"] - before_totals.get("prompt_tokens", 0)
    delta_completion = after["completion_tokens"] - before_totals.get(
        "completion_tokens", 0
    )
    delta_calls = after["calls"] - before_totals.get("calls", 0)
    if delta_calls == 0 and (delta_prompt + delta_completion) == 0:
        return
    console.print(
        f"[dim]This action:[/dim] "
        f"[cyan]{delta_prompt + delta_completion}[/cyan] tokens "
        f"([dim]{delta_prompt} in / {delta_completion} out[/dim], "
        f"{delta_calls} call{'s' if delta_calls != 1 else ''})"
    )
    console.print()


def _show_stats(journal: Journal, server_config: dict[str, Any]) -> None:
    """Per-agent token breakdown for the current session."""
    totals = journal.totals
    table = Table(title=f"Session {journal.session_id} — token usage", expand=True)
    table.add_column("Agent / endpoint", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Prompt", justify="right")
    table.add_column("Completion", justify="right")
    table.add_column("Total", justify="right", style="bold")
    for agent, slot in sorted(
        totals["by_agent"].items(),
        key=lambda kv: kv[1].get("total_tokens", 0),
        reverse=True,
    ):
        table.add_row(
            agent,
            str(slot.get("calls", 0)),
            _format_tokens(slot.get("prompt_tokens", 0)),
            _format_tokens(slot.get("completion_tokens", 0)),
            _format_tokens(slot.get("total_tokens", 0)),
        )
    table.add_section()
    table.add_row(
        "[bold]Σ total[/bold]",
        str(totals["calls"]),
        _format_tokens(totals["prompt_tokens"]),
        _format_tokens(totals["completion_tokens"]),
        _format_tokens(totals["total_tokens"]),
    )
    console.print(table)

    ctx_window = server_config.get("model_context_window") or 0
    threshold_pct = server_config.get("compact_threshold_pct") or 0.4
    threshold = int(ctx_window * threshold_pct) if ctx_window else 0
    if threshold:
        pct = min(1.0, totals["total_tokens"] / threshold)
        bar = Progress(
            TextColumn("[bold]Compaction threshold[/bold]"),
            BarColumn(bar_width=40),
            TextColumn(
                f"{_format_tokens(totals['total_tokens'])} / "
                f"{_format_tokens(threshold)} ({pct * 100:.0f}%)"
            ),
        )
        task_id = bar.add_task("threshold", total=1.0, completed=pct)
        bar.update(task_id, completed=pct)
        console.print(bar)

    endpoints = journal._meta.get("endpoints", {})
    if endpoints:
        console.print(
            "[dim]Endpoints used:[/dim] "
            + ", ".join(f"{e}×{c}" for e, c in endpoints.items())
        )


# ── Compaction ─────────────────────────────────────────────────


def _maybe_compact(
    client: SdlicitClient,
    journal: Journal,
    server_config: dict[str, Any],
    *,
    forced: bool = False,
) -> None:
    """Trigger ToM compaction when running total crosses threshold."""
    ctx_window = server_config.get("model_context_window") or 0
    threshold_pct = server_config.get("compact_threshold_pct") or 0.4
    threshold = int(ctx_window * threshold_pct) if ctx_window else 0
    used = journal.totals["total_tokens"]
    if not forced and (threshold == 0 or used < threshold):
        return
    label = "Forced compaction" if forced else "Threshold reached — compacting"
    with console.status(f"[bold]{label}…[/bold]"):
        try:
            result = client.compact_session()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Compaction failed:[/yellow] {exc}")
            return
    if result.get("status") == "ok":
        journal.write_compact(result)
        rel = journal.compact_path.relative_to(Path(journal.working_dir))
        console.print(f"[green]✓ Compacted[/green] [dim]→ {rel}[/dim]")
    else:
        console.print(f"[dim]Compaction skipped: {result.get('status')}[/dim]")


# ── Crash recovery ───────────────────────────────────────────────


def _handle_crashed_sessions(working_dir: str) -> None:
    crashed = detect_crashed_sessions(working_dir)
    if not crashed:
        return
    console.print(
        Panel(
            f"[bold yellow]{len(crashed)} crashed session"
            f"{'s' if len(crashed) != 1 else ''} detected[/bold yellow]\n"
            "[dim]Sessions whose meta.json still says 'active' from a previous "
            "run.  They were not closed gracefully.[/dim]",
            border_style="yellow",
        )
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Session", style="cyan")
    table.add_column("Started")
    table.add_column("Last event")
    table.add_column("Events", justify="right")
    table.add_column("Tokens", justify="right")
    for i, c in enumerate(crashed, 1):
        table.add_row(
            str(i),
            c["session_id"],
            str(c.get("started_at", "?"))[:19],
            str(c.get("last_event_at", "?"))[:19],
            str(c.get("event_count", 0)),
            str(c.get("tokens", 0)),
        )
    console.print(table)
    choice = Prompt.ask(
        "[bold]Mark them all as crashed and continue?[/bold] "
        "(raw chat/ logs are preserved)",
        choices=["y", "n"],
        default="y",
    )
    if choice == "y":
        for c in crashed:
            mark_session_crashed(working_dir, c["session_id"])
        console.print(
            f"[green]✓[/green] Marked {len(crashed)} session(s) as crashed.\n"
        )


# ── Save preference ──────────────────────────────────────────────


_PREF_KEYS = [
    "interaction_style",
    "scaffolding_preference",
    "expertise_areas",
    "common_patterns",
    "other",
]


def _save_preference_flow(
    client: SdlicitClient,
    journal: Journal,
    *,
    preset_value: str | None = None,
    preset_note: str | None = None,
) -> None:
    """Capture an explicit user preference and write user_model.json.

    Triggered from the ``[p]`` menu or the inline ``P`` hotkey after an
    action.  The CLI is the sole writer of ``user_model.json``; the
    backend computes the updated model and returns it.
    """
    console.print(Panel("[bold]Save user preference[/bold]", border_style="cyan"))
    key = Prompt.ask("Preference key", choices=_PREF_KEYS, default="other")
    if key == "other":
        key = Prompt.ask("Custom key").strip() or "other"
    value = preset_value or Prompt.ask("Value")
    note = (
        preset_note
        if preset_note is not None
        else Prompt.ask("Note (optional)", default="")
    )
    try:
        result = client.save_preference(key, value, note=note)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗  /preference failed:[/red] {exc}")
        return
    if result.get("status") != "ok":
        console.print(f"[yellow]Preference not saved:[/yellow] {result.get('status')}")
        return
    user_model = result.get("user_model") or {}
    journal.write_user_model(user_model)
    journal.note("user_preference", {"key": key, "value": value, "note": note})
    rel = journal.user_model_path.relative_to(Path(journal.working_dir))
    console.print(f"[green]✓[/green] Saved {key}=[bold]{value}[/bold] → {rel}")


# ── End-of-session + shutdown handlers ────────────────────────────


def _persist_end_of_session(client: SdlicitClient, journal: Journal) -> None:
    """Call /session/end and write the returned ToM artifacts to disk."""
    try:
        result = client.end_session()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]/session/end failed:[/yellow] {exc}")
        result = {}
    if result.get("session_model"):
        try:
            journal.write_tom_analysis(result["session_model"])
        except Exception:  # noqa: BLE001
            pass
    if result.get("user_model"):
        try:
            journal.write_user_model(result["user_model"])
        except Exception:  # noqa: BLE001
            pass
    journal.mark_closed("closed")


_shutdown_done = False


def _install_shutdown_handlers(client: SdlicitClient, journal: Journal) -> None:
    """Register atexit + signal handlers that flush the journal on exit."""

    def _shutdown(*_args: Any) -> None:
        global _shutdown_done
        if _shutdown_done:
            return
        _shutdown_done = True
        try:
            _persist_end_of_session(client, journal)
        except Exception:  # noqa: BLE001
            try:
                journal.mark_closed("interrupted")
            except Exception:  # noqa: BLE001
                pass

    atexit.register(_shutdown)

    def _signal_handler(signum: int, _frame: Any) -> None:
        _shutdown()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass


# -- Menu entry type: (key, label, description, callable) ----------------------

MenuEntry = tuple[str, str, str, object]


def _build_menu(
    client: SdlicitClient, working_dir: str
) -> list[tuple[str, list[MenuEntry]]]:
    """Build grouped menu entries from all stage plugins.

    The Guided stage is special-cased to come first; its entries are
    numbered from 0, with the rest of the menu starting at 1.
    """
    stages: list[tuple[str, list[MenuEntry]]] = [
        ("Guided", guided_entries(client, working_dir)),
    ]

    # Each plugin returns list of (label, description, callable)
    for stage_name, entries_fn in [
        ("Intake", intake_entries),
        ("Composing", composing_entries),
        ("Expanding", expansion_entries),
        ("Generating", generation_entries),
    ]:
        raw = entries_fn(client, working_dir)
        stages.append((stage_name, raw))
    return stages


def _draw_menu(stages: list[tuple[str, list[MenuEntry]]]) -> dict[str, object]:
    """Render the menu and return key→action lookup.

    Guided entries are numbered 0, 0a, 0b... (only one expected).  All
    other stages then start at 1.
    """
    choices: dict[str, object] = {}
    idx = 1
    for stage_name, entries in stages:
        is_guided = stage_name == "Guided"
        console.print(f"  [bold bright_cyan]── {stage_name} ──[/bold bright_cyan]")
        for i, (_key, label, desc, action) in enumerate(entries):
            if is_guided:
                key = "0" if i == 0 else f"0{chr(ord('a') + i - 1)}"
            else:
                key = str(idx)
                idx += 1
            console.print(f"    [bold][{key}][/bold]  {label}")
            if desc:
                console.print(f"        [dim]{desc}[/dim]")
            choices[key] = action
        console.print()

    console.print("    [bold]\\[t][/bold]  Session token stats")
    console.print("    [bold]\\[c][/bold]  Compact session now")
    console.print("    [bold]\\[p][/bold]  Save user preference")
    console.print("    [bold]\\[q][/bold]  Quit")
    console.print()
    return choices


def main() -> None:
    load_dotenv()
    _clear()

    console.print(
        Panel.fit(
            "[bold]Sdlicit CLI[/bold] — SDLC artifact helper\n"
            "[dim]Knowledge-grounded requirements elicitation[/dim]",
            border_style="bright_blue",
        )
    )
    console.print()

    working_dir = Prompt.ask(
        "[bold]Working directory[/bold]\n"
        "  [dim]Path to the project root (must contain .sdlicit/config.yaml)[/dim]",
        default=DEFAULT_WORKING_DIR,
    )
    server_url = Prompt.ask("[bold]Server URL[/bold]", default=DEFAULT_BASE_URL)

    _handle_crashed_sessions(working_dir)

    idx = read_index(working_dir)
    if idx.get("last_session_id"):
        console.print(f"[dim]Previous session: {idx['last_session_id']}[/dim]\n")

    client = SdlicitClient(base_url=server_url)

    with console.status("[bold]Connecting to Sdlicit server…[/bold]"):
        if not client.health():
            console.print(
                f"\n[red]✗  Cannot reach server at {client.server_url}[/red]\n"
                "[dim]Start the backend with: uvicorn sdlicit.main:app[/dim]\n"
            )
            return

    with console.status("[bold]Initialising project…[/bold]"):
        try:
            client.init_project(working_dir)
        except Exception as exc:
            console.print(
                f"\n[red]✗  Failed to initialise project:[/red] {exc}\n"
                "[dim]Ensure .sdlicit/config.yaml exists in the project directory.[/dim]\n"
            )
            return

    try:
        server_config = client.get_config()
    except Exception:
        server_config = {}

    session_id: str | None = None
    try:
        session_id = client.start_session(stage="cli")
    except Exception:
        session_id = None

    journal = Journal(working_dir=working_dir, session_id=session_id)
    journal.set_server_config(server_config)
    client.attach_journal(journal)
    _install_shutdown_handlers(client, journal)
    journal.note(
        "cli_start",
        {"working_dir": working_dir, "model": server_config.get("model")},
    )

    _clear()
    _draw_header(server_config, working_dir, journal)
    console.print(
        f"[green]✓[/green] Connected · session [cyan]{journal.session_id}[/cyan]\n"
    )

    stages = _build_menu(client, working_dir)

    while True:
        choices = _draw_menu(stages)
        valid = [*choices.keys(), "t", "c", "p", "q"]
        choice = Prompt.ask("[bold]Choose an action[/bold]", choices=valid)

        if choice == "q":
            with console.status("[dim]Saving session…[/dim]"):
                _persist_end_of_session(client, journal)
            _clear()
            console.print("[dim]Goodbye![/dim]")
            break

        if choice == "t":
            _clear()
            _draw_header(server_config, working_dir, journal)
            _show_stats(journal, server_config)
            console.print()
            Prompt.ask("[dim]Press Enter to return to menu[/dim]", default="")
            _clear()
            _draw_header(server_config, working_dir, journal)
            continue

        if choice == "c":
            _maybe_compact(client, journal, server_config, forced=True)
            console.print()
            Prompt.ask("[dim]Press Enter to return to menu[/dim]", default="")
            _clear()
            _draw_header(server_config, working_dir, journal)
            continue

        if choice == "p":
            _save_preference_flow(client, journal)
            console.print()
            Prompt.ask("[dim]Press Enter to return to menu[/dim]", default="")
            _clear()
            _draw_header(server_config, working_dir, journal)
            continue

        # Stage action --------------------------------------------------
        _clear()
        _draw_header(server_config, working_dir, journal)
        action = choices[choice]
        before = dict(journal.totals)
        journal.note("menu_select", {"choice": choice})
        try:
            action()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Action failed:[/red] {exc}")
            journal.note("action_error", {"choice": choice, "error": str(exc)})

        _draw_action_usage(journal, before)
        _maybe_compact(client, journal, server_config)

        console.print()
        post_choice = Prompt.ask(
            "[dim]Press Enter to return to menu, or [bold]P[/bold] to save the "
            "last value as a preference[/dim]",
            default="",
        )
        if post_choice.strip().lower() == "p":
            _save_preference_flow(client, journal)
            Prompt.ask("[dim]Press Enter to return to menu[/dim]", default="")

        _clear()
        _draw_header(server_config, working_dir, journal)


if __name__ == "__main__":
    main()
