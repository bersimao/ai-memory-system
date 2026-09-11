#!/usr/bin/env bash
# Shared: the character cap for one project store's context/MEMORY.md.
#
# Sourced by check-caps.sh (the alert), curate.sh (the eligibility gate) and
# distill.sh (the prompt). It lives in one file because the parser below is
# subtle enough that three copies would drift, and a drifted copy fails in the
# one direction that matters: silently ALLOWING a bigger file.
#
# Why a per-store cap at all: one number for ~30 stores was the mismatch. A
# single-demand client repo and a multi-subject store like claude-mem (9 topic
# pages, pipeline, engine decision, environment quirks) got the same budget,
# and ~900 of those chars are the topic INDEX alone — which grows with the
# number of pages. Trimming prose cannot fix that: measured 2026-08-31, nine
# wording edits recovered ~80 chars; only removing whole entries worked.
#
# Every rejection path returns the DEFAULT, which is the stricter value. A typo
# must never widen or disable a cap; raising one stays a deliberate act, a file
# somebody had to create.
DEFAULT_PROJECT_CAP=2500

# store_cap <context-dir> -> the cap for that store's MEMORY.md
store_cap() {
  local c
  # Test for the file FIRST: a failed input redirection is reported by the
  # shell itself, before any `2>/dev/null` on the command applies, so the
  # missing-file case (the overwhelming majority of stores) would print to
  # stderr on every run and spam the cron log daily.
  [ -f "$1/.cap" ] || { echo "$DEFAULT_PROJECT_CAP"; return; }

  # Validate the raw BYTES before any command substitution. `$(cat …)` silently
  # DROPS NUL bytes (with a warning on stderr), so "9<NUL>000" would arrive as
  # the string "9000" and sail through the regex below — the splice defect
  # reopened through a channel the regex cannot see. `wc -c` rather than
  # comparing text, because a count survives `$( )` intact; the offending bytes
  # would not.
  [ "$(LC_ALL=C tr -d '0-9[:space:]' <"$1/.cap" 2>/dev/null | wc -c)" -eq 0 ] || {
    echo "$DEFAULT_PROJECT_CAP"; return; }

  c=$(cat "$1/.cap" 2>/dev/null)
  # The WHOLE file must be exactly one integer. Deleting whitespace instead
  # spliced any whitespace-separated digits into a single larger number:
  # "4000\n5000" became 40005000 and "9 000" became 9000. Surrounding
  # whitespace stays fine, so an editor's trailing newline (or a CRLF from the
  # Windows side) is not an error.
  [[ "$c" =~ ^[[:space:]]*([0-9]{1,9})[[:space:]]*$ ]] || {
    echo "$DEFAULT_PROJECT_CAP"; return; }
  # {1,9} also bounds the magnitude: a 20-digit value made `[ -ge ]` abort with
  # "integer expected" on stderr, which these cron jobs must never emit.
  c=$((10#${BASH_REMATCH[1]}))   # 10# so "04000" is not read as octal
  [ "$c" -ge 1 ] && echo "$c" || echo "$DEFAULT_PROJECT_CAP"
}
