#!/usr/bin/env bash
set -euo pipefail

# Anjo Scaffold — First-time local dev setup
# Usage: ./setup.sh

echo "=== Anjo Scaffold Setup ==="
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────────────

check_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required but not found."
    echo "Install Python 3.11+ from https://python.org and try again."
    exit 1
  fi

  PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
  PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

  if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    echo "Error: Python 3.11+ is required. Found: $PYTHON_VERSION"
    echo "Install Python 3.11+ from https://python.org and try again."
    exit 1
  fi

  echo "Python $PYTHON_VERSION ... ok"
}

check_python

# ── Virtual environment ────────────────────────────────────────────────────────

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Virtual environment ... ok"

# ── Dependencies ───────────────────────────────────────────────────────────────

echo "Installing dependencies (pip install -e \".[test]\")..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[test]"
echo "Dependencies installed ... ok"
echo "Note: on first run, sentence-transformers will download a ~90MB embedding model."

# ── Environment files ──────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "  Created .env from .env.example"
  echo "  >> Edit .env and set ANTHROPIC_API_KEY, ANJO_SECRET, and ANJO_ADMIN_SECRET"
else
  echo ".env already exists ... skipping"
fi

if [ ! -f "mobile/.env.local" ]; then
  cp mobile/.env.example mobile/.env.local
  echo "  Created mobile/.env.local from mobile/.env.example"
  echo "  >> Edit mobile/.env.local if your backend runs on a non-default URL"
else
  echo "mobile/.env.local already exists ... skipping"
fi

# ── Done ───────────────────────────────────────────────────────────────────────

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit .env — set at minimum:"
echo "       ANTHROPIC_API_KEY=sk-ant-..."
echo "       ANJO_SECRET=\$(openssl rand -hex 32)"
echo "       ANJO_ADMIN_SECRET=\$(openssl rand -hex 24)"
echo ""
echo "  2. Implement the two scaffold stubs:"
echo "       anjo/core/prompt_builder.py   — your companion's voice"
echo "       anjo/reflection/engine.py     — how your companion learns"
echo ""
echo "  3. Start the backend:"
echo "       source .venv/bin/activate"
echo "       ANJO_ENV=dev uvicorn anjo.dashboard.app:app --reload --port 8000"
echo ""
echo "  4. Open http://localhost:8000"
echo ""
echo "  5. Run tests:"
echo "       pytest"
echo ""
echo "  Using Claude Code? CLAUDE.md has the full architecture context."
echo "       claude"
echo ""
