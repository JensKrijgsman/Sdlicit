"""Tests for the CLI's per-artifact traceability check (cli/stages/expansion/traceability.py)."""

from __future__ import annotations

from stages.expansion.traceability import _severity_colour, action_check_artifact_traceability


def test_severity_colour_maps_known_severities():
    assert _severity_colour("error") == "red"
    assert _severity_colour("warning") == "yellow"
    assert _severity_colour("info") == "dim"


def test_severity_colour_defaults_to_white_for_unknown():
    assert _severity_colour("something-else") == "white"


class _FakeClient:
    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls: list[tuple] = []

    def check_traceability(self, artifact_id, artifact_content=None, project_dir=None):
        self.calls.append((artifact_id, artifact_content, project_dir))
        return self._result


def test_check_artifact_traceability_calls_client_with_picked_adr(monkeypatch, tmp_path):
    adr_dir = tmp_path / ".sdlicit" / "artifacts" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(
        "---\nid: ADR-0001\nstatus: accepted\n---\n\n# Use Postgres\n"
    )

    client = _FakeClient(
        {"issues": [], "impacted_nodes": [], "suggested_implements": []}
    )
    monkeypatch.setattr(
        "stages.expansion.traceability.Prompt.ask", lambda *a, **k: "1"
    )

    action_check_artifact_traceability(client, str(tmp_path))

    assert len(client.calls) == 1
    artifact_id, content, project_dir = client.calls[0]
    assert artifact_id == "0001-use-postgres"
    assert "Use Postgres" in content
    assert project_dir == str(tmp_path)


def test_check_artifact_traceability_no_adrs_does_not_call_client(tmp_path):
    client = _FakeClient({})
    action_check_artifact_traceability(client, str(tmp_path))
    assert client.calls == []


def test_check_artifact_traceability_client_error_is_caught(monkeypatch, tmp_path):
    adr_dir = tmp_path / ".sdlicit" / "artifacts" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-x.md").write_text("---\nid: ADR-0001\n---\n\n# X\n")

    class _RaisingClient:
        def check_traceability(self, *a, **k):
            raise RuntimeError("server unreachable")

    monkeypatch.setattr(
        "stages.expansion.traceability.Prompt.ask", lambda *a, **k: "1"
    )
    # Should not raise — errors are caught and printed, not propagated.
    action_check_artifact_traceability(_RaisingClient(), str(tmp_path))
