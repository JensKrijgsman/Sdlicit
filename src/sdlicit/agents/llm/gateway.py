"""LLM gateway — model-aware DSPy module selection with compiled state.

Picks the optimal DSPy module per signature:
- Thinking models (o1/o3, DeepSeek-R1): ``dspy.Predict`` — native hidden
  reasoning tokens make explicit CoT *harmful*.
- Standard models + simple tasks (classification, extraction):
  ``dspy.Predict`` — no reasoning overhead needed.
- Standard models + reasoning tasks: ``dspy.ChainOfThought`` — the
  explicit scratchpad improves accuracy.

Supports the DSPy optimizer workflow:
  Build pipeline → Run Optimizer offline → ``save_state()`` →
  ``load_state()`` in production for fast, optimized execution.

Validated programs (``dspy.Module`` subclasses using ``dspy.Refine``)
can be registered per signature name.  When a registered program
exists, ``predict()`` dispatches to it transparently so that calling
agents need no changes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from sdlicit.logging import get_logger
from sdlicit.logging.usage import current as _current_usage
from sdlicit.logging.usage import estimate_tokens

_log = get_logger("tool")

# Signatures that are inherently simple (classification, extraction) and
# never benefit from chain-of-thought overhead, regardless of model type.
_SIMPLE_SIGNATURES: frozenset[str] = frozenset(
    {
        "ADRDomainClassification",
        "ExtractSOW",
    }
)


class LLMGateway:
    """Model-aware async DSPy gateway with compiled-state support."""

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        self._model_type = model_type
        self._modules: dict[str, dspy.Module] = {}
        self._programs: dict[str, dspy.Module] = {}
        self._compiled_state: dict[str, Any] = {}
        self._predict_lock = asyncio.Lock()
        self._overrides: dict[str, Any] = {}  # sig_name → dspy.LM

    def set_overrides(self, overrides: dict[str, Any]) -> None:
        """Set per-signature model overrides (sig_name → dspy.LM instance)."""
        self._overrides = overrides
        if overrides:
            _log.info("Model overrides active for: %s", list(overrides.keys()))

    # -- Module selection -------------------------------------------------------

    def _get_module(self, signature: type[dspy.Signature]) -> dspy.Module:
        """Return (or create) the optimal DSPy module for *signature*.

        Resolution order:
        1. Registered validated program  (``register_program``)
        2. ``dspy.Predict``              if thinking model or simple task
        3. ``dspy.ChainOfThought``       otherwise
        """
        sig_name = signature.__name__

        # 1. Pre-built validated program wins
        if sig_name in self._programs:
            return self._programs[sig_name]

        # 2. Lazy-create the right module type
        if sig_name not in self._modules:
            if self._model_type == "thinking" or sig_name in _SIMPLE_SIGNATURES:
                module = dspy.Predict(signature)
                _log.debug(
                    "module %s → Predict (model_type=%s)", sig_name, self._model_type
                )
            else:
                module = dspy.ChainOfThought(signature)
                _log.debug("module %s → ChainOfThought", sig_name)

            # Apply saved compiled state if available
            if sig_name in self._compiled_state:
                module.load_state(self._compiled_state[sig_name])
                _log.info("Applied compiled state for %s", sig_name)

            self._modules[sig_name] = module

        return self._modules[sig_name]

    # -- Program registry -------------------------------------------------------

    def register_program(self, sig_name: str, program: dspy.Module) -> None:
        """Register a validated program (Module with dspy.Refine) for *sig_name*.

        When ``predict()`` is called for a signature whose ``__name__``
        matches *sig_name*, the registered program is used instead of
        the default Predict / ChainOfThought wrapper.
        """
        self._programs[sig_name] = program
        _log.info("Registered validated program for %s", sig_name)

    # -- Predict ----------------------------------------------------------------

    async def predict(
        self, signature: type[dspy.Signature], **kwargs: str
    ) -> dspy.Prediction:
        """Run a DSPy signature with model-appropriate module selection.

        The Prediction object carries typed Pydantic outputs when BAMLAdapter
        is configured (e.g. ``result.suggestion`` is a ``StepSuggestionOutput``).

        If a model override is configured for this signature name, uses
        that LM via ``dspy.context(lm=...)``.
        """
        lm = dspy.settings.lm
        if lm is None:
            _log.warning("DSPy LM not configured — returning empty prediction")
            return dspy.Prediction()

        sig_name = signature.__name__
        override_lm = self._overrides.get(sig_name)
        effective_lm = override_lm or lm

        _log.info(
            "predict  signature=%s  inputs=%s  model=%s",
            sig_name, list(kwargs.keys()),
            getattr(effective_lm, "model", "default"),
        )

        module = self._get_module(signature)

        # Serialise LLM calls to prevent DSPy history race conditions
        async with self._predict_lock:
            # Snapshot LM history length so we can attribute new entries to
            # this call when recording usage on the active request counter.
            history_before = len(getattr(effective_lm, "history", []) or [])

            loop = asyncio.get_running_loop()
            if override_lm is not None:
                def _run_with_override() -> dspy.Prediction:
                    with dspy.context(lm=override_lm):
                        return module(**kwargs)

                result = await loop.run_in_executor(None, _run_with_override)
            else:
                result = await loop.run_in_executor(None, lambda: module(**kwargs))

            # Record usage on the request-scoped counter (if one is bound).
            self._record_usage(sig_name, effective_lm, history_before, kwargs, result)

        _log.info("predict  signature=%s → done", sig_name)
        return result

    # -- Agentic predict (ReAct with tools) ------------------------------------

    async def predict_react(
        self,
        signature: str | type[dspy.Signature],
        tools: list[Any],
        max_iters: int = 5,
        **kwargs: str,
    ) -> dspy.Prediction:
        """Run a DSPy ReAct module with tool-calling.

        Uses ``dspy.ReAct`` which autonomously reasons and selects tools.
        Each tool should be either a ``dspy.Tool`` instance or a callable
        (which will be auto-wrapped).

        Args:
            signature: DSPy signature (class or inline string).
            tools: List of dspy.Tool instances or plain callables.
            max_iters: Maximum reasoning-action iterations.
            **kwargs: Input field values for the signature.

        Returns:
            dspy.Prediction with the final outputs after tool execution.
        """
        lm = dspy.settings.lm
        if lm is None:
            _log.warning("DSPy LM not configured — returning empty prediction")
            return dspy.Prediction()

        # Auto-wrap plain callables as dspy.Tool
        wrapped_tools = []
        for t in tools:
            if isinstance(t, dspy.Tool):
                wrapped_tools.append(t)
            elif callable(t):
                wrapped_tools.append(dspy.Tool(t))
            else:
                raise TypeError(f"Expected callable or dspy.Tool, got {type(t)}")

        sig_name = signature.__name__ if hasattr(signature, "__name__") else str(signature)
        override_lm = self._overrides.get(sig_name)
        effective_lm = override_lm or lm

        _log.info(
            "predict_react  signature=%s  tools=%s  max_iters=%d  model=%s",
            sig_name,
            [t.name for t in wrapped_tools],
            max_iters,
            getattr(effective_lm, "model", "default"),
        )

        react_module = dspy.ReAct(signature, tools=wrapped_tools, max_iters=max_iters)

        async with self._predict_lock:
            history_before = len(getattr(effective_lm, "history", []) or [])

            loop = asyncio.get_running_loop()
            if override_lm is not None:
                def _run_react_with_override() -> dspy.Prediction:
                    with dspy.context(lm=override_lm):
                        return react_module(**kwargs)

                result = await loop.run_in_executor(None, _run_react_with_override)
            else:
                result = await loop.run_in_executor(None, lambda: react_module(**kwargs))

            self._record_usage(sig_name, effective_lm, history_before, kwargs, result)

        _log.info("predict_react  signature=%s → done", sig_name)
        return result

    # -- Usage recording --------------------------------------------------------

    def _record_usage(
        self,
        sig_name: str,
        lm: Any,
        history_before: int,
        inputs: dict[str, Any],
        result: dspy.Prediction,
    ) -> None:
        """Push tokens consumed by this predict call into the active counter."""
        counter = _current_usage()
        if counter is None:
            return

        history = getattr(lm, "history", None) or []
        new_entries = history[history_before:] if history else []

        prompt_tok = 0
        completion_tok = 0

        for entry in new_entries:
            usage = entry.get("usage") if isinstance(entry, dict) else None
            if usage and isinstance(usage, dict):
                prompt_tok += int(
                    usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                )
                completion_tok += int(
                    usage.get("completion_tokens") or usage.get("output_tokens") or 0
                )

        # Fallback: estimate from prompt/response text on the entry.
        if prompt_tok == 0 and completion_tok == 0:
            for entry in new_entries:
                if not isinstance(entry, dict):
                    continue
                prompt_text = entry.get("prompt") or ""
                if not prompt_text and "messages" in entry:
                    prompt_text = "\n".join(
                        str(m.get("content", "")) for m in entry["messages"] or []
                    )
                response_text = entry.get("response") or entry.get("output") or ""
                model_name = entry.get("model") or counter.model
                prompt_tok += estimate_tokens(str(prompt_text), model_name)
                completion_tok += estimate_tokens(str(response_text), model_name)

        # Last-resort: estimate from inputs/result strings.
        if prompt_tok == 0 and completion_tok == 0:
            prompt_tok = estimate_tokens(
                "\n".join(str(v) for v in inputs.values()), counter.model
            )
            completion_tok = estimate_tokens(str(result), counter.model)

        counter.record_usage(sig_name, prompt_tok, completion_tok)

        # Prompt audit logging (if enabled via config)
        from sdlicit.logging.prompt_logger import log_prompt_exchange

        for entry in new_entries:
            if not isinstance(entry, dict):
                continue
            prompt_text = entry.get("prompt") or ""
            if not prompt_text and "messages" in entry:
                prompt_text = "\n".join(
                    str(m.get("content", "")) for m in entry.get("messages") or []
                )
            response_text = entry.get("response") or entry.get("output") or ""
            log_prompt_exchange(
                signature=sig_name,
                prompt=str(prompt_text),
                response=str(response_text),
                inputs=inputs,
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
                model=getattr(lm, "model", None) or counter.model,
            )

    # -- Compiled-state persistence ---------------------------------------------

    def save_state(self, path: Path) -> None:
        """Persist all module states to *path* (JSON).

        Run after an optimizer pass (``BootstrapFewShot``, ``MIPROv2``,
        ``SIMBA``) to capture few-shot demonstrations and tuned
        instructions.  Load the file in production with ``load_state``.
        """
        state: dict[str, Any] = {}
        for sig_name, module in {**self._modules, **self._programs}.items():
            state[sig_name] = module.dump_state()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        _log.info("Saved compiled state → %s (%d modules)", path, len(state))

    def load_state(self, path: Path) -> None:
        """Load compiled module states from *path*.

        States are applied lazily: when ``_get_module`` first creates
        a module, it checks ``_compiled_state`` and applies matching
        entries.  Already-instantiated modules are patched immediately.
        """
        if not path.exists():
            _log.debug("No compiled state at %s — using defaults", path)
            return

        self._compiled_state = json.loads(path.read_text(encoding="utf-8"))
        _log.info("Loaded compiled state ← %s", path)

        # Patch already-instantiated modules
        for sig_name, module_state in self._compiled_state.items():
            target = self._modules.get(sig_name) or self._programs.get(sig_name)
            if target is not None:
                target.load_state(module_state)
                _log.info("Hot-applied compiled state for %s", sig_name)


class StubGateway(LLMGateway):
    """In-process stub — no network calls.  For testing."""

    async def predict(
        self, signature: type[dspy.Signature], **kwargs: str
    ) -> dspy.Prediction:
        return dspy.Prediction()


def create_gateway(
    model_type: Literal["standard", "thinking"] = "standard",
) -> LLMGateway:
    """Create an LLM gateway.  ``"dspy"`` (default) or ``"stub"``."""
    return LLMGateway(model_type=model_type)
