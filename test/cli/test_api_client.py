"""Tests for the CLI's HTTP client (cli/api_client.py).

Mocks the module level httpx.post/httpx.get calls with canned
httpx.Response objects — no real network traffic, no live server.
"""

from __future__ import annotations

import httpx
import pytest
from api_client import DEFAULT_BASE_URL, SdlicitClient


def _response(status_code: int, json_body: dict, url: str = "http://x/y") -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", url),
    )


class _FakeJournal:
    def __init__(self) -> None:
        self.requests: list[tuple] = []
        self.responses: list[tuple] = []
        self.errors: list[tuple] = []

    def record_request(self, endpoint, method, payload):
        ctx = {"endpoint": endpoint, "method": method}
        self.requests.append((endpoint, method, payload))
        return ctx

    def record_response(self, ctx, *, status_code, response=None, usage=None, error=None):
        if error:
            self.errors.append((ctx["endpoint"], status_code, error))
        else:
            self.responses.append((ctx["endpoint"], status_code, response))


def test_post_returns_parsed_json_body(monkeypatch):
    client = SdlicitClient()
    monkeypatch.setattr(
        "api_client.httpx.post",
        lambda url, json=None, timeout=None: _response(200, {"ok": True}),
    )
    body = client._post("/init", json={"project_dir": "x"}, endpoint="init")
    assert body == {"ok": True}


def test_post_raises_and_records_error_on_http_failure(monkeypatch):
    client = SdlicitClient()
    journal = _FakeJournal()
    client.attach_journal(journal)

    def _raise(url, json=None, timeout=None):
        resp = _response(500, {"detail": "boom"})
        raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)

    monkeypatch.setattr("api_client.httpx.post", _raise)

    with pytest.raises(httpx.HTTPStatusError):
        client._post("/init", json={}, endpoint="init")

    assert journal.errors, "error should be journaled before re-raising"
    endpoint, status_code, error = journal.errors[0]
    assert endpoint == "init"
    assert status_code == 500
    assert "boom" not in error  # journaled error is type+message of the exception, not the body


def test_post_journals_successful_round_trip(monkeypatch):
    client = SdlicitClient()
    journal = _FakeJournal()
    client.attach_journal(journal)
    monkeypatch.setattr(
        "api_client.httpx.post",
        lambda url, json=None, timeout=None: _response(200, {"result": "ok"}),
    )

    client._post("/init", json={"project_dir": "x"}, endpoint="init")

    assert journal.requests == [("init", "POST", {"project_dir": "x"})]
    assert journal.responses == [("init", 200, {"result": "ok"})]


def test_get_returns_parsed_json_body(monkeypatch):
    client = SdlicitClient()
    monkeypatch.setattr(
        "api_client.httpx.get",
        lambda url, params=None, timeout=None: _response(200, {"items": [1, 2]}),
    )
    body = client._get("/expansion/kb/manifest", endpoint="kb-manifest")
    assert body == {"items": [1, 2]}


def test_health_true_on_200(monkeypatch):
    client = SdlicitClient()
    monkeypatch.setattr(
        "api_client.httpx.get",
        lambda url, timeout=None: _response(200, {}),
    )
    assert client.health() is True


def test_health_false_on_connection_error(monkeypatch):
    client = SdlicitClient()

    def _raise(url, timeout=None):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr("api_client.httpx.get", _raise)
    assert client.health() is False


def test_server_url_strips_api_v1_suffix():
    client = SdlicitClient(base_url="http://127.0.0.1:9000/api/v1")
    assert client.server_url == "http://127.0.0.1:9000"


def test_default_base_url_used_when_unset():
    client = SdlicitClient()
    assert client._base == DEFAULT_BASE_URL.rstrip("/")


def test_project_dir_round_trips_through_setter():
    client = SdlicitClient()
    assert client.project_dir == ""
    client.project_dir = "/tmp/some-project"
    assert client.project_dir == "/tmp/some-project"
