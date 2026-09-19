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

LOG="$HOME/.memsearch/cron.log"
MEM_ROOT="${AI_MEMORY_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"  # script lives in the memory root
# Alerts go to ~/.claude/logs/alerts.log, never to the Obsidian INBOX; the
# channel and the reason live in alert.sh. /end-of-day reads that log.
. "$(dirname "${BASH_SOURCE[0]}")/alert.sh"

# chars <file> -> character count (UTF-8 aware), 0 if unreadable
# Prints nothing when the file cannot be read. It used to print 0 on failure,
# and 0 reads as "well under cap": an unreadable over-cap store closed a real
# alert and the check exited 0 (Codex, 2026-09-09). Unreadable is a VIOLATION.
chars() { python3 -c "import sys;print(len(open(sys.argv[1],encoding='utf-8',errors='replace').read()))" "$1" 2>/dev/null; }

over=""
snap_over=""
check() {  # check <file> <cap> <label>
  local file="$1" cap="$2" label="$3" n
  [ -f "$file" ] || return 0
  n=$(chars "$file")
  case "$n" in
    ''|*[!0-9]*) over="${over}  ${label}: ILEGIVEL (nao deu para medir)"$'\n'; return 0 ;;
  esac
  [ "$n" -le "$cap" ] && return 0
  over="${over}  ${label}: ${n}/${cap}"$'\n'
}

check "$MEM_ROOT/context/USER.md"   1375 "USER.md"
check "$MEM_ROOT/context/MEMORY.md" 4000 "global MEMORY.md"

# Per-store cap: optional `context/.cap`, default 2500. Parser and rationale
# live in store-cap.sh, shared with curate.sh and distill.sh so the three
# cannot drift apart.
. "$(dirname "${BASH_SOURCE[0]}")/store-cap.sh"

shopt -s nullglob
for f in "$MEM_ROOT"/projects/*/context/MEMORY.md; do
  check "$f" "$(store_cap "$(dirname "$f")")" "$(basename "$(dirname "$(dirname "$f")")")"
done

# Snapshot bytes are now bounded by the compiler, not estimated here.
rc=0

if [ -z "$over" ]; then
  # A escrita do alerta pode falhar (log sem permissao, disco cheio). Sem checar,
  # a recuperacao sai 0 e o alerta antigo fica aberto para sempre — ou o alerta
  # novo some e o check sai 0 como se estivesse tudo bem. Falhou = sai 1.
  alert_ok "MEMORY-CAP ALERT" "todos os arquivos dentro do cap" || rc=1
else
  echo "[$(date -Iseconds)] cap violations:" >>"$LOG"
  printf '%s' "$over" >>"$LOG"
  # Self-contained alert: the count AND the offending files, so the log is
  # readable without cross-referencing cron.log.
  count=$(printf '%s' "$over" | grep -c .)
  alert "MEMORY-CAP ALERT" "${count} file(s) over cap - curate alone will not fix this; preserve complete originals in topic pages.
$(printf '%s' "$over")"
  rc=1
fi

# Close only the obsolete estimator alert; the compiler enforces its own budget.
alert_ok "MEMORY-SNAPSHOT ALERT" "snapshot compiler now enforces a byte bound" || rc=1

exit $rc
