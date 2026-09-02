#!/usr/bin/env bash
# Sdlicit — Full Rebuild Script
# Cleans stale build artifacts and rebuilds everything from scratch.
# Usage: ./rebuild.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== Sdlicit Full Rebuild ==="

# 1. Extension — clean & compile
echo ""
echo "[1/3] Cleaning extension build artifacts…"
rm -rf ./out
echo "      Removed out/"

echo "[2/3] Compiling TypeScript extension…"
npm run compile
cd "$ROOT"
echo "      Extension compiled OK"

# 3. Python — verify importability
echo "[3/3] Verifying Python backend…"
../.venv/bin/python -c "import sdlicit; print('      Backend import OK')"

echo ""
echo "=== Rebuild complete ==="
echo "Next steps:"
echo "  • Reload VS Code window (Ctrl+Shift+P → 'Developer: Reload Window')"
echo "  • Restart backend:  uv run uvicorn sdlicit.main:app --reload"
