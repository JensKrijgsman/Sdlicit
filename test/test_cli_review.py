"""Tests for the Accept/Regenerate/Edit/Skip review prompt (cli/shared/review.py)."""

from __future__ import annotations

from shared.review import _edit_in_external_editor, prompt_review


def test_prompt_review_accept_returns_current_content(monkeypatch):
    monkeypatch.setattr("shared.review.Prompt.ask", lambda *a, **k: "a")
    outcome = prompt_review(artifact_label="SOW", current_content="draft text")
    assert outcome.action == "accept"
    assert outcome.content == "draft text"


def test_prompt_review_regenerate_captures_notes(monkeypatch):
    answers = iter(["r", "make it shorter"])
    monkeypatch.setattr("shared.review.Prompt.ask", lambda *a, **k: next(answers))
    outcome = prompt_review(artifact_label="SOW", current_content="draft text")
    assert outcome.action == "regenerate"
    assert outcome.notes == "make it shorter"


def test_prompt_review_skip_returns_no_content(monkeypatch):
    monkeypatch.setattr("shared.review.Prompt.ask", lambda *a, **k: "s")
    outcome = prompt_review(artifact_label="SOW", current_content="draft text")
    assert outcome.action == "skip"
    assert outcome.content == ""


def test_prompt_review_edit_reads_back_editor_output(monkeypatch):
    monkeypatch.setattr("shared.review.Prompt.ask", lambda *a, **k: "e")
    monkeypatch.setattr(
        "shared.review._edit_in_external_editor",
        lambda content, suffix=".md": "edited content",
    )
    outcome = prompt_review(artifact_label="SOW", current_content="draft text")
    assert outcome.action == "edit"
    assert outcome.content == "edited content"


def test_prompt_review_edit_cancelled_falls_back_to_accept(monkeypatch):
    monkeypatch.setattr("shared.review.Prompt.ask", lambda *a, **k: "e")
    monkeypatch.setattr(
        "shared.review._edit_in_external_editor",
        lambda content, suffix=".md": None,
    )
    outcome = prompt_review(artifact_label="SOW", current_content="draft text")
    assert outcome.action == "accept"
    assert outcome.content == "draft text"


def test_edit_in_external_editor_returns_written_content(monkeypatch):
    # subprocess.call is a no-op here — the temp file keeps the content it
    # was seeded with, which is exactly what a real editor session that
    # saves without changes would leave behind.
    monkeypatch.setattr("shared.review.subprocess.call", lambda args: 0)
    result = _edit_in_external_editor("hello world")
    assert result == "hello world"


def test_edit_in_external_editor_cleans_up_temp_file(monkeypatch, tmp_path):
    seen_paths = []

    def _fake_call(args):
        seen_paths.append(args[1])
        return 0

    monkeypatch.setattr("shared.review.subprocess.call", _fake_call)
    _edit_in_external_editor("content")
    assert seen_paths, "editor should have been invoked with a temp file path"
    from pathlib import Path

    assert not Path(seen_paths[0]).exists()  # cleaned up after read-back


def test_edit_in_external_editor_returns_none_on_missing_editor(monkeypatch):
    def _raise(args):
        raise FileNotFoundError("no such editor")

    monkeypatch.setattr("shared.review.subprocess.call", _raise)
    assert _edit_in_external_editor("content") is None
