#!/usr/bin/env bash
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
# The installer puts these scripts inside the memory root, so the script's own
# location IS the root. Without this export, an install at `--root /srv/mem`
# had every Python step fall back to ~/.claude. An explicit env still wins.
export AI_MEMORY_HOME="${AI_MEMORY_HOME:-$root}"
log="$AI_MEMORY_HOME/data/memory-system/maintenance.log"
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
rc=0
if maintain "$@" >>"$log" 2>&1; then
  alert_ok "MEMORY-MAINTENANCE ALERT" "daily maintenance completed"
else
  rc=1
  alert "MEMORY-MAINTENANCE ALERT" "daily maintenance failed - see $log"
fi

# Optional hook for a user's OWN backup job. The release does not ship one and the
# installer never creates it; it runs only if the user put an executable here.
if [ -x "$root/cron/backup-push.sh" ]; then "$root/cron/backup-push.sh" >>"$log" 2>&1 || true; fi
# The backup never masks a maintenance failure: cron/systemd see the real status.
exit "$rc"
