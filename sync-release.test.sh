#!/usr/bin/env bash
# Self-check for sync-release.sh's leak guard.
#
# Exists because the guard shipped broken twice: it carried a hardcoded
# username, and it did not scan itself — "the leak checker is the one file
# nobody checks". Case 4 is that regression specifically.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
SUT="$PWD/sync-release.sh"
fails=0
ok()   { echo "ok   $1"; }
bad()  { echo "FAIL $1"; fails=$((fails+1)); }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# Fake leaks assembled from fragments. Written out in full, the guard (which
# now scans the WHOLE release, this file included) would match its own
# fixtures and raise an eternal false positive.
H='/home'

# Fake source + a git repo standing in for the release checkout.
src="$tmp/home"; dst="$tmp/repo"
mkdir -p "$src/hooks" "$src/cron" "$src/scripts" "$dst"
git -C "$dst" init -q
cp "$SUT" "$dst/sync-release.sh"
printf 'clean\n' > "$dst/README.md"

# Minimal stand-ins for every manifest entry, so nothing reports MISSING.
for rel in $(sed -n '/^MANIFEST=(/,/^)/p' "$SUT" | grep -oE '^  [a-z].*'); do
  mkdir -p "$src/$(dirname "$rel")"; printf 'clean\n' > "$src/$rel"
done
run() { ( cd "$dst" && CLAUDE_HOME="$src" ./sync-release.sh "$@" 2>&1 ); }

# 1 — clean source: passes, and does NOT flag its own pattern definition.
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "clean source passes (no false positive on itself)" \
  || bad "clean source should pass; rc=$rc"

# 2 — leak in a manifest file: caught, deleted (regenerable), non-zero exit.
printf "# ${H}/alice/x\n" >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORT <<<"$out" && [ ! -f "$dst/cron/distill.sh" ] \
  && ok "leak in a manifest file: detected and removed" \
  || bad "manifest leak was not contained; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 3 — leak in a hand-written file: caught, but NOT deleted.
printf "# ${H}/bob/x\n" >> "$dst/README.md"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORT <<<"$out" && [ -f "$dst/README.md" ] \
  && ok "leak in a hand-written file: detected without deleting" \
  || bad "README should not have been deleted; rc=$rc"
printf 'clean\n' > "$dst/README.md"

# 4 — REGRESSION: the guard must scan itself.
printf "# ${H}/carol/x\n" >> "$dst/sync-release.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q 'sync-release.sh' <<<"$out" && [ -f "$dst/sync-release.sh" ] \
  && ok "the guard scans itself" \
  || bad "the guard does NOT scan itself (regression)"
cp "$SUT" "$dst/sync-release.sh"

# 6 — REGRESSION: a TRACKED hand-written file with uncommitted work must not
# be rolled back. Branching on tracked-vs-untracked destroyed this case: once
# README.md is committed, `git checkout --` would discard every uncommitted
# line, not just the leaking one.
git -C "$dst" add -A >/dev/null 2>&1
git -C "$dst" -c user.email=t@t -c user.name=t commit -qm base >/dev/null 2>&1
printf "hand-written work that must not vanish\n# ${H}/dave/x\n" >> "$dst/README.md"
out=$(run --from-home); rc=$?
if [ $rc -ne 0 ] && grep -q 'hand-written work' "$dst/README.md"; then
  ok "tracked hand-written file is not reverted"
else
  bad "rollback destroyed uncommitted work in README.md; rc=$rc"
fi
git -C "$dst" checkout -- README.md 2>/dev/null

# 7 — REGRESSION: a leaking line that also mentions $HOME must still be caught.
# `grep -v '$HOME'` dropped the whole line, so any leak sharing a line with a
# legitimate $HOME slipped through.
printf "# copy of ${H}/erin/x to \\"\$HOME/.claude\\"\n" >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORT <<<"$out" \
  && ok "a leak on the same line as \$HOME is detected" \
  || bad "a line with \$HOME escaped the guard; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 8 — legitimate uses of $HOME must not raise false positives.
printf 'LOG="$HOME/.memsearch/cron.log"\n' >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ok "legitimate \$HOME use is not a false positive" \
  || bad "false positive on legitimate \$HOME; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 5 — the username must not be baked in.
# Compare against the REAL user running this: writing a name here would repeat
# exactly the defect this case exists to catch.
grep -qE "id -un" "$SUT" && ! grep -qF "$(id -un)" "$SUT" \
  && ok "username comes from id -un, not hardcoded" \
  || bad "username hardcoded in the guard"

[ $fails -eq 0 ] && { echo PASS; exit 0; } || { echo "FAIL ($fails)"; exit 1; }
