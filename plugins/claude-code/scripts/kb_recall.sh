#!/usr/bin/env bash
set -eu

KB_HOME="${KB_HOME:?Set KB_HOME to the kb-core repository path}"
KB_PYTHON="${KB_MCP_PYTHON:-}"
if [ -z "$KB_PYTHON" ] && [ -x "$KB_HOME/.venv/bin/python" ]; then
  KB_PYTHON="$KB_HOME/.venv/bin/python"
fi
if [ -z "$KB_PYTHON" ] && [ -x "$KB_HOME/venv/bin/python" ]; then
  KB_PYTHON="$KB_HOME/venv/bin/python"
fi
if [ -z "$KB_PYTHON" ]; then
  KB_PYTHON="$(command -v python3)"
fi

cd "$KB_HOME"
exec "$KB_PYTHON" -m kb.recall "$@"
