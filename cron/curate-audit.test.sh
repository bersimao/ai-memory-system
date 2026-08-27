#!/usr/bin/env bash
# Self-check for curate.sh's audit_and_veto. No LLM, no cron.
# Run: bash ~/.claude/cron/curate-audit.test.sh
set -uo pipefail

SRC="$(dirname "$0")/curate.sh"
CUT_ALERT=30
CUT_VETO=50
STAMP="testrun1"

# Extract the function under test. Guard against a silent empty extraction —
# an empty probe looks exactly like a passing one (learned 2026-08-23).
# audit_and_veto depends on chars() — extract BOTH, or the test runs against a
# broken function and the vetoes pass for the wrong reason (seen 2026-08-23).
fn="$(sed -n '/^chars() {/p;/^register() {/,/^}/p;/^audit_and_veto() {/,/^}/p' "$SRC")"
[ -n "$fn" ] || { echo "FAIL: could not extract from $SRC"; exit 1; }
grep -q 'CUT_VETO' <<<"$fn" || { echo "FAIL: extracted text is not the function"; exit 1; }
grep -q '^chars() {' <<<"$fn" || { echo "FAIL: chars() was not extracted"; exit 1; }
grep -q '^register() {' <<<"$fn" || { echo "FAIL: register() was not extracted"; exit 1; }
eval "$fn"
# sanity: does the extracted function actually measure?
[ "$(chars "$SRC")" -gt 100 ] || { echo "FAIL: extracted chars() measures nothing"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
check() { # <name> <expected-substring> <expected-final-size> <file>
  if ! grep -q -- "$2" <<<"$OUT"; then echo "FAIL $1: expected '$2' in: $OUT"; fail=1; return; fi
  local got; got="$(wc -c <"$4")"
  if [ "$got" != "$3" ]; then echo "FAIL $1: final size $got, expected $3"; fail=1; return; fi
  echo "ok   $1"
}

# REALISTIC project-store path: the overflow hint matches
# */projects/*/context/MEMORY.md, so the fixture needs that shape.
mk() { # <name> <before> <after> [cap] -> sets f
  mkdir -p "$T/projects/$1/context"
  f="$T/projects/$1/context/MEMORY.md"
  head -c "$2" /dev/zero | tr '\0' 'a' >"$f"   # ASCII: chars == bytes
  cp -p "$f" "$f.bak"
  head -c "$3" /dev/zero | tr '\0' 'b' >"$f"   # simulate the LLM edit
  SIZES="$2|${4:-2500}|$f|$1"
}

# the veto must PRESERVE the proposal, or the alert is unactionable
mk vetoprop 1000 300; OUT="$(audit_and_veto)"
if [ -f "$f.rejected-$STAMP" ] && [ "$(wc -c <"$f.rejected-$STAMP")" = 300 ]; then
  echo "ok   veto preserves the proposal in .rejected"
else
  echo "FAIL veto did not preserve the proposal (.rejected missing or wrong)"; fail=1
fi
grep -q 'diff ' <<<"$OUT" || { echo "FAIL veto alert carries no diff command"; fail=1; }
# accepting must CONSUME the artifact, or the command is not idempotent:
# accept -> memory edited later -> the same cp again wipes the new edits.
grep -q 'accept: cp .* && rm ' <<<"$OUT" \
  && echo "ok   accepting consumes the .rejected (cp && rm)" \
  || { echo "FAIL accepting does not remove the .rejected"; fail=1; }

mk shrink10 1000 900;  OUT="$(audit_and_veto)"; check "10%% cut = ok"          "ok shrink10"   900  "$f"
mk shrink35 1000 650;  OUT="$(audit_and_veto)"; check "35%% cut = alert"       "large cut in curate"  650  "$f"
mk shrink60 1000 400;  OUT="$(audit_and_veto)"; check "60%% cut = VETO"        "CURATE VETO"   1000 "$f"
mk emptied  1000 0;    OUT="$(audit_and_veto)"; check "emptied = VETO"         "CURATE VETO"   1000 "$f"
mk grew     1000 1500; OUT="$(audit_and_veto)"; check "grew = ok"              "ok grew"       1500 "$f"

# exact boundary: 50% must veto (>=), 49% must not
mk exact50  1000 500;  OUT="$(audit_and_veto)"; check "50%% cut = VETO"        "CURATE VETO"   1000 "$f"
mk under50  1000 510;  OUT="$(audit_and_veto)"; check "49%% cut = alert only"  "large cut in curate"  510  "$f"

# a veto on a file over its cap must say SPLIT, not compress — otherwise
# curate cuts / the veto restores / nothing improves, every single week.
mk bloated 7600 2500 2500; OUT="$(audit_and_veto)"
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && echo "ok   over-cap veto suggests splitting into topics/" \
  || { echo "FAIL over-cap veto did not suggest splitting"; fail=1; }
# the global layer has NO topics/ — its hint must say to route the fact out
gf="$T/global-MEMORY.md"
head -c 6000 /dev/zero | tr '\0' 'a' >"$gf"; cp -p "$gf" "$gf.bak"
head -c 2000 /dev/zero | tr '\0' 'b' >"$gf"
SIZES="6000|4000|$gf|global"; OUT="$(audit_and_veto)"
grep -q 'belongs somewhere else' <<<"$OUT" \
  && echo "ok   global veto says route the overflow out, not compress" \
  || { echo "FAIL global veto did not give the right exit"; fail=1; }
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && { echo "FAIL global veto suggested topics/ (does not exist there)"; fail=1; } \
  || echo "ok   global veto does NOT suggest topics/"

# and a veto within the cap must not pollute the alert with the hint
mk small 2000 800 2500; OUT="$(audit_and_veto)"
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && { echo "FAIL under-cap veto should not suggest splitting"; fail=1; } \
  || echo "ok   under-cap veto does not suggest splitting"

# --- veto artifacts ----------------------------------------------------------
# curate DELETES the file: the proposal is the deletion, there is nothing to
# copy. The alert must not point diff/cp at a nonexistent .rejected.
mk deleted 3000 1 2500; rm -f "$f"
OUT="$(audit_and_veto 2>&1)"
grep -q 'cannot stat' <<<"$OUT" && { echo "FAIL veto with deleted file still tries to copy"; fail=1; } \
  || echo "ok   veto with deleted file does not try to copy"
grep -q 'proposal was to DELETE' <<<"$OUT" \
  && echo "ok   alert explains the proposal was a deletion" \
  || { echo "FAIL alert does not explain the deletion"; fail=1; }
[ -f "$f" ] && echo "ok   deleted file was restored" || { echo "FAIL did not restore"; fail=1; }

# a .rejected from a previous round must not survive register()
mk stale 3000 2900 2500
echo "OLD PROPOSAL" > "$f.rejected-previous"
TARGETS=""; SIZES=""
register "$f" 2500 stale >/dev/null
[ -f "$f.rejected-previous" ] && { echo "FAIL old .rejected survived register"; fail=1; } \
  || echo "ok   register discards a previous round's .rejected"

# An alert from a previous round stores a literal path. After a new round that
# path must NOT exist — or the old acceptance silently applies a proposal the
# user never reviewed.
mk aliasing 3000 1000 2500
STAMP="week1"; audit_and_veto >/dev/null
old="$f.rejected-week1"
[ -f "$old" ] || { echo "FAIL round 1 did not create the stamped artifact"; fail=1; }
cp -p "$f.bak" "$f"                      # simulate the week passing
STAMP="week2"; TARGETS=""; SIZES=""
register "$f" 2500 aliasing >/dev/null   # a new round clears the previous one
head -c 500 /dev/zero | tr '\0' 'c' >"$f"
audit_and_veto >/dev/null
[ -f "$old" ] && { echo "FAIL the OLD alert's path still exists (silent acceptance)"; fail=1; } \
  || echo "ok   the old alert cannot reach the new proposal"
[ -f "$f.rejected-week2" ] && echo "ok   the new round has its own artifact" \
  || { echo "FAIL the new round created no artifact"; fail=1; }

[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
