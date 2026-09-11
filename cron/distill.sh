#!/usr/bin/env bash
# Daily: for each project whose daily log was touched in the last 7 days,
# extract durable facts from today's log into that project's MEMORY.md.
# Global ~/.claude/context/ has no daily log, so it is not distilled here
# (curate.sh prunes the global file weekly).
set -uo pipefail

LOG="$HOME/.memsearch/cron.log"
LLM_RUN="$HOME/.claude/scripts/llm-run"
# Per-store cap (optional context/.cap, default 2500) — shared with
# check-caps.sh and curate.sh so the prompt cannot state a cap the tripwire
# disagrees with.
. "$(dirname "${BASH_SOURCE[0]}")/store-cap.sh"
TODAY="$(date -I)"
PROJECTS_ROOT="$HOME/.claude/projects"

mkdir -p "$(dirname "$LOG")"

# Phase 0: transcripts → daily logs for days the agent never logged.
# Must run first — both the fallback below and session startup read its output.
# Recover days the Stop hook never captured, straight from Claude Code's own
# .jsonl, BEFORE backfill runs — it only distills days that have a transcript.
python3 "$HOME/.claude/cron/jsonl-to-transcript.py" || true
"$HOME/.claude/cron/backfill-daily-logs.sh"

# Tripwire: warn (cron.log + Obsidian INBOX) if the memory hooks ever drop out
# of settings.json again. Non-fatal — distill proceeds regardless.
"$HOME/.claude/cron/check-hooks.sh" || true

# Tripwire: the per-layer char caps live only as PROMPT text (here and in
# curate.sh), so nothing binds an interactive session writing memory directly.
# Measure them for real. Non-fatal, reporting only — never edits a memory file.
"$HOME/.claude/cron/check-caps.sh" || true

# Optional: only exists in installs using the Codex plugin. Detects that a
# `claude plugin update` swept away the stop-gate limit patch.
[ -x "$HOME/.claude/cron/check-codex-gate.sh" ] && \
  "$HOME/.claude/cron/check-codex-gate.sh" || true

ts() { date -Iseconds; }

{
  echo
  echo "=== [$(ts)] distill ==="

  shopt -s nullglob
  for ctx in "$PROJECTS_ROOT"/*/context; do
    [ -d "$ctx/memory" ] || continue

    # Activity gate: any daily log modified in last 7 days?
    if [ -z "$(find "$ctx/memory" -maxdepth 1 -name '*.md' -mtime -7 -print -quit)" ]; then
      continue
    fi

    # Anacron fires near boot, before today's log exists — fall back to
    # yesterday's (often just backfilled). Prompt is dedup-guarded, so a rare
    # second pass over the same log is harmless.
    log_file="$ctx/memory/${TODAY}.md"
    [ -s "$log_file" ] || log_file="$ctx/memory/$(date -I -d "$TODAY - 1 day").md"
    mem_file="$ctx/MEMORY.md"

    [ -s "$log_file" ] || { echo "skip $(basename "$(dirname "$ctx")"): no recent log"; continue; }
    [ -f "$mem_file" ] || { echo "skip $(basename "$(dirname "$ctx")"): no MEMORY.md"; continue; }

    # Backup before edit (atomic, single .bak slot).
    cp -p "$mem_file" "${mem_file}.bak"

    echo "distill $(basename "$(dirname "$ctx")")"

    cap=$(store_cap "$ctx")
    prompt="Read the daily log at ${log_file} and the working memory at ${mem_file}. Extract durable facts from the log (URLs, decisions, preferences, project structure, gotchas) that are NOT already in ${mem_file}. Append them under the appropriate section in ${mem_file}. Enforce the ${cap}-char cap on ${mem_file} — if appending would exceed it, consolidate existing entries first. If nothing durable is found, leave ${mem_file} unchanged. Do not edit any other file."

    # Append-only extract is low-risk → cheap tier (backend mapping lives in llm-run).
    "$LLM_RUN" cheap "$prompt"; rc=$?
    if [ "$rc" = 3 ]; then
      echo "  ABORTING: quota/spend limit exhausted — skipping the remaining projects."
      break
    fi
    [ "$rc" = 0 ] || echo "  distill failed for $ctx"
  done
} >>"$LOG" 2>&1

# Retrieval-review nudge: counts real memsearch queries, alerts once at threshold.
# Before the backup, so a raised alert is part of today's pushed state.
"$HOME/.claude/cron/check-mem-review.sh" || true

# Safety-net backup AFTER the distill pass, so the pushed state includes today's
# distilled MEMORY.md edits. Logs itself; never blocks.
"$HOME/.claude/cron/backup-push.sh" || true

# Drop the *.bak snapshots (curate.sh:25, distill.sh:49) — but ONLY once git
# actually holds the content. They exist to undo an LLM edit BEFORE it is
# committed, which git cannot do at that moment: backup-push runs at the tail, so
# during the edit the newest commit is from the PREVIOUS run.
# backup-push always exits 0 (it swallows push errors), so its status proves
# nothing — test the end state instead. Uncommitted work, unpushed commits, or no
# upstream => keep the snapshots.
if [ -z "$(git -C "$HOME/.claude" status --porcelain)" ] \
   && [ "$(git -C "$HOME/.claude" rev-list --count @{u}..HEAD 2>/dev/null || echo 1)" = "0" ]; then
  find "$HOME/.claude" -name '*.bak' -not -path '*/.git/*' -delete
  echo "[$(date -Iseconds)] pruned *.bak (tree clean + pushed)" >>"$LOG"
fi
