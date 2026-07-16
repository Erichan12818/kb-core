#!/usr/bin/env bash
# SessionStart hook: print lightweight KB project context when kb-core is connected.
# Any failure must not block Claude Code startup.

set +e
INPUT="$(cat 2>/dev/null || true)"

if [ -z "${KB_HOME:-}" ]; then
  echo "KB 未接通：設定 KB_HOME=<kb-core 路徑> 或跑 kb-core 安裝"
  exit 0
fi

KB_SCRIPT="$KB_HOME/kb/session_context.py"
KB_PYTHON="${KB_MCP_PYTHON:-}"
KB_MODULE="kb.session_context"

if [ ! -f "$KB_SCRIPT" ]; then
  echo "KB 未接通：設定 KB_HOME=<kb-core 路徑> 或跑 kb-core 安裝"
  exit 0
fi

if [ -z "$KB_PYTHON" ] && [ -x "$KB_HOME/.venv/bin/python" ]; then
  KB_PYTHON="$KB_HOME/.venv/bin/python"
fi
if [ -z "$KB_PYTHON" ] && [ -x "$KB_HOME/venv/bin/python" ]; then
  KB_PYTHON="$KB_HOME/venv/bin/python"
fi
if [ -z "$KB_PYTHON" ]; then
  KB_PYTHON="$(command -v python3 2>/dev/null)"
fi

if [ -z "${KB_PYTHON:-}" ]; then
  echo "KB 未接通：設定 KB_HOME=<kb-core 路徑> 或跑 kb-core 安裝"
  exit 0
fi

cd "$KB_HOME" 2>/dev/null || exit 0
(
  "$KB_PYTHON" -m "$KB_MODULE" <<< "$INPUT" 2>/dev/null
) &
pid="$!"

i=0
while kill -0 "$pid" 2>/dev/null; do
  if [ "$i" -ge 18 ]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    exit 0
  fi
  sleep 0.05
  i=$((i + 1))
done

wait "$pid" 2>/dev/null || true
exit 0
