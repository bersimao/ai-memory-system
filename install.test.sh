#!/usr/bin/env bash
# Self-check for install.sh.
#
# Case 5 is the one that matters most: a failed settings.json merge used to
# still print "pronto" and exit 0 -- the user would believe the system was
# installed while no hook was registered and nothing ever ran.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
SUT="$PWD/install.sh"
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
hooks_of() { python3 -c "
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print(-1); raise SystemExit
print(sum(len(m.get('hooks',[])) for ms in d.get('hooks',{}).values() for m in ms))" "$1"; }

# 1 — dry-run creates nothing.
h="$tmp/a"; CLAUDE_HOME="$h/.claude" "$SUT" --dry-run >/dev/null 2>&1
[ ! -d "$h/.claude" ] && ok "dry-run nao cria nada" || bad "dry-run criou arquivos"

# 2 — fresh install: files land, hooks registered, exit 0.
h="$tmp/b"; mkdir -p "$h"
CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1; rc=$?
n=$(hooks_of "$h/.claude/settings.json")
[ $rc -eq 0 ] && [ -f "$h/.claude/hooks/memory-inject.js" ] && [ "$n" = 4 ] \
  && ok "install limpo registra os 4 hooks" || bad "install limpo falhou (rc=$rc, hooks=$n)"

# 3 — idempotent.
CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1
[ "$(hooks_of "$h/.claude/settings.json")" = 4 ] \
  && ok "reinstalar nao duplica hook" || bad "reinstalar duplicou hooks"

# 4 — merge preserves the user's own hooks and unrelated settings.
h="$tmp/c"; mkdir -p "$h/.claude"
cat > "$h/.claude/settings.json" <<'JSON'
{"model":"opus","hooks":{"Stop":[{"hooks":[{"type":"command","command":"/bin/true meu-hook"}]}]}}
JSON
CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1
keep=$(grep -c 'meu-hook' "$h/.claude/settings.json")
model=$(python3 -c "import json;print(json.load(open('$h/.claude/settings.json')).get('model'))")
[ "$keep" -ge 1 ] && [ "$model" = opus ] && [ -f "$h/.claude/settings.json.bak-preinstall" ] \
  && ok "preserva hooks e settings do usuario, com backup" \
  || bad "merge perdeu config do usuario (hook=$keep model=$model)"

# 5 — REGRESSION: invalid settings.json must fail loudly, not silently.
h="$tmp/d"; mkdir -p "$h/.claude"
printf '{ nao e json' > "$h/.claude/settings.json"
CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1; rc=$?
intact=$(grep -c 'nao e json' "$h/.claude/settings.json")
[ $rc -ne 0 ] && [ "$intact" = 1 ] \
  && ok "settings.json invalido: recusa, preserva e sai != 0" \
  || bad "instalacao quebrada reportou sucesso (rc=$rc) — falha silenciosa"

# 6 — a hook already registered by absolute path must not be duplicated.
h="$tmp/e"; mkdir -p "$h/.claude"
cat > "$h/.claude/settings.json" <<JSON
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"\"/usr/bin/node\" \"$h/.claude/hooks/memory-inject.js\""}]}]}}
JSON
CLAUDE_HOME="$h/.claude" "$SUT" >/dev/null 2>&1
dup=$(grep -o 'memory-inject' "$h/.claude/settings.json" | wc -l)
[ "$dup" = 1 ] && ok "hook ja registrado por caminho absoluto nao duplica" \
  || bad "memory-inject registrado ${dup}x"

# 7 — REGRESSION: the instructions must actually land. Without them the system
# captures transcripts but never writes memory — half-alive, and silently so.
h="$tmp/f"; mkdir -p "$h"
CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1
g="$h/.claude/CLAUDE.md"
if [ -f "$g" ] && grep -q 'search before denying' "$g" \
   && [ -f "$h/.claude/skills/memory-write/SKILL.md" ]; then
  ok "--yes instala instrucoes e skill memory-write"
else
  bad "instrucoes/skill nao chegaram no install"
fi

# 8 — appending twice must not duplicate them.
CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1
n=$(grep -c 'memory-system:instructions' "$g")
[ "$n" = 2 ] && ok "reinstalar nao duplica as instrucoes" \
  || bad "instrucoes duplicadas (${n} marcadores, esperado 2)"

# 9 — an existing CLAUDE.md must survive, with a backup.
h="$tmp/g"; mkdir -p "$h/.claude"
printf '# meu\nnao pode sumir\n' > "$h/.claude/CLAUDE.md"
CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1
if grep -q 'nao pode sumir' "$h/.claude/CLAUDE.md" \
   && [ -f "$h/.claude/CLAUDE.md.bak-preinstall" ]; then
  ok "CLAUDE.md existente preservado, com backup"
else
  bad "install destruiu CLAUDE.md do usuario"
fi

# 10 — REGRESSION: without a terminal the installer must SKIP, never block.
# It first used `read </dev/tty`, then a bare `read`, which hung forever when
# stdin was an open-but-empty pipe -- and an installer that hangs is
# indistinguishable from one that crashed.
h="$tmp/h"; mkdir -p "$h"
out=$(timeout 90 env CLAUDE_HOME="$h/.claude" "$SUT" </dev/null 2>&1); rc=$?
if [ $rc -ne 124 ] && [ ! -f "$h/.claude/CLAUDE.md" ] && grep -q 'non-interactive' <<<"$out"; then
  ok "sem tty: pula sem travar e diz como obter"
else
  bad "sem tty o install travou ou escreveu mesmo assim (rc=$rc)"
fi

# 11 — com ~/.codex presente, as instrucoes vao para CLAUDE.md E AGENTS.md.
# Claude Code le CLAUDE.md, Codex le AGENTS.md: instalar so num deixa o outro
# agente com a maquinaria e sem as regras.
h="$tmp/i"; mkdir -p "$h/.codex"
printf '# Codex\nregra do usuario\n' > "$h/.codex/AGENTS.md"
HOME="$h" CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1
a=$(grep -c 'memory-system:instructions' "$h/.claude/CLAUDE.md" 2>/dev/null || echo 0)
b=$(grep -c 'memory-system:instructions' "$h/.codex/AGENTS.md" 2>/dev/null || echo 0)
if [ "$a" = 2 ] && [ "$b" = 2 ] && grep -q 'regra do usuario' "$h/.codex/AGENTS.md"; then
  ok "instala em CLAUDE.md e AGENTS.md, preservando o AGENTS.md do usuario"
else
  bad "alvo duplo falhou (CLAUDE.md=$a AGENTS.md=$b)"
fi

# 12 — sem ~/.codex nao inventa AGENTS.md.
h="$tmp/j"; mkdir -p "$h"
HOME="$h" CLAUDE_HOME="$h/.claude" "$SUT" --yes >/dev/null 2>&1
[ ! -e "$h/.codex" ] && ok "sem ~/.codex nao cria AGENTS.md" \
  || bad "criou .codex sem o usuario ter Codex"

[ $fails -eq 0 ] && { echo PASS; exit 0; } || { echo "FAIL ($fails)"; exit 1; }
