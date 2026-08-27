#!/usr/bin/env bash
# Install the memory system into ~/.claude.
#
#   ./install.sh              install (or update an existing install)
#   ./install.sh --dry-run    show what would change, touch nothing
#
# Safe to re-run. The settings.json merge adds only hooks that are missing, so
# re-running never duplicates them and never drops hooks you already had.
set -uo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
DRY=0
ASSUME_YES=0
WANT_CRON=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;   # non-interactive: append the instructions too
    --cron)    WANT_CRON=1 ;;    # non-interactive: register the cron jobs too
    *) echo "usage: $0 [--dry-run] [--yes] [--cron]" >&2; exit 2 ;;
  esac
done
say() { printf '%s\n' "$*"; }
run() { [ $DRY -eq 1 ] && say "  [dry-run] $*" || eval "$*"; }

# --- dependencies --------------------------------------------------------
missing=""
for dep in node python3 git; do
  command -v "$dep" >/dev/null 2>&1 || missing="$missing $dep"
done
if [ -n "$missing" ]; then
  say "MISSING:$missing"
  say "node runs the hooks, python3 runs the cron scripts, git anchors the per-repo store."
  exit 1
fi
say "dependencies ok: node $(node -v), python3 $(python3 -V 2>&1 | cut -d' ' -f2)"
# Optional on purpose. Saying it here avoids the worse discovery: installing,
# using it for days, and only then hitting "search failed" with no idea a
# package was missing.
if python3 -c 'import memsearch' >/dev/null 2>&1; then
  say "optional: memsearch found — vector search tier available"
else
  say "optional: memsearch not installed — grep tier works, fuzzy search does not."
  say "          enable later with: pip install memsearch && ~/.claude/cron/memsearch-index.sh"
fi

# --- files ---------------------------------------------------------------
say
say "installing into $DEST"
for d in hooks cron scripts data; do
  run "mkdir -p '$DEST/$d'"
done
if [ -d "$SRC/skills/memory-write" ]; then
  run "mkdir -p '$DEST/skills/memory-write'"
  run "cp -p '$SRC/skills/memory-write/SKILL.md' '$DEST/skills/memory-write/SKILL.md'"
  say "  skills/memory-write/SKILL.md"
fi
for d in hooks cron scripts; do
  for f in "$SRC/$d"/*; do
    [ -f "$f" ] || continue
    b="$(basename "$f")"
    if [ -f "$DEST/$d/$b" ] && cmp -s "$f" "$DEST/$d/$b"; then continue; fi
    run "cp -p '$f' '$DEST/$d/$b'"
    say "  ${d}/${b}"
  done
done
[ -f "$DEST/data/memory.env" ] || run "cp '$SRC/data/memory.env.example' '$DEST/data/memory.env'"

# --- settings.json -------------------------------------------------------
say
say "registering hooks in $DEST/settings.json"
merge_rc=0
if [ $DRY -eq 1 ]; then
  say "  [dry-run] merge settings.example.json (only what is missing)"
else
  python3 - "$SRC/settings.example.json" "$DEST/settings.json" <<'PY'
import json, os, sys, shutil, tempfile

example_path, target_path = sys.argv[1], sys.argv[2]
example = json.load(open(example_path, encoding='utf-8'))

target = {}
if os.path.exists(target_path):
    try:
        target = json.load(open(target_path, encoding='utf-8'))
    except Exception as e:
        sys.exit(f"settings.json exists but is not valid JSON ({e}). "
                 f"Fix or move it before installing — I will not overwrite it.")
    shutil.copy2(target_path, target_path + '.bak-preinstall')

hooks = target.setdefault('hooks', {})
added = 0
for event, matchers in example.get('hooks', {}).items():
    existing = hooks.setdefault(event, [])
    # A hook counts as registered if ANY command mentions the same script.
    registered = {
        os.path.basename(h.get('command', '').split('"')[-2] if '"' in h.get('command', '') else h.get('command', ''))
        for m in existing for h in m.get('hooks', [])
    }
    registered |= {n for m in existing for h in m.get('hooks', [])
                   for n in [s for s in h.get('command', '').split('/') if s.endswith('.js')]}
    new = []
    for m in matchers:
        for h in m.get('hooks', []):
            script = h.get('command', '').split('/')[-1].strip('"')
            if script in registered:
                continue
            new.append(h)
    if new:
        existing.append({'hooks': new})
        added += len(new)

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target_path) or '.', suffix='.tmp')
with os.fdopen(fd, 'w', encoding='utf-8') as fh:
    json.dump(target, fh, indent=2, ensure_ascii=False)
    fh.write('\n')
os.replace(tmp, target_path)
print(f"  {added} hook(s) added; the ones already there were preserved")
PY
  merge_rc=$?
  # This failure is the worst of all: with no hooks registered nothing fires,
  # and the user walks away believing it is installed. Never say "done" over it.
  [ $merge_rc -ne 0 ] && say "  settings.json merge FAILED — no hooks were registered"
fi

# --- instructions --------------------------------------------------------
# Without this the system is half-alive: the hooks inject and capture, but
# nothing tells the agent to WRITE memory, honor the caps, or search before
# denying.
#
# Where the agent reads instructions depends on the CLI: Claude Code uses
# CLAUDE.md, Codex uses AGENTS.md. Same content, different targets — install
# into both when the machine has both.
say
MARK="<!-- memory-system:instructions -->"

GUIDES="$DEST/CLAUDE.md"
[ -d "$HOME/.codex" ] && GUIDES="$GUIDES $HOME/.codex/AGENTS.md"

append_instructions() {
  guide="$1"
  [ -f "$guide" ] && cp -p "$guide" "$guide.bak-preinstall"
  {
    printf '\n%s\n' "$MARK"
    sed '1,/^---$/d' "$SRC/docs/memory-instructions.md"
    printf '%s\n' "<!-- /memory-system:instructions -->"
  } >>"$guide"
  say "  appended to $guide"
}

pending=""
for g in $GUIDES; do
  if [ -f "$g" ] && grep -qF "$MARK" "$g"; then
    say "instructions already present in $g"
  else
    pending="$pending $g"
  fi
done

if [ -z "$pending" ]; then
  :
elif [ $DRY -eq 1 ]; then
  for g in $pending; do say "  [dry-run] append memory instructions to $g"; done
else
  say "The agent also needs the instructions that make it USE this system"
  say "(layers, caps, split-don't-compress, the retrieval ladder)."
  for g in $pending; do say "  target: $g"; done
  if [ $ASSUME_YES -eq 1 ]; then
    answer=y
  elif [ -t 0 ]; then
    say "Append them now? A backup is kept. [y/N]"
    read -r answer || answer=n
  else
    # No terminal (CI, pipeline, called from another script). NEVER hang on a
    # read nobody can answer — an installer that hangs is indistinguishable
    # from one that crashed. Skip, and say how to get them anyway.
    answer=n
    say "  (non-interactive: skipping. Re-run with --yes to append them.)"
  fi
  case "$answer" in
    [yY]*) for g in $pending; do append_instructions "$g"; done ;;
    *)
      say "  skipped. Paste docs/memory-instructions.md into the file(s) above —"
      say "  without it the system captures transcripts but never writes memory."
      ;;
  esac
fi

# --- scheduling ----------------------------------------------------------
# The hooks fire around sessions; the daily distill and the weekly curation
# need a scheduler. Without this section the README promised maintenance that
# a fresh install never ran — the cron/ scripts were copied and then sat there.
#
# Consent is separate from --yes on purpose: these jobs run `claude -p`
# headless with --dangerously-skip-permissions on files under ~/.claude, and
# they spend the user's own Claude usage. That is opted into explicitly
# (interactive y/N, or the --cron flag), never as a side effect of --yes.
say
CRON_MARK="# memory-system"
cron_rc=0
# Warn whenever registered jobs sit on a dead daemon — not only on a fresh
# registration. WSL and minimal containers often have crontab but no cron.
warn_if_no_daemon() {
  if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
    say "  NOTE: no cron daemon is running — the jobs are registered but will"
    say "  NOT fire. On WSL: sudo service cron start, and enable systemd (or"
    say "  start it per boot) so they actually run."
  fi
}
if ! command -v crontab >/dev/null 2>&1; then
  say "crontab not found — schedule these two yourself (cron, anacron, or a systemd timer):"
  say "  daily:  $DEST/cron/distill.sh    (transcripts -> daily logs -> memory, plus the tripwires)"
  say "  weekly: $DEST/cron/curate.sh     (tightens the memory files, with the veto)"
# Check each job, not just the marker: with a marker-only test, a crontab
# holding one of the two jobs read as "already registered" and the other job
# was silently never added — maintenance half-alive, reported as complete.
elif cur="$(crontab -l 2>/dev/null || true)" \
     && grep -qF "$CRON_MARK:distill" <<<"$cur" \
     && grep -qF "$CRON_MARK:curate" <<<"$cur"; then
  say "cron jobs already registered (see: crontab -l | grep memory-system)"
  warn_if_no_daemon
else
  say "Scheduled maintenance (runs the claude CLI headless — this spends YOUR Claude usage):"
  say "  daily  12:15       $DEST/cron/distill.sh"
  say "  weekly Mon 12:45   $DEST/cron/curate.sh"
  if [ $DRY -eq 1 ]; then
    say "  [dry-run] register the two crontab entries"
  else
    if [ $WANT_CRON -eq 1 ]; then
      answer=y
    elif [ -t 0 ]; then
      say "Register them in your crontab now? [y/N]"
      read -r answer || answer=n
    else
      answer=n
      say "  (non-interactive: skipping. Re-run with --cron to register them.)"
    fi
    case "$answer" in
      [yY]*)
        # Read first, then write: `crontab -l | ... | crontab -` reads and
        # replaces the same store in one pipeline, which is a race. Add only
        # the entries that are missing, so a partial registration is completed
        # rather than duplicated.
        cur="$(crontab -l 2>/dev/null || true)"
        { [ -n "$cur" ] && printf '%s\n' "$cur"
          grep -qF "$CRON_MARK:distill" <<<"$cur" \
            || echo "15 12 * * * $DEST/cron/distill.sh $CRON_MARK:distill"
          grep -qF "$CRON_MARK:curate" <<<"$cur" \
            || echo "45 12 * * 1 $DEST/cron/curate.sh $CRON_MARK:curate"
        } | crontab - && say "  registered (edit times with crontab -e; remove with: crontab -l | grep -v memory-system | crontab -)" \
          || { say "  crontab registration FAILED"; cron_rc=1; }
        # Verify the end state, not the exit code: both jobs must actually be
        # in the crontab now, or the install is incomplete and must say so.
        after="$(crontab -l 2>/dev/null || true)"
        if ! grep -qF "$CRON_MARK:distill" <<<"$after" || ! grep -qF "$CRON_MARK:curate" <<<"$after"; then
          say "  cron entries missing after registration — schedule by hand (see README)"
          cron_rc=1
        fi
        warn_if_no_daemon
        ;;
      *)
        say "  skipped. Without a schedule the daily distill and weekly curation"
        say "  never run — transcripts pile up but the memory files stay stale."
        say "  Enable later: ./install.sh --cron"
        ;;
    esac
  fi
fi

# --- verify --------------------------------------------------------------
say
say "self-checks"
rc=$((merge_rc + cron_rc))
for t in "node $DEST/hooks/project-store.test.js" \
         "bash $DEST/cron/curate-audit.test.sh" \
         "python3 $DEST/cron/split-memory.test.py"; do
  name="$(basename "${t##* }")"
  if [ $DRY -eq 1 ]; then say "  [dry-run] $name"; continue; fi
  if $t >/dev/null 2>&1; then say "  ok   $name"; else say "  FAILED $name"; rc=1; fi
done

say
if [ $rc -eq 0 ]; then
  say "done. Open a Claude Code session in any git repo —"
  say "the store is created at $DEST/projects/<repo>/context/ on first use."
  say "Optional config (INBOX path): $DEST/data/memory.env"
else
  say "installation INCOMPLETE — see the error above. The hooks may not be"
  say "registered, and without them nothing in the memory system runs."
fi
exit $rc
