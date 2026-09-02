"""Per-request token-usage accounting.

A request-scoped ``UsageCounter`` is bound to a ``contextvars.ContextVar``
so that any LLM call made anywhere in the request chain can record its
token usage without threading the counter through every signature.

Two recording paths exist:

* **DSPy path** — :class:`LLMGateway` snapshots ``dspy.settings.lm.history``
  before/after each ``predict`` and pushes the new entries' ``usage`` into
  the active counter.
* **LightRAG path** — :func:`make_counting_llm` (existing) wraps the raw
  LLM function and pushes prompt/completion text into the counter via
  :meth:`UsageCounter.record_text`.

Token estimation falls back through three tiers:
1. Provider-reported counts (preferred — exact).
2. ``tiktoken`` if installed.
3. ``len(text) // 4`` whitespace heuristic.

The FastAPI middleware in :mod:`sdlicit.main` activates a counter for
the duration of every request and writes ``X-Sdlicit-Tokens-*`` response
headers so the CLI can journal real usage per call.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

try:
    import tiktoken  # type: ignore[import-untyped]

    _TIKTOKEN_OK = True
except ImportError:  # pragma: no cover
    _TIKTOKEN_OK = False


# ── Token estimation ───────────────────────────────────────────────────────


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Best-effort token count for *text*.

    Tries ``tiktoken`` first (using the model name to pick an encoding),
    then falls back to a 4-chars-per-token heuristic.
    """
    if not text:
        return 0
    if _TIKTOKEN_OK:
        try:
            enc = (
                tiktoken.encoding_for_model(model)
                if model
                else tiktoken.get_encoding("cl100k_base")
            )
            return len(enc.encode(text))
        except (KeyError, ValueError):
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
    return max(1, len(text) // 4)


# ── Usage counter ──────────────────────────────────────────────────────────


@dataclass
class AgentUsage:
    """Token totals attributed to a single agent within one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
        }


@dataclass
class UsageCounter:
    """Aggregates per-agent token usage for a single request.

    Use :func:`current` to fetch the active counter, or :func:`bind` /
    :func:`unbind` (typically via the request middleware) to scope one.
    """

    by_agent: dict[str, AgentUsage] = field(default_factory=dict)
    model: str | None = None

    # -- Recording ----------------------------------------------------------

    def record_usage(
        self,
        agent: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        slot = self.by_agent.setdefault(agent, AgentUsage())
        slot.prompt_tokens += prompt_tokens
        slot.completion_tokens += completion_tokens
        slot.calls += 1

    def record_text(
        self,
        agent: str,
        prompt_text: str,
        completion_text: str,
    ) -> None:
        self.record_usage(
            agent,
            estimate_tokens(prompt_text, self.model),
            estimate_tokens(completion_text, self.model),
        )

    # -- Aggregates ---------------------------------------------------------

    @property
    def prompt_tokens(self) -> int:
        return sum(a.prompt_tokens for a in self.by_agent.values())

    @property
    def completion_tokens(self) -> int:
        return sum(a.completion_tokens for a in self.by_agent.values())

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def calls(self) -> int:
        return sum(a.calls for a in self.by_agent.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "by_agent": {k: v.as_dict() for k, v in self.by_agent.items()},
        }


# ── ContextVar plumbing ────────────────────────────────────────────────────

_current: contextvars.ContextVar[UsageCounter | None] = contextvars.ContextVar(
    "sdlicit_usage_counter", default=None
)


def current() -> UsageCounter | None:
    """Return the request-scoped counter, or ``None`` outside a request."""
    return _current.get()


def bind(counter: UsageCounter) -> contextvars.Token[UsageCounter | None]:
    """Bind *counter* as the active counter; returns a reset token."""
    return _current.set(counter)


def unbind(token: contextvars.Token[UsageCounter | None]) -> None:
    """Reverse a previous :func:`bind`."""
    _current.reset(token)


def record_for_agent(
    agent: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Convenience wrapper — records on the active counter if one exists."""
    counter = current()
    if counter is not None:
        counter.record_usage(agent, prompt_tokens, completion_tokens)
