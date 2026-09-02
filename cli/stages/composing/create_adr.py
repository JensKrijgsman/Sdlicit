"""Composing stage — create ADR action.

Template is selected first (determines which sections are required).
After template selection the terminal shows a **split-pane layout**:

  Left  (60 %) — field log that updates in-place via clear-and-redraw
  Right (40 %) — agent suggestions colour-coded by originating step

Each step is a ``_Step`` object with ``run()`` / ``undo()``.  Typing
``/b`` at any prompt goes back to the previous step.  The step pipeline
is driven by ``_run_wizard()`` with a cursor and a history stack.

Uses the SdlicitClient to call the backend over REST.
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, NamedTuple

from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from shared.files import list_adr_files, write_adr, save_artifact_via_backend
from shared.madr import AdrFields, ComposeResponse, render_madr  # noqa: F401

from .suggest_directions import offer_directions_then_prompt_title

if TYPE_CHECKING:
    from api_client import SdlicitClient

console = Console()

# ── Constants ─────────────────────────────────────────────────────────────────

_BACK_CMD = "/b"
_BACK_HINT = " [dim]/b=back[/dim]"

_TEMPLATE_ROWS = [
    (
        "minimal",
        "Status + date frontmatter",
        "Context · Options · Decision · Consequences",
    ),
    (
        "full",
        "Full YAML frontmatter",
        "Decision Drivers · Pros & Cons · Confirmation · More Info",
    ),
    ("bare", "Empty shell", "Only title asked — fill the rest in your editor"),
]

_STEP_COLORS: dict[str, str] = {
    "title": "bright_blue",
    "status": "blue",
    "context": "bright_green",
    "decision_drivers": "bright_yellow",
    "options_considered": "bright_cyan",
    "chosen_option": "bright_magenta",
    "decision": "magenta",
    "consequences": "red",
    "decision_makers": "white",
    "consulted": "white",
    "informed": "white",
}
_DEFAULT_COLOR = "white"

_PLACEHOLDER_SUGGESTIONS: list[dict[str, Any]] = []

# ── Types ─────────────────────────────────────────────────────────────────────


class _R(Enum):
    """Step result."""

    NEXT = auto()
    BACK = auto()
    SKIP = auto()


class _Suggestion(NamedTuple):
    step: str
    color: str
    kind: str  # "suggestion" | "compliance"
    agent: str
    message: str


# ── Input helpers (all support /b) ────────────────────────────────────────────


def _ask(
    prompt: str,
    *,
    choices: list[str] | None = None,
    default: str | None = None,
) -> tuple[str, bool]:
    """Prompt returning ``(value, is_back)``.  ``/b`` → back."""
    label = prompt
    if choices:
        label += f" [dim]({'/'.join(choices)})[/dim]"
    label += _BACK_HINT
    while True:
        raw = (
            Prompt.ask(label, default=default)
            if default is not None
            else Prompt.ask(label)
        )
        if raw.strip() == _BACK_CMD:
            return "", True
        if choices is not None and raw not in choices:
            console.print(f"[red]Choose one of: {', '.join(choices)}[/red]")
            continue
        return raw, False


def _confirm(prompt: str, default: bool = False) -> tuple[bool, bool]:
    """Yes/No prompt returning ``(value, is_back)``."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = Prompt.ask(
            f"{prompt} {hint}{_BACK_HINT}", default="y" if default else "n"
        )
        if raw.strip() == _BACK_CMD:
            return False, True
        if raw.lower() in ("y", "yes"):
            return True, False
        if raw.lower() in ("n", "no"):
            return False, False
        console.print("[red]Please enter y or n.[/red]")


def _ask_list(prompt: str) -> tuple[list[str], bool]:
    """Collect items until empty input.  ``/b`` → back."""
    items: list[str] = []
    while True:
        raw = Prompt.ask(
            f"  {prompt} [dim](empty to stop){_BACK_HINT}[/dim]", default=""
        )
        if raw.strip() == _BACK_CMD:
            return [], True
        if not raw:
            return items, False
        items.append(raw)


# ── Session (shared state + panels + background events) ──────────────────────


class _ADRSession:
    """Owns all wizard state.  Step objects mutate it via public helpers.

    ``render()`` clears the screen and redraws both panels as static
    Rich output.  Background threads append suggestions under _lock;
    the very next ``render()`` will include them.
    """

    def __init__(
        self, client: "SdlicitClient", working_dir: str, template: str
    ) -> None:
        self.client = client
        self.working_dir = working_dir
        self.template = template
        self.fields: dict[str, Any] = {"template": template}

        self._log: list[tuple[str, str, str]] = []  # (color, label, preview)
        self._hint: str = ""
        self._suggestions: list[_Suggestion] = []
        self._pending: list[threading.Thread] = []
        # Socratic state — per field
        self._probes: dict[str, list[dict[str, Any]]] = {}
        self._clarifications: dict[str, list[dict[str, Any]]] = {}
        self._last_step_value: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render(self) -> None:
        """Clear the screen and redraw both panels side-by-side."""
        console.clear()
        console.print("\n")  # blank line before the prompt
        tbl = Table.grid(expand=True, padding=0)
        tbl.add_column(ratio=3)
        tbl.add_column(ratio=2)
        tbl.add_row(self._build_left(), self._build_right())
        console.print(tbl)
        # console.print()          # blank line before the prompt

    def _build_left(self) -> Panel:
        with self._lock:
            log, hint = list(self._log), self._hint
        items: list[Any] = []
        for color, label, preview in log:
            t = Text()
            t.append("✓ ", style=f"bold {color}")
            t.append(f"{label}: ", style=f"bold {color}")
            t.append(preview, style="dim")
            items.append(t)
        if hint:
            t = Text()
            t.append("→ ", style="bold white")
            t.append(hint, style="bold white italic")
            items.append(t)
        body = Group(*items) if items else Text("Waiting for first field…", style="dim")
        return Panel(
            body,
            title=f"[bold]MADR — {self.template}[/bold]",
            border_style="green",
            expand=True,
            padding=(0, 1),
        )

    def _build_right(self) -> Panel:
        with self._lock:
            sugs = list(self._suggestions)
        if not sugs:
            return Panel(
                Text(
                    "Suggestions will appear here\nas you fill in each field…",
                    style="dim",
                ),
                title="[cyan]Agent Suggestions[/cyan]",
                border_style="cyan",
                expand=True,
                padding=(0, 1),
            )
        items: list[Any] = []
        prev_step = ""
        for s in sugs:
            if s.step != prev_step:
                if prev_step:
                    items.append(Text(""))
                h = Text()
                h.append(f"── {s.step} ", style=f"bold {s.color}")
                items.append(h)
                prev_step = s.step

            ln = Text()
            if s.kind == "compliance":
                ln.append(f" {s.agent} ", style="bold yellow")
                ln.append("📋 ")
                ln.append(s.message, style="dim italic")
            else:
                ln.append(f" {s.agent} ", style=f"bold {s.color}")
                ln.append("💡 ")
                ln.append(s.message, style="italic")
            items.append(ln)
        return Panel(
            Group(*items),
            title="[cyan]Agent Suggestions[/cyan]",
            border_style="cyan",
            expand=True,
            padding=(0, 1),
        )

    # ── State helpers ─────────────────────────────────────────────────────────

    def set_hint(self, hint: str) -> None:
        with self._lock:
            self._hint = hint

    def clear_hint(self) -> None:
        with self._lock:
            self._hint = ""

    def add_log(self, field: str, label: str, preview: str) -> None:
        color = _STEP_COLORS.get(field, _DEFAULT_COLOR)
        with self._lock:
            self._log.append((color, label, preview))

    def pop_log(self, count: int = 1) -> None:
        with self._lock:
            for _ in range(min(count, len(self._log))):
                self._log.pop()

    # ── Background step events ────────────────────────────────────────────────

    def fire(self, step_name: str, value: Any) -> None:
        color = _STEP_COLORS.get(step_name, _DEFAULT_COLOR)
        snapshot = dict(self.fields)
        self._last_step_value[step_name] = value
        t = threading.Thread(
            target=self._fetch, args=(step_name, value, color, snapshot), daemon=True
        )
        t.start()
        with self._lock:
            self._pending.append(t)

    def _fetch(
        self, step_name: str, value: Any, color: str, snapshot: dict[str, Any]
    ) -> None:
        try:
            data = self.client.step_event(
                step_name=step_name,
                step_value=value,
                partial_fields=snapshot,
                project_dir=self.working_dir,
                clarifications=list(self._clarifications.get(step_name, [])),
            )
        except Exception as exc:
            with self._lock:
                self._suggestions.append(
                    _Suggestion(
                        step=step_name,
                        color=color,
                        kind="suggestion",
                        agent="System",
                        message=f"⚠ Agent unavailable: {exc}",
                    )
                )
            return

        batch: list[_Suggestion] = []

        suggestion = data.get("suggestion")
        if suggestion:
            severity = suggestion.get("severity", "")
            text = suggestion.get("message", "")
            msg = f"[{severity}] {text}" if severity else text
            batch.append(
                _Suggestion(
                    step=step_name,
                    color=color,
                    kind="suggestion",
                    agent="ADR Agent",
                    message=msg,
                )
            )

        compliance = data.get("compliance")
        if compliance:
            batch.append(
                _Suggestion(
                    step=step_name,
                    color="yellow",
                    kind="compliance",
                    agent="Requirements",
                    message=(
                        compliance if isinstance(compliance, str) else str(compliance)
                    ),
                )
            )

        probe = data.get("socratic_probe")
        with self._lock:
            if batch:
                self._suggestions.extend(batch)
            if probe:
                self._probes.setdefault(step_name, []).append(probe)

    def wait_for_latest(self, timeout: float = 8.0) -> None:
        """Block until the most recent pending thread completes (or *timeout*)."""
        with self._lock:
            t = self._pending[-1] if self._pending else None
        if t:
            t.join(timeout=timeout)

    def drain(self, timeout: float = 60.0) -> None:
        with self._lock:
            threads = list(self._pending)
        for t in threads:
            t.join(timeout=timeout)

    # ── Socratic probe handling ──────────────────────────────────────────────

    def take_pending_probe(self) -> tuple[str, dict[str, Any]] | None:
        """Pop the next queued probe (FIFO).  Returns ``(step_name, probe)``."""
        with self._lock:
            for step_name, probes in self._probes.items():
                if probes:
                    return step_name, probes.pop(0)
        return None

    def add_clarification(
        self, step_name: str, question: str, answer: str, turn: int
    ) -> None:
        with self._lock:
            self._clarifications.setdefault(step_name, []).append(
                {"question": question, "answer": answer, "turn": turn}
            )

    def replay_step_sync(self, step_name: str) -> dict[str, Any]:
        """Re-call the step event synchronously (for Socratic resume).

        Returns the raw response dict.  Suggestions / probes are also
        merged into the session state so the right pane stays consistent.
        """
        value = self._last_step_value.get(step_name)
        if value is None:
            return {}
        try:
            data = self.client.step_event(
                step_name=step_name,
                step_value=value,
                partial_fields=dict(self.fields),
                project_dir=self.working_dir,
                clarifications=list(self._clarifications.get(step_name, [])),
            )
        except Exception as exc:
            return {"error": str(exc)}

        color = _STEP_COLORS.get(step_name, _DEFAULT_COLOR)
        suggestion = data.get("suggestion")
        if suggestion:
            severity = suggestion.get("severity", "")
            text = suggestion.get("message", "")
            msg = f"[{severity}] {text}" if severity else text
            with self._lock:
                self._suggestions.append(
                    _Suggestion(
                        step=step_name,
                        color=color,
                        kind="suggestion",
                        agent="ADR Agent",
                        message=msg,
                    )
                )
        probe = data.get("socratic_probe")
        if probe:
            with self._lock:
                self._probes.setdefault(step_name, []).append(probe)
        return data


_STYLE_LABEL = {
    "assumption": "Hidden assumption",
    "contradiction": "Contradiction",
    "depth": "Deeper reflection",
    "perspective": "Unconsidered angle",
}


def _render_probe(probe: dict[str, Any]) -> None:
    """Render a Socratic probe in a distinctive panel."""
    style = probe.get("style", "depth")
    label = _STYLE_LABEL.get(style, "Reflection")
    turn = probe.get("turn", 1)
    max_turns = probe.get("max_turns", 7)
    grounding = probe.get("rag_grounding", "")
    body = f"[bold italic]{probe.get('question', '')}[/bold italic]"
    if grounding:
        body += f"\n\n[dim]Grounded in: {grounding}[/dim]"
    console.print()
    console.print(
        Panel(
            body,
            title=f"[bold magenta]Socratic — {label} (turn {turn}/{max_turns})[/bold magenta]",
            border_style="magenta",
        )
    )


def _drain_probes(session: _ADRSession) -> None:
    """Process every queued Socratic probe before the wizard advances.

    For each pending probe we render it, prompt the user, append the
    answer to the per-field clarification list, and re-fire the step
    event synchronously.  The user can type ``/skip`` to dismiss the
    dialogue for that field; the agent's resolution judge will also
    end the loop on its own when it detects fatigue.
    """
    while True:
        item = session.take_pending_probe()
        if item is None:
            return
        step_name, probe = item
        _render_probe(probe)
        answer = Prompt.ask(
            "[magenta]Your answer[/magenta] [dim](or /skip to proceed)[/dim]",
            default="",
        ).strip()
        if not answer or answer.lower() == "/skip":
            console.print(
                "[dim]Skipping further Socratic probing for this field.[/dim]"
            )
            session.add_clarification(
                step_name,
                probe.get("question", ""),
                "skip",
                int(probe.get("turn", 1)),
            )
            # One more synchronous replay so the resolution judge stops it.
            session.replay_step_sync(step_name)
            # Discard any further probes for this field
            with session._lock:
                session._probes.pop(step_name, None)
            continue
        session.add_clarification(
            step_name,
            probe.get("question", ""),
            answer,
            int(probe.get("turn", 1)),
        )
        with console.status("[dim]Re-consulting agent…[/dim]"):
            session.replay_step_sync(step_name)


# ── Step base class ───────────────────────────────────────────────────────────


class _Step:
    """One step in the wizard.  ``undo`` generically pops log entries."""

    field: str
    label: str
    _log_n: int = 0

    def run(self, s: _ADRSession) -> _R:
        raise NotImplementedError

    def undo(self, s: _ADRSession) -> None:
        s.pop_log(self._log_n)
        s.fields.pop(self.field, None)
        self._log_n = 0


# ── Concrete steps ────────────────────────────────────────────────────────────


class _FieldStep(_Step):
    """Single-value prompt (text or constrained choice)."""

    def __init__(
        self,
        field: str,
        label: str,
        prompt: str,
        *,
        choices: list[str] | None = None,
        default: str | None = None,
        fire: bool = True,
    ) -> None:
        self.field = field
        self.label = label
        self.prompt = prompt
        self.choices = choices
        self.default = default
        self.fire = fire
        self._log_n = 0

    def run(self, s: _ADRSession) -> _R:
        s.set_hint(self.label)
        s.render()
        value, back = _ask(self.prompt, choices=self.choices, default=self.default)
        if back:
            return _R.BACK
        s.fields[self.field] = value
        preview = (value[:58] + "…") if len(value) > 58 else value
        s.add_log(self.field, self.label, preview)
        self._log_n = 1
        if self.fire:
            s.fire(self.field, value)
        return _R.NEXT


class _ListStep(_Step):
    """Variable-length list of items."""

    def __init__(
        self, field: str, label: str, item_prompt: str, *, fire: bool = True
    ) -> None:
        self.field = field
        self.label = label
        self.item_prompt = item_prompt
        self.fire = fire
        self._log_n = 0

    def run(self, s: _ADRSession) -> _R:
        s.set_hint(self.label)
        s.render()
        items, back = _ask_list(self.item_prompt)
        if back:
            return _R.BACK
        s.fields[self.field] = items
        preview = (
            (", ".join(items[:3]) + ("…" if len(items) > 3 else "")) if items else "—"
        )
        s.add_log(self.field, self.label, preview)
        self._log_n = 1
        if self.fire and items:
            s.fire(self.field, items)
        return _R.NEXT


class _ChosenOptionStep(_Step):
    """Pick from ``options_considered`` — auto-SKIPs when list is empty."""

    field = "chosen_option"
    label = "Chosen option"

    def __init__(self) -> None:
        self._log_n = 0

    def run(self, s: _ADRSession) -> _R:
        options = s.fields.get("options_considered") or []
        if not options:
            return _R.SKIP
        s.set_hint(self.label)
        s.render()
        value, back = _ask(
            "[bold]Chosen option[/bold]", choices=options, default=options[0]
        )
        if back:
            return _R.BACK
        s.fields[self.field] = value
        s.add_log(self.field, self.label, value)
        self._log_n = 1
        return _R.NEXT


class _ConseqStep(_Step):
    """Confirm gate → positive list → negative list."""

    field = "consequences_positive"
    label = "Consequences"

    def __init__(self) -> None:
        self._log_n = 0

    def run(self, s: _ADRSession) -> _R:
        s.set_hint(self.label)
        s.render()
        add, back = _confirm("Add consequences?", default=False)
        if back:
            return _R.BACK
        if not add:
            s.fields["consequences_positive"] = []
            s.fields["consequences_negative"] = []
            return _R.NEXT
        console.print("  [green]Positive[/green]:")
        pos: list[str] = []
        while True:
            raw = Prompt.ask(
                f"    + [dim](empty to stop){_BACK_HINT}[/dim]", default=""
            )
            if raw.strip() == _BACK_CMD:
                return _R.BACK
            if not raw:
                break
            pos.append(raw)
        console.print("  [red]Negative[/red]:")
        neg: list[str] = []
        while True:
            raw = Prompt.ask(
                f"    - [dim](empty to stop){_BACK_HINT}[/dim]", default=""
            )
            if raw.strip() == _BACK_CMD:
                return _R.BACK
            if not raw:
                break
            neg.append(raw)
        s.fields["consequences_positive"] = pos
        s.fields["consequences_negative"] = neg
        _p = (", ".join(pos[:2]) + ("…" if len(pos) > 2 else "")) if pos else "—"
        _n = (", ".join(neg[:2]) + ("…" if len(neg) > 2 else "")) if neg else "—"
        s.add_log("consequences", "Consequences (+)", _p)
        s.add_log("consequences", "Consequences (−)", _n)
        self._log_n = 2
        return _R.NEXT

    def undo(self, s: _ADRSession) -> None:
        s.pop_log(self._log_n)
        s.fields.pop("consequences_positive", None)
        s.fields.pop("consequences_negative", None)
        self._log_n = 0


class _FrontmatterStep(_Step):
    """Full-template optional fields: decision-makers, consulted, informed."""

    field = "decision_makers"
    label = "Frontmatter"

    def __init__(self) -> None:
        self._log_n = 0

    def run(self, s: _ADRSession) -> _R:
        s.set_hint("Optional frontmatter")
        s.render()
        console.print("[dim]Optional frontmatter — Enter to leave blank[/dim]")
        dm, back = _ask("[bold]Decision makers[/bold]", default="")
        if back:
            return _R.BACK
        co, back = _ask("[bold]Consulted[/bold]", default="")
        if back:
            return _R.BACK
        inf, back = _ask("[bold]Informed[/bold]", default="")
        if back:
            return _R.BACK
        s.fields.update({"decision_makers": dm, "consulted": co, "informed": inf})
        n = 0
        if dm:
            s.add_log("decision_makers", "Decision makers", dm)
            n += 1
        if co:
            s.add_log("consulted", "Consulted", co)
            n += 1
        if inf:
            s.add_log("informed", "Informed", inf)
            n += 1
        self._log_n = n
        return _R.NEXT

    def undo(self, s: _ADRSession) -> None:
        s.pop_log(self._log_n)
        for f in ("decision_makers", "consulted", "informed"):
            s.fields.pop(f, None)
        self._log_n = 0


# ── Step list builders ────────────────────────────────────────────────────────


def _common_steps(title_default: str | None = None) -> list[_Step]:
    return [
        _FieldStep(
            "title",
            "Title",
            "[bold]Title[/bold] (short imperative phrase)",
            default=title_default,
        ),
        _FieldStep(
            "status",
            "Status",
            "[bold]Status[/bold]",
            choices=["proposed", "accepted", "deprecated", "superseded"],
            default="proposed",
            fire=False,
        ),
    ]


def _minimal_steps() -> list[_Step]:
    return [
        _FieldStep(
            "context",
            "Context",
            "[bold]Context[/bold] — what problem motivates this decision?",
        ),
        _ListStep("options_considered", "Considered options", "Option"),
        _ChosenOptionStep(),
        _FieldStep(
            "decision",
            "Decision",
            "[bold]Decision[/bold] — justification ('because …')",
        ),
        _ConseqStep(),
    ]


def _full_steps() -> list[_Step]:
    return [
        _FieldStep(
            "context",
            "Context",
            "[bold]Context[/bold] — what problem motivates this decision?",
        ),
        _ListStep("decision_drivers", "Decision drivers", "Driver"),
        _ListStep("options_considered", "Considered options", "Option"),
        _ChosenOptionStep(),
        _FieldStep(
            "decision",
            "Decision",
            "[bold]Decision[/bold] — justification ('because …')",
        ),
        _ConseqStep(),
        _FrontmatterStep(),
    ]


# ── Template selection ────────────────────────────────────────────────────────


def _select_template() -> str:
    """Prompt for a MADR template variant — required, no way to skip."""
    tbl = Table(show_header=True, header_style="bold", show_edge=False, padding=(0, 2))
    tbl.add_column("Variant", style="bold cyan", width=10)
    tbl.add_column("Frontmatter", width=28)
    tbl.add_column("Sections included")
    for name, fm, sections in _TEMPLATE_ROWS:
        tbl.add_row(name, fm, sections)
    console.print(
        Panel(tbl, title="[bold]Choose a MADR template[/bold]", border_style="green")
    )
    return Prompt.ask(
        "Template", choices=["minimal", "full", "bare"], default="minimal"
    )


# ── Wizard runner ─────────────────────────────────────────────────────────────


def _run_wizard(session: _ADRSession, steps: list[_Step]) -> None:
    """Walk through *steps* with a cursor.  /b pops the history stack.

    Before rendering each step we wait briefly for the *previous* step's
    background suggestion thread so the suggestions are visible as soon as
    the user advances — not only in the final drain at the end.
    """
    history: list[int] = []
    cursor = 0
    while cursor < len(steps):
        session.wait_for_latest()
        _drain_probes(session)
        result = steps[cursor].run(session)
        if result is _R.NEXT:
            history.append(cursor)
            cursor += 1
        elif result is _R.SKIP:
            cursor += 1
        elif result is _R.BACK:
            if history:
                prev = history.pop()
                session.clear_hint()
                steps[prev].undo(session)
                cursor = prev


# ── Entry point ───────────────────────────────────────────────────────────────


def action_create_adr(client: "SdlicitClient", working_dir: str) -> None:
    """Interactively guide the user through creating a new MADR ADR."""
    console.print(
        Panel(
            "New ADR — MADR format  [dim](agent-assisted)[/dim]", border_style="green"
        )
    )

    existing = list_adr_files(working_dir)
    existing_names = [str(f["filename"]) for f in existing]

    # 0. Offer AI ideation pass — suggest WHICH ADR to write next.
    #    The agent only proposes decision titles + rationale, never
    #    solutions.  A picked title prefills the title step below.
    suggested_title = offer_directions_then_prompt_title(client, working_dir)

    # 1. Template (before split — determines required sections)
    template = _select_template()

    # 2. Build step list
    session = _ADRSession(client, working_dir, template)
    steps: list[_Step] = _common_steps(title_default=suggested_title)
    if template == "minimal":
        steps += _minimal_steps()
    elif template == "full":
        steps += _full_steps()

    # 3. Run the wizard
    _run_wizard(session, steps)

    # 4. Final render — drain pending threads so all suggestions are visible
    session.drain()
    _drain_probes(session)
    session.clear_hint()
    session.render()

    # 5. Render MADR and optionally write
    console.print("[bold]Rendering MADR…[/bold]")
    adr_fields = AdrFields(**session.fields)
    result = render_madr(fields=adr_fields, existing_files=existing_names)

    console.print(Panel(result.content, title=result.filename, border_style="cyan"))

    if Confirm.ask("Write this ADR to disk?", default=True):
        path = write_adr(working_dir, result.filename, result.content)
        console.print(f"[green]✓[/green] Written to [bold]{path}[/bold]")
        # Backend-canonical save for traceability
        save_artifact_via_backend(
            client,
            "adr",
            {
                "title": adr_fields.title,
                "context": adr_fields.context,
                "decision": adr_fields.decision,
                "rationale": getattr(adr_fields, "rationale", ""),
                "consequences": getattr(adr_fields, "consequences", ""),
                "options": getattr(adr_fields, "options", []),
            },
            working_dir=working_dir,
        )
        # Auto-ingest into KB (best-effort — failures don't block the user)
        adr_id = path.stem
        ingest_resp = client.ingest_artifact(
            text=result.content, artifact_type="adr", name=adr_id, replace=True
        )
        if ingest_resp.get("chunks"):
            console.print(
                f"[dim]✓ Ingested into KB ({ingest_resp['chunks']} chunks)[/dim]"
            )
    else:
        console.print("[dim]ADR not written.[/dim]")
