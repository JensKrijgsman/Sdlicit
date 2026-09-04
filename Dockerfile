# Sdlicit backend server image.
#
# Build (from sdlicit-public/):
#   docker build -t sdlicit-backend .
#
# Run standalone (needs a project directory mounted, and an LLM provider
# key in the environment):
#   docker run -p 8000:8000 -v /path/to/project:/data/project \
#     -e OPENROUTER_API_KEY=sk-... sdlicit-backend
#
# See services/compose.yaml for the full stack (backend, Ollama, LightRAG
# WebUI, Postgres) and docs/deployment.md for details.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer first so it is cached across source only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable

COPY src ./src
COPY README.md ./
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim

# Fixed at 1000:1000, the common default first-user id on Linux hosts, so a
# bind mounted project directory owned by that host user is writable without
# extra setup. A different host uid needs an explicit --user override at run
# time (see docs/deployment.md), the sdlicit user name itself is unused then.
RUN groupadd --gid 1000 sdlicit && useradd --uid 1000 --gid sdlicit --create-home sdlicit

WORKDIR /app
COPY --from=builder --chown=sdlicit:sdlicit /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

RUN mkdir -p /data/project && chown sdlicit:sdlicit /data/project

USER sdlicit

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "sdlicit.main:app", "--host", "0.0.0.0", "--port", "8000"]
