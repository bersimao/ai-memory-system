#!/usr/bin/env bash
# Daily mechanical curation: over-cap project indexes move sections to their hubs (no model call).
# A whole-index archive opens INDEX-ARCHIVE ALERT from memory-maintain.py itself (distill can archive too).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(python3 "$root/scripts/memory-paths.py" --sh)" || exit 1  # memory_core.layout(): one rule for every entry point
# Another PC may have pushed memory since: rebase before curation rewrites indexes.
if [ -x "$root/cron/backup-push.sh" ]; then "$root/cron/backup-push.sh" --memory-pull || true; fi
python3 "$root/scripts/memory-maintain.py" curate "$@"
