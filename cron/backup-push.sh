#!/usr/bin/env bash
# Daily safety-net backup: commit + push ~/.claude so a skipped /end-of-day
# still gets backed up at the next WSL start (anacron catch-up, via distill.sh).
# /end-of-day remains the primary sync — this only fires when something was
# left uncommitted, and pushes any unpushed commits either way.
# Limitation (accepted): runs only while WSL is up; a machine that stays off
# keeps its last-push state until the next boot.
set -uo pipefail

LOG="$HOME/.memsearch/cron.log"
REPO="$HOME/.claude"

{
  echo
  echo "=== [$(date -Iseconds)] backup-push ==="
  cd "$REPO" || { echo "repo missing"; exit 1; }
  # Optional by design: an install that never made ~/.claude a git repo (or
  # gave it no remote) gets ONE quiet line, not a nightly error. Setting up the
  # backup repo is the user's call — see the README.
  git rev-parse --git-dir >/dev/null 2>&1 \
    || { echo "~/.claude is not a git repo — backup disabled (git init + add a remote to enable)"; exit 0; }
  git remote get-url origin >/dev/null 2>&1 \
    || { echo "~/.claude has no 'origin' remote — backup disabled (add one to enable)"; exit 0; }
  branch="$(git branch --show-current)"

  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "🔧 chore: Backup automático diário (catch-up anacron)" --quiet \
      && echo "committed $(git log -1 --format=%h)"
  else
    echo "clean tree"
  fi

  # Push regardless — covers commits left unpushed by an interrupted sync.
  git push origin "$branch" 2>&1 | tail -1 || echo "push failed (offline?) — will retry next run"
} >>"$LOG" 2>&1
