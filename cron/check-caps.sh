#!/usr/bin/env bash
# Daily tripwire: verify the memory-layer character caps are actually respected.
#
# History: the caps existed ONLY as prompt text inside distill.sh and curate.sh,
# so they bound the two cron jobs and nothing else. An interactive session wrote
# ~7.6k chars straight into a project store's context/MEMORY.md on 2026-07-27
# taking it to 4x its 2500 cap; distill nibbled 168 chars off
# it over two runs and curate only reruns weekly. This turns that silent drift
# into a visible alert.
#
# Measures CHARACTERS, not bytes — pt-BR accents make byte counts ~2% higher and
# would produce false alarms near the limit.
# Called from distill.sh; safe to run standalone. Never edits memory files.
set -uo pipefail

# Optional local config (Obsidian INBOX path, etc). Absent = alerts go to the
# log only; every INBOX write below is already guarded by [ -f "$INBOX" ].
[ -f "$HOME/.claude/data/memory.env" ] && . "$HOME/.claude/data/memory.env"
INBOX="${CLAUDE_MEM_INBOX:-}"
LOG="$HOME/.memsearch/cron.log"

# chars <file> -> character count (UTF-8 aware), 0 if unreadable
chars() { python3 -c "import sys;print(len(open(sys.argv[1],encoding='utf-8',errors='replace').read()))" "$1" 2>/dev/null || echo 0; }

over=""
check() {  # check <file> <cap> <label>
  local file="$1" cap="$2" label="$3" n
  [ -f "$file" ] || return 0
  n=$(chars "$file")
  [ "$n" -le "$cap" ] && return 0
  over="${over}  ${label}: ${n}/${cap}"$'\n'
}

check "$HOME/.claude/context/USER.md"   1375 "USER.md"
check "$HOME/.claude/context/MEMORY.md" 4000 "global MEMORY.md"

shopt -s nullglob
for f in "$HOME"/.claude/projects/*/context/MEMORY.md; do
  check "$f" 2500 "$(basename "$(dirname "$(dirname "$f")")")"
done

[ -z "$over" ] && exit 0

echo "[$(date -Iseconds)] cap violations:" >>"$LOG"
printf '%s' "$over" >>"$LOG"

# Surface where the user actually looks: the Obsidian INBOX (swept daily).
# Dedup guard — one open alert at a time, no daily spam.
#
# The marker must NOT contain "MEMORY-HOOKS ALERT": check-hooks.sh dedups on that
# substring, so sharing it would let a cap alert suppress the (more severe)
# hooks-missing alert.
count=$(printf '%s' "$over" | grep -c .)
msg="⚠️ MEMORY-CAP ALERT: ${count} file(s) over cap — curate alone will not fix this; consolidate or split into topic pages. Details in ~/.memsearch/cron.log"
if [ -f "$INBOX" ] && ! grep -qF "MEMORY-CAP ALERT:" "$INBOX"; then
  printf -- '- [ ] %s\n' "$msg" >>"$INBOX"
fi
exit 1
