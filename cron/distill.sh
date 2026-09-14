#!/usr/bin/env bash
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
log="${AI_MEMORY_HOME:-$HOME/.claude}/data/memory-system/maintenance.log"
mkdir -p "$(dirname "$log")"
. "$root/cron/alert.sh"

maintain() {
  date -Iseconds
  bash "$root/cron/check-hooks.sh" || true
  bash "$root/cron/check-caps.sh" || true
  if [ -x "$root/cron/check-codex-gate.sh" ]; then "$root/cron/check-codex-gate.sh" || true; fi
  python3 "$root/cron/jsonl-to-transcript.py" || return 1
  python3 "$root/scripts/memory-maintain.py" backfill "$@" || return 1
  python3 "$root/scripts/memory-maintain.py" distill "$@" || return 1
  python3 "$root/scripts/memory-maintain.py" index || return 1
}

# A failure must be loud and must not cost the backup. Until 2026-09-14 every step
# ended the whole script with `exit 1`, so one extractor parse error skipped
# backup-push silently for two nights. cron/distill.test.sh holds both guarantees.
if maintain "$@" >>"$log" 2>&1; then
  alert_ok "MEMORY-MAINTENANCE ALERT" "daily maintenance completed"
else
  alert "MEMORY-MAINTENANCE ALERT" "daily maintenance failed - see $log"
fi

# Preserve the existing private backup job, without adding publication to the core.
# This runs only when the scheduled wrapper is invoked, never during installation.
if [ -x "$root/cron/backup-push.sh" ]; then "$root/cron/backup-push.sh" >>"$log" 2>&1 || true; fi
