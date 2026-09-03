"""Tests for the ADR supersession hint capture in the create-ADR wizard.

_ADRSession._fetch() is the one place a backend supersedes_hint (surfaced
when the LLM notices a new ADR appears to replace an existing one) gets
picked up into wizard state, so the save flow can later offer to call
client.supersede_adr(). This only covers that capture logic, not the
full interactive wizard (Rich Live rendering is not practically unit
testable — see the module docstring in create_adr.py).
"""

from __future__ import annotations

from stages.composing.create_adr import _ADRSession


class _FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = iter(responses)

    def step_event(self, **kwargs):
        return next(self._responses)


def test_fetch_captures_first_supersedes_hint():
    client = _FakeClient(
        [{"supersedes_hint": {"old_adr_id": "ADR-0002", "reason": "same topic"}}]
    )
    session = _ADRSession(client, working_dir="/tmp/proj", template="minimal")

    session._fetch("context", "value", "cyan", snapshot={})

    assert session.supersedes_hint == {"old_adr_id": "ADR-0002", "reason": "same topic"}


def test_fetch_does_not_overwrite_an_existing_hint():
    client = _FakeClient(
        [
            {"supersedes_hint": {"old_adr_id": "ADR-0002", "reason": "first"}},
            {"supersedes_hint": {"old_adr_id": "ADR-0009", "reason": "second"}},
        ]
    )
    session = _ADRSession(client, working_dir="/tmp/proj", template="minimal")

    session._fetch("context", "value", "cyan", snapshot={})
    session._fetch("decision", "value2", "cyan", snapshot={})

    # First hint wins — later steps should not clobber it.
    assert session.supersedes_hint == {"old_adr_id": "ADR-0002", "reason": "first"}


def test_fetch_with_no_hint_leaves_supersedes_hint_none():
    client = _FakeClient([{"suggestion": {"message": "looks good"}}])
    session = _ADRSession(client, working_dir="/tmp/proj", template="minimal")

    session._fetch("context", "value", "cyan", snapshot={})

    assert session.supersedes_hint is None


def test_fetch_client_error_appends_system_suggestion_not_raise():
    class _RaisingClient:
        def step_event(self, **kwargs):
            raise RuntimeError("connection reset")

    session = _ADRSession(_RaisingClient(), working_dir="/tmp/proj", template="minimal")
    session._fetch("context", "value", "cyan", snapshot={})

    assert any(
        s.agent == "System" and "connection reset" in s.message
        for s in session._suggestions
    )
