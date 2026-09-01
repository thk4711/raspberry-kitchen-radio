#!/bin/sh
# Remove disposable developer artifacts only. Credentials, .venv and Buildroot
# output are deliberately preserved.
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
find "$ROOT" \
    -path "$ROOT/.git" -prune -o \
    -path "$ROOT/.venv" -prune -o \
    -path "$ROOT/buildroot/output" -prune -o \
    -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$ROOT/.pytest_cache" "$ROOT/.mypy_cache" "$ROOT/.ruff_cache"
rm -f "$ROOT/.coverage" "$ROOT"/.coverage.* "$ROOT/.DS_Store"
echo "Removed Python/test/OS artifacts; preserved .venv, credentials and build output."