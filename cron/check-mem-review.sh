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

LOG="$HOME/.memsearch/cron.log"
. "$(dirname "${BASH_SOURCE[0]}")/alert.sh"
USAGE="$HOME/.claude/data/memsearch-usage.jsonl"
# ponytail: 25 is a guess at "enough to see a score distribution", not a power
# calculation. Raise it if the first review is inconclusive.
THRESHOLD=25

[ -f "$USAGE" ] || exit 0

# Sem `|| echo 0`: grep -c JA imprime 0 e sai 1 quando nao casa nada, e o
# fallback acrescentava uma segunda linha ("0\n0") que quebrava o teste numerico
# e caia direto no alerta (Codex, 2026-09-09).
n=$(grep -c '"ev": "search"' "$USAGE" 2>/dev/null); n=${n:-0}
echo "[$(date -Iseconds)] mem-review: ${n}/${THRESHOLD} searches logged" >>"$LOG"

[ "$n" -lt "$THRESHOLD" ] && exit 0

# The marker must stay unique to this check: alert.sh dedups on a substring,
# so sharing one with another tripwire would let this alert suppress that one.
msg="📊 ${n} real searches logged — run \`~/.claude/scripts/mem report\` and decide (a) the miss threshold, (b) sources never opened, (c) whether entry format should change."
# Stays OPEN until the review actually happens — there is no automatic
# recovery signal for "the user read the report", which is the point: the
# nudge should keep showing up in /end-of-day until it is dealt with.
# A escrita do alerta pode falhar (log sem permissao, disco cheio). Sem checar,
# a recuperacao sai 0 e o alerta antigo fica aberto para sempre — ou o alerta
# novo some e o check sai 0 como se estivesse tudo bem. Falhou = sai 1.
# alert_once, nao alert: a contagem so cresce, entao um `alert` reabria o nudge
# na manha seguinte a cada fechamento manual. Fechou = revisado; para pedir
# outra revisao, reabra de proposito.
alert_once "MEMORY-RECALL REVIEW" "$msg" || exit 1
exit 0
