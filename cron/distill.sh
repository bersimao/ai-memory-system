#!/usr/bin/env bash
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
log="${AI_MEMORY_HOME:-$HOME/.claude}/data/memory-system/maintenance.log"
mkdir -p "$(dirname "$log")"
{
  date -Iseconds
  bash "$root/cron/check-hooks.sh" || true
  bash "$root/cron/check-caps.sh" || true
  if [ -x "$root/cron/check-codex-gate.sh" ]; then "$root/cron/check-codex-gate.sh" || true; fi
  python3 "$root/cron/jsonl-to-transcript.py" || exit 1
  python3 "$root/scripts/memory-maintain.py" backfill "$@" || exit 1
  python3 "$root/scripts/memory-maintain.py" distill "$@" || exit 1
  python3 "$root/scripts/memory-maintain.py" index || exit 1
} >>"$log" 2>&1

# Preserve the existing private backup job, without adding publication to the core.
# This runs only when the scheduled wrapper is invoked, never during installation.
if [ -x "$root/cron/backup-push.sh" ]; then "$root/cron/backup-push.sh" >>"$log" 2>&1 || true; fi
