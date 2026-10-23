#!/usr/bin/env bash
# Daily mechanical curation: over-cap project indexes move sections to their hubs (no model call).
# A whole-index archive opens INDEX-ARCHIVE ALERT from memory-maintain.py itself (distill can archive too).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export AI_MEMORY_HOME="${AI_MEMORY_HOME:-$root}"  # same root rule as distill.sh
python3 "$root/scripts/memory-maintain.py" curate "$@"
