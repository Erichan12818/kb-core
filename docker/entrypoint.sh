#!/bin/sh
set -eu

# A bind-mounted vault starts as a bare directory, so lay out the skeleton the
# rest of the toolchain expects. Without this a first-run install reports its
# own storage as unhealthy and the first /add has nowhere to write.
KB_VAULT="${KB_ROOT:-/vault}"
mkdir -p "$KB_VAULT/raw_files" "$KB_VAULT/state" "$KB_VAULT/notes" \
         "$KB_VAULT/catalog" "$KB_VAULT/trash"

role="${1:-api}"
shift || true

case "$role" in
  api|serve)
    exec kb serve "$@"
    ;;
  worker)
    exec python -m kb.worker "$@"
    ;;
  oneshot)
    exec kb "$@"
    ;;
  *)
    exec kb "$role" "$@"
    ;;
esac
