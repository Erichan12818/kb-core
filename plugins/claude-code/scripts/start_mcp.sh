#!/usr/bin/env bash
# Start the packaged kb-core MCP server.
set -eu

KB_HOME="${KB_HOME:?Set KB_HOME to the kb-core repository path}"
KB_MCP_PYTHON="${KB_MCP_PYTHON:-}"

if [ -z "$KB_MCP_PYTHON" ] && [ -x "$KB_HOME/.venv/bin/python" ]; then
  KB_MCP_PYTHON="$KB_HOME/.venv/bin/python"
fi
if [ -z "$KB_MCP_PYTHON" ] && [ -x "$KB_HOME/venv/bin/python" ]; then
  KB_MCP_PYTHON="$KB_HOME/venv/bin/python"
fi
if [ -z "$KB_MCP_PYTHON" ]; then
  KB_MCP_PYTHON="$(command -v python3)"
fi

cd "$KB_HOME"
exec "$KB_MCP_PYTHON" -m kb.mcp
