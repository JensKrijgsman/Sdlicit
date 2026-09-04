"""Tests for SdlicitConfig defaults, env overrides, and project loading.

auto_detect_thinking is disabled everywhere unless a test is specifically
about the probe, so no test here ever makes a network call.
"""

from __future__ import annotations

import pytest
import yaml

from sdlicit.config import SdlicitConfig


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def test_defaults_match_documented_values():
    cfg = SdlicitConfig(auto_detect_thinking=False)
    assert cfg.provider == "openrouter"
    assert cfg.model_type == "standard"
    assert cfg.enable_rag is True
    assert cfg.enable_tom is True
    assert cfg.enable_socratic is True
    assert cfg.agentic is False
    assert cfg.trace_check_mode == "structural"
    assert cfg.traceability_graph_source == "lightrag"
    assert cfg.socratic_judge_mode == "hybrid"


def test_auto_detect_thinking_off_never_probes():
    # No API key and no ollama host reachable — if the probe ran, this
    # would either raise or hang. Passing quickly proves it did not run.
    cfg = SdlicitConfig(auto_detect_thinking=False, model_type="standard")
    assert cfg.model_type == "standard"


def test_openrouter_probe_skipped_without_api_key():
    # provider defaults to openrouter with an empty api_key, so
    # _probe_thinking's own guard should skip the network call even
    # with auto_detect_thinking left at its default True.
    cfg = SdlicitConfig()
    assert cfg.model_type == "standard"


def test_api_key_env_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    cfg = SdlicitConfig(auto_detect_thinking=False)
    assert cfg.api_key == "sk-test-123"


def test_explicit_api_key_is_overwritten_by_env(monkeypatch):
    # model_post_init always applies the env var when present, even if
    # the caller passed an explicit api_key — documenting real behavior.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    cfg = SdlicitConfig(auto_detect_thinking=False, api_key="sk-explicit")
    assert cfg.api_key == "sk-from-env"


def test_ollama_host_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example.internal:11434")
    cfg = SdlicitConfig(auto_detect_thinking=False)
    assert cfg.ollama_host == "http://example.internal:11434"


def test_auto_detect_thinking_promotes_model_type(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(SdlicitConfig, "_probe_openrouter", lambda self: True)
    cfg = SdlicitConfig()
    assert cfg.model_type == "thinking"


def test_auto_detect_thinking_leaves_standard_when_probe_false(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(SdlicitConfig, "_probe_openrouter", lambda self: False)
    cfg = SdlicitConfig()
    assert cfg.model_type == "standard"


def test_explicit_thinking_type_is_not_reprobed(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(
        SdlicitConfig, "_probe_openrouter", lambda self: calls.append(1) or True
    )
    cfg = SdlicitConfig(model_type="thinking")
    assert cfg.model_type == "thinking"
    assert calls == []  # only probes when model_type is left at "standard"


def test_kb_path_relative_resolves_against_project_dir(tmp_path):
    cfg = SdlicitConfig(auto_detect_thinking=False, project_dir=tmp_path)
    assert cfg.kb_path == tmp_path / ".sdlicit" / "knowledge" / "lightrag_workdir"


def test_kb_path_absolute_is_used_as_is(tmp_path):
    absolute = tmp_path / "elsewhere" / "workdir"
    cfg = SdlicitConfig(
        auto_detect_thinking=False,
        kb_working_dir=str(absolute),
        project_dir=tmp_path,
    )
    assert cfg.kb_path == absolute


def test_from_project_missing_config_uses_defaults(tmp_path):
    cfg = SdlicitConfig.from_project(tmp_path)
    assert cfg.model == "openai/gpt-5.4-nano"
    assert cfg.project_dir == tmp_path


def test_from_project_loads_declared_values(tmp_path):
    sdlicit_dir = tmp_path / ".sdlicit"
    sdlicit_dir.mkdir()
    (sdlicit_dir / "config.yaml").write_text(
        yaml.dump({"model": "openai/gpt-4o", "enable_tom": False}),
        encoding="utf-8",
    )
    cfg = SdlicitConfig.from_project(tmp_path)
    assert cfg.model == "openai/gpt-4o"
    assert cfg.enable_tom is False


def test_from_project_malformed_yaml_raises(tmp_path):
    sdlicit_dir = tmp_path / ".sdlicit"
    sdlicit_dir.mkdir()
    (sdlicit_dir / "config.yaml").write_text("model: [unterminated", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        SdlicitConfig.from_project(tmp_path)


def test_from_project_ignores_project_dir_key_in_yaml(tmp_path):
    # project_dir is always set from the call argument, a stray key in the
    # file itself must not be able to override it.
    sdlicit_dir = tmp_path / ".sdlicit"
    sdlicit_dir.mkdir()
    (sdlicit_dir / "config.yaml").write_text(
        yaml.dump({"project_dir": "/somewhere/else"}), encoding="utf-8"
    )
    cfg = SdlicitConfig.from_project(tmp_path)
    assert cfg.project_dir == tmp_path
