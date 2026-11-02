#!/usr/bin/env bash
# Compatibility scheduler entry point. Models return data; the writer journals changes.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(python3 "$root/scripts/memory-paths.py" --sh)" || exit 1  # memory_core.layout(): one rule for every entry point
python3 "$root/scripts/memory-maintain.py" backfill "$@"
