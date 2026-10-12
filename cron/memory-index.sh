#!/usr/bin/env bash
# Daily: rebuild the memory indexes in data/memory-system/index.sqlite.
#   sdk          FTS5 over the offline SDK reference (after the generated pages)
#   curated      L1 - skill knowledge + curated memory, chunks + bge-m3 vectors
#   transcripts  L3 - raw dialogue, last resort
# Split, not score-weighted: transcripts outrank curated pages on raw vector
# similarity (they restate the question verbatim), so one collection would bury
# the authoritative answer (measured 2026-07-29).
set -uo pipefail
# Same root rule as distill.sh: the script lives inside the memory root, an
# explicit AI_MEMORY_HOME still wins.
R="${AI_MEMORY_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG="$R/logs/cron.log"
mkdir -p "$R/logs"
# RAG plan T1.1/T1.3: generated pages + FTS5 sdk index.
# A failure is logged and fails the run; it never skips the indexing below.
# The two generators are not part of the public release, so a root with NEITHER skips
# the sdk step. With only one of them the step still runs and fails: that is a broken
# install, not a release without the SDK reference.
pre_rc=0
if [ -f "$R/cron/build-system-map.py" ] || [ -f "$R/scripts/sdk-to-md.py" ]; then
  {
    AI_MEMORY_HOME="$R" /usr/bin/python3 "$R/cron/build-system-map.py" &&
    /usr/bin/python3 "$R/scripts/sdk-to-md.py" "$R" &&
    /usr/bin/python3 "$R/scripts/memory-index.py" --root "$R" --collection sdk
  } >>"$LOG" 2>&1
  pre_rc=$?
fi
# RAG plan T3.2: chunks + bge-m3 vectors in index.sqlite (the `sqlite` engine). Daily, not
# post-write (user, 2026-09-27): the lexical leg reads files live, so same-day pages are found anyway.
# Transcripts too: the day's file is re-chunked as it grows, ~500 chunks / ~5-10 min a day; the
# first full run was 23.5k chunks / 5 h, done by hand. One alert per collection.
# Capped: an unbudgeted embed batch asked for a single 4 GiB buffer, and a runaway indexer is how the
# WSL went OOM on 2026-09-27. Own alert, because nothing else reads cron.log.
. "$R/cron/alert.sh"
for coll in curated transcripts; do
  if ( ulimit -v 10000000; timeout 2h /usr/bin/python3 "$R/scripts/memory-index.py" --root "$R" --collection "$coll" ) \
      >>"$LOG" 2>&1; then
    alert_ok "SQLITE-INDEX ALERT $coll" "$coll reindexado e embutido" || pre_rc=1
  else
    alert "SQLITE-INDEX ALERT $coll" "memory-index.py --collection $coll falhou (exit $?); vetores do motor sqlite parados. Ver $LOG" || true
    pre_rc=1
  fi
done
exit "$pre_rc"
