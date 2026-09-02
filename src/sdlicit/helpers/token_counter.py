"""Lightweight token-counting middleware for LLM calls.

Wraps any async LLM function and tallies approximate token usage
using a simple whitespace-based heuristic (avoids tiktoken dependency).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenCounter:
    """Accumulates token counts across multiple LLM calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_count: int = 0

    def record(self, prompt_text: str, completion_text: str) -> None:
        self.prompt_tokens += _estimate_tokens(prompt_text)
        self.completion_tokens += _estimate_tokens(completion_text)
        self.call_count += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "call_count": self.call_count,
        }

    def reset(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.call_count = 0


def _estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token heuristic (avoids tiktoken dependency)."""
    return max(1, len(text) // 4)


def make_counting_llm(base_llm_func, counter: TokenCounter):
    """Wrap an async LLM function to count tokens on every call.

    Preserves the original positional-arg convention used by LightRAG
    (e.g. ``openai_complete_if_cache(model, prompt, ...)``).

    Also records usage onto the request-scoped counter (if any) under
    the agent name ``"lightrag"`` so per-request token headers reflect
    KB-side LLM cost.
    """

    from sdlicit.logging.usage import current as _current_usage

    async def counting_func(*args, **kwargs):
        result = await base_llm_func(*args, **kwargs)
        # Best-effort: grab the prompt from args (pos 1 for openai, pos 0 for ollama)
        prompt_text = str(args[1]) if len(args) > 1 else str(args[0]) if args else ""
        counter.record(prompt_text, str(result))

        request_counter = _current_usage()
        if request_counter is not None:
            request_counter.record_text("lightrag", prompt_text, str(result))
        return result

    return counting_func
