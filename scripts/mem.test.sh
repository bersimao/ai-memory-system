#!/usr/bin/env bash
# Self-check for `mem`: bash mem.test.sh
#
# Covers the weak-result warning only. `mem` is a thin seam over a third-party
# backend (memsearch), so the backend is faked here through the same contract
# documented in ai-memory-system/docs/retrieval-interface.md: backend_search
# prints JSON [{chunk_hash, score, source, content}].
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

# A copy of `mem` with the backend swapped for a stub, so no model is loaded and
# no usage line is appended to the real log.
make_mem() {  # make_mem <path> <json the fake backend returns>
  local dst="$1" body="$2"
  sed -e "s|^backend_search() {|backend_search() { printf '%s' '$body'; return 0; }\nunused_backend_search() {|" \
      -e "s|^  echo \"\$line\" >> \"\$LOG\"|  :|" \
      "$HERE/mem" > "$dst"
  # neutralise the usage log wherever it is written
  sed -i "s|>> \"\$LOG\"|>> /dev/null|g" "$dst"
  chmod +x "$dst"
}

run() { HOME="$tmp" "$1" search "$2" -k 5 -c curated 2>/dev/null; }

# --- weak results: the retry order must fire -------------------------------
# Today's real failure: five confident-looking hits, all the wrong sense of the
# word, none above 0.50. Silence here is how retrieval fails without saying so.
make_mem "$tmp/mem-weak" '[{"chunk_hash":"a1","score":0.5000,"source":"/x/menu-ids.md","content":"Posições no depósito"},{"chunk_hash":"b2","score":0.4919,"source":"/x/deposit-card.md","content":"bank deposit matching"}]'
out=$(run "$tmp/mem-weak" "códigos de depósito")
echo "$out" | grep -qF "0.50 (weak)" || { echo "FAIL weak results -> no weak warning"; fail=1; }
echo "$out" | grep -qi "OTHER language" || { echo "FAIL weak results -> no other-language retry order"; fail=1; }
echo "$out" | grep -qF "a1" || { echo "FAIL weak results -> the hits themselves stopped printing"; fail=1; }
[ $fail -eq 0 ] && echo "ok   weak results -> warns and still prints the hits"

# --- no results at all: same warning ---------------------------------------
make_mem "$tmp/mem-none" '[]'
out=$(run "$tmp/mem-none" "nada")
echo "$out" | grep -qF "(no results)" || { echo "FAIL empty -> lost the '(no results)' line"; fail=1; }
echo "$out" | grep -qi "OTHER language" || { echo "FAIL empty -> no other-language retry order"; fail=1; }
echo "ok   no results -> keeps '(no results)' and adds the retry order"

# --- strong result: must stay quiet ----------------------------------------
# The warning is only useful because it is rare; firing it on a good hit would
# train the reader to ignore it.
make_mem "$tmp/mem-strong" '[{"chunk_hash":"c3","score":0.8100,"source":"/x/right.md","content":"the right answer"}]'
out=$(run "$tmp/mem-strong" "boa query")
echo "$out" | grep -qi "OTHER language" && { echo "FAIL strong result -> warned anyway (cries wolf)"; fail=1; }
echo "$out" | grep -qF "c3" || { echo "FAIL strong result -> hit not printed"; fail=1; }
echo "ok   strong result -> no warning"

# --- boundary: 0.50 is weak, just above it is not --------------------------
# The threshold must match `mem report`'s "weak/no match (top score <= 0.50)".
make_mem "$tmp/mem-edge" '[{"chunk_hash":"d4","score":0.5001,"source":"/x/edge.md","content":"just above"}]'
out=$(run "$tmp/mem-edge" "borda")
echo "$out" | grep -qi "OTHER language" && { echo "FAIL 0.5001 treated as weak (threshold drifted from mem report)"; fail=1; }
echo "ok   0.5001 -> not weak (matches mem report's <= 0.50)"

[ $fail -eq 0 ] && echo "ok   all mem checks passed" || exit 1
