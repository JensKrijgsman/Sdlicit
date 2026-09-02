"""Thin HTTP client that talks to the Sdlicit FastAPI backend.

All HTTP traffic flows through three internal helpers — :meth:`_post`,
:meth:`_get` and :meth:`_stream` — so a single :class:`Journal` instance
attached via :meth:`attach_journal` records every request, response and
token usage automatically.  Stage modules call the regular methods
(``create_sow``, ``query_rag``, …) and journaling is transparent.

Stage modules can still emit stage-level events through
``client.journal.note(...)`` for breadcrumbs that are not HTTP calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"


# LLM round-trips (DSPy + BAML extraction) routinely take 15-40 s.
_TIMEOUT = httpx.Timeout(timeout=120.0)


class SdlicitClient:
    """Synchronous client for the Sdlicit REST API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self._base = base_url.rstrip("/")
        # Set via attach_journal() once the CLI has bootstrapped a session.
        self.journal: Any = None
        # Stored after init_project(); used as default when endpoints need it.
        self._project_dir: str = ""

    # -- Journal wiring -------------------------------------------------------

    def attach_journal(self, journal: Any) -> None:
        """Bind a :class:`cli.shared.journal.Journal` for auto-recording."""
        self.journal = journal

    @property
    def server_url(self) -> str:
        """Root server URL (without /api/v1)."""
        return self._base.rsplit("/api", 1)[0]

    @property
    def project_dir(self) -> str:
        """Project directory registered with the backend (set after init)."""
        return self._project_dir

    @project_dir.setter
    def project_dir(self, value: str) -> None:
        self._project_dir = value

    # -- Internal HTTP helpers ------------------------------------------------

    def _begin(
        self, endpoint: str, method: str, payload: Any = None
    ) -> dict[str, Any] | None:
        if self.journal is None:
            return None
        return self.journal.record_request(endpoint, method, payload)

    def _record_response(
        self,
        ctx: dict[str, Any] | None,
        resp: httpx.Response,
        body: Any,
    ) -> None:
        if self.journal is None or ctx is None:
            return
        from shared.journal import parse_usage_headers

        self.journal.record_response(
            ctx,
            status_code=resp.status_code,
            response=body,
            usage=parse_usage_headers(resp.headers),
        )

    def _record_error(
        self,
        ctx: dict[str, Any] | None,
        exc: Exception,
        status_code: int = 0,
    ) -> None:
        if self.journal is None or ctx is None:
            return
        self.journal.record_response(
            ctx,
            status_code=status_code,
            error=f"{type(exc).__name__}: {exc}",
        )

    def _post(
        self,
        path: str,
        *,
        json: Any = None,
        timeout: httpx.Timeout | float | None = _TIMEOUT,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        ctx = self._begin(endpoint or path.lstrip("/"), "POST", json)
        try:
            resp = httpx.post(url, json=json, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            self._record_error(
                ctx,
                exc,
                status_code=getattr(getattr(exc, "response", None), "status_code", 0)
                or 0,
            )
            raise
        self._record_response(ctx, resp, body)
        return body

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        payload = {"params": params} if params else None
        ctx = self._begin(endpoint or path.lstrip("/"), "GET", payload)
        try:
            resp = httpx.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            self._record_error(
                ctx,
                exc,
                status_code=getattr(getattr(exc, "response", None), "status_code", 0)
                or 0,
            )
            raise
        self._record_response(ctx, resp, body)
        return body

    # -- health / config -------------------------------------------------------

    def _resolve_project_dir(self, project_dir: str = "") -> str:
        """Return *project_dir* if given, else fall back to stored default."""
        return project_dir or self._project_dir

    def health(self) -> bool:
        """GET /health — returns True if the server is reachable."""
        try:
            resp = httpx.get(f"{self.server_url}/health", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def init_project(self, project_dir: str) -> dict[str, Any]:
        """POST /init — send the project root so the backend bootstraps."""
        result = self._post("/init", json={"project_dir": project_dir}, endpoint="init")
        self._project_dir = project_dir
        return result

    def get_config(self) -> dict[str, Any]:
        """GET /config — non-secret server configuration."""
        return self._get("/config", endpoint="config")

    # -- session lifecycle -----------------------------------------------------

    def start_session(self, stage: str = "unknown") -> str | None:
        """POST /session/start — begin a ToM session."""
        body = self._post(
            "/session/start",
            json={"stage": stage},
            timeout=10.0,
            endpoint="session-start",
        )
        return body.get("session_id")

    def end_session(self) -> dict[str, Any]:
        """POST /session/end — end the current ToM session."""
        return self._post("/session/end", endpoint="session-end")

    def compact_session(self) -> dict[str, Any]:
        """POST /session/compact — mid-session compaction."""
        return self._post("/session/compact", endpoint="session-compact")

    def save_preference(self, key: str, value: str, note: str = "") -> dict[str, Any]:
        """POST /preference — capture an explicit user preference."""
        return self._post(
            "/preference",
            json={"key": key, "value": value, "note": note},
            timeout=30.0,
            endpoint="preference",
        )

    # -- intake stage ----------------------------------------------------------

    def create_sow(
        self,
        raw_brief: str,
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /intake/sow — generate SOW from a raw brief."""
        return self._post(
            "/intake/sow",
            json={"raw_brief": raw_brief, "clarifications": clarifications or []},
            endpoint="intake-sow",
        )

    @contextmanager
    def create_sow_stream(
        self,
        raw_brief: str,
        clarifications: list[dict[str, Any]] | None = None,
    ) -> Iterator[Iterator[dict[str, Any]]]:
        """POST /intake/sow/stream — SSE stream of section-by-section SOW generation.

        Usage::

            with client.create_sow_stream(brief) as events:
                for event in events:
                    ...

        Yields parsed JSON event dicts.
        """
        import json as _json

        url = f"{self._base}/intake/sow/stream"
        payload = {"raw_brief": raw_brief, "clarifications": clarifications or []}
        ctx = self._begin("intake-sow-stream", "POST", payload)

        def _iter_events(resp: httpx.Response) -> Iterator[dict[str, Any]]:
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    with suppress(_json.JSONDecodeError):
                        yield _json.loads(line[6:])

        try:
            with httpx.stream(
                "POST",
                url,
                json=payload,
                timeout=httpx.Timeout(timeout=300.0, connect=10.0),
            ) as resp:
                resp.raise_for_status()
                yield _iter_events(resp)
                self._record_response(ctx, resp, {"streamed": True})
        except Exception as exc:
            self._record_error(ctx, exc)
            raise

    # -- composing stage -------------------------------------------------------

    def step_event(
        self,
        step_name: str,
        step_value: Any,
        partial_fields: dict[str, Any],
        project_dir: str = "",
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /composing/step — send a single ADR creation step to the agent."""
        return self._post(
            "/composing/step",
            json={
                "step_name": step_name,
                "step_value": step_value,
                "partial_fields": partial_fields,
                "project_dir": project_dir,
                "clarifications": clarifications or [],
            },
            endpoint="composing-step",
        )

    def analyse_input(
        self, user_input: str, context_files: list[str]
    ) -> dict[str, Any]:
        """POST /composing/analyse — full-sweep analysis."""
        return self._post(
            "/composing/analyse",
            json={"user_input": user_input, "context_files": context_files},
            endpoint="composing-analyse",
        )

    def suggest_adr_directions(
        self,
        brief: str,
        project_dir: str = "",
        downstream_artifacts: str = "",
    ) -> dict[str, Any]:
        """POST /composing/adr/suggest-directions — ranked list of ADR topics."""
        return self._post(
            "/composing/adr/suggest-directions",
            json={
                "brief": brief,
                "project_dir": project_dir,
                "downstream_artifacts": downstream_artifacts,
            },
            endpoint="composing-adr-suggest",
        )

    # -- expansion stage -------------------------------------------------------

    def expand_adr(
        self,
        adr_filename: str,
        project_dir: str,
        session_id: str = "",
    ) -> dict[str, Any]:
        """POST /expansion/expand — multi-agent review of a completed ADR."""
        return self._post(
            "/expansion/expand",
            json={
                "adr_filename": adr_filename,
                "project_dir": project_dir,
                "session_id": session_id,
            },
            endpoint="expansion-expand",
        )

    def query_kb(self, query: str, mode: str = "hybrid") -> dict[str, Any]:
        """POST /expansion/query-kb — query the knowledge base."""
        return self._post(
            "/expansion/query-kb",
            json={"query": query, "mode": mode},
            endpoint="expansion-query-kb",
        )

    def query_rag(
        self,
        query: str,
        store: str = "all",
        mode: str = "hybrid",
        probe_first: bool = False,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """POST /expansion/query-rag — store-aware RAG query."""
        return self._post(
            "/expansion/query-rag",
            json={
                "query": query,
                "store": store,
                "mode": mode,
                "probe_first": probe_first,
                "top_k": top_k,
            },
            endpoint="expansion-query-rag",
        )

    @contextmanager
    def ingest_kb(
        self,
        project_dir: str,
        selected_files: list[str] | None = None,
    ) -> Iterator[httpx.Response]:
        """POST /expansion/ingest-kb — stream ingestion progress via SSE.

        Streaming responses are journaled with a metadata-only entry
        (status code + completion time); the per-line SSE payload is
        consumed by the caller and not persisted to the journal.
        """
        payload: dict[str, Any] = {"project_dir": project_dir}
        if selected_files is not None:
            payload["selected_files"] = selected_files
        ctx = self._begin("expansion-ingest-kb", "POST", payload)
        try:
            with httpx.stream(
                "POST",
                f"{self._base}/expansion/ingest-kb",
                json=payload,
                timeout=httpx.Timeout(timeout=600.0, connect=10.0),
            ) as resp:
                resp.raise_for_status()
                yield resp
                # Best-effort: journal stream completion (no body, headers only).
                self._record_response(ctx, resp, {"streamed": True})
        except Exception as exc:
            self._record_error(ctx, exc)
            raise

    def scan_documents(self, project_dir: str) -> dict[str, Any]:
        """GET /expansion/scan-documents — list ingestible files."""
        return self._get(
            "/expansion/scan-documents",
            params={"project_dir": project_dir},
            timeout=30.0,
            endpoint="expansion-scan-documents",
        )

    def ingest_artifact(
        self,
        text: str,
        artifact_type: str,
        name: str,
        replace: bool = False,
    ) -> dict[str, Any]:
        """POST /expansion/ingest-artifact — auto-ingest a produced artifact.

        Best-effort: failures are swallowed so artifact creation is not
        blocked by KB issues.  Returns ``{}`` on failure.
        """
        try:
            return self._post(
                "/expansion/ingest-artifact",
                json={
                    "text": text,
                    "artifact_type": artifact_type,
                    "name": name,
                    "replace": replace,
                },
                timeout=httpx.Timeout(timeout=600.0, connect=10.0),
                endpoint="expansion-ingest-artifact",
            )
        except Exception:
            return {}

    def supersede_adr(
        self,
        old_adr_id: str,
        new_adr_id: str,
        new_text: str,
    ) -> dict[str, Any]:
        """POST /expansion/supersede-adr — remove old ADR chunks, add new ones."""
        try:
            return self._post(
                "/expansion/supersede-adr",
                json={
                    "old_adr_id": old_adr_id,
                    "new_adr_id": new_adr_id,
                    "new_text": new_text,
                },
                timeout=httpx.Timeout(timeout=600.0, connect=10.0),
                endpoint="expansion-supersede-adr",
            )
        except Exception:
            return {}

    def kb_status(self, project_dir: str = "") -> dict[str, Any]:
        """GET /expansion/kb-status — knowledge base status."""
        params = {"project_dir": project_dir} if project_dir else None
        return self._get(
            "/expansion/kb-status",
            params=params,
            timeout=10.0,
            endpoint="expansion-kb-status",
        )

    # -- generation stage ------------------------------------------------------

    def generate_personas(
        self,
        project_dir: str,
        srs_content: str = "",
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /generation/personas — generate user personas."""
        return self._post(
            "/generation/personas",
            json={
                "project_dir": project_dir,
                "srs_content": srs_content,
                "clarifications": clarifications or [],
            },
            endpoint="generation-personas",
        )

    def generate_gherkin(
        self,
        project_dir: str,
        personas: list[dict[str, Any]],
        requirements: str,
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /generation/gherkin — generate Gherkin scenarios."""
        return self._post(
            "/generation/gherkin",
            json={
                "project_dir": project_dir,
                "personas": personas,
                "requirements": requirements,
                "clarifications": clarifications or [],
            },
            endpoint="generation-gherkin",
        )

    def generate_stories(
        self,
        project_dir: str,
        personas: str,
        requirements: str,
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /generation/stories — generate user stories."""
        return self._post(
            "/generation/stories",
            json={
                "project_dir": project_dir,
                "personas": personas,
                "requirements": requirements,
                "clarifications": clarifications or [],
            },
            endpoint="generation-stories",
        )

    def generate_srs(
        self,
        project_dir: str,
        sow_content: str = "",
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /generation/srs — generate a structured SRS from a SOW."""
        return self._post(
            "/generation/srs",
            json={
                "project_dir": project_dir,
                "sow_content": sow_content,
                "clarifications": clarifications or [],
            },
            endpoint="generation-srs",
        )

    # -- cross-cutting Socratic ------------------------------------------------

    def consult_socratic(
        self,
        originating_agent: str,
        what_was_asked: str,
        what_is_known: str,
        suspect_output: str = "",
        issue: str = "ambiguous_input",
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /socratic/consult — request a free-form Socratic probe."""
        return self._post(
            "/socratic/consult",
            json={
                "originating_agent": originating_agent,
                "what_was_asked": what_was_asked,
                "what_is_known": what_is_known,
                "suspect_output": suspect_output,
                "issue": issue,
                "clarifications": clarifications or [],
            },
            endpoint="socratic-consult",
        )

    # -- artifact store (canonical backend save/load) --------------------------

    def save_artifact(
        self,
        artifact_type: str,
        data: dict[str, Any],
        project_dir: str = "",
        render_markdown: bool = True,
    ) -> dict[str, Any]:
        """POST /artifacts/save — save structured artifact via the backend."""
        return self._post(
            "/artifacts/save",
            json={
                "artifact_type": artifact_type,
                "data": data,
                "project_dir": self._resolve_project_dir(project_dir),
                "render_markdown": render_markdown,
            },
            endpoint="artifacts-save",
        )

    def load_artifact(
        self,
        artifact_type: str,
        project_dir: str = "",
        filename: str | None = None,
    ) -> dict[str, Any]:
        """GET /artifacts/{type} — load artifact(s) by type."""
        params: dict[str, Any] = {}
        pd = self._resolve_project_dir(project_dir)
        if pd:
            params["project_dir"] = pd
        if filename:
            params["filename"] = filename
        return self._get(
            f"/artifacts/{artifact_type}",
            params=params or None,
            timeout=30.0,
            endpoint=f"artifacts-load-{artifact_type}",
        )

    def load_all_artifacts(
        self,
        artifact_type: str,
        project_dir: str = "",
    ) -> dict[str, Any]:
        """GET /artifacts/{type}/all — load all artifacts of a type."""
        params: dict[str, Any] = {}
        pd = self._resolve_project_dir(project_dir)
        if pd:
            params["project_dir"] = pd
        return self._get(
            f"/artifacts/{artifact_type}/all",
            params=params or None,
            timeout=30.0,
            endpoint=f"artifacts-load-all-{artifact_type}",
        )

    def render_artifact_markdown(
        self,
        artifact_type: str,
        project_dir: str = "",
        filename: str | None = None,
    ) -> dict[str, Any]:
        """GET /artifacts/{type}/markdown — render as markdown."""
        params: dict[str, Any] = {}
        pd = self._resolve_project_dir(project_dir)
        if pd:
            params["project_dir"] = pd
        if filename:
            params["filename"] = filename
        return self._get(
            f"/artifacts/{artifact_type}/markdown",
            params=params or None,
            timeout=30.0,
            endpoint=f"artifacts-markdown-{artifact_type}",
        )

    def list_artifacts(self, project_dir: str = "") -> dict[str, Any]:
        """GET /artifacts/list — list all artifacts in the workspace."""
        params: dict[str, Any] = {}
        pd = self._resolve_project_dir(project_dir)
        if pd:
            params["project_dir"] = pd
        return self._get(
            "/artifacts/list",
            params=params or None,
            timeout=30.0,
            endpoint="artifacts-list",
        )

    # -- traceability ----------------------------------------------------------

    def get_traceability_graph(self) -> dict[str, Any]:
        """GET /expansion/traceability-graph — full DAG for graph view."""
        return self._get(
            "/expansion/traceability-graph",
            timeout=30.0,
            endpoint="expansion-traceability-graph",
        )

    def check_traceability(
        self,
        artifact_id: str,
        artifact_content: str = "",
        project_dir: str = "",
    ) -> dict[str, Any]:
        """POST /expansion/check-traceability — validate artifact links."""
        return self._post(
            "/expansion/check-traceability",
            json={
                "artifact_id": artifact_id,
                "artifact_content": artifact_content,
                "project_dir": self._resolve_project_dir(project_dir),
            },
            endpoint="expansion-check-traceability",
        )

    def get_trace_coverage(self, mode: str = "") -> dict[str, Any]:
        """POST /expansion/trace-coverage — coverage metrics."""
        return self._post(
            "/expansion/trace-coverage",
            json={"mode": mode},
            endpoint="expansion-trace-coverage",
        )

    # -- KB management ---------------------------------------------------------

    def delete_from_kb(
        self,
        artifact_type: str,
        name: str,
    ) -> dict[str, Any]:
        """POST /expansion/delete-artifact — remove artifact chunks from KB."""
        return self._post(
            "/expansion/delete-artifact",
            json={"artifact_type": artifact_type, "name": name},
            timeout=httpx.Timeout(timeout=600.0, connect=10.0),
            endpoint="expansion-delete-artifact",
        )

    def get_artifact_kb_status(self) -> dict[str, Any]:
        """GET /expansion/artifact-kb-status — ingestion status of all artifacts."""
        return self._get(
            "/expansion/artifact-kb-status",
            timeout=30.0,
            endpoint="expansion-artifact-kb-status",
        )

    # -- locate chunk ----------------------------------------------------------

    def locate_chunk(
        self,
        source_ref: str,
        snippet: str = "",
        project_dir: str = "",
    ) -> dict[str, Any]:
        """POST /expansion/locate-chunk — find source location of a KB chunk."""
        return self._post(
            "/expansion/locate-chunk",
            json={
                "source_ref": source_ref,
                "snippet": snippet,
                "project_dir": self._resolve_project_dir(project_dir),
            },
            endpoint="expansion-locate-chunk",
        )

    # -- SOW regenerate section ------------------------------------------------

    def regenerate_sow_section(
        self,
        raw_brief: str,
        section_name: str,
        prior_sections: str = "",
        user_feedback: str = "",
        current_content: str = "",
        clarifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /intake/sow/regenerate-section — regenerate a single SOW section."""
        return self._post(
            "/intake/sow/regenerate-section",
            json={
                "raw_brief": raw_brief,
                "section_name": section_name,
                "prior_sections": prior_sections,
                "user_feedback": user_feedback,
                "current_content": current_content,
                "clarifications": clarifications or [],
            },
            endpoint="intake-sow-regenerate-section",
        )

    # -- validate gherkin ------------------------------------------------------

    def validate_gherkin(self, gherkin_text: str) -> dict[str, Any]:
        """POST /generation/validate-gherkin — syntax validation only."""
        return self._post(
            "/generation/validate-gherkin",
            json={"gherkin_text": gherkin_text},
            endpoint="generation-validate-gherkin",
        )
