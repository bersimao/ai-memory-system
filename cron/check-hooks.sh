#!/usr/bin/env bash
# Daily tripwire: verify the memory hooks are still registered in settings.json.
# History: memory-inject.js was silently dropped from SessionStart once
# (re-registered 2026-05-22) — this turns that silent failure into a visible one.
# Called from distill.sh; safe to run standalone.
set -uo pipefail

SETTINGS="$HOME/.claude/settings.json"
# Optional local config (Obsidian INBOX path, etc). Absent = alerts go to the
# log only; every INBOX write below is already guarded by [ -f "$INBOX" ].
[ -f "$HOME/.claude/data/memory.env" ] && . "$HOME/.claude/data/memory.env"
INBOX="${CLAUDE_MEM_INBOX:-}"
LOG="$HOME/.memsearch/cron.log"

missing=""
for hook in memory-inject.js transcript-capture.js capture-maintenance.js daily-log-nudge.js; do
  grep -q "$hook" "$SETTINGS" 2>/dev/null || missing="$missing $hook"
done

[ -z "$missing" ] && exit 0

msg="⚠️ ALERTA memória-hooks: hook(s)$missing fora do settings.json — memória não será injetada/capturada. Reregistrar!"
echo "[$(date -Iseconds)] $msg" >>"$LOG"

# Surface where the user actually looks: the Obsidian INBOX (swept daily).
# Dedup guard — one open alert at a time, no daily spam.
if [ -f "$INBOX" ] && ! grep -qF "ALERTA memória-hooks:" "$INBOX"; then
  printf -- '- [ ] %s\n' "$msg" >>"$INBOX"
fi
exit 1
