#!/usr/bin/env bash
# Daily tripwire: nudge when the memsearch usage log has enough REAL queries to
# be worth reviewing (`mem search`/`mem expand` write it; see scripts/mem).
#
# Why this exists: the review trigger was "~30 real searches", recorded only as a
# checkbox in the Obsidian note. Nothing counted, so nothing would ever fire and
# the log would sit unread. This counts, and speaks up once.
#
# Also logs the running count every day even when under threshold — if after
# weeks the count is still tiny, that IS the finding: L1 is barely used and the
# tier is over-built, not mistuned.
# Called from distill.sh; safe to run standalone. Never edits memory files.
set -uo pipefail

# Optional local config (Obsidian INBOX path, etc). Absent = alerts go to the
# log only; every INBOX write below is already guarded by [ -f "$INBOX" ].
[ -f "$HOME/.claude/data/memory.env" ] && . "$HOME/.claude/data/memory.env"
INBOX="${CLAUDE_MEM_INBOX:-}"
LOG="$HOME/.memsearch/cron.log"
USAGE="$HOME/.claude/data/memsearch-usage.jsonl"
# ponytail: 25 is a guess at "enough to see a score distribution", not a power
# calculation. Raise it if the first review is inconclusive.
THRESHOLD=25

[ -f "$USAGE" ] || exit 0

n=$(grep -c '"ev": "search"' "$USAGE" 2>/dev/null || echo 0)
echo "[$(date -Iseconds)] mem-review: ${n}/${THRESHOLD} searches logged" >>"$LOG"

[ "$n" -lt "$THRESHOLD" ] && exit 0

# Dedup guard — one open alert at a time. Marker substring is unique to this
# check (check-hooks.sh dedups on "MEMORY-HOOKS ALERT", check-caps.sh on
# "MEMORY-CAP ALERT"); do not reuse either.
msg="📊 MEMORY-RECALL REVIEW: ${n} real searches logged — run \`~/.claude/scripts/mem report\` and decide (a) the miss threshold, (b) sources never opened, (c) whether entry format should change."
if [ -f "$INBOX" ] && ! grep -qF "MEMORY-RECALL REVIEW:" "$INBOX"; then
  printf -- '- [ ] %s\n' "$msg" >>"$INBOX"
  echo "[$(date -Iseconds)] mem-review: alert raised" >>"$LOG"
fi
exit 0
