"""Tests for the CLI's crash-safe session journal (cli/shared/journal.py)."""

from __future__ import annotations

import json

from shared import journal as journal_mod
from shared.journal import Journal, detect_crashed_sessions, mark_session_crashed, read_index


def test_journal_init_creates_active_session_dirs_and_index(tmp_path):
    j = Journal(tmp_path, session_id="sess1")

    assert j.chat_session_dir.is_dir()
    assert j.sdlicit_session_dir.is_dir()
    assert j.user_dir.is_dir()
    assert j.meta_path.exists()

    meta = json.loads(j.meta_path.read_text())
    assert meta["status"] == "active"
    assert meta["session_id"] == "sess1"

    index = read_index(tmp_path)
    assert index["active_session_id"] == "sess1"
    assert index["last_session_id"] == "sess1"
    assert index["recent"][0]["session_id"] == "sess1"


def test_record_request_then_response_writes_one_file_and_accumulates_tokens(tmp_path):
    j = Journal(tmp_path, session_id="sess1")
    ctx = j.record_request("intake/sow", "POST", {"raw_brief": "hello"})
    j.record_response(
        ctx,
        status_code=200,
        response={"sow": "..."},
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "calls": 1},
    )

    request_file = j.chat_session_dir / ctx["filename"]
    assert request_file.exists()
    entry = json.loads(request_file.read_text())
    # The response write overwrites the same per-request file (final state
    # carries both the request and the outcome), not a second file.
    assert entry["kind"] == "response"
    assert entry["status_code"] == 200

    assert j.totals["total_tokens"] == 15
    assert j.totals["calls"] == 1


def test_record_response_error_path_sets_kind_error(tmp_path):
    j = Journal(tmp_path, session_id="sess1")
    ctx = j.record_request("intake/sow", "POST", {})
    j.record_response(ctx, status_code=0, error="ConnectError: refused")

    entry = json.loads((j.chat_session_dir / ctx["filename"]).read_text())
    assert entry["kind"] == "error"
    assert entry["error"] == "ConnectError: refused"


def test_mark_closed_updates_status_and_clears_active_session(tmp_path):
    j = Journal(tmp_path, session_id="sess1")
    j.mark_closed("closed")

    meta = json.loads(j.meta_path.read_text())
    assert meta["status"] == "closed"

    index = read_index(tmp_path)
    assert index["active_session_id"] is None
    assert index["last_session_id"] == "sess1"


def test_detect_crashed_sessions_finds_still_active_meta(tmp_path):
    Journal(tmp_path, session_id="crashed-one")
    j2 = Journal(tmp_path, session_id="closed-one")
    j2.mark_closed("closed")

    crashed = detect_crashed_sessions(tmp_path)
    ids = {c["session_id"] for c in crashed}
    assert ids == {"crashed-one"}


def test_mark_session_crashed_flips_status(tmp_path):
    Journal(tmp_path, session_id="sess1")
    mark_session_crashed(tmp_path, "sess1")

    crashed = detect_crashed_sessions(tmp_path)
    assert crashed == []  # no longer "active" once flipped to "crashed"

    meta_path = tmp_path / journal_mod.SDLICIT_DIR / "sess1" / journal_mod.META_FILE
    assert json.loads(meta_path.read_text())["status"] == "crashed"


def test_read_index_missing_file_returns_empty_dict(tmp_path):
    assert read_index(tmp_path) == {}


def test_parse_usage_headers_reads_by_agent_json():
    headers = {
        "X-Sdlicit-Tokens-Prompt": "100",
        "X-Sdlicit-Tokens-Completion": "40",
        "X-Sdlicit-Tokens-Total": "140",
        "X-Sdlicit-Tokens-Calls": "2",
        "X-Sdlicit-Tokens-By-Agent": json.dumps({"sow": {"total_tokens": 140}}),
    }
    usage = journal_mod.parse_usage_headers(headers)
    assert usage["total_tokens"] == 140
    assert usage["by_agent"] == {"sow": {"total_tokens": 140}}


def test_parse_usage_headers_missing_headers_defaults_to_zero():
    usage = journal_mod.parse_usage_headers({})
    assert usage == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
        "by_agent": {},
    }


def test_summarize_truncates_long_strings_and_lists():
    long_str = "x" * (journal_mod._MAX_PAYLOAD_CHARS + 100)
    assert journal_mod._summarize(long_str).endswith("chars]")
    assert len(journal_mod._summarize(list(range(60)))) == 51  # 50 items + truncation marker
