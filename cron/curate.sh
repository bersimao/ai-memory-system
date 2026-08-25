#!/usr/bin/env bash
# Weekly: prune stale entries, merge duplicates, consolidate related facts in
# - global ~/.claude/context/MEMORY.md (4000-char cap)
# - each ~/.claude/projects/<slug>/context/MEMORY.md whose daily log was
#   touched in the last 7 days (2500-char cap)
# USER.md is left alone (curated only on user request).
set -uo pipefail

LOG="$HOME/.memsearch/cron.log"
LLM_RUN="$HOME/.claude/scripts/llm-run"

# Audit trail + veto (pendência 4). Curation must COMPRESS — same facts, fewer
# chars. A big shrink is deletion, not compression, and an unattended LLM running
# with --dangerously-skip-permissions does not get to delete memory unreviewed.
# Real incident: a project MEMORY.md went 6534 -> 2568 chars with no record.
# A synchronous human gate was rejected (it would freeze the cron), so: always
# log the delta, alert past CUT_ALERT, and roll back past CUT_VETO.
CUT_ALERT=30   # % shrink -> record it loudly
CUT_VETO=50    # % shrink -> restore from .bak, do not accept
# Carimbo da rodada. O artefato vetado NÃO pode ser um slot fixo: o alerta vive
# no INBOX por semanas e é um comando com caminho literal. Com slot fixo, um
# alerta antigo passa a apontar para a proposta de OUTRA rodada e o aceite
# aplica silenciosamente algo que o usuário nunca revisou. Com carimbo, o
# comando do alerta velho falha (`cannot stat`) em vez de acertar o alvo errado.
STAMP="$(date +%Y%m%d-%H%M%S)"
# Optional notification channel. NOT required: cron.log always gets the record,
# so the audit trail survives with no Obsidian, no vault, no INBOX file.
[ -f "$HOME/.claude/data/memory.env" ] && . "$HOME/.claude/data/memory.env"
INBOX="${CLAUDE_MEM_INBOX:-}"
PROJECTS_ROOT="$HOME/.claude/projects"
GLOBAL_MEM="$HOME/.claude/context/MEMORY.md"

mkdir -p "$(dirname "$LOG")"

ts() { date -Iseconds; }

# Register a file for curation: back it up (.bak) and add a "- <path> (cap N)"
# line to the shared prompt list. Eligible files are curated in ONE LLM call.
TARGETS=""   # newline-separated "- <file> (cap <cap>)" lines for the prompt
SIZES=""     # newline-separated "<chars_before>|<cap>|<file>|<label>" for the audit

# Characters, not bytes — pt-BR accents inflate byte counts ~2%, and check-caps.sh
# measures chars. Two components disagreeing about "over cap" is its own bug.
chars() { python3 -c "import sys;print(len(open(sys.argv[1],encoding='utf-8',errors='replace').read()))" "$1" 2>/dev/null || echo 0; }

register() {
  local file="$1" cap="$2" label="$3"
  [ -f "$file" ] || { echo "skip $label: no file"; return; }
  cp -p "$file" "${file}.bak"
  # Uma proposta vetada de uma rodada ANTERIOR não pode sobreviver a esta: o
  # alerta novo aponta para o mesmo caminho, e o usuário aceitaria um `.rejected`
  # que não corresponde mais ao que o curate propôs agora. Qualquer `.rejected`
  # em disco tem que pertencer à rodada corrente.
  for old in "${file}".rejected "${file}".rejected-*; do
    [ -e "$old" ] || continue
    echo "  descartando proposta vetada anterior, não revisada: $old"
    rm -f "$old"
  done
  echo "register $label (cap $cap)"
  TARGETS="${TARGETS}- ${file} (cap ${cap})"$'\n'
  SIZES="${SIZES}$(chars "$file")|${cap}|${file}|${label}"$'\n'
}

# Compare every registered file against its .bak, log the delta, roll back a cut
# past CUT_VETO. Per file, not all-or-nothing: one gutted file must not discard
# good curation of the others. Echoes into the caller's cron.log block.
audit_and_veto() {
  local before cap file label after pct msg hint review rej
  ALERTS=""
  while IFS='|' read -r before cap file label; do
    [ -n "${file:-}" ] || continue
    after="$(chars "$file")"
    [ "$before" -gt 0 ] || continue
    pct=$(( (before - after) * 100 / before ))
    [ "$pct" -lt 0 ] && pct=0          # grew: fine, nothing to police
    if [ "$pct" -ge "$CUT_VETO" ]; then
      # Preserve the PROPOSAL before undoing it. Without this the alert is
      # unactionable: the file is safe but what curate wanted to do is gone, so
      # there is nothing to judge. Single slot, same convention as .bak; a later
      # veto on the same file overwrites it. NOT *.bak on purpose — distill.sh
      # sweeps those, and this has to survive until the user looks at it.
      # O curate pode ter APAGADO o arquivo em vez de esvaziá-lo. Aí não há o
      # que copiar, e mandar `diff`/`cp` contra um `.rejected` inexistente faz o
      # alerta instruir um comando que falha (ou, com artefato velho em disco,
      # restaurar a proposta ERRADA por cima da memória boa).
      rej="${file}.rejected-${STAMP}"
      if [ -f "$file" ]; then
        cp -p "$file" "$rej"
        # `aceitar` CONSOME o artefato: sem o `rm`, o `.rejected` sobrevive ao
        # aceite e o comando não é idempotente — se a memória for editada depois,
        # rodar o mesmo `cp` de novo (o alerta segue aberto no INBOX) sobrescreve
        # as edições novas com a versão antiga já aceita.
        review="Revisar: diff \"${file}\" \"${rej}\" | aceitar: cp \"${rej}\" \"${file}\" && rm \"${rej}\" | descartar: rm \"${rej}\""
      else
        review="A proposta era APAGAR o arquivo — não há proposta para revisar. Se a exclusão for mesmo correta, apagar à mão"
      fi
      cp -p "${file}.bak" "$file"
      # O restore devolve o arquivo ao tamanho original. Se ELE já estourava o
      # cap, comprimir não é a saída: curate só sabe comprimir (perde conteúdo),
      # e a correção certa é QUEBRAR em topics/ (não perde nada). Sem esta dica o
      # ciclo se repete toda semana: curate corta, veto restaura, nada melhora.
      # A saída para overflow depende de QUAL camada é. Store de projeto quebra
      # em topics/ + índice; o MEMORY.md global não tem topics/ — lá o excedente
      # é fato de domínio, e a saída é rotear para o skill dono via `kb`.
      hint=""
      if [ "$before" -gt "$cap" ]; then
        case "$file" in
          */projects/*/context/MEMORY.md)
            hint=" ATENÇÃO: segue ${before}/${cap} acima do cap. Corte desse tamanho quase sempre significa QUEBRAR em topics/ + linha de índice (skill memory-write), não comprimir." ;;
          *)
            hint=" ATENÇÃO: segue ${before}/${cap} acima do cap. O global não tem topics/ — o excedente costuma ser fato de domínio: rotear para o skill dono via \`kb\` (knowledge/ = dados, references/ = lições), não comprimir." ;;
        esac
      fi
      msg="VETO curate — ${label}: ${before} -> ${after} chars (-${pct}%), restaurado. ${review}.${hint}"
      ALERTS="${ALERTS}${msg}"$'\n'
    elif [ "$pct" -ge "$CUT_ALERT" ]; then
      msg="corte grande no curate — ${label}: ${before} -> ${after} chars (-${pct}%)"
      ALERTS="${ALERTS}${msg}"$'\n'
    else
      msg="ok ${label}: ${before} -> ${after} chars (-${pct}%)"
    fi
    echo "  $msg"
  done <<< "$SIZES"
}

{
  echo
  echo "=== [$(ts)] curate ==="

  register "$GLOBAL_MEM" 4000 "global"

  shopt -s nullglob
  for ctx in "$PROJECTS_ROOT"/*/context; do
    [ -d "$ctx/memory" ] || continue
    if [ -z "$(find "$ctx/memory" -maxdepth 1 -name '*.md' -mtime -7 -print -quit)" ]; then
      continue
    fi
    register "$ctx/MEMORY.md" 2500 "$(basename "$(dirname "$ctx")")"
  done

  if [ -z "$TARGETS" ]; then
    echo "nothing to curate"
  else
    # Single LLM call for all eligible files. Deletion/merge is judgment work →
    # smart tier (backend mapping lives in llm-run; never drift to a lesser model).
    prompt="Curate these SAP-memory files. For EACH file independently: (1) remove stale or resolved entries, (2) merge duplicate or near-duplicate facts, (3) consolidate related entries that can be expressed more compactly. Tighten the writing: make each entry more direct, precise and concise — that is the ONLY compression you should do. Do NOT delete content to fit the character cap: if the file is still over cap after tightening, that is fine and expected, because a separate step will split it into index notes without losing anything. Deleting a durable fact to hit a number is a bug, not curation. Preserve the section headings. Do NOT touch any content between '<!-- BEGIN skills:auto -->' and '<!-- END skills:auto -->' markers — that block is auto-generated by a script. ACROSS files: if facts in the listed files contradict each other, do not silently keep both — flag it by prepending a single line '⚠️ CONTRADIÇÃO: <resumo> (vs <outro arquivo>)' at the top of the affected file's relevant section, keeping the newer/more specific fact. If a file is already lean and has nothing to prune, leave it unchanged. Edit ONLY the files listed below — no other files.
${TARGETS}"
    "$LLM_RUN" smart "$prompt"; rc=$?
    [ "$rc" = 3 ] && echo "  cota/limite de gasto estourado — curate não rodou nesta semana."
    [ "$rc" = 0 ] || echo "  curate failed (rc=$rc)"

    # Always runs, even if the LLM call failed (then every delta is 0%).
    echo "-- audit --"
    audit_and_veto

    # -- split -------------------------------------------------------------
    # Arquivo ainda acima do cap depois do aperto de escrita: quebrar em
    # `topics/` + linha de índice, que é LOSSLESS. Antes a única ferramenta era
    # comprimir (lossy) e foi assim que um store perdeu 3.966 chars sem registro.
    # O LLM só devolve o PLANO; quem move é o split-memory.py, que verifica que
    # nenhuma linha sumiu e reverte se sumiu. "Keys, not prompts".
    echo "-- split --"
    while IFS='|' read -r before cap file label; do
      [ -n "${file:-}" ] || continue
      case "$file" in */projects/*/context/MEMORY.md) ;; *) continue ;; esac
      atual="$(chars "$file")"
      [ "$atual" -gt "$cap" ] || continue
      plano="$(mktemp)"
      splitprompt="Read ${file}. It is ${atual} characters, over its ${cap}-character cap. Pick the whole '## ' section(s) whose removal would bring it under the cap with the least disruption — prefer sections that are self-contained detail rather than the index or short shared context. Write ONLY the file ${plano}, containing JSON and nothing else: {\"moves\":[{\"heading\":\"## exact heading text\",\"slug\":\"kebab-case-slug\",\"index_line\":\"- [Human Title](topics/kebab-case-slug.md) — one-line summary\"}]}. The heading must match a line in the file EXACTLY. The slug must be lowercase letters, digits and hyphens only. Do not edit ${file} or any other file — only write ${plano}."
      # "o LLM só devolve o plano, não escreve no store" era uma frase do PROMPT
      # — e prompt não prende nada (a lição do dia inteiro). Com
      # --dangerously-skip-permissions o modelo PODE escrever no MEMORY.md.
      # Então: hash antes, hash depois. Se ele mexeu, desfaz a mexida dele e
      # segue só com o plano — quem move continua sendo o código.
      antes_hash="$(sha256sum "$file" | cut -d" " -f1)"
      guarda="$(mktemp)"; cp -p "$file" "$guarda"
      "$LLM_RUN" cheap "$splitprompt"; src=$?
      if [ "$src" = 3 ]; then echo "  cota estourada — split adiado"; rm -f "$plano" "$guarda"; break; fi
      if [ "$(sha256sum "$file" | cut -d" " -f1)" != "$antes_hash" ]; then
        cp -p "$guarda" "$file"
        echo "  ATENÇÃO: o LLM editou ${label} apesar da instrução — edição desfeita, seguindo só com o plano"
      fi
      rm -f "$guarda"
      # Passa o hash do estado ATUAL: o split recusa se o arquivo mudar entre
      # aqui e a aplicacao (outra sessao escrevendo durante o cron).
      agora_hash="$(sha256sum "$file" | cut -d" " -f1)"
      python3 "$HOME/.claude/cron/split-memory.py" "$file" "$cap" "$plano" "$agora_hash" 2>&1 | sed 's/^/  /'
      rm -f "$plano"
    done <<< "$SIZES"
  fi

  # Deterministic post-step (NOT an LLM edit): regenerate the auto skill list in
  # global MEMORY.md from the skills on disk. Runs AFTER the LLM pass so the final
  # on-disk state is always correct regardless of what the model did to the file.
  # Optional: only present in installs that keep a skills/ tree.
  if [ -x "$HOME/.claude/cron/sync-skill-list.sh" ]; then
    "$HOME/.claude/cron/sync-skill-list.sh" || echo "  sync-skill-list failed"
  fi
} >>"$LOG" 2>&1

# Second channel, best-effort. The record already landed in cron.log above; this
# only surfaces it where the user actually looks. Absent INBOX = no alert here,
# never a lost audit trail.
if [ -n "${ALERTS:-}" ] && [ -f "$INBOX" ]; then
  printf -- '- [ ] %s\n' "$(printf '%s' "$ALERTS" | head -20)" >>"$INBOX"
fi
