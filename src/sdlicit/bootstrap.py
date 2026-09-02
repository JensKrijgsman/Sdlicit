"""System bootstrap — the single place where the whole system is assembled.

Both the CLI and the FastAPI server call ``create_system()`` to get
a fully-configured Orchestrator.  No caller ever needs to import
``dspy`` or know how the LLM is wired up.

This is the composition root: assemble once, pass values down.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dspy

from sdlicit.agents.llm.dspy_programs import (
    ValidatedADRProgram,
    ValidatedGherkinProgram,
    ValidatedRequirementProgram,
    ValidatedUserStoryProgram,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.config import SdlicitConfig
from sdlicit.logging import get_logger
from sdlicit.orchestrator import Orchestrator

_log = get_logger("bootstrap")

# Compiled state lives next to config: .sdlicit/compiled_state.json
_COMPILED_STATE_FILENAME = "compiled_state.json"


def _configure_dspy(config: SdlicitConfig) -> None:
    """Wire DSPy to the provider declared in config.  Internal detail."""
    if config.api_key and config.provider == "openrouter":
        try:
            from dspy.adapters.baml_adapter import BAMLAdapter

            dspy.configure(
                lm=dspy.LM(
                    model=f"openrouter/{config.model}",
                    api_key=config.api_key,
                ),
                adapter=BAMLAdapter(),
            )
        except ImportError:
            dspy.configure(
                lm=dspy.LM(
                    model=f"openrouter/{config.model}",
                    api_key=config.api_key,
                )
            )
    elif config.provider == "ollama":
        dspy.configure(
            lm=dspy.LM(
                model=f"ollama_chat/{config.model}",
                api_base=config.ollama_host,
                timeout=120,
                num_retries=3,
            )
        )


def _register_programs(gateway: LLMGateway, model_type: str) -> None:
    """Register validated DSPy programs (Modules with dspy.Refine).

    These programs wrap generation-stage signatures with ``dspy.Refine``
    so that format violations trigger DSPy's feedback-driven retry
    loop instead of silent failures.
    """
    gateway.register_program(
        "GherkinGeneration", ValidatedGherkinProgram(model_type=model_type)
    )
    gateway.register_program(
        "UserStoryGeneration", ValidatedUserStoryProgram(model_type=model_type)
    )
    gateway.register_program(
        "RequirementGeneration", ValidatedRequirementProgram(model_type=model_type)
    )
    gateway.register_program(
        "ADRGeneration", ValidatedADRProgram(model_type=model_type)
    )


def create_system(project_dir: Path) -> tuple[SdlicitConfig, Orchestrator]:
    """Load config, configure the LLM, and return a ready Orchestrator.

    Returns ``(config, orchestrator)`` — pure values, no side-effects
    beyond DSPy global configuration (a DSPy constraint).
    """
    config = SdlicitConfig.from_project(project_dir)
    _configure_dspy(config)

    # Wire prompt logging (JSONL audit trail for thesis reproducibility)
    from sdlicit.logging import prompt_logger
    prompt_logger.configure(
        enabled=config.log_prompts,
        log_dir=config.project_dir / ".sdlicit" / "logs",
    )

    orchestrator = Orchestrator(config)

    # Register validated programs on the gateway
    _register_programs(orchestrator.llm, config.model_type)

    # Load optimizer-compiled state if present (.sdlicit/compiled_state.json)
    compiled_path = config.project_dir / ".sdlicit" / _COMPILED_STATE_FILENAME
    orchestrator.llm.load_state(compiled_path)

    # Wire per-signature model overrides (for thesis ablations)
    if config.model_overrides:
        override_lms: dict[str, Any] = {}
        for sig_name, model_str in config.model_overrides.items():
            if config.provider == "openrouter":
                prefix = "openrouter/" if not model_str.startswith("openrouter/") else ""
                override_lms[sig_name] = dspy.LM(
                    model=f"{prefix}{model_str}",
                    api_key=config.api_key,
                )
            elif config.provider == "ollama":
                override_lms[sig_name] = dspy.LM(
                    model=f"ollama_chat/{model_str}",
                    api_base=config.ollama_host,
                    timeout=120,
                    num_retries=3,
                )
        orchestrator.llm.set_overrides(override_lms)

    _log.info(
        "System bootstrapped — model=%s model_type=%s embed=%s provider=%s",
        config.model,
        config.model_type,
        config.embed_model,
        config.provider,
    )
    return config, orchestrator
