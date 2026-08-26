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
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;   # non-interactive: append the instructions too
    *) echo "usage: $0 [--dry-run] [--yes]" >&2; exit 2 ;;
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
    # Um hook ja esta registrado se QUALQUER comando cita o mesmo script.
    registered = {
        os.path.basename(h.get('command', '').split('"')[-2] if '"' in h.get('command', '') else h.get('command', ''))
        for m in existing for h in m.get('hooks', [])
    }
    registered |= {n for m in existing for h in m.get('hooks', [])
                   for n in [s for s in h.get('command', '').split('/') if s.endswith('.js')]}
    novos = []
    for m in matchers:
        for h in m.get('hooks', []):
            script = h.get('command', '').split('/')[-1].strip('"')
            if script in registered:
                continue
            novos.append(h)
    if novos:
        existing.append({'hooks': novos})
        added += len(novos)

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target_path) or '.', suffix='.tmp')
with os.fdopen(fd, 'w', encoding='utf-8') as fh:
    json.dump(target, fh, indent=2, ensure_ascii=False)
    fh.write('\n')
os.replace(tmp, target_path)
print(f"  {added} hook(s) added; the ones already there were preserved")
PY
  merge_rc=$?
  # Falha aqui e a pior de todas: sem os hooks registrados nada dispara, e o
  # usuario fica achando que instalou. Nunca dizer "pronto" por cima disso.
  [ $merge_rc -ne 0 ] && say "  settings.json merge FAILED — no hooks were registered"
fi

# --- instructions --------------------------------------------------------
# Sem isto o sistema fica meio vivo: os hooks injetam e capturam, mas nada diz
# ao agente para ESCREVER memoria, respeitar os caps ou buscar antes de negar.
#
# Onde o agente le instrucoes depende do CLI: Claude Code usa CLAUDE.md, Codex
# usa AGENTS.md. Mesmo conteudo, destinos diferentes — instala nos dois quando a
# maquina tem os dois.
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
    # Sem terminal (CI, pipeline, chamado por outro script). NUNCA travar num
    # read que ninguem pode responder — instalador que trava e indistinguivel de
    # instalador que quebrou. Pula e diz como obter mesmo assim.
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

# --- verify --------------------------------------------------------------
say
say "self-checks"
rc=$merge_rc
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
