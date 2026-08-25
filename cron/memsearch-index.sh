#!/usr/bin/env bash
# Daily: re-index memory for memsearch, split by retrieval tier (CLAUDE.md).
#   memsearch_chunks      (default) = L1 — skill knowledge + curated memory
#   memsearch_transcripts           = L3 — raw dialogue, last resort
# Split, not score-weighted: transcripts outrank curated pages on raw vector
# similarity (they restate the question verbatim), so keeping them in the same
# collection buries the authoritative answer. Measured 2026-07-29: 13/15 top
# hits were transcripts and skills/ was not indexed at all.
set -uo pipefail
shopt -s nullglob  # a project with no context/ must expand to nothing, not a literal glob

LOG="$HOME/.memsearch/cron.log"
mkdir -p "$(dirname "$LOG")"

ts() { date -Iseconds; }

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
    "$HOME"/.claude/skills/*/knowledge \
    "$HOME"/.claude/skills/*/references \
    "$HOME/.claude/context" \
    "$HOME"/.claude/projects/*/context/*.md \
    "$HOME"/.claude/projects/*/context/topics \
    "$HOME"/.claude/projects/*/context/memory

  echo "--- L3: transcripts -> memsearch_transcripts"
  /usr/bin/python3 -m memsearch index -c memsearch_transcripts \
    "$HOME"/.claude/projects/*/context/transcripts

  # Prune chunks whose source file no longer exists. memsearch only cleans stale
  # chunks for files it is handed, so a deleted or MOVED file leaves its chunks
  # behind forever — search then returns dead paths. (37 such chunks existed
  # after the 2026-07-29 legacy-store migration.)
  echo "--- prune: sources that no longer exist"
  /usr/bin/python3 - <<'PY'
import os
from pymilvus import MilvusClient
from memsearch.store import _escape_filter_value
c = MilvusClient(uri=os.path.expanduser("~/.memsearch/milvus.db"))
for coll in ("memsearch_chunks", "memsearch_transcripts"):
    c.load_collection(coll)
    srcs = {r["source"] for r in c.query(coll, filter='chunk_hash != ""',
                                        output_fields=["source"], limit=16000)}
    dead = [s for s in srcs if not os.path.exists(s)]
    for s in dead:
        c.delete(coll, filter=f'source == "{_escape_filter_value(s)}"')
    print(f"{coll}: pruned {len(dead)} dead sources")
c.close()
PY
} >>"$LOG" 2>&1
