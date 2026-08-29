#!/usr/bin/env bash
# Sync the memory system from the live install (~/.claude) into this repo.
#
# Why a script and not "copy the folder": ~/.claude also holds hundreds of
# files of per-client project memory and domain skills. Neither is part of
# the memory system and neither may reach a public repo. The manifest below is
# the release surface — nothing outside it ships, by construction.
#
# Why sync at all: two copies of the same file with no comparator diverge
# within a day of normal work.
#
#   ./sync-release.sh --check     show what differs (default)
#   ./sync-release.sh --from-home copy live -> repo
set -uo pipefail

SRC="${CLAUDE_HOME:-$HOME/.claude}"
DST="$(cd "$(dirname "$0")" && pwd)"

# The release surface. Anything not listed here does not ship.
MANIFEST=(
  hooks/memory-inject.js
  hooks/transcript-capture.js
  hooks/capture-maintenance.js
  hooks/daily-log-nudge.js
  hooks/daily-log-nudge.test.js
  hooks/project-store.js
  hooks/project-store.test.js
  hooks/memory-inject.test.js
  cron/curate.sh
  cron/curate-audit.test.sh
  cron/curate-gate.test.sh
  cron/split-memory.py
  cron/split-memory.test.py
  cron/distill.sh
  cron/backfill-daily-logs.sh
  cron/jsonl-to-transcript.py
  cron/jsonl-to-transcript.test.py
  cron/memsearch-index.sh
  cron/check-caps.sh
  cron/check-hooks.sh
  cron/check-mem-review.sh
  cron/backup-push.sh
  scripts/mem
  scripts/llm-run
  scripts/skill-grep
  scripts/skill-grep.test.sh
)

# Repo-native files (they do not come from ~/.claude) the guard also scans.
EXTRA_SCAN=(docs/memory-instructions.md docs/codex-support.md
            skills/memory-write/SKILL.md docs/assets/banner.svg)

mode="${1:---check}"
rc=0

for rel in "${MANIFEST[@]}"; do
  s="$SRC/$rel"; d="$DST/$rel"
  if [ ! -f "$s" ]; then
    printf 'MISSING at source %s\n' "$rel"; rc=1; continue
  fi
  case "$mode" in
    --check)
      if [ ! -f "$d" ]; then
        printf 'new              %s\n' "$rel"; rc=1
      elif ! cmp -s "$s" "$d"; then
        printf 'differs          %s\n' "$rel"; rc=1
      fi
      ;;
    --from-home)
      mkdir -p "$(dirname "$d")"
      cmp -s "$s" "$d" || { cp -p "$s" "$d"; printf 'copied           %s\n' "$rel"; }
      ;;
    *) echo "usage: $0 [--check|--from-home]" >&2; exit 2 ;;
  esac
done

# Client data / personal path tripwire: the release must never carry either.
if [ "$mode" = --from-home ]; then
  echo
  # Any literal home path, plus whoever is running this — the tripwire must not
  # be tuned to one machine's username, or it stops catching the next person's.
  #
  # The pattern is assembled from fragments on purpose. Spelled out inline it
  # would contain "<slash>home<slash>" literally, so the guard would match its
  # own source and could never scan itself — and "the leak checker is the one
  # file nobody checks" is exactly how a username shipped here in the first
  # place. Assembled, no fragment is a whole match, so self-scanning is clean.
  me=$(id -un); h='/home'; u='/Users'; m='/mnt'
  pat="${h}/[A-Za-z0-9._-]+|${u}/[A-Za-z0-9._-]+|${m}/[a-z]${u}/[A-Za-z0-9._-]+|\b${me}\b"

  # Scan the ENTIRE release surface, this script included — not just the code
  # dirs. README and the example configs ship too, and can leak just as well.
  scan=()
  for rel in "${MANIFEST[@]}" "${EXTRA_SCAN[@]}" sync-release.sh sync-release.test.sh \
             install.sh install.test.sh README.md CLAUDE.md settings.example.json \
             data/memory.env.example .gitignore; do
    [ -f "$DST/$rel" ] && scan+=("$DST/$rel")
  done
  # Strip the legitimate "$HOME" occurrences from the LINE before matching,
  # instead of discarding the whole line containing them: with `grep -v`, a
  # leaking line only had to mention $HOME in passing to escape the guard.
  #
  # ponytail: `\b${me}\b` catches leaks outside a path (author line, e-mail),
  # but anyone with a generic username ("dev", "test") will see false
  # positives. Accepted on purpose: the guard aborts loudly and shows the
  # line, and a leak guard that errs loudly beats one that errs silently.
  bad=""
  for f in "${scan[@]}"; do
    hit=$(sed 's/\$HOME//g; s/${HOME}//g' "$f" | grep -nE "$pat" 2>/dev/null) || true
    [ -n "$hit" ] && bad="${bad}${bad:+$'\n'}$(printf '%s\n' "$hit" | sed "s|^|$f:|")"
  done
  if [ -n "$bad" ]; then
    echo "ABORT: a personal path reached the release:"; echo "$bad"
    # Revert, not just warn. The warning scrolls off screen and a later
    # `git add -A` commits the leak anyway — that is exactly how ephemeral
    # state entered this project's repos before. A contaminated file goes back
    # to its committed state.
    printf '%s\n' "$bad" | cut -d: -f1 | sort -u | while read -r f; do
      rel="${f#$DST/}"
      # The right question is "is this regenerable?", NOT "is this tracked?".
      # Tracked vs untracked was the wrong axis: as soon as README.md and this
      # script are committed they become tracked, and a `git checkout --`
      # would throw away EVERY uncommitted change in the file — not just the
      # leaking line. A MANIFEST file is a copy of $SRC and comes back for
      # free; a hand-written file comes back from nowhere.
      if printf '%s\n' "${MANIFEST[@]}" | grep -qxF "$rel"; then
        if git -C "$DST" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
          git -C "$DST" checkout -- "$rel" && echo "  reverted       $rel"
        else
          rm -f "$f" && echo "  removed        $rel"
        fi
      else
        echo "  NOT reverted   $rel (hand-written — fix it and run again)"
      fi
    done
    rc=1
  else
    echo "tripwire: no personal path in the release ✔"
  fi
fi

[ "$mode" = --check ] && [ $rc -eq 0 ] && echo "repo up to date with $SRC ✔"
exit $rc
