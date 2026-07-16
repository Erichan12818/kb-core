#!/bin/sh
set -eu

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
