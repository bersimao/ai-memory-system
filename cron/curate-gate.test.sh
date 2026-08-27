#!/usr/bin/env bash
# Self-check for curate.sh's eligibility gate. No LLM, no cron.
# Run: bash ~/.claude/cron/curate-gate.test.sh
#
# O que trava aqui: a porta de atividade sozinha deixava um store acima do cap
# invisível para sempre se o projeto ficasse quieto ([[2026-08-26]]).
set -uo pipefail

SRC="$(dirname "$0")/curate.sh"

# Extrai chars() + o laço de elegibilidade. Guarda contra extração vazia — uma
# sonda vazia parece exatamente uma que passou (lição de 2026-08-23).
code="$(sed -n '/^chars() {/p;/^  for ctx in "\$PROJECTS_ROOT"/,/^  done$/p' "$SRC")"
[ -n "$code" ] || { echo "FAIL: extração vazia de $SRC"; exit 1; }
grep -q 'for ctx in' <<<"$code" || { echo "FAIL: não extraiu o laço"; exit 1; }
grep -q '^chars() {' <<<"$code" || { echo "FAIL: não extraiu chars()"; exit 1; }
grep -q 'register' <<<"$code" || { echo "FAIL: laço extraído não chama register"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PROJECTS_ROOT="$T/projects"
REGISTERED=""
register() { REGISTERED="${REGISTERED}$3"$'\n'; }   # stub: só anota o label
[ "$(bash -c "$(sed -n '/^chars() {/p' "$SRC"); chars '$SRC'")" -gt 100 ] \
  || { echo "FAIL: chars() extraída não mede nada"; exit 1; }

mk() { # <label> <chars> <idade-do-log-em-dias | "sem-log">
  local d="$PROJECTS_ROOT/$1/context"
  mkdir -p "$d"
  head -c "$2" /dev/zero | tr '\0' 'a' >"$d/MEMORY.md"   # ASCII: chars == bytes
  [ "$3" = sem-log ] && return 0
  mkdir -p "$d/memory"
  : >"$d/memory/2026-01-01.md"
  touch -d "$3 days ago" "$d/memory/2026-01-01.md"
}

mk ativo-sob-cap    2000 1
mk ativo-sobre-cap  2800 1
mk quieto-sobre-cap 2800 40   # <- o caso CLARK: o buraco que o fix fecha
mk quieto-sob-cap   2000 40
# Sem memory/ nenhum: a porta de atividade NUNCA pode ser satisfeita, então o
# cap é a única entrada. Guardar o laço por `-d memory/` matava esses stores
# antes de qualquer porta (achado pelo gate do Codex, [[2026-08-26]]).
mk sem-log-sobre-cap 2800 sem-log
mk sem-log-sob-cap   2000 sem-log
# Store com daily log e SEM MEMORY.md: não há o que curar, e não pode explodir.
mkdir -p "$PROJECTS_ROOT/sem-memoria/context/memory"
: >"$PROJECTS_ROOT/sem-memoria/context/memory/2026-01-01.md"

eval "$code"

fail=0
# Devolver não-zero no erro importa: sem isso o `&& echo ok` dispara logo
# abaixo do FAIL e a saída tranquiliza sobre o caso que acabou de quebrar.
want() { grep -qx "$1" <<<"$REGISTERED" || { echo "FAIL: $1 devia registrar"; fail=1; return 1; }; }
dont() { grep -qx "$1" <<<"$REGISTERED" && { echo "FAIL: $1 NÃO devia registrar"; fail=1; return 1; }; return 0; }

want ativo-sob-cap    && echo "ok   ativo dentro do cap registra"
want ativo-sobre-cap  && echo "ok   ativo acima do cap registra"
want quieto-sobre-cap && echo "ok   QUIETO acima do cap registra (o fix)"
dont quieto-sob-cap   && echo "ok   quieto dentro do cap é pulado"
want sem-log-sobre-cap && echo "ok   SEM memory/ acima do cap registra (o fix 2)"
dont sem-log-sob-cap   && echo "ok   sem memory/ dentro do cap é pulado"
dont sem-memoria       && echo "ok   store sem MEMORY.md é pulado sem quebrar"

[ "$fail" = 0 ] && echo "PASS" || { echo "FALHOU"; exit 1; }
