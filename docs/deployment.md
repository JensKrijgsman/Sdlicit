# Deployment

Three ways to run Sdlicit, from lightest to heaviest.

## 1. The Python package alone

The backend is a real, buildable Python package. Build it and install it
anywhere:

```bash
cd sdlicit-public
uv build              # writes dist/sdlicit-0.1.0.whl and a matching sdist
pip install dist/sdlicit-0.1.0-py3-none-any.whl
```

Or add it straight into another project without building a wheel first:

```bash
uv add /path/to/sdlicit-public
```

This installs one console script, `sdlicit-server`, and the `sdlicit`
package itself. At minimum you need an LLM provider key in the
environment, `OPENROUTER_API_KEY` for the default cloud provider. Then:

```bash
OPENROUTER_API_KEY=sk-... sdlicit-server
```

The server starts uninitialised on `127.0.0.1:8000` and waits for a
client (the CLI, the extension, or a plain `POST /api/v1/init`) to hand
it a project directory containing `.sdlicit/config.yaml`.

If a project's config sets `enable_rag: false`, nothing else is needed,
the backend runs standalone. If RAG is enabled, the backend still needs
nothing extra to run itself, LightRAG is a normal Python dependency
already bundled in the wheel, and it manages its own file based storage
under `<project_dir>/.sdlicit/knowledge/lightrag_workdir`. What you do
need, only if the project's config sets `provider: ollama` instead of
`openrouter`, is a running Ollama instance reachable at the configured
`ollama_host`. That is covered in the Docker section below.

A note on the wheel size and platform: this pulls in dspy, lightrag-hku,
scikit-learn, numpy and a few native extension packages (pymupdf,
tokenizers). All ship prebuilt wheels for the common platforms, so a
plain `pip install` does not need a compiler on a typical Linux, macOS
or Windows host. The one environment where this project has actually
hit native library loading trouble is NixOS outside of a nix shell,
where the system does not provide the FHS standard shared libraries
(`libstdc++.so.6` and similar) these wheels' native extensions expect.
That is a NixOS specific quirk in how it lays out the filesystem, not a
defect in the package, this repository works around it for local
development with `shell.nix` and `flake.nix` (see the getting started
page), but a plain `pip install` of the wheel outside a Nix environment
on NixOS may need the same kind of dynamic linker patching those files
already do.

## 2. Docker, backend only

```bash
cd sdlicit-public
docker build -t sdlicit-backend .
docker run -p 8000:8000 \
  -v /path/to/your/project:/data/project \
  -e OPENROUTER_API_KEY=sk-... \
  sdlicit-backend
```

The image runs as a fixed, non root user (uid 1000, the common default
first user id on Linux). If the directory you mount is owned by a
different host user, either `chown` it to match, or override the
container's user at run time:

```bash
docker run --user "$(id -u):$(id -g)" ...
```

Point a client (CLI or extension) at `http://localhost:8000` and call
`/api/v1/init` with `project_dir: "/data/project"`, the path inside the
container, not the host path.

## 3. Docker Compose, the full stack

`sdlicit-public/services/compose.yaml` defines four services. Only two
of them are wired to anything the backend actually uses:

| Service | What it is for | Needed when |
|---|---|---|
| `sdlicit-backend` | The Sdlicit API server, from the Dockerfile above | Always |
| `ollama` | Local LLM and embedding serving | Only if a project's `config.yaml` sets `provider: ollama` |
| `lightrag` | LightRAG's own standalone API server and web UI | Optional, only for browsing the knowledge graph visually |
| `sdlicit-db` | Postgres 16 with pgvector and the AGE graph extension | Not currently used by anything, see below |

The most important thing to understand here: `sdlicit-backend` does
**not** call the `lightrag` service over the network. The backend
embeds the LightRAG Python library directly and reads and writes its
own knowledge base as files, under `<project_dir>/.sdlicit/knowledge/
lightrag_workdir`. The `lightrag` service in this compose file is a
separate, independent standalone server that happens to be pointed at
that same directory (via `SDLICIT_PROJECT_DIR`), so you can open its web
UI to inspect the knowledge graph the backend has built, but the backend
works whether or not that container is running.

`sdlicit-db` builds a real Postgres image with pgvector and Apache AGE
compiled in, but nothing in the current backend code connects to it.
LightRAG here is configured for JSON file based storage
(`LIGHTRAG_KV_STORAGE=JsonKVStorage` and similar in the `lightrag`
service's environment), not the Postgres backed storage LightRAG also
supports. It is provisioned for that future possibility, not live
today. Because of that it is behind a Compose profile and is not
started by default:

```bash
docker compose -f services/compose.yaml up -d                    # backend, ollama, lightrag
docker compose -f services/compose.yaml --profile postgres up -d # also starts sdlicit-db
```

Point the backend at a real project directory and match your host user
id, so file permissions on the mounted volume work without extra setup:

```bash
export SDLICIT_PROJECT_DIR=/path/to/your/project
export UID=$(id -u)
export GID=$(id -g)
cp .env.example .env   # fill in OPENROUTER_API_KEY
docker compose -f services/compose.yaml up -d --build
curl http://localhost:8000/health
```

`docker compose down` stops everything, add `-v` only if you also want
to delete the named volumes (`sdlicit_pgdata`, `sdlicit_ollama`), your
mounted project directory itself is a bind mount and is never touched
by that flag.
