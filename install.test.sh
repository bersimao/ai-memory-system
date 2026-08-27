#!/usr/bin/env bash
# Self-check for install.sh.
#
# Case 5 is the one that matters most: a failed settings.json merge used to
# still print "done" and exit 0 -- the user would believe the system was
# installed while no hook was registered and nothing ever ran.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
SUT="$PWD/install.sh"
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
hooks_of() { python3 -c "
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print(-1); raise SystemExit
print(sum(len(m.get('hooks',[])) for ms in d.get('hooks',{}).values() for m in ms))" "$1"; }

# A crontab STUB so the tests never touch the real user crontab. Stateful:
# `crontab -` stores, `crontab -l` reads back — so idempotency is testable.
stub="$tmp/bin"; mkdir -p "$stub"
cat > "$stub/crontab" <<'EOF'
#!/usr/bin/env bash
f="${CRONTAB_FILE:?}"
case "${1:-}" in
  -l) [ -f "$f" ] && cat "$f" || exit 1 ;;
  -)  cat > "$f" ;;
  *)  exit 2 ;;
esac
EOF
chmod +x "$stub/crontab"
CT="$tmp/crontab-state"
runsut() { CRONTAB_FILE="$CT" PATH="$stub:$PATH" "$@"; }

# 1 — dry-run creates nothing.
h="$tmp/a"; runsut env CLAUDE_HOME="$h/.claude" "$SUT" --dry-run >/dev/null 2>&1 </dev/null
[ ! -d "$h/.claude" ] && ok "dry-run creates nothing" || bad "dry-run created files"

# 2 — fresh install: files land, hooks registered, exit 0.
h="$tmp/b"; mkdir -p "$h"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1 </dev/null; rc=$?
n=$(hooks_of "$h/.claude/settings.json")
[ $rc -eq 0 ] && [ -f "$h/.claude/hooks/memory-inject.js" ] && [ "$n" = 4 ] \
  && ok "clean install registers the 4 hooks" || bad "clean install failed (rc=$rc, hooks=$n)"

# 3 — idempotent.
runsut env CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1 </dev/null
[ "$(hooks_of "$h/.claude/settings.json")" = 4 ] \
  && ok "reinstalling does not duplicate hooks" || bad "reinstalling duplicated hooks"

# 4 — merge preserves the user's own hooks and unrelated settings.
h="$tmp/c"; mkdir -p "$h/.claude"
cat > "$h/.claude/settings.json" <<'JSON'
{"model":"opus","hooks":{"Stop":[{"hooks":[{"type":"command","command":"/bin/true my-hook"}]}]}}
JSON
runsut env CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1 </dev/null
keep=$(grep -c 'my-hook' "$h/.claude/settings.json")
model=$(python3 -c "import json;print(json.load(open('$h/.claude/settings.json')).get('model'))")
[ "$keep" -ge 1 ] && [ "$model" = opus ] && [ -f "$h/.claude/settings.json.bak-preinstall" ] \
  && ok "preserves the user's hooks and settings, with a backup" \
  || bad "merge lost user config (hook=$keep model=$model)"

# 5 — REGRESSION: invalid settings.json must fail loudly, not silently.
h="$tmp/d"; mkdir -p "$h/.claude"
printf '{ not json' > "$h/.claude/settings.json"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1 </dev/null; rc=$?
intact=$(grep -c 'not json' "$h/.claude/settings.json")
[ $rc -ne 0 ] && [ "$intact" = 1 ] \
  && ok "invalid settings.json: refuses, preserves, exits != 0" \
  || bad "a broken install reported success (rc=$rc) — silent failure"

# 6 — a hook already registered by absolute path must not be duplicated.
h="$tmp/e"; mkdir -p "$h/.claude"
cat > "$h/.claude/settings.json" <<JSON
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"\"/usr/bin/node\" \"$h/.claude/hooks/memory-inject.js\""}]}]}}
JSON
runsut env CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1 </dev/null
dup=$(grep -o 'memory-inject' "$h/.claude/settings.json" | wc -l)
[ "$dup" = 1 ] && ok "hook already registered by absolute path is not duplicated" \
  || bad "memory-inject registered ${dup}x"

# 7 — REGRESSION: the instructions must actually land. Without them the system
# captures transcripts but never writes memory — half-alive, and silently so.
h="$tmp/f"; mkdir -p "$h"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1 </dev/null
g="$h/.claude/CLAUDE.md"
if [ -f "$g" ] && grep -q 'search before denying' "$g" \
   && [ -f "$h/.claude/skills/memory-write/SKILL.md" ]; then
  ok "--yes installs the instructions and the memory-write skill"
else
  bad "instructions/skill did not land on install"
fi

# 8 — appending twice must not duplicate them.
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1 </dev/null
n=$(grep -c 'memory-system:instructions' "$g")
[ "$n" = 2 ] && ok "reinstalling does not duplicate the instructions" \
  || bad "instructions duplicated (${n} markers, expected 2)"

# 9 — an existing CLAUDE.md must survive, with a backup.
h="$tmp/g"; mkdir -p "$h/.claude"
printf '# mine\nmust not vanish\n' > "$h/.claude/CLAUDE.md"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1 </dev/null
if grep -q 'must not vanish' "$h/.claude/CLAUDE.md" \
   && [ -f "$h/.claude/CLAUDE.md.bak-preinstall" ]; then
  ok "existing CLAUDE.md preserved, with a backup"
else
  bad "install destroyed the user's CLAUDE.md"
fi

# 10 — REGRESSION: without a terminal the installer must SKIP, never block.
# It first used `read </dev/tty`, then a bare `read`, which hung forever when
# stdin was an open-but-empty pipe -- and an installer that hangs is
# indistinguishable from one that crashed.
h="$tmp/h"; mkdir -p "$h"
out=$(timeout 90 env CRONTAB_FILE="$CT" PATH="$stub:$PATH" CLAUDE_HOME="$h/.claude" "$SUT" </dev/null 2>&1); rc=$?
if [ $rc -ne 124 ] && [ ! -f "$h/.claude/CLAUDE.md" ] && grep -q 'non-interactive' <<<"$out"; then
  ok "no tty: skips without hanging and says how to get them"
else
  bad "without a tty the install hung or wrote anyway (rc=$rc)"
fi

# 11 — with ~/.codex present, the instructions go to CLAUDE.md AND AGENTS.md.
# Claude Code reads CLAUDE.md, Codex reads AGENTS.md: installing into only one
# leaves the other agent with the machinery and none of the rules.
h="$tmp/i"; mkdir -p "$h/.codex"
printf '# Codex\nuser rule\n' > "$h/.codex/AGENTS.md"
runsut env HOME="$h" CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1 </dev/null
a=$(grep -c 'memory-system:instructions' "$h/.claude/CLAUDE.md" 2>/dev/null || echo 0)
b=$(grep -c 'memory-system:instructions' "$h/.codex/AGENTS.md" 2>/dev/null || echo 0)
if [ "$a" = 2 ] && [ "$b" = 2 ] && grep -q 'user rule' "$h/.codex/AGENTS.md"; then
  ok "installs into CLAUDE.md and AGENTS.md, preserving the user's AGENTS.md"
else
  bad "dual target failed (CLAUDE.md=$a AGENTS.md=$b)"
fi

# 12 — without ~/.codex it does not invent AGENTS.md.
h="$tmp/j"; mkdir -p "$h"
runsut env HOME="$h" CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1 </dev/null
[ ! -e "$h/.codex" ] && ok "without ~/.codex it does not create AGENTS.md" \
  || bad "created .codex without the user having Codex"

# 13 — cron consent is separate from --yes: the cron jobs run the claude CLI
# with skip-permissions and spend the user's quota, so --yes alone must NOT
# register them.
rm -f "$CT"; h="$tmp/k"; mkdir -p "$h"
out=$(runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes </dev/null 2>&1)
if [ ! -f "$CT" ] && grep -q -- '--cron' <<<"$out"; then
  ok "--yes alone does not touch the crontab, and says how to enable"
else
  bad "--yes registered cron without explicit consent (state=$([ -f "$CT" ] && cat "$CT"))"
fi

# 14 — --cron registers both jobs; re-running does not duplicate them.
rm -f "$CT"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes --cron >/dev/null 2>&1 </dev/null
d1=$(grep -c 'distill.sh' "$CT" 2>/dev/null || echo 0)
c1=$(grep -c 'curate.sh' "$CT" 2>/dev/null || echo 0)
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes --cron >/dev/null 2>&1 </dev/null
d2=$(grep -c 'distill.sh' "$CT" 2>/dev/null || echo 0)
if [ "$d1" = 1 ] && [ "$c1" = 1 ] && [ "$d2" = 1 ]; then
  ok "--cron registers distill + curate once, idempotently"
else
  bad "cron registration wrong (distill=$d1 curate=$c1 after-rerun=$d2)"
fi

# 15 — a user crontab that already has OTHER entries must survive.
printf '0 0 * * * /bin/true my-job\n' > "$CT"
runsut env CLAUDE_HOME="$h/.claude" "$SUT" --yes --cron >/dev/null 2>&1 </dev/null
if grep -q 'my-job' "$CT" && grep -q 'memory-system:distill' "$CT"; then
  ok "existing crontab entries preserved alongside the new ones"
else
  bad "cron registration clobbered the user's crontab"
fi

[ $fails -eq 0 ] && { echo PASS; exit 0; } || { echo "FAIL ($fails)"; exit 1; }
