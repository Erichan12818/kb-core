#!/usr/bin/env bash
# End-to-end smoke test: bring the stack up, add a note, read it back.
#
# This is the QUICKSTART path a first-time user follows, run unattended. It
# exists because the failure that matters most for a self-hosted product is the
# one where the author's machine works and a fresh clone does not.
#
# The first run downloads the ~2.3GB embedding model, so INGEST_TIMEOUT is
# generous. Later runs reuse the hf_cache volume and finish in seconds.
#
# Usage:
#   bin/smoke.sh                    # build, test, leave the stack running
#   KEEP_STACK=0 bin/smoke.sh       # tear the stack down afterwards
#   API_PORT=8378 bin/smoke.sh      # avoid a port clash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

API_PORT="${API_PORT:-8377}"
API="http://127.0.0.1:${API_PORT}"
INGEST_TIMEOUT="${INGEST_TIMEOUT:-900}"
KEEP_STACK="${KEEP_STACK:-1}"
COMPOSE="${COMPOSE:-docker compose}"

pass() { printf '  ✅ %s\n' "$1"; }
fail() { printf '  ❌ %s\n' "$1"; exit 1; }
step() { printf '\n▶ %s\n' "$1"; }

cleanup() {
  if [ "$KEEP_STACK" = "0" ]; then
    step "Tearing down"
    $COMPOSE down -v >/dev/null 2>&1
  fi
}
trap cleanup EXIT

step "Preflight"
command -v docker >/dev/null 2>&1 || fail "docker not found"
docker info >/dev/null 2>&1 || fail "docker daemon not running (start Docker Desktop)"
pass "docker ready"

# A foreign listener on the API port makes compose fail in a way that is easy to
# miss, because the other services still come up regardless. Our own stack
# holding the port is fine — re-running the smoke test should just work.
if lsof -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -sf --max-time 5 "$API/health" >/dev/null 2>&1; then
    pass "port $API_PORT held by a running kb-core stack (will reuse)"
  else
    fail "port $API_PORT is used by something else — stop it or set API_PORT"
  fi
else
  pass "port $API_PORT free"
fi

[ -f config/kb_config.yaml ] || cp config/kb_config.example.yaml config/kb_config.yaml
pass "config present"

step "Starting stack"
$COMPOSE up -d --build >/dev/null 2>&1 || fail "compose up failed"
for _ in $(seq 1 60); do
  curl -sf --max-time 5 "$API/health" >/dev/null 2>&1 && break
  sleep 5
done
curl -sf --max-time 5 "$API/health" >/dev/null 2>&1 || fail "API never became reachable"
pass "API reachable at $API"

step "Adding a note"
MARKER="smoke-$(date +%s)"
ADD=$(curl -sf --max-time 60 -X POST "$API/add" \
  -H 'Content-Type: application/json' \
  -d "{\"content\":\"kb-core smoke note $MARKER: Qdrant keeps durable agent memory.\",\"category\":\"smoke\",\"title\":\"kb-core smoke\"}") \
  || fail "POST /add failed"
echo "$ADD" | grep -q '"path"' || fail "POST /add returned no path: $ADD"
pass "note written"

step "Waiting for ingest (first run downloads the embedding model)"
DEADLINE=$((SECONDS + INGEST_TIMEOUT))
FOUND=0
while [ $SECONDS -lt $DEADLINE ]; do
  BODY=$(curl -sf --max-time 300 -X POST "$API/recall" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"$MARKER durable agent memory\",\"top_k\":3}" 2>/dev/null)
  if echo "$BODY" | grep -q "$MARKER"; then FOUND=1; break; fi
  sleep 15
done
[ "$FOUND" = "1" ] || fail "note never became searchable within ${INGEST_TIMEOUT}s"
pass "note is searchable"

step "Checking surfaces"
# Capture before matching: piping curl into `grep -q` makes grep exit on the
# first hit, curl die of SIGPIPE, and pipefail report the whole thing as failed
# even though the request succeeded.
check_body() {
  local path="$1" needle="$2" label="$3" body
  body=$(curl -sf --max-time 10 "$API$path") || fail "$label: request failed"
  case "$body" in
    *"$needle"*) pass "$label" ;;
    *) fail "$label: response did not contain '$needle'" ;;
  esac
}

check_body /ui "kb-core" "GET /ui serves the UI"
check_body /taxonomy "categories" "GET /taxonomy responds"
check_body /proposals "proposals" "GET /proposals responds"

# ollama is optional (local-llm profile), so it is excluded from this gate.
HEALTH=$(curl -sf --max-time 20 "$API/health")
for check in nas qdrant index_fresh integrity; do
  echo "$HEALTH" | python3 -c "
import json,sys
d = json.load(sys.stdin)
sys.exit(0 if d.get('$check', {}).get('ok') else 1)
" || fail "health check '$check' is failing: $HEALTH"
done
pass "health checks green (ollama excluded — optional profile)"

printf '\n🎉 Smoke test passed.\n'
[ "$KEEP_STACK" = "1" ] && printf '   Stack left running. Open %s/ui or run: %s down -v\n' "$API" "$COMPOSE"
exit 0
