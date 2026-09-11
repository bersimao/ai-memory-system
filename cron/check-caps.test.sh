#!/usr/bin/env bash
# Self-check for the per-store cap override: bash check-caps.test.sh
#
# The guarantee being protected: a malformed `.cap` must fall back to the
# STRICTER default, never silently widen (or disable) the tripwire for a store.
# That is the whole safety argument for the feature — if a typo could raise a
# cap, the file stops being a deliberate act and becomes an accident waiting.
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-caps.sh"
fails=0

# run <cap-file-content|NONE> <chars> -> exit code; sets $LOGLINE to the
# violation the script reported ("<n>/<cap>"), empty when it reported none.
# Builds a throwaway $HOME so the real store is never read or written.
LOGLINE=""
RAW=""
run() {
  local capfile="$1" n="$2" home ctx rc
  home=$(mktemp -d)
  ctx="$home/.claude/projects/-fake-store/context"
  mkdir -p "$ctx" "$home/.memsearch" "$home/.claude/context"
  python3 -c "import sys;open(sys.argv[1],'w').write('x'*int(sys.argv[2]))" "$ctx/MEMORY.md" "$n"
  # RAW=1 makes printf interpret escapes, which is the only way to actually put
  # a NUL byte in the file — the *_raw helpers below rely on it.
  if [ "$capfile" != NONE ]; then
    if [ -n "$RAW" ]; then printf "$capfile" >"$ctx/.cap"
    else printf '%s' "$capfile" >"$ctx/.cap"; fi
  fi
  # stderr is CAPTURED, not discarded. Discarding it hid a real bug on
  # 2026-08-31: a store without `.cap` made the shell print a failed-redirection
  # error before `2>/dev/null` could apply, which would spam the cron log every
  # day. Twelve green tests missed it because run() threw stderr away.
  STDERR=$(HOME="$home" bash "$SCRIPT" 2>&1 >/dev/null)
  rc=$?
  LOGLINE=$(grep -oE '[0-9]+/[0-9]+' "$home/.memsearch/cron.log" 2>/dev/null | tail -1)
  rm -rf "$home"
  return $rc
}

# The tripwire must stay silent on stderr: it runs daily from cron, and noise
# there is indistinguishable from a real failure.
expect_quiet() {
  local cap="$1" n="$2" label="$3"
  run "$cap" "$n"
  if [ -z "$STDERR" ]; then
    echo "ok   $label"
  else
    echo "FAIL $label — stderr sujo: $(printf '%s' "$STDERR" | head -1)"
    fails=$((fails + 1))
  fi
}

# expect_cap <want "n/cap"> <cap-content> <chars> <label>
# The exit code alone cannot distinguish a fallback to 2500 from an absurd cap
# of 0 — BOTH flag a 2600-char file. Only the reported number tells them apart,
# and a mutation that accepted `.cap=0` passed the exit-code test on 2026-08-31
# precisely because of that blind spot. Assert the number.
expect_cap() {
  local want="$1" cap="$2" n="$3" label="$4"
  run "$cap" "$n"
  if [ "$LOGLINE" = "$want" ]; then
    echo "ok   $label"
  else
    echo "FAIL $label — esperado '$want', veio '$LOGLINE'"
    fails=$((fails + 1))
  fi
}

# expect <want: over|ok> <cap-content> <chars> <label>
expect() {
  local want="$1" cap="$2" n="$3" label="$4" rc
  run "$cap" "$n"; rc=$?
  local got=ok; [ $rc -eq 1 ] && got=over
  if [ "$got" = "$want" ]; then
    echo "ok   $label"
  else
    echo "FAIL $label — esperado $want, veio $got"
    fails=$((fails + 1))
  fi
}

# Variantes que escrevem o arquivo com escapes interpretados (bytes de verdade).
expect_cap_raw()   { RAW=1; expect_cap   "$@"; RAW=""; }
expect_quiet_raw() { RAW=1; expect_quiet "$@"; RAW=""; }

# --- the default still binds when no .cap exists ----------------------------
expect over NONE 2600 "sem .cap: 2600 estoura o default de 2500"
expect ok   NONE 2400 "sem .cap: 2400 passa"

# --- a valid .cap raises the ceiling for that store only ---------------------
expect ok   4000 2600 ".cap=4000: 2600 passa"
expect over 4000 4100 ".cap=4000: 4100 ainda estoura"
expect ok   "4000
"                2600 ".cap com newline/espaco e' tolerado"

# --- malformed .cap MUST fall back to the stricter default ------------------
# Each of these would be a silent tripwire-disable if it were honored.
# Asserted by the REPORTED cap, not just the exit code: every one of these must
# come back as /2500, i.e. the default actually took over.
expect_cap 2600/2500 "abc"    2600 ".cap nao-numerico -> default"
expect_cap 2600/2500 ""       2600 ".cap vazio -> default"
expect_cap 2600/2500 "0"      2600 ".cap=0 -> default (cap zero e' absurdo)"
expect_cap 2600/2500 "-5"     2600 ".cap negativo -> default"
expect_cap 2600/2500 "2500.5" 2600 ".cap fracionario -> default"

# --- SPLICING: malformado nunca pode AUMENTAR o teto ------------------------
# Achados pelo gate do Codex em 2026-08-31 na 1a versao, que usava
# `tr -d '[:space:]'`. Cada caso abaixo virava um numero MAIOR, silenciosamente.
# O antigo caso "25 00" passava por sorte: o splice dava 2500, igual ao default.
expect_cap 2600/2500 "4000
5000"                         2600 "duas linhas nao viram 40005000"
expect_cap 2600/2500 "9 000"  2600 "espaco no meio nao vira 9000"
expect_cap 2600/2500 "2 5 0 0 0" 2600 "digitos soltos nao viram 25000"
expect_cap 2600/2500 "99999999999999999999" 2600 "20 digitos -> default"
expect_quiet "99999999999999999999" 2600 "20 digitos nao sujam o stderr"
expect_cap 4100/4000 "04000" 4100 ".cap com zero a esquerda nao e' octal"

# --- bytes NUL: o splice por um canal que a regex nao enxerga ---------------
# `$(cat)` descarta NUL em silencio, entao "9<NUL>000" chega como "9000" e
# passaria na regex. Achado pelo gate do Codex em 2026-08-31, DEPOIS do fix do
# splice por espaco — mesma falha, outra porta. Precisa validar BYTES.
expect_cap_raw 2600/2500 '9\000000'      2600 "NUL no meio nao vira 9000"
expect_cap_raw 2600/2500 '4000\0005000' 2600 "NUL nao emenda 4000+5000"
expect_cap_raw 2600/2500 '40\00000'      2600 "NUL em qualquer posicao -> default"
expect_quiet_raw '9\000000' 2600 "NUL nao suja o stderr (bash avisa em command subst)"

# --- casos de borda que DEVEM continuar funcionando / falhando --------------
# CRLF e' realista aqui: o usuario edita atravessando WSL<->Windows.
expect_cap_raw 4100/4000 '4000\r\n' 4100 "CRLF continua valido (\\r e' [:space:])"
expect_cap_raw 2600/2500 '\357\273\2774000' 2600 "BOM UTF-8 -> default"
expect_cap_raw 2600/2500 '+4000' 2600 "sinal + -> default"
expect_cap 4100/4000 4000     4100 ".cap valido e' o numero REPORTADO"

# --- a lower .cap must be able to TIGHTEN, not only loosen ------------------
expect over 1000 1200 ".cap=1000 aperta: 1200 estoura"

# --- silencio no stderr (o caso comum: store SEM .cap) ----------------------
expect_quiet NONE 2400 "store sem .cap nao escreve nada no stderr"
expect_quiet NONE 2600 "store sem .cap, mesmo violando, stderr limpo"
expect_quiet 4000 2600 "store com .cap valido, stderr limpo"

# --- DRY: ninguem pode voltar a cravar 2500 no codigo -----------------------
# O ponto do store-cap.sh e' que alerta (check-caps), portao de elegibilidade
# (curate) e prompt (distill) concordem. Um literal reintroduzido em qualquer um
# deles desfaz isso em silencio — e o modo de falha e' o pior: o alarme cala em
# 4000 enquanto o curate ainda espreme para 2500. Comentarios podem citar o
# numero; codigo, nao.
CRON="$(dirname "${BASH_SOURCE[0]}")"
for s in check-caps.sh curate.sh distill.sh; do
  hit=$(grep -v '^[[:space:]]*#' "$CRON/$s" | grep -n '2500' || true)
  if [ -z "$hit" ]; then
    echo "ok   $s nao crava 2500 em codigo"
  else
    echo "FAIL $s voltou a cravar 2500: $hit"
    fails=$((fails + 1))
  fi
  if grep -q 'store-cap.sh' "$CRON/$s"; then
    echo "ok   $s carrega store-cap.sh"
  else
    echo "FAIL $s nao carrega store-cap.sh"
    fails=$((fails + 1))
  fi
  bash -n "$CRON/$s" 2>/dev/null && echo "ok   $s tem sintaxe valida" \
    || { echo "FAIL $s tem erro de sintaxe"; fails=$((fails + 1)); }
done

# --- as INSTRUCÇOES tambem contam como enforcement ---------------------------
# O gate do Codex achou isto em 31/08, depois do codigo ja estar certo: o teto
# real de um store nao e' so o que o cron mede, e' tambem o numero que o AGENTE
# le antes de aparar o arquivo. Um doc versionado dizendo "Cap 2.500" mantem o
# comportamento antigo mesmo com curate e distill corrigidos — e nada testava
# isso, porque o teste so olhava para .sh. Regra: se o arquivo cita o cap de
# projeto, tem que citar tambem o `.cap`. Roda nas duas arvores (install e repo),
# pulando o que nao existir daquele lado.
ROOT="$(cd "$CRON/.." && pwd)"

# Por MENCAO, nao por arquivo. A primeira versao deste guard perguntava "o
# arquivo cita .cap em algum lugar?", o que deixava passar um doc cuja PROSA
# voltasse a dizer "Cap 2.500 chars" enquanto uma tabela noutra secao ainda
# citava o override — e a mutacao original so falhou porque removeu as duas
# mencoes de uma vez. Cada citacao do cap precisa do override por perto.
WINDOW=4   # a prosa quebra em varias linhas; a mencao vem logo abaixo
check_doc() {
  local f="$1" rel="$2" ln
  while IFS=: read -r ln _; do
    [ -n "$ln" ] || continue
    sed -n "${ln},$((ln + WINDOW))p" "$f" | grep -q '\.cap' && continue
    echo "FAIL $rel:$ln cita o cap sem o override .cap por perto — agente apara no numero errado"
    fails=$((fails + 1))
    return
  done < <(grep -nE '(^|[^0-9])2[.,]?500([^0-9]|$)' "$f")
  echo "ok   $rel: toda mencao ao cap vem com o .cap"
}

# Lista explicita MAIS qualquer .md na raiz e em docs/, para que um documento
# novo de instrucoes nao escape do guard so por nao estar na lista.
docs=$(printf '%s\n' CLAUDE.md docs/memory-instructions.md skills/memory-write/SKILL.md README.md \
       $(cd "$ROOT" 2>/dev/null && ls *.md docs/*.md 2>/dev/null) | sort -u)
for doc in $docs; do
  [ -f "$ROOT/$doc" ] || continue
  grep -qE '(^|[^0-9])2[.,]?500([^0-9]|$)' "$ROOT/$doc" || continue
  check_doc "$ROOT/$doc" "$doc"
done

[ $fails -eq 0 ] && { echo "ok"; exit 0; }
echo "$fails caso(s) falharam"; exit 1
