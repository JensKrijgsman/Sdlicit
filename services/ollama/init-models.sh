#!/usr/bin/env bash
# -----------------------------------------------------------------------
# Ollama model provisioning script
#
# Usage (after `docker compose up -d ollama`):
#   docker compose -f services/compose.yaml exec ollama /models/init-models.sh
#
# Or run from host against localhost:11434:
#   OLLAMA_HOST=http://localhost:11434 bash services/ollama/init-models.sh
# -----------------------------------------------------------------------
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "==> Waiting for Ollama to be ready…"
for i in $(seq 1 30); do
    if ollama list > /dev/null 2>&1; then
        echo "    Ollama is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "    ERROR: Ollama did not start within 30 s" >&2
        exit 1
    fi
    sleep 1
done

# 1) Pull base models
echo "==> Pulling nomic-embed-text (embedding model)…"
ollama pull nomic-embed-text

echo "==> Pulling qwen3-embedding:8b (3072-dim MRL embedding model)…"
ollama pull qwen3-embedding:8b

echo "==> Pulling qwen3.5:4b (base LLM)…"
ollama pull qwen3.5:4b

echo "==> Pulling qwen3.5:0.8b (small baseline for evaluation)…"
ollama pull qwen3.5:0.8b

echo "==> Pulling LiquidAI/lfm2.5-1.2b-instruct (hybrid architecture baseline)…"
ollama pull LiquidAI/lfm2.5-1.2b-instruct

# 2) Create the Sdlicit-tuned variants from Modelfiles
MODELFILE="/models/qwen3.5-4b-sdlicit.Modelfile"
if [ -f "$MODELFILE" ]; then
    echo "==> Creating qwen3.5:4b-sdlicit from Modelfile…"
    ollama create qwen3.5:4b-sdlicit -f "$MODELFILE"
else
    echo "    WARN: $MODELFILE not found — skipping custom model creation"
fi

MODELFILE="/models/qwen3.5-0.8b-sdlicit.Modelfile"
if [ -f "$MODELFILE" ]; then
    echo "==> Creating qwen3.5:0.8b-sdlicit from Modelfile…"
    ollama create qwen3.5:0.8b-sdlicit -f "$MODELFILE"
else
    echo "    WARN: $MODELFILE not found — skipping custom model creation"
fi

MODELFILE="/models/lfm2.5-1.2b-sdlicit.Modelfile"
if [ -f "$MODELFILE" ]; then
    echo "==> Creating lfm2.5:1.2b-sdlicit from Modelfile…"
    ollama create lfm2.5:1.2b-sdlicit -f "$MODELFILE"
else
    echo "    WARN: $MODELFILE not found — skipping custom model creation"
fi

echo "==> Models ready:"
ollama list
