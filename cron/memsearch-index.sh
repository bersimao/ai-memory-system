#!/usr/bin/env bash
# Daily: re-index memory for memsearch, split by retrieval tier (CLAUDE.md).
#   memsearch_chunks      (default) = L1 — skill knowledge + curated memory
#   memsearch_transcripts           = L3 — raw dialogue, last resort
# Split, not score-weighted: transcripts outrank curated pages on raw vector
# similarity (they restate the question verbatim), so keeping them in the same
# collection buries the authoritative answer. Measured 2026-07-29: 13/15 top
# hits were transcripts and skills/ was not indexed at all.
set -uo pipefail
# Same root rule as distill.sh: the script lives inside the memory root, an
# explicit AI_MEMORY_HOME still wins. The lock MUST be the one memory-maintain.py
# reindex uses (<root>/data/memory-system/index.lock), or a custom-root install
# runs two indexers at once.
R="${AI_MEMORY_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
# Share the same lock as semantic retrieval and accepted-write indexing.
mkdir -p "$R/data/memory-system"
exec 9>"$R/data/memory-system/index.lock"
flock -w 300 9 || exit 1
# One memsearch DB per user account: only the root that owns it may index it,
# because `memsearch index` prunes every source it was not handed.
if ! /usr/bin/python3 -c 'import sys; sys.path.insert(0, sys.argv[1] + "/scripts"); from memory_core import semantic_db_owned_by; sys.exit(0 if semantic_db_owned_by(sys.argv[1]) else 3)' "$R"; then
  echo "memsearch-index: semantic DB belongs to another memory root (or memsearch missing); skipped" >&2
  exit 0
fi
shopt -s nullglob  # a project with no context/ must expand to nothing, not a literal glob

LOG="$HOME/.memsearch/cron.log"
mkdir -p "$(dirname "$LOG")"

ts() { date -Iseconds; }

rc=0
{
  echo
  echo "=== [$(ts)] memsearch-index ==="

  echo "--- L1: skills + curated memory -> memsearch_chunks"
  # skills/*/knowledge + references only: the whole skill tree also pulls in
  # SKILL.md (redundant — invocation loads it anyway), references-index.md pointer
  # files, and meta-skills (skill-creator/caveman/ponytail), which measurably ate
  # result slots: 3 of 15 top hits were index files, plus skill-creator/SKILL.md
  # ranking 5th for an unrelated WSL query.
  # The pre-central-store layout (projects/*/memory, no context/) is GONE as of
  # 2026-07-29 — the live stores were migrated into context/, the rest deleted.
  # If a */memory dir ever reappears it is NOT indexed; migrate it to context/.
  # context/*.md is MEMORY.md (the index); the pages it links to live in
  # context/topics/ since 2026-08-21 — both globs are needed.
  /usr/bin/python3 -m memsearch index \
    "$R"/skills/*/knowledge \
    "$R"/skills/*/references \
    "$R/context" \
    "$R"/projects/*/context/*.md \
    "$R"/projects/*/context/topics \
    "$R"/projects/*/context/memory \
    "$R"/projects/*/context/checkpoints || rc=1

  echo "--- L3: transcripts -> memsearch_transcripts"
  /usr/bin/python3 -m memsearch index -c memsearch_transcripts \
    "$R"/projects/*/context/transcripts || rc=1

  # Prune chunks whose source file no longer exists. memsearch only cleans stale
  # chunks for files it is handed, so a deleted or MOVED file leaves its chunks
  # behind forever — search then returns dead paths. (37 such chunks existed
  # after the 2026-07-29 legacy-store migration.)
  echo "--- prune: sources that no longer exist"
  /usr/bin/python3 - <<'PY' || rc=1
import os
from pymilvus import MilvusClient
from memsearch.config import resolve_config
from memsearch.store import _escape_filter_value
# The SAME database the ownership check above resolved — never a hardcoded path,
# or a root configured with its own DB would prune another root's default DB.
c = MilvusClient(uri=os.path.expanduser(resolve_config().milvus.uri))
for coll in ("memsearch_chunks", "memsearch_transcripts"):
    c.load_collection(coll)
    # Iterate instead of one query with a fixed limit: limit=16000 hid 80 of 377
    # transcript sources (25,042 rows) from this prune — 2026-09-19. The count
    # check below makes a short scan loud instead of silent.
    total = c.query(coll, filter='chunk_hash != ""', output_fields=["count(*)"])[0]["count(*)"]
    it = c.query_iterator(coll, batch_size=1000, filter='chunk_hash != ""', output_fields=["source"])
    seen, srcs = 0, set()
    while batch := it.next():
        seen += len(batch)
        srcs.update(r["source"] for r in batch)
    it.close()
    if seen != total:
        print(f"{coll}: WARNING scanned {seen} of {total} rows; prune may be incomplete")
    dead = [s for s in srcs if not os.path.exists(s)]
    for s in dead:
        c.delete(coll, filter=f'source == "{_escape_filter_value(s)}"')
    print(f"{coll}: pruned {len(dead)} dead sources")
c.close()
PY
} >>"$LOG" 2>&1
# An indexing failure must not hide behind a successful prune.
exit "$rc"
