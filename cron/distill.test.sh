#!/usr/bin/env bash
# distill.sh must raise an alert when maintenance fails, and must never let that
# failure skip the backup. Regression for 2026-09-13/14: an extractor parse error
# ended the script before backup-push, silently, two nights running.
# Runs distill.sh in a throwaway tree with stubbed steps; touches nothing real.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fails=0

run_case() {  # run_case <exit code of the backfill step>
  local tmp; tmp="$(mktemp -d)"
  mkdir -p "$tmp/cron" "$tmp/scripts"
  cp "$HERE/distill.sh" "$HERE/alert.sh" "$tmp/cron/"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/cron/check-hooks.sh"
  cp "$tmp/cron/check-hooks.sh" "$tmp/cron/check-caps.sh"
  printf 'import sys\nsys.exit(0)\n' > "$tmp/cron/jsonl-to-transcript.py"
  printf 'import sys\nsys.exit(%s if sys.argv[1] == "backfill" else 0)\n' "$1" > "$tmp/scripts/memory-maintain.py"
  printf '#!/usr/bin/env bash\ntouch "%s/backup-ran"\n' "$tmp" > "$tmp/cron/backup-push.sh"
  chmod +x "$tmp/cron/"*.sh
  AI_MEMORY_HOME="$tmp" CLAUDE_ALERT_LOG="$tmp/alerts.log" bash "$tmp/cron/distill.sh" >/dev/null 2>&1
  echo $? > "$tmp/exit-code"
  printf '%s' "$tmp"
}
check() {  # check <label> <0|1 result>
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi
}

t="$(run_case 1)"
[ -e "$t/backup-ran" ]; check "manutenção falhou: backup roda mesmo assim" $?
grep -q 'MEMORY-MAINTENANCE ALERT\] \[!\]' "$t/alerts.log" 2>/dev/null; check "manutenção falhou: alerta aberto" $?
[ "$(cat "$t/exit-code")" != 0 ]; check "manutenção falhou: exit != 0 mesmo com backup ok" $?
rm -rf "$t"

t="$(run_case 0)"
[ -e "$t/backup-ran" ]; check "manutenção ok: backup roda" $?
[ ! -s "$t/alerts.log" ]; check "manutenção ok: nenhum alerta escrito" $?
[ "$(cat "$t/exit-code")" = 0 ]; check "manutenção ok: exit 0" $?
rm -rf "$t"

# --root install: with no AI_MEMORY_HOME in the environment, the Python steps
# must get the script's own root, not fall back to ~/.claude.
t="$(mktemp -d)"
mkdir -p "$t/cron" "$t/scripts" "$t/fakehome"
cp "$HERE/distill.sh" "$HERE/alert.sh" "$t/cron/"
printf '#!/usr/bin/env bash\nexit 0\n' > "$t/cron/check-hooks.sh"
cp "$t/cron/check-hooks.sh" "$t/cron/check-caps.sh"
printf 'import sys\nsys.exit(0)\n' > "$t/cron/jsonl-to-transcript.py"
printf 'import os\nopen(os.path.join(os.path.dirname(__file__), "seen-root"), "w").write(os.environ.get("AI_MEMORY_HOME", "UNSET"))\n' > "$t/scripts/memory-maintain.py"
chmod +x "$t/cron/"*.sh
env -u AI_MEMORY_HOME HOME="$t/fakehome" CLAUDE_ALERT_LOG="$t/alerts.log" bash "$t/cron/distill.sh" >/dev/null 2>&1
[ "$(cat "$t/scripts/seen-root" 2>/dev/null)" = "$t" ]; check "--root: passos Python recebem a raiz do próprio script" $?
[ ! -e "$t/fakehome/.claude" ]; check "--root: nada escrito em ~/.claude" $?
rm -rf "$t"

if [ "$fails" -eq 0 ]; then echo PASS; else echo "FAILED ($fails)"; exit 1; fi
