#!/usr/bin/env bash
# Weekly: prune stale entries, merge duplicates, consolidate related facts in
# - global ~/.claude/context/MEMORY.md (4000-char cap)
# - each ~/.claude/projects/<slug>/context/MEMORY.md whose daily log was
#   touched in the last 7 days (2500-char cap)
# USER.md is left alone (curated only on user request).
set -uo pipefail

LOG="$HOME/.memsearch/cron.log"
LLM_RUN="$HOME/.claude/scripts/llm-run"
# Per-store cap (optional context/.cap, default 2500) — shared with
# check-caps.sh and distill.sh so the alert and the enforcement agree.
. "$(dirname "${BASH_SOURCE[0]}")/store-cap.sh"

# Audit trail + veto. Curation must COMPRESS — same facts, fewer chars. A big
# shrink is deletion, not compression, and an unattended LLM running with
# --dangerously-skip-permissions does not get to delete memory unreviewed.
# Real incident: a project MEMORY.md went 6534 -> 2568 chars with no record.
# A synchronous human gate was rejected (it would freeze the cron), so: always
# log the delta, alert past CUT_ALERT, and roll back past CUT_VETO.
CUT_ALERT=30   # % shrink -> record it loudly
CUT_VETO=50    # % shrink -> restore from .bak, do not accept
# Round stamp. The vetoed artifact must NOT live in a fixed slot: the alert
# sits in the INBOX for weeks and is a command with a literal path. With a
# fixed slot, an old alert would point at ANOTHER round's proposal and
# accepting it would silently apply something the user never reviewed. With a
# stamp, the old alert's command fails (`cannot stat`) instead of hitting the
# wrong target.
STAMP="$(date +%Y%m%d-%H%M%S)"
# Optional notification channel. NOT required: cron.log always gets the record,
# so the audit trail survives with no Obsidian, no vault, no INBOX file.
[ -f "$HOME/.claude/data/memory.env" ] && . "$HOME/.claude/data/memory.env"
INBOX="${CLAUDE_MEM_INBOX:-}"
PROJECTS_ROOT="$HOME/.claude/projects"
GLOBAL_MEM="$HOME/.claude/context/MEMORY.md"

mkdir -p "$(dirname "$LOG")"

ts() { date -Iseconds; }

# Register a file for curation: back it up (.bak) and add a "- <path> (cap N)"
# line to the shared prompt list. Eligible files are curated in ONE LLM call.
TARGETS=""   # newline-separated "- <file> (cap <cap>)" lines for the prompt
SIZES=""     # newline-separated "<chars_before>|<cap>|<file>|<label>" for the audit

# Characters, not bytes — accented text inflates byte counts ~2%, and check-caps.sh
# measures chars. Two components disagreeing about "over cap" is its own bug.
chars() { python3 -c "import sys;print(len(open(sys.argv[1],encoding='utf-8',errors='replace').read()))" "$1" 2>/dev/null || echo 0; }

register() {
  local file="$1" cap="$2" label="$3"
  [ -f "$file" ] || { echo "skip $label: no file"; return; }
  cp -p "$file" "${file}.bak"
  # A vetoed proposal from a PREVIOUS round must not survive into this one:
  # the new alert points at the same path, and the user would accept a
  # `.rejected` that no longer matches what curate proposed now. Any
  # `.rejected` on disk must belong to the current round.
  for old in "${file}".rejected "${file}".rejected-*; do
    [ -e "$old" ] || continue
    echo "  discarding previous vetoed proposal, never reviewed: $old"
    rm -f "$old"
  done
  echo "register $label (cap $cap)"
  TARGETS="${TARGETS}- ${file} (cap ${cap})"$'\n'
  SIZES="${SIZES}$(chars "$file")|${cap}|${file}|${label}"$'\n'
}

# Compare every registered file against its .bak, log the delta, roll back a cut
# past CUT_VETO. Per file, not all-or-nothing: one gutted file must not discard
# good curation of the others. Echoes into the caller's cron.log block.
audit_and_veto() {
  local before cap file label after pct msg hint review rej
  ALERTS=""
  while IFS='|' read -r before cap file label; do
    [ -n "${file:-}" ] || continue
    after="$(chars "$file")"
    [ "$before" -gt 0 ] || continue
    pct=$(( (before - after) * 100 / before ))
    [ "$pct" -lt 0 ] && pct=0          # grew: fine, nothing to police
    if [ "$pct" -ge "$CUT_VETO" ]; then
      # Preserve the PROPOSAL before undoing it. Without this the alert is
      # unactionable: the file is safe but what curate wanted to do is gone, so
      # there is nothing to judge. Single slot, same convention as .bak; a later
      # veto on the same file overwrites it. NOT *.bak on purpose — distill.sh
      # sweeps those, and this has to survive until the user looks at it.
      # Curate may have DELETED the file rather than emptied it. Then there is
      # nothing to copy, and pointing `diff`/`cp` at a nonexistent `.rejected`
      # makes the alert instruct a command that fails (or, with a stale
      # artifact on disk, restores the WRONG proposal over good memory).
      rej="${file}.rejected-${STAMP}"
      if [ -f "$file" ]; then
        cp -p "$file" "$rej"
        # `accept` CONSUMES the artifact: without the `rm`, the `.rejected`
        # survives the acceptance and the command is not idempotent — if the
        # memory is edited later, running the same `cp` again (the alert is
        # still open in the INBOX) overwrites the new edits with the already-
        # accepted old version.
        review="Review: diff \"${file}\" \"${rej}\" | accept: cp \"${rej}\" \"${file}\" && rm \"${rej}\" | discard: rm \"${rej}\""
      else
        review="The proposal was to DELETE the file — there is nothing to review. If deleting really is right, do it by hand"
      fi
      cp -p "${file}.bak" "$file"
      # The restore returns the file to its original size. If THAT was already
      # over cap, compressing is not the way out: curate only knows how to
      # compress (loses content), and the right fix is to SPLIT into topics/
      # (loses nothing). Without this hint the cycle repeats weekly: curate
      # cuts, the veto restores, nothing improves.
      # The overflow exit depends on WHICH layer it is. A project store splits
      # into topics/ + an index line; the global MEMORY.md has no topics/ —
      # there the excess is usually reusable knowledge that belongs elsewhere.
      hint=""
      if [ "$before" -gt "$cap" ]; then
        case "$file" in
          */projects/*/context/MEMORY.md)
            hint=" NOTE: still ${before}/${cap} over cap. A cut this large almost always means SPLIT into topics/ + an index line (memory-write skill), not compress." ;;
          *)
            hint=" NOTE: still ${before}/${cap} over cap. The global layer has no topics/ — the overflow is usually reusable knowledge that belongs somewhere else (a skill, a doc), not something to compress away." ;;
        esac
      fi
      msg="CURATE VETO — ${label}: ${before} -> ${after} chars (-${pct}%), restored from backup. ${review}.${hint}"
      ALERTS="${ALERTS}${msg}"$'\n'
    elif [ "$pct" -ge "$CUT_ALERT" ]; then
      msg="large cut in curate — ${label}: ${before} -> ${after} chars (-${pct}%)"
      ALERTS="${ALERTS}${msg}"$'\n'
    else
      msg="ok ${label}: ${before} -> ${after} chars (-${pct}%)"
    fi
    echo "  $msg"
  done <<< "$SIZES"
}

{
  echo
  echo "=== [$(ts)] curate ==="

  register "$GLOBAL_MEM" 4000 "global"

  shopt -s nullglob
  for ctx in "$PROJECTS_ROOT"/*/context; do
    # The precondition is the FILE curate curates, not the daily-log directory.
    # Gating on `-d memory/` unconditionally excluded every store with a
    # MEMORY.md and no memory/ — 6 of them on 2026-08-26 — and none would ever
    # reach the activity gate. Same hole as the CLARK case, one level up and
    # worse: there the store became unreachable once over cap; here it was
    # born outside.
    [ -f "$ctx/MEMORY.md" ] || continue
    # Two gates, not one. The activity gate alone leaves a permanent hole: a
    # store that goes over cap and THEN goes quiet is never registered again,
    # so it is never split into topics/ — and pays the startup cost forever.
    # check-caps.sh sees those files (it has no gate) and warns that "curate
    # alone will not fix this"; that was literally true. Being over cap is a
    # sufficient condition by itself. (CLARK/addon-controle-qualidade, 2516
    # chars with its daily log idle since 07-23, was the case that exposed it.)
    cap=$(store_cap "$ctx")
    if [ -z "$(find "$ctx/memory" -maxdepth 1 -name '*.md' -mtime -7 -print -quit 2>/dev/null)" ] \
       && [ "$(chars "$ctx/MEMORY.md")" -le "$cap" ]; then
      continue
    fi
    register "$ctx/MEMORY.md" "$cap" "$(basename "$(dirname "$ctx")")"
  done

  if [ -z "$TARGETS" ]; then
    echo "nothing to curate"
  else
    # Single LLM call for all eligible files. Deletion/merge is judgment work →
    # smart tier (backend mapping lives in llm-run; never drift to a lesser model).
    prompt="Curate these memory files. For EACH file independently: (1) remove stale or resolved entries, (2) merge duplicate or near-duplicate facts, (3) consolidate related entries that can be expressed more compactly. Tighten the writing: make each entry more direct, precise and concise — that is the ONLY compression you should do. Do NOT delete content to fit the character cap: if the file is still over cap after tightening, that is fine and expected, because a separate step will split it into index notes without losing anything. Deleting a durable fact to hit a number is a bug, not curation. Preserve the section headings. Do NOT touch any content between '<!-- BEGIN skills:auto -->' and '<!-- END skills:auto -->' markers — that block is auto-generated by a script. ACROSS files: if facts in the listed files contradict each other, do not silently keep both — flag it by prepending a single line '⚠️ CONTRADICTION: <summary> (vs <other file>)' at the top of the affected file's relevant section, keeping the newer/more specific fact. If a file is already lean and has nothing to prune, leave it unchanged. Edit ONLY the files listed below — no other files.
${TARGETS}"
    "$LLM_RUN" smart "$prompt"; rc=$?
    [ "$rc" = 3 ] && echo "  quota/spend limit reached — curate did not run this week."
    [ "$rc" = 0 ] || echo "  curate failed (rc=$rc)"

    # Always runs, even if the LLM call failed (then every delta is 0%).
    echo "-- audit --"
    audit_and_veto

    # -- split -------------------------------------------------------------
    # File still over cap after the writing pass: split into `topics/` + an
    # index line, which is LOSSLESS. Before this, the only tool was compressing
    # (lossy), and that is how a store lost 3,966 chars with no record. The LLM
    # only returns the PLAN; the mover is split-memory.py, which verifies that
    # no line was lost and reverts if any was. "Keys, not prompts".
    echo "-- split --"
    while IFS='|' read -r before cap file label; do
      [ -n "${file:-}" ] || continue
      case "$file" in */projects/*/context/MEMORY.md) ;; *) continue ;; esac
      current="$(chars "$file")"
      [ "$current" -gt "$cap" ] || continue
      plan="$(mktemp)"
      splitprompt="Read ${file}. It is ${current} characters, over its ${cap}-character cap. Pick the whole '## ' section(s) whose removal would bring it under the cap with the least disruption — prefer sections that are self-contained detail rather than the index or short shared context. Write ONLY the file ${plan}, containing JSON and nothing else: {\"moves\":[{\"heading\":\"## exact heading text\",\"slug\":\"kebab-case-slug\",\"index_line\":\"- [Human Title](topics/kebab-case-slug.md) — one-line summary\"}]}. The heading must match a line in the file EXACTLY. The slug must be lowercase letters, digits and hyphens only. Do not edit ${file} or any other file — only write ${plan}."
      # "the LLM only returns the plan, it never writes to the store" was a
      # sentence in the PROMPT — and a prompt binds nothing (the lesson of that
      # whole day). With --dangerously-skip-permissions the model CAN write to
      # MEMORY.md. So: hash before, hash after. If it touched the file, undo
      # its edit and continue with the plan only — the mover stays the code.
      before_hash="$(sha256sum "$file" | cut -d" " -f1)"
      guard="$(mktemp)"; cp -p "$file" "$guard"
      "$LLM_RUN" cheap "$splitprompt"; src=$?
      if [ "$src" = 3 ]; then echo "  quota exhausted — split postponed"; rm -f "$plan" "$guard"; break; fi
      if [ "$(sha256sum "$file" | cut -d" " -f1)" != "$before_hash" ]; then
        cp -p "$guard" "$file"
        echo "  NOTE: the LLM edited ${label} despite the instruction — edit reverted, continuing with the plan only"
      fi
      rm -f "$guard"
      # Pass the hash of the CURRENT state: the split refuses if the file
      # changes between here and application (another session writing during
      # the cron).
      now_hash="$(sha256sum "$file" | cut -d" " -f1)"
      python3 "$HOME/.claude/cron/split-memory.py" "$file" "$cap" "$plan" "$now_hash" 2>&1 | sed 's/^/  /'
      rm -f "$plan"
    done <<< "$SIZES"
  fi

  # Deterministic post-step (NOT an LLM edit): regenerate the auto skill list in
  # global MEMORY.md from the skills on disk. Runs AFTER the LLM pass so the final
  # on-disk state is always correct regardless of what the model did to the file.
  # Optional: only present in installs that keep a skills/ tree.
  if [ -x "$HOME/.claude/cron/sync-skill-list.sh" ]; then
    "$HOME/.claude/cron/sync-skill-list.sh" || echo "  sync-skill-list failed"
  fi
} >>"$LOG" 2>&1

# Second channel, best-effort. The record already landed in cron.log above; this
# only surfaces it where the user actually looks. Absent INBOX = no alert here,
# never a lost audit trail.
# One checkbox PER alert: a single printf over the whole block collapsed every
# alert of the round into one checkbox with embedded newlines — broken list.
if [ -n "${ALERTS:-}" ] && [ -f "$INBOX" ]; then
  printf '%s\n' "$ALERTS" | head -20 | sed '/^$/d; s/^/- [ ] /' >>"$INBOX"
fi
