"""Centralised configuration for the Sdlicit system.

Reads from ``.sdlicit/config.yaml`` in the project directory, with
environment-variable overrides for secrets (API keys).
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SdlicitConfig(BaseModel):
    """Single source of truth for all runtime settings."""

    provider: Literal["openrouter", "ollama"] = "openrouter"
    model: str = "openai/gpt-5.4-nano"
    model_type: Literal["standard", "thinking"] = "standard"
    # When True, a model left at the default "standard" type is probed once and
    # promoted to "thinking" if it emits reasoning tokens. Set False to pin the
    # type explicitly. Small local reasoning models parse structured output more
    # reliably under the "standard" (ChainOfThought) contract than under the
    # "thinking" (bare Predict) path, so the benchmark pins them. See T008.
    auto_detect_thinking: bool = True
    api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    embed_model: str = "openai/text-embedding-3-large"
    embed_dim: int = 3072
    embed_max_tokens: int = 8192
    # Embedder provider, decoupled from the generation `provider`. An empty
    # string means "use `provider`". Set to "openrouter" to keep a local
    # (ollama) generation model while embedding against the shared,
    # OpenRouter-built knowledge base, so the query embedder matches the
    # embedder that built the corpus.
    embed_provider: str = ""
    kb_working_dir: str = ".sdlicit/knowledge/lightrag_workdir"
    suggestion_threshold: float = 0.5
    log_prompts: bool = False
    project_dir: Path = Field(default_factory=lambda: Path.cwd())

    # --- RAG routing configuration ---
    # Probe method: "networkx" (check graph entities) or "local" (LightRAG local top_k=1)
    kb_probe_method: Literal["networkx", "local"] = "networkx"
    # Per-agent default store preferences: "knowledge", "artifacts", or "all"
    kb_agent_stores: dict[str, str] = Field(
        default_factory=lambda: {
            "sow": "all",
            "adr": "artifacts",
            "socratic": "all",
            "requirement": "knowledge",
        }
    )
    # Source path prefixes for store filtering
    kb_knowledge_prefix: str = "knowledge/"
    kb_artifacts_prefix: str = "artifacts/"

    # --- Per-agent/stage model overrides ---
    # Maps agent or stage name → model string. Agents not listed use the
    # global ``model`` field.  Example in config.yaml:
    #   model_overrides:
    #     sow: "openai/gpt-4o"
    #     socratic: "anthropic/claude-sonnet-4"
    model_overrides: dict[str, str] = Field(default_factory=dict)

    # --- Agent feature flags (for ablation testing) ---
    enable_rag: bool = True
    enable_tom: bool = True
    enable_socratic: bool = True

    # --- Context injection (fast testing without LightRAG) ---
    # When set, all KB queries return this text instead of querying LightRAG.
    # Works even with enable_rag=False — agents receive a static context provider.
    # Set to "" (default) for normal KB operation.
    context_override: str = ""

    # --- Traceability configuration ---
    # Trace check mode controls the depth of analysis:
    # "structural" = ID cross-reference validation only (fast, deterministic)
    # "semantic"   = structural + TF-IDF/Jaccard/NMF content similarity (needs scikit-learn)
    # "full"       = structural + semantic + graph-based conflict detection (may use LLM)
    trace_check_mode: Literal["structural", "semantic", "full"] = "structural"

    # Graph source for traceability: where edges come from
    # "frontmatter" = parse YAML frontmatter only (fast, no LLM)
    # "lightrag"    = frontmatter + LightRAG's entity-relation graph (richer, needs KB)
    traceability_graph_source: Literal["frontmatter", "lightrag"] = "lightrag"

    # --- ToM focus configuration ---
    # Controls what the ToM agent pays attention to when building user context.
    # Options: "all" | "scaffolding" | "frustration" | "preferences"
    # This allows testing different ToM aspects independently.
    tom_focus: Literal["all", "scaffolding", "frustration", "preferences"] = "all"

    # --- Socratic cross-cutting consultation ---
    # "dspy"      → always run InputAdequacyJudge + OutputGroundingJudge
    # "hybrid"    → cheap heuristics first, fall back to DSPy judges
    # "heuristic" → length-based heuristics only (DISCOURAGED — no hallucination check)
    socratic_judge_mode: Literal["dspy", "hybrid", "heuristic"] = "hybrid"
    socratic_max_turns: int = 7  # per-field limit (not per-session)

    # --- Session token budgeting / compaction ---
    # Approximate context window of the active model (used as denominator
    # for compaction threshold).  Override per-model in config.yaml when the
    # default is wrong.
    model_context_window: int = 8192
    # Fraction of model_context_window that triggers ToM-driven compaction.
    # 0.4 = 40%.  Compaction can also be invoked manually from the CLI.
    compact_threshold_pct: float = 0.4

    # --- Agentic mode (DSPy tool-calling) ---
    # When True, agents use dspy.ReAct with tool functions instead of fixed
    # signature-only workflows. Tools are still callable directly when False.
    # This allows switching between deterministic pipelines and LLM-driven
    # tool selection at runtime.
    agentic: bool = False
    # Maximum tool-calling iterations per agent invocation (ReAct loop cap).
    agentic_max_iters: int = 5

    def _probe_thinking(self) -> bool:
        """Ask the model for a tiny response; return True if it emits reasoning tokens.

        - OpenRouter: POST /v1/chat/completions with default settings (no forced
          reasoning), check reasoning_tokens / message.reasoning. Only models that
          reason by default register, so reasoning-capable standard models do not.
        - Ollama:     POST /api/chat (native endpoint), thinking is on by default for
          capable models, check message.thinking in the response.

        Falls back to False on any error so startup is never blocked.
        """
        try:
            if self.provider == "openrouter" and self.api_key:
                return self._probe_openrouter()
            if self.provider == "ollama":
                return self._probe_ollama()
        except Exception:
            pass
        return False

    def _probe_openrouter(self) -> bool:
        # Detect whether the model reasons BY DEFAULT, not merely whether it can
        # reason when asked. Forcing reasoning.enabled=true made every
        # reasoning-capable model (mistral-small, gpt-5.x) report reasoning tokens
        # and look like a thinking model. So send a plain request with room to
        # think and check whether reasoning appears unprompted. Genuine reasoning
        # models (o-series, R1) emit reasoning tokens here; standard models emit
        # none and answer in plain text.
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": "What is 17 times 24? Think first."}
                ],
                "max_tokens": 64,
            }
        ).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        reasoning_tokens = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        ) or 0
        return (
            reasoning_tokens > 0
            or bool(message.get("reasoning"))
            or bool(message.get("reasoning_details"))
        )

    def _probe_ollama(self) -> bool:
        # Ollama native /api/chat — thinking enabled by default for capable models.
        # https://docs.ollama.com/capabilities/thinking
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }
        ).encode()
        base = self.ollama_host.rstrip("/")
        req = urllib.request.Request(
            f"{base}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return bool((data.get("message") or {}).get("thinking"))

    def model_post_init(self, __context: object) -> None:
        if env_key := os.environ.get("OPENROUTER_API_KEY"):
            self.api_key = env_key
        if env_host := os.environ.get("OLLAMA_HOST"):
            self.ollama_host = env_host
        # Auto-detect thinking models when model_type is not explicitly set.
        # Probe approach is provider-agnostic and future-proof — no regex to maintain.
        if (
            self.auto_detect_thinking
            and self.model_type == "standard"
            and self._probe_thinking()
        ):
            self.model_type = "thinking"

    @classmethod
    def from_project(cls, project_dir: Path) -> SdlicitConfig:
        """Load config from ``<project_dir>/.sdlicit/config.yaml``."""
        config_path = project_dir / ".sdlicit" / "config.yaml"
        data: dict = {}
        if config_path.exists():
            raw = config_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        data["project_dir"] = project_dir

        return cls(**data)

    @property
    def kb_path(self) -> Path:
        """Absolute path to the LightRAG working directory."""
        p = Path(self.kb_working_dir)
        if not p.is_absolute():
            return self.project_dir / p
        return p
