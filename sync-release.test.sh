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

# 9 — REGRESSION (2026-09-01): local POLICY prose in a shipped code file. The
# real leak was a comment in cron/backup-push.sh citing a section of the author's
# personal instructions file. It carried no absolute path and no username, so the
# path pattern scored zero and the sync reported the release clean.
#
# The rule is deny-by-default, so these probes vary the SYNTAX on purpose: a
# line comment, a TRAILING comment, a Python docstring, a bare string. The first
# version of this guard anchored comments to the start of a line and knew nothing
# about docstrings — two of these four walked straight through it.
# Two axes vary together: the COMMENT SYNTAX (a syntax-keyed guard misses
# docstrings and trailing comments) and the way the path is SPELLED. Every
# spelling below defeated an earlier version of this guard:
#   ~/...             the only one the first pattern handled
#   ${HOME}/...       brace form — flagged by Codex
#   "$HOME"/...       quotes between prefix and slash
#   path.join(...)    no "/.claude/" substring at all; how every shipped .js does it
C='~/.cla''ude'; D='$HOME/.cla''ude'; E='${HOME}/.cla''ude'
i=0
for probe in "cron/backup-push.sh|# see %s for the rule|line comment, ~" \
             "hooks/daily-log-nudge.js|const X = 30;  // per %s|trailing comment, \${HOME}" \
             "cron/split-memory.py|\"\"\"See %s for the rule.\"\"\"|docstring, \"\$HOME\"" \
             "scripts/mem|MSG='see %s'|bare string, ~"; do
  file="${probe%%|*}"; rest="${probe#*|}"; tmpl="${rest%%|*}"; kind="${rest##*|}"
  i=$((i+1))
  case $i in
    1) ref="$C/CLAUDE.md" ;;
    2) ref="$E/commands/commit.md" ;;
    3) ref="\"$D\"/skills/db" ;;
    4) ref="$C/settings.json" ;;
  esac
  # shellcheck disable=SC2059
  printf "$tmpl\n" "$ref" >> "$src/$file"
  out=$(run --from-home); rc=$?
  if [ $rc -ne 0 ] && grep -q ABORT <<<"$out" \
     && ! grep -q "$ref" "$dst/$file" 2>/dev/null; then
    # Assert the INVARIANT (prose does not reach the release), not the mechanism:
    # case 6 commits the tree, so from here the guard reverts instead of deleting.
    ok "policy reference in $file ($kind) is kept out of the release"
  else
    bad "policy reference in $file ($kind) slipped through; rc=$rc"
  fi
  printf 'clean\n' > "$src/$file"; run --from-home >/dev/null 2>&1
done

# 9b — the JS form. `path.join(home, '.claude', 'commands', ...)` contains no
# "/.claude/" substring anywhere, so every path-shaped pattern missed it — and
# that is how all eight shipped .js files build their paths.
DOT='.cla''ude'                                  # concatenated OUTSIDE quotes, or
                                                # printf emits the two quotes literally
printf "const p = path.join(home, '%s', 'commands', 'commit.md');\n" "$DOT" \
  >> "$src/hooks/memory-inject.js"
out=$(run --from-home); rc=$?
if [ $rc -ne 0 ] && grep -q ABORT <<<"$out" \
   && ! grep -q "commit.md" "$dst/hooks/memory-inject.js" 2>/dev/null; then
  ok "policy reference built with path.join is caught"
else
  bad "path.join form slipped through; rc=$rc"
fi
printf 'clean\n' > "$src/hooks/memory-inject.js"; run --from-home >/dev/null 2>&1

# 10 — the exemption list is what keeps the rule usable. cron/check-hooks.sh
# genuinely reads the settings file; a guard that fired there would be switched
# off within a week. Named in CONFIG_OK, visible to a reviewer.
printf 'SETTINGS="%s/settings.json"\n' "$D" >> "$src/cron/check-hooks.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "a file named in CONFIG_OK may operate on the config surface" \
  || bad "false positive on the exempted file; rc=$rc"
printf 'clean\n' > "$src/cron/check-hooks.sh"; run --from-home >/dev/null 2>&1

# 10a — the SECOND exempted file. memsearch-index.sh indexes the skills' knowledge
# dirs, and was passing the earlier guard only because of the quoting gap. Without
# this case, dropping it from CONFIG_OK breaks nothing in the suite while breaking
# the real sync.
printf '  "%s"/skills/*/knowledge \\\n' "$D" >> "$src/cron/memsearch-index.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "the skills indexer may read the skills tree" \
  || bad "false positive on memsearch-index.sh; rc=$rc"
printf 'clean\n' > "$src/cron/memsearch-index.sh"; run --from-home >/dev/null 2>&1

# 10c — the delimiters around commands|skills|agents are load-bearing. Those are
# ordinary English words; without a quote or slash around them, any comment that
# mentions the store and the word "commands" in prose would abort the release.
printf '# the .cla''ude store holds no commands of its own\n' >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "a bare word in prose is not a config reference" \
  || bad "false positive on prose using the word commands; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 10b — ...and the exemption is per FILE, not blanket: the same line in a file
# that is not on the list is still a leak. Without this, CONFIG_OK could be
# widened to everything and every case above would still pass.
printf 'SETTINGS="%s/settings.json"\n' "$D" >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORT <<<"$out" \
  && ok "a file NOT in CONFIG_OK may not name the config surface" \
  || bad "unexempted config reference slipped through; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 10d — the .claude context is load-bearing too, in the other direction: a bare
# filename with no store path is generic mechanism talk, not a config reference.
# Without this case the context filter could be dropped and everything still pass.
# The cost is stated in CLAUDE.md: "per the user's settings.json" would be missed.
printf '# the harness reads settings.json at startup\n' >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "a bare filename without the store path is not a config reference" \
  || bad "false positive on a generic settings.json mention; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 11 — docs are exempt: telling the reader to edit their own instructions file is
# what the docs are FOR. A Markdown heading starts with `#`, so it is
# comment-shaped; if the scan were not scoped to code directories, every doc that
# names the config file would abort the release.
printf "Append this to %s/CLAUDE.md\n## Editing %s/CLAUDE.md\n" "$C" "$C" >> "$dst/README.md"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORT <<<"$out" \
  && ok "docs may instruct the reader about their own config" \
  || bad "false positive on documentation; rc=$rc"
printf 'clean\n' > "$dst/README.md"

# 5 — the username must not be baked in.
# Compare against the REAL user running this: writing a name here would repeat
# exactly the defect this case exists to catch.
grep -qE "id -un" "$SUT" && ! grep -qF "$(id -un)" "$SUT" \
  && ok "username comes from id -un, not hardcoded" \
  || bad "username hardcoded in the guard"

[ $fails -eq 0 ] && { echo PASS; exit 0; } || { echo "FAIL ($fails)"; exit 1; }
