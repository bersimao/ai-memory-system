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
# check (check-hooks.sh dedups on "ALERTA memória-hooks", check-caps.sh on
# "ALERTA memória-cap"); do not reuse either.
msg="📊 REVISAR memória-recall: ${n} buscas reais registradas — rodar \`~/.claude/scripts/mem report\` e decidir (a) limiar de miss, (b) fontes nunca abertas, (c) mudar formato das entradas."
if [ -f "$INBOX" ] && ! grep -qF "REVISAR memória-recall:" "$INBOX"; then
  printf -- '- [ ] %s\n' "$msg" >>"$INBOX"
  echo "[$(date -Iseconds)] mem-review: alert raised" >>"$LOG"
fi
exit 0
