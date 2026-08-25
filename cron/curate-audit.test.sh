#!/usr/bin/env bash
# Self-check for curate.sh's audit_and_veto (pendência 4). No LLM, no cron.
# Run: bash ~/.claude/cron/curate-audit.test.sh
set -uo pipefail

SRC="$(dirname "$0")/curate.sh"
CUT_ALERT=30
CUT_VETO=50
STAMP="testrun1"

# Extract the function under test. Guard against a silent empty extraction —
# an empty probe looks exactly like a passing one (learned 2026-08-23).
# audit_and_veto depende de chars() — extrair as DUAS, senão o teste roda contra
# uma função quebrada e os vetos passam pelo motivo errado (visto 2026-08-23).
fn="$(sed -n '/^chars() {/p;/^register() {/,/^}/p;/^audit_and_veto() {/,/^}/p' "$SRC")"
[ -n "$fn" ] || { echo "FAIL: could not extract from $SRC"; exit 1; }
grep -q 'CUT_VETO' <<<"$fn" || { echo "FAIL: extracted text is not the function"; exit 1; }
grep -q '^chars() {' <<<"$fn" || { echo "FAIL: chars() nao foi extraida"; exit 1; }
grep -q '^register() {' <<<"$fn" || { echo "FAIL: register() nao foi extraida"; exit 1; }
eval "$fn"
# sanidade: a função extraída realmente mede?
[ "$(chars "$SRC")" -gt 100 ] || { echo "FAIL: chars() extraida nao mede nada"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
check() { # <name> <expected-substring> <expected-final-size> <file>
  if ! grep -q -- "$2" <<<"$OUT"; then echo "FAIL $1: esperava '$2' em: $OUT"; fail=1; return; fi
  local got; got="$(wc -c <"$4")"
  if [ "$got" != "$3" ]; then echo "FAIL $1: tamanho final $got, esperava $3"; fail=1; return; fi
  echo "ok   $1"
}

# Caminho REALISTA de store de projeto: a dica de overflow casa em
# */projects/*/context/MEMORY.md, então o fixture precisa ter essa forma.
mk() { # <name> <before> <after> [cap] -> sets f
  mkdir -p "$T/projects/$1/context"
  f="$T/projects/$1/context/MEMORY.md"
  head -c "$2" /dev/zero | tr '\0' 'a' >"$f"   # ASCII: chars == bytes
  cp -p "$f" "$f.bak"
  head -c "$3" /dev/zero | tr '\0' 'b' >"$f"   # simulate the LLM edit
  SIZES="$2|${4:-2500}|$f|$1"
}

# o veto tem que PRESERVAR a proposta, senão o alerta é inacionável
mk vetoprop 1000 300; OUT="$(audit_and_veto)"
if [ -f "$f.rejected-$STAMP" ] && [ "$(wc -c <"$f.rejected-$STAMP")" = 300 ]; then
  echo "ok   veto preserva a proposta em .rejected"
else
  echo "FAIL veto nao preservou a proposta (.rejected ausente ou errado)"; fail=1
fi
grep -q 'diff ' <<<"$OUT" || { echo "FAIL alerta do veto nao traz o comando de diff"; fail=1; }
# aceitar tem que CONSUMIR o artefato, senão o comando nao e idempotente:
# aceite -> memoria editada -> mesmo cp de novo apaga as edicoes novas.
grep -q 'accept: cp .* && rm ' <<<"$OUT" \
  && echo "ok   aceitar consome o .rejected (cp && rm)" \
  || { echo "FAIL aceitar nao remove o .rejected"; fail=1; }

mk shrink10 1000 900;  OUT="$(audit_and_veto)"; check "corte 10%% = ok"        "ok shrink10"   900  "$f"
mk shrink35 1000 650;  OUT="$(audit_and_veto)"; check "corte 35%% = alerta"    "large cut in curate"  650  "$f"
mk shrink60 1000 400;  OUT="$(audit_and_veto)"; check "corte 60%% = VETO"      "CURATE VETO"   1000 "$f"
mk emptied  1000 0;    OUT="$(audit_and_veto)"; check "esvaziado = VETO"       "CURATE VETO"   1000 "$f"
mk grew     1000 1500; OUT="$(audit_and_veto)"; check "cresceu = ok"           "ok grew"       1500 "$f"

# limite exato: 50% deve vetar (>=), 49% não
mk exact50  1000 500;  OUT="$(audit_and_veto)"; check "corte 50%% = VETO"      "CURATE VETO"   1000 "$f"
mk under50  1000 510;  OUT="$(audit_and_veto)"; check "corte 49%% = so alerta" "large cut in curate"  510  "$f"

# o veto num arquivo acima do cap tem que mandar QUEBRAR, não comprimir —
# senão curate corta / veto restaura / nada melhora, toda semana.
mk bloated 7600 2500 2500; OUT="$(audit_and_veto)"
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && echo "ok   veto acima do cap sugere quebrar em topics/" \
  || { echo "FAIL veto acima do cap nao sugeriu quebrar"; fail=1; }
# o global NÃO tem topics/ — a dica lá tem que mandar rotear para skill via kb
gf="$T/global-MEMORY.md"
head -c 6000 /dev/zero | tr '\0' 'a' >"$gf"; cp -p "$gf" "$gf.bak"
head -c 2000 /dev/zero | tr '\0' 'b' >"$gf"
SIZES="6000|4000|$gf|global"; OUT="$(audit_and_veto)"
grep -q 'belongs somewhere else' <<<"$OUT" \
  && echo "ok   veto no global manda rotear para fora, nao comprimir" \
  || { echo "FAIL veto no global nao deu a saida certa"; fail=1; }
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && { echo "FAIL veto no global sugeriu topics/ (nao existe la)"; fail=1; } \
  || echo "ok   veto no global NAO sugere topics/"

# e um veto DENTRO do cap não deve poluir com a dica
mk small 2000 800 2500; OUT="$(audit_and_veto)"
grep -q 'SPLIT into topics/' <<<"$OUT" \
  && { echo "FAIL veto dentro do cap nao devia sugerir quebrar"; fail=1; } \
  || echo "ok   veto dentro do cap nao sugere quebrar"

# --- artefatos do veto -------------------------------------------------------
# curate APAGA o arquivo: a proposta é a exclusão, não há o que copiar. O alerta
# não pode mandar diff/cp contra um .rejected inexistente.
mk deleted 3000 1 2500; rm -f "$f"
OUT="$(audit_and_veto 2>&1)"
grep -q 'cannot stat' <<<"$OUT" && { echo "FAIL veto com arquivo apagado ainda tenta copiar"; fail=1; } \
  || echo "ok   veto com arquivo apagado nao tenta copiar"
grep -q 'proposal was to DELETE' <<<"$OUT" \
  && echo "ok   alerta explica que a proposta era exclusao" \
  || { echo "FAIL alerta nao explica a exclusao"; fail=1; }
[ -f "$f" ] && echo "ok   arquivo apagado foi restaurado" || { echo "FAIL nao restaurou"; fail=1; }

# .rejected de rodada anterior nao pode sobreviver ao register()
mk stale 3000 2900 2500
echo "PROPOSTA VELHA" > "$f.rejected-anterior"
TARGETS=""; SIZES=""
register "$f" 2500 stale >/dev/null
[ -f "$f.rejected-anterior" ] && { echo "FAIL .rejected velho sobreviveu ao register"; fail=1; } \
  || echo "ok   register descarta .rejected de rodada anterior"

# Um alerta de rodada anterior guarda um caminho literal. Depois de uma rodada
# nova, esse caminho tem que NAO existir — senao o aceite antigo aplica em
# silencio uma proposta que o usuario nunca revisou.
mk aliasing 3000 1000 2500
STAMP="semana1"; audit_and_veto >/dev/null
velho="$f.rejected-semana1"
[ -f "$velho" ] || { echo "FAIL rodada 1 nao criou o artefato carimbado"; fail=1; }
cp -p "$f.bak" "$f"                      # simula a semana passando
STAMP="semana2"; TARGETS=""; SIZES=""
register "$f" 2500 aliasing >/dev/null   # rodada nova limpa a anterior
head -c 500 /dev/zero | tr '\0' 'c' >"$f"
audit_and_veto >/dev/null
[ -f "$velho" ] && { echo "FAIL caminho do alerta ANTIGO ainda existe (aceite silencioso)"; fail=1; } \
  || echo "ok   alerta antigo nao alcanca a proposta nova"
[ -f "$f.rejected-semana2" ] && echo "ok   rodada nova tem artefato proprio" \
  || { echo "FAIL rodada nova nao criou artefato"; fail=1; }

[ "$fail" = 0 ] && echo "PASS" || { echo "FALHOU"; exit 1; }
