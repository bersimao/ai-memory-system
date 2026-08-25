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
[ "${1:-}" = "--dry-run" ] && DRY=1
say() { printf '%s\n' "$*"; }
run() { [ $DRY -eq 1 ] && say "  [dry-run] $*" || eval "$*"; }

# --- dependencies --------------------------------------------------------
missing=""
for dep in node python3 git; do
  command -v "$dep" >/dev/null 2>&1 || missing="$missing $dep"
done
if [ -n "$missing" ]; then
  say "FALTA:$missing"
  say "node roda os hooks, python3 os scripts de cron, git ancora o store por repo."
  exit 1
fi
say "dependencias ok: node $(node -v), python3 $(python3 -V 2>&1 | cut -d' ' -f2)"

# --- files ---------------------------------------------------------------
say
say "instalando em $DEST"
for d in hooks cron scripts data; do
  run "mkdir -p '$DEST/$d'"
done
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
say "registrando os hooks em $DEST/settings.json"
merge_rc=0
if [ $DRY -eq 1 ]; then
  say "  [dry-run] merge de settings.example.json (só o que faltar)"
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
        sys.exit(f"settings.json existe mas nao e JSON valido ({e}). "
                 f"Corrija ou mova antes de instalar — nao vou sobrescrever.")
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
print(f"  {added} hook(s) adicionado(s); os que ja existiam foram preservados")
PY
  merge_rc=$?
  # Falha aqui e a pior de todas: sem os hooks registrados nada dispara, e o
  # usuario fica achando que instalou. Nunca dizer "pronto" por cima disso.
  [ $merge_rc -ne 0 ] && say "  merge de settings.json FALHOU — nenhum hook foi registrado"
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
  if $t >/dev/null 2>&1; then say "  ok   $name"; else say "  FALHOU $name"; rc=1; fi
done

say
if [ $rc -eq 0 ]; then
  say "pronto. Abra uma sessao do Claude Code em qualquer repo git —"
  say "o store nasce em $DEST/projects/<repo>/context/ no primeiro uso."
  say "Config opcional (caminho de INBOX): $DEST/data/memory.env"
else
  say "instalacao INCOMPLETA — veja o erro acima. Os hooks podem nao estar"
  say "registrados, e sem eles nada do sistema de memoria roda."
fi
exit $rc
