#!/usr/bin/env bash
# Format, lint, and test — mirrors CI (ruff format, mypy, pylint, pytest).
# Used by the Cursor `stop` hook and by `.githooks/pre-commit`.
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$root"

# Cursor hooks send JSON on stdin; git hooks do not.
if [[ ! -t 0 ]]; then
  cat >/dev/null || true
fi

run() {
  echo "+ $*"
  "$@"
}

status=0
run uv run --with ruff ruff format app tests migrations || status=$?
run uv run --with mypy mypy app tests || status=$?
run uv run --with pylint pylint app tests migrations || status=$?
run uv run pytest -q || status=$?

if [[ "$status" -ne 0 ]]; then
  # stop hooks can surface a follow-up; git pre-commit ignores this JSON.
  printf '%s\n' "{\"followup_message\":\"Project checks failed (format/lint/tests). See terminal output.\"}"
  exit "$status"
fi

printf '%s\n' '{}'
exit 0
