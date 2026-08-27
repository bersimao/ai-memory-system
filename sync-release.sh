#!/usr/bin/env bash
# Sync the memory system from the live install (~/.claude) into this repo.
#
# Why a script and not "copy the folder": ~/.claude also holds 929 files of
# per-client project memory and 185 files of SAP B1 skills. Neither is part of
# the memory system and neither may reach a public repo. The manifest below is
# the release surface — nothing outside it ships, by construction.
#
# Why sync at all: the ia-skills repo taught this lesson. Two copies of the same
# file with no comparator diverge within a day of normal work.
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
)

# Arquivos nativos do repo (nao vem de ~/.claude) que o guard tambem varre.
EXTRA_SCAN=(docs/memory-instructions.md docs/codex-support.md skills/memory-write/SKILL.md)

mode="${1:---check}"
rc=0

for rel in "${MANIFEST[@]}"; do
  s="$SRC/$rel"; d="$DST/$rel"
  if [ ! -f "$s" ]; then
    printf 'FALTA na origem  %s\n' "$rel"; rc=1; continue
  fi
  case "$mode" in
    --check)
      if [ ! -f "$d" ]; then
        printf 'novo             %s\n' "$rel"; rc=1
      elif ! cmp -s "$s" "$d"; then
        printf 'difere           %s\n' "$rel"; rc=1
      fi
      ;;
    --from-home)
      mkdir -p "$(dirname "$d")"
      cmp -s "$s" "$d" || { cp -p "$s" "$d"; printf 'copiado          %s\n' "$rel"; }
      ;;
    *) echo "uso: $0 [--check|--from-home]" >&2; exit 2 ;;
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
  # Remove as ocorrencias legitimas de "$HOME" da LINHA antes de casar, em vez
  # de descartar a linha inteira que as contem: com `grep -v` bastava a linha
  # do vazamento mencionar $HOME de passagem para escapar do guard.
  #
  # ponytail: `\b${me}\b` pega vazamento fora de path (linha de autor, e-mail),
  # mas quem tiver um usuario generico ("dev", "test") vai ver falso-positivo.
  # Aceito de proposito: o guard aborta ruidosamente e mostra a linha, e para
  # um guard de vazamento errar alto e melhor do que errar calado.
  bad=""
  for f in "${scan[@]}"; do
    hit=$(sed 's/\$HOME//g; s/${HOME}//g' "$f" | grep -nE "$pat" 2>/dev/null) || true
    [ -n "$hit" ] && bad="${bad}${bad:+$'\n'}$(printf '%s\n' "$hit" | sed "s|^|$f:|")"
  done
  if [ -n "$bad" ]; then
    echo "ABORTA: path pessoal chegou no release:"; echo "$bad"
    # Reverter, nao so avisar. O aviso rola na tela e um `git add -A` depois
    # comita o vazamento assim mesmo — foi exatamente assim que estado efemero
    # ja entrou nos repos deste projeto. Arquivo contaminado volta ao commit.
    printf '%s\n' "$bad" | cut -d: -f1 | sort -u | while read -r f; do
      rel="${f#$DST/}"
      # A pergunta certa e "isto e regeneravel?", NAO "isto esta versionado?".
      # Versionado x nao-versionado era o eixo errado: assim que README.md e
      # este script forem commitados eles viram tracked, e um `git checkout --`
      # jogaria fora TODA alteracao nao commitada do arquivo — nao so a linha
      # que vazou. Arquivo do MANIFEST e copia de $SRC e volta de graca;
      # arquivo escrito a mao nao volta de lugar nenhum.
      if printf '%s\n' "${MANIFEST[@]}" | grep -qxF "$rel"; then
        if git -C "$DST" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
          git -C "$DST" checkout -- "$rel" && echo "  revertido      $rel"
        else
          rm -f "$f" && echo "  removido       $rel"
        fi
      else
        echo "  NAO revertido  $rel (escrito a mao — corrija e rode de novo)"
      fi
    done
    rc=1
  else
    echo "tripwire: nenhum path pessoal no release ✔"
  fi
fi

[ "$mode" = --check ] && [ $rc -eq 0 ] && echo "repo em dia com $SRC ✔"
exit $rc
