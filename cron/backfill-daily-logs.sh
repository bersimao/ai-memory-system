#!/usr/bin/env bash
# Phase 0 of the daily distill: for each project, turn any recent day's
# transcript into a daily log when the agent didn't write one.
# Session startup injects daily logs, never transcripts — without this,
# sessions that end without an agent-written log are invisible next morning.
# Called from distill.sh before the MEMORY.md pass; safe to run standalone.
set -uo pipefail

LOG="$HOME/.memsearch/cron.log"
LLM_RUN="$HOME/.claude/scripts/llm-run"
TODAY="$(date -I)"
PROJECTS_ROOT="$HOME/.claude/projects"
# 35d ~= Claude Code's own .jsonl retention: jsonl-to-transcript.py can only
# recover a day while Claude Code still holds it, so the window must cover it.
# Days that already have a log are skipped, so steady-state cost stays ~0.
WINDOW_DAYS=35

DRY=0; REDO=0; LIMIT=0; DONE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --redo)    REDO=1 ;;   # bypasses the guard: re-distills history
    --limit=*) LIMIT="${a#*=}" ;;
  esac
done

ts() { date -Iseconds; }

{
  echo
  echo "=== [$(ts)] backfill-daily-logs ==="

  cutoff="$(date -I -d "$TODAY - ${WINDOW_DAYS} days")"

  shopt -s nullglob
  for ctx in "$PROJECTS_ROOT"/*/context; do
    tdir="$ctx/transcripts"
    [ -d "$tdir" ] || continue
    proj="$(basename "$(dirname "$ctx")")"

    for t in "$tdir"/*.md; do
      day="$(basename "$t" .md)"
      case "$day" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
        *) continue ;;
      esac

      [ "$day" = "$TODAY" ] && continue      # still accumulating
      [[ "$day" < "$cutoff" ]] && continue   # outside window (ISO dates sort lexically)
      [ -s "$t" ] || continue                # empty transcript
      dlog="$ctx/memory/${day}.md"
      # The log EXISTING is not enough. Same bug as the transcript, one layer
      # up: an eager writer records a PARTIAL and the existence test locks out
      # the complete path forever. A log written mid-session does not cover the
      # rest of the day; and old logs were distilled from transcripts the Stop
      # hook had clipped to 8-43%. Decide by PROVENANCE + freshness:
      #   log newer than the transcript            -> nothing changed, skip
      #   backfill log (marker) and out of date    -> redo (its source was worse)
      #   agent-written log and out of date        -> EXTEND, preserve the text
      mode="create"
      if [ -s "$dlog" ]; then
        if [ "$REDO" = 0 ] && [ ! "$t" -nt "$dlog" ]; then
          continue
        fi
        if head -1 "$dlog" | grep -q '^<!-- auto-distilled'; then
          mode="redistill"
        else
          mode="extend"
        fi
      fi

      mkdir -p "$ctx/memory"
      echo "backfill[$mode] $proj $day"
      [ "$DRY" = 1 ] && continue
      if [ "$LIMIT" != 0 ] && [ "$DONE" -ge "$LIMIT" ]; then continue; fi
      DONE=$((DONE+1))

      prompt="The file ${t} holds timestamped excerpts from Claude Code sessions on ${day} in project ${proj} — one block per session stop, each with the user prompt (\`**user:**\`) and the assistant reply (\`**assistant:**\`), both possibly clipped. Older blocks may carry the assistant side only. Treat the user's own words as the authoritative statement of goals and decisions. Create the daily log ${dlog} with exactly this structure: first line '<!-- auto-distilled from transcript -->', then one '#### Session N' block per distinct work thread, each with lines '**Goal**:', '**Deliverables**:', '**Decisions**:', '**Open threads**:'. State only facts present in the transcript; where it does not say, write 'unclear'. Where the transcript shows the reasoning behind a decision — a measurement, a rejected alternative, a cause that was diagnosed — record it, not just the outcome: a log that says only what was done cannot tell a later session why. Keep the whole file under 2500 characters. Create ${dlog} only; do not edit any other file."

      if [ "$mode" = "extend" ]; then
        prompt="${prompt} IMPORTANT: ${dlog} already exists and was written by hand during the session — it is richer than anything you would produce, but it stops partway through the day. PRESERVE every line of it verbatim and only APPEND what the transcript shows happened afterwards, continuing the same '#### Session N' numbering. Never rewrite or condense what is already there."
      elif [ "$mode" = "redistill" ]; then
        prompt="${prompt} ${dlog} already exists but was distilled from a truncated transcript (the old Stop hook kept only 8-43% of the day). Replace it entirely using the fuller transcript now available."
      fi

      # 2500-char target (was 1500): the backfill writes ~52% of the daily
      # logs, and its logs had a median of 633 chars against 2,400 for the ones
      # the agent wrote in-session — they lost the reasoning behind decisions.
      # Reading the transcript (the expensive part, up to ~47k tokens) is
      # already paid for; raising the target costs a few hundred OUTPUT tokens
      # on the haiku tier. Measured on 08-24.
      # Structured extraction from a transcript is low-risk → cheap tier.
      "$LLM_RUN" cheap "$prompt"; rc=$?
      if [ "$rc" = 3 ]; then
        echo "  ABORTING the batch: quota/spend limit exhausted (llm-run exit 3)."
        echo "  Nothing to gain from trying the remaining days — all would die the same way."
        break 2
      fi
      [ "$rc" = 0 ] || echo "  backfill failed: $t"
    done
  done
} >>"$LOG" 2>&1
