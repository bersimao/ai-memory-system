#!/usr/bin/env bash
# Self-check for curate.sh's eligibility gate. No LLM, no cron.
# Run: bash ~/.claude/cron/curate-gate.test.sh
#
# What this pins down: the activity gate alone left a store that was over cap
# invisible forever if the project went quiet ([[2026-08-26]]).
set -uo pipefail

SRC="$(dirname "$0")/curate.sh"

# Extract chars() + the eligibility loop. Guard against an empty extraction —
# an empty probe looks exactly like a passing one (lesson of 2026-08-23).
code="$(sed -n '/^chars() {/p;/^  for ctx in "\$PROJECTS_ROOT"/,/^  done$/p' "$SRC")"
[ -n "$code" ] || { echo "FAIL: empty extraction from $SRC"; exit 1; }
grep -q 'for ctx in' <<<"$code" || { echo "FAIL: did not extract the loop"; exit 1; }
grep -q '^chars() {' <<<"$code" || { echo "FAIL: did not extract chars()"; exit 1; }
grep -q 'register' <<<"$code" || { echo "FAIL: extracted loop does not call register"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PROJECTS_ROOT="$T/projects"
REGISTERED=""
register() { REGISTERED="${REGISTERED}$3"$'\n'; }   # stub: only records the label
[ "$(bash -c "$(sed -n '/^chars() {/p' "$SRC"); chars '$SRC'")" -gt 100 ] \
  || { echo "FAIL: extracted chars() measures nothing"; exit 1; }

mk() { # <label> <chars> <log-age-in-days | "no-log">
  local d="$PROJECTS_ROOT/$1/context"
  mkdir -p "$d"
  head -c "$2" /dev/zero | tr '\0' 'a' >"$d/MEMORY.md"   # ASCII: chars == bytes
  [ "$3" = no-log ] && return 0
  mkdir -p "$d/memory"
  : >"$d/memory/2026-01-01.md"
  touch -d "$3 days ago" "$d/memory/2026-01-01.md"
}

mk active-under-cap 2000 1
mk active-over-cap  2800 1
mk quiet-over-cap   2800 40   # <- the CLARK case: the hole the fix closes
mk quiet-under-cap  2000 40
# No memory/ at all: the activity gate can NEVER be satisfied, so the cap is
# the only way in. Guarding the loop with `-d memory/` killed these stores
# before any gate (found by the Codex gate, [[2026-08-26]]).
mk no-log-over-cap  2800 no-log
mk no-log-under-cap 2000 no-log
# Store with a daily log and NO MEMORY.md: nothing to curate, must not blow up.
mkdir -p "$PROJECTS_ROOT/no-memory/context/memory"
: >"$PROJECTS_ROOT/no-memory/context/memory/2026-01-01.md"

eval "$code"

fail=0
# Returning non-zero on error matters: without it the `&& echo ok` right below
# a FAIL fires anyway and the output reassures about the case that just broke.
want() { grep -qx "$1" <<<"$REGISTERED" || { echo "FAIL: $1 should register"; fail=1; return 1; }; }
dont() { grep -qx "$1" <<<"$REGISTERED" && { echo "FAIL: $1 should NOT register"; fail=1; return 1; }; return 0; }

want active-under-cap && echo "ok   active under cap registers"
want active-over-cap  && echo "ok   active over cap registers"
want quiet-over-cap   && echo "ok   QUIET over cap registers (the fix)"
dont quiet-under-cap  && echo "ok   quiet under cap is skipped"
want no-log-over-cap  && echo "ok   NO memory/ over cap registers (fix 2)"
dont no-log-under-cap && echo "ok   no memory/ under cap is skipped"
dont no-memory        && echo "ok   store without MEMORY.md is skipped without breaking"

[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
