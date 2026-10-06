#!/usr/bin/env bash
# afterFileEdit: format + lint edited Python under app/, tests/, migrations/.
set -uo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$root" || exit 0

file="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("file_path") or "")')"
[[ -n "$file" ]] || exit 0

case "$file" in
  /*) rel="${file#"$root"/}" ;;
  *) rel="$file" ;;
esac

[[ "$rel" == *.py ]] || exit 0
case "$rel" in
  app/* | tests/* | migrations/*) ;;
  *) exit 0 ;;
esac

[[ -f "$rel" ]] || exit 0

status=0
uv run --with ruff ruff format "$rel" || status=$?
uv run --with pylint pylint "$rel" || status=$?
case "$rel" in
  app/* | tests/*) uv run --with mypy mypy "$rel" || status=$? ;;
esac
exit "$status"
