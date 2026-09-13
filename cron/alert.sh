#!/usr/bin/env bash
# Shared alert channel for the cron tripwires. SOURCE it, or run it as a CLI.
#
# History (2026-09-09): every tripwire published to the Obsidian INBOX with its
# own copy of the same guard, and daily machine alerts were burying the human
# items in that note. Alerts moved here — but the first two versions each hid
# failures in their own way, so the format is now deliberately rigid:
#
#   v1 recorded EVENTS, dated, and /end-of-day read "entries dated today". A day
#      the cron missed read as all-clear, and a still-broken condition went quiet
#      after its first announcement.
#   v2 added the lifecycle but parsed it by SUBSTRING. A message containing
#      "[MARK] [OK] " flipped its own marker's state, so the CLI refused to close
#      a genuinely open alert; a marker containing "]" was written but never read
#      back; and an unindented detail line starting with "[" was dropped.
#
# Entry format — three bracket fields, then the message:
#
#   [2026-09-09T11:25:57-03:00] [MEMORY-CAP ALERT] [!] 7 file(s) over cap
#     global MEMORY.md: 4611/4000        <- detail lines, ALWAYS indented on write
#   [2026-09-10T06:00:02-03:00] [MEMORY-CAP ALERT] [OK] all files under cap
#
# Both the reader and the writer treat only the first three bracket groups as
# structure; everything after them is opaque message text. That is what makes
# arbitrary message content — brackets, tabs, emoji, quotes — unable to forge a
# state or a marker.
#
# alert    <marker> <message>  — the condition is failing. Announced once a day
#                                while it stays open; a condition that was closed
#                                and broke again is announced immediately.
#                                OPEN regardless of date until an OK lands.
# alert_ok <marker> <message>  — the condition passed. Written ONLY if the marker
#                                is currently open, so a healthy check does not
#                                append a line every day.
# alerts_open                  — the whole latest entry (header + details) of
#                                every still-open marker. /end-of-day reads this.
#
#   alert.sh                     -> list open alerts
#   alert.sh ok <marker> [nota]  -> close one by hand
#
# Growth: ~200 bytes an entry; four tripwires re-announcing daily is roughly
# 300 KB/year, and the realistic case (mostly quiet) far less. No rotation on purpose: truncating this file
# would drop the OPEN state it exists to hold. Compact it only by hand.
ALERTS="${CLAUDE_ALERT_LOG:-$HOME/.claude/logs/alerts.log}"

# Markers are a FIELD, not a substring: brackets and whitespace would make the
# entry ambiguous to parse (and untypeable on the CLI), so they are folded away
# here — by every caller, on both the write and the read side, so the same input
# always yields the same marker.
_alert_mark() { printf '%s' "$1" | tr -d '[]' | tr '\t\n' '  ' | sed 's/  */ /g; s/^ //; s/ $//'; }

# Serialise read-decide-write. Without it, a cron announcement that decided to
# append can land AFTER a concurrent manual close and silently reopen the alert.
_alert_locked() {
  mkdir -p "$(dirname "$ALERTS")" 2>/dev/null
  if command -v flock >/dev/null 2>&1; then
    # `flock 9; cmd` ran the body even when the acquisition FAILED — the lock
    # was decorative in exactly the case it mattered. Fail instead: the caller
    # propagates it and cron sees a failed check.
    ( flock -w 10 9 || { echo "alert: nao consegui o lock de $ALERTS" >&2; exit 1; }
      "$@" ) 9>>"$ALERTS.lock"
  else
    # No flock: mkdir is atomic on every POSIX fs, so it still serialises.
    local lock="$ALERTS.mkdir-lock" rc
    _alert_lock_mkdir "$lock" "${CLAUDE_ALERT_LOCK_TRIES:-50}" || return 1
    "$@"; rc=$?
    rmdir "$lock" 2>/dev/null
    return $rc
  fi
}

# _alert_lock_mkdir <dir> <tries> -> 0 if THIS call created it.
# The first version broke mutual exclusion twice over when contended: on timeout
# it ran the body unlocked, and then removed the lock the OTHER process still
# held, letting the next waiter in on top of it. Timing out is a failure, not a
# licence to proceed — the caller propagates it exactly like a failed flock.
_alert_lock_mkdir() {
  local lock="$1" tries="$2" i=0
  while ! mkdir "$lock" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge "$tries" ]; then
      echo "alert: lock $lock ocupado apos $tries tentativas; nada foi escrito." >&2
      echo "alert: se nenhum processo esta rodando, remova o diretorio a mao." >&2
      return 1
    fi
    sleep 0.2
  done
  return 0
}

# _alert_last <marker> -> "<state> <YYYY-MM-DD>" of the marker's most recent
# header entry ("! 2026-09-09", "OK 2026-09-09", or "" if never seen).
_alert_last() {
  [ -f "$ALERTS" ] || return 0
  # ENVIRON, not `awk -v`: -v processes escape sequences, so a marker holding a
  # literal backslash-t arrived as a TAB and never matched the bytes on disk —
  # the marker became impossible to close or dedup (Codex, 2026-09-09).
  _AL_WANT="$1" awk '
    BEGIN { want = ENVIRON["_AL_WANT"] }
    substr($0,1,1) != "[" { next }                        # detail line
    {
      p = index($0, "] [");            if (!p) next       # end of [ts]
      rest = substr($0, p + 3)
      q = index(rest, "] [");          if (!q) next       # end of [marker]
      marker = substr(rest, 1, q - 1)
      tail = substr(rest, q + 3)
      r = index(tail, "] ");           if (!r) next       # end of [state]
      state = substr(tail, 1, r - 1)
      if (marker == want && (state == "!" || state == "OK"))
        last = state " " substr($0, 2, 10)
    }
    END { print last }
  ' "$ALERTS"
}

_alert_write() {  # _alert_write <state> <marker> <message>
  mkdir -p "$(dirname "$ALERTS")" 2>/dev/null
  # Continuation lines are indented HERE, not by the caller: a detail line that
  # began with "[" used to be indistinguishable from a header and was dropped.
  { printf '[%s] [%s] [%s] ' "$(date -Iseconds)" "$2" "$1"
    printf '%s\n' "$3" | awk 'NR==1{print} NR>1{sub(/^[ \t]+/,""); print "  " $0}'
  } >>"$ALERTS" || {
    # A silent write failure is the whole failure mode this channel exists to
    # remove. cron captures stderr, so this reaches the daily log.
    echo "alert: FALHOU escrever em $ALERTS — alerta '$2' perdido" >&2
    return 1
  }
}

_alert_do() {
  local marker="$1" msg="$2"
  # Skip only when this marker's LAST entry is an alert already raised today: a
  # continuously open condition. A condition closed earlier today and broken
  # again is news, and must be announced.
  [ "$(_alert_last "$marker")" = "! $(date +%F)" ] && return 0
  _alert_write '!' "$marker" "$msg"
}

_alert_ok_do() {
  case "$(_alert_last "$1")" in
    '! '*) _alert_write 'OK' "$1" "$2" ;;
    *) return 0 ;;
  esac
}

# alert_once: like alert, but a marker CLOSED by hand stays closed. For a
# threshold nudge ("25 searches logged") the condition never becomes false again,
# so plain `alert` reopened it the morning after every manual close — the nag
# loop this channel exists to avoid. Reopen it deliberately if you want another.
_alert_once_do() {
  case "$(_alert_last "$1")" in
    'OK '*) return 0 ;;
    *) _alert_do "$@" ;;
  esac
}

alert()      { _alert_locked _alert_do      "$(_alert_mark "$1")" "$2"; }
alert_once() { _alert_locked _alert_once_do "$(_alert_mark "$1")" "$2"; }
alert_ok() { _alert_locked _alert_ok_do "$(_alert_mark "$1")" "$2"; }

alerts_open() {
  [ -f "$ALERTS" ] || return 0
  # Prints the WHOLE latest entry of each open marker, header plus details.
  awk '
    substr($0,1,1) == "[" {
      p = index($0, "] [")
      rest = (p ? substr($0, p + 3) : "")
      q = (p ? index(rest, "] [") : 0)
      tail = (q ? substr(rest, q + 3) : "")
      r = (q ? index(tail, "] ") : 0)
      state = (r ? substr(tail, 1, r - 1) : "")
      if (r && (state == "!" || state == "OK")) {
        marker = substr(rest, 1, q - 1)
        st[marker] = state
        block[marker] = $0
        cur = marker
        if (!(marker in seen)) { seen[marker] = ++n; order[n] = marker }
        next
      }
    }
    cur != "" { block[cur] = block[cur] "\n" $0 }
    END { for (i = 1; i <= n; i++) if (st[order[i]] == "!") print block[order[i]] }
  ' "$ALERTS"
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  case "${1:-open}" in
    open) alerts_open ;;
    ok)
      shift
      [ $# -ge 1 ] || { echo "uso: alert.sh ok <marker> [nota]" >&2; exit 2; }
      m="$(_alert_mark "$1")"; shift
      case "$(_alert_last "$m")" in
        '! '*)
          # "fechado" so pode ser impresso DEPOIS de a escrita dar certo: dizer
          # que fechou um alerta que continua aberto e' pior do que nao fechar.
          if alert_ok "$m" "${*:-fechado manualmente}"; then
            echo "fechado: $m"
          else
            echo "NAO fechou '$m': falhou escrever em $ALERTS" >&2
            exit 1
          fi ;;
        'OK '*) echo "ja estava fechado: $m" ;;
        *)      echo "marker desconhecido: $m — veja 'alert.sh' para os abertos" >&2; exit 1 ;;
      esac ;;
    *) echo "uso: alert.sh [open|ok <marker> [nota]]" >&2; exit 2 ;;
  esac
fi
