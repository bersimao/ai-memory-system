#!/usr/bin/env bash
# Compatibility scheduler entry point. Models return data; the writer journals changes.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$root/scripts/memory-maintain.py" backfill "$@"
