#!/usr/bin/env bash
# Self-check for the sdk-step guard: bash memory-index.test.sh
#
# The guarantee being protected: the public release ships memory-index.sh but not
# the two sdk generators, so a root without them must index and exit 0 (it exited
# non-zero on every run before the guard). A root with only ONE generator is a
# broken install and must still fail, without skipping curated/transcripts.
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/memory-index.sh"
fails=0

# run <generator>... -> exit code of memory-index.sh in a throwaway root holding
# only the named generators (as stubs that succeed). Sets $R to that root;
# $R/calls lists the collections the stub indexer was asked for.
R=""
run() {
  R=$(mktemp -d)
  mkdir -p "$R/cron" "$R/scripts"
  cp "$SCRIPT" "$R/cron/memory-index.sh"
  printf 'alert() { :; }\nalert_ok() { :; }\n' >"$R/cron/alert.sh"
  cat >"$R/scripts/memory-index.py" <<'EOF'
import os, sys
open(os.path.join(sys.argv[sys.argv.index('--root') + 1], 'calls'), 'a').write(sys.argv[-1] + '\n')
EOF
  local g
  for g in "$@"; do : >"$R/$g"; done
  HOME="$R" AI_MEMORY_HOME= bash "$R/cron/memory-index.sh"
}

check() {  # check <name> <want-rc> <got-rc> <want-calls>
  local got
  got=$(tr '\n' ' ' <"$R/calls" 2>/dev/null)
  if [ "$3" -eq "$2" ] && [ "$got" = "$4" ] && [ -f "$R/logs/cron.log" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: rc=$3 (want $2) calls='$got' (want '$4') log=$([ -f "$R/logs/cron.log" ] && echo yes || echo no)"
    fails=$((fails + 1))
  fi
  rm -rf "$R"
}

run;                                                 check "no generators: sdk skipped, exit 0" 0 $? "curated transcripts "
run cron/build-system-map.py scripts/sdk-to-md.py;   check "both generators: sdk indexed" 0 $? "sdk curated transcripts "
run scripts/sdk-to-md.py; rc=$?
[ "$rc" -ne 0 ] && rc=1;                             check "one generator: fails, still indexes" 1 "$rc" "curated transcripts "

[ "$fails" -eq 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
