#!/usr/bin/env bash
# Self-check for sync-release.sh's leak guard.
#
# Exists because the guard shipped broken twice: it carried a hardcoded
# username, and it did not scan itself — "the leak checker is the one file
# nobody checks". Case 4 is that regression specifically.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
SUT="$PWD/sync-release.sh"
fails=0
ok()   { echo "ok   $1"; }
bad()  { echo "FAIL $1"; fails=$((fails+1)); }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# Vazamentos de mentira montados por fragmento. Escritos por extenso, o guard
# (que agora varre TODO o release, este arquivo incluido) casaria com as
# proprias fixtures e acusaria falso-positivo eterno.
H='/home'

# Fake source + a git repo standing in for the release checkout.
src="$tmp/home"; dst="$tmp/repo"
mkdir -p "$src/hooks" "$src/cron" "$src/scripts" "$dst"
git -C "$dst" init -q
cp "$SUT" "$dst/sync-release.sh"
printf 'clean\n' > "$dst/README.md"

# Minimal stand-ins for every manifest entry, so nothing reports FALTA.
for rel in $(sed -n '/^MANIFEST=(/,/^)/p' "$SUT" | grep -oE '^  [a-z].*'); do
  mkdir -p "$src/$(dirname "$rel")"; printf 'clean\n' > "$src/$rel"
done
run() { ( cd "$dst" && CLAUDE_HOME="$src" ./sync-release.sh "$@" 2>&1 ); }

# 1 — clean source: passes, and does NOT flag its own pattern definition.
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ! grep -q ABORTA <<<"$out" \
  && ok "fonte limpa passa (sem falso-positivo em si mesmo)" \
  || bad "fonte limpa deveria passar; rc=$rc"

# 2 — leak in a manifest file: caught, deleted (regenerable), non-zero exit.
printf "# ${H}/alice/x\n" >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORTA <<<"$out" && [ ! -f "$dst/cron/distill.sh" ] \
  && ok "vazamento em arquivo do manifesto: detecta e remove" \
  || bad "vazamento no manifesto nao foi contido; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 3 — leak in a hand-written file: caught, but NOT deleted.
printf "# ${H}/bob/x\n" >> "$dst/README.md"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORTA <<<"$out" && [ -f "$dst/README.md" ] \
  && ok "vazamento em arquivo escrito a mao: detecta sem apagar" \
  || bad "README nao deveria ter sido apagado; rc=$rc"
printf 'clean\n' > "$dst/README.md"

# 4 — REGRESSION: the guard must scan itself.
printf "# ${H}/carol/x\n" >> "$dst/sync-release.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q 'sync-release.sh' <<<"$out" && [ -f "$dst/sync-release.sh" ] \
  && ok "o guard varre a si mesmo" \
  || bad "o guard NAO varre a si mesmo (regressao)"
cp "$SUT" "$dst/sync-release.sh"

# 6 — REGRESSION: a TRACKED hand-written file with uncommitted work must not
# be rolled back. Branching on tracked-vs-untracked destroyed this case: once
# README.md is committed, `git checkout --` would discard every uncommitted
# line, not just the leaking one.
git -C "$dst" add -A >/dev/null 2>&1
git -C "$dst" -c user.email=t@t -c user.name=t commit -qm base >/dev/null 2>&1
printf "trabalho a mao que nao pode sumir\n# ${H}/dave/x\n" >> "$dst/README.md"
out=$(run --from-home); rc=$?
if [ $rc -ne 0 ] && grep -q 'trabalho a mao' "$dst/README.md"; then
  ok "arquivo versionado escrito a mao nao e revertido"
else
  bad "rollback destruiu trabalho nao commitado em README.md; rc=$rc"
fi
git -C "$dst" checkout -- README.md 2>/dev/null

# 7 — REGRESSION: a leaking line that also mentions $HOME must still be caught.
# `grep -v '$HOME'` dropped the whole line, so any leak sharing a line with a
# legitimate $HOME slipped through.
printf "# copia de ${H}/erin/x para \\"\$HOME/.claude\\"\n" >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -ne 0 ] && grep -q ABORTA <<<"$out" \
  && ok "vazamento na mesma linha que \$HOME e detectado" \
  || bad "linha com \$HOME escapou do guard; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 8 — usos legitimos de $HOME nao podem gerar falso-positivo.
printf 'LOG="$HOME/.memsearch/cron.log"\n' >> "$src/cron/distill.sh"
out=$(run --from-home); rc=$?
[ $rc -eq 0 ] && ok "uso legitimo de \$HOME nao vira falso-positivo" \
  || bad "falso-positivo em \$HOME legitimo; rc=$rc"
printf 'clean\n' > "$src/cron/distill.sh"; run --from-home >/dev/null 2>&1

# 5 — the username must not be baked in.
# Compara com o usuario REAL de quem roda: escrever um nome aqui seria repetir
# exatamente o defeito que este caso existe para pegar.
grep -qE "id -un" "$SUT" && ! grep -qF "$(id -un)" "$SUT" \
  && ok "usuario vem de id -un, nao hardcoded" \
  || bad "usuario hardcoded no guard"

[ $fails -eq 0 ] && { echo PASS; exit 0; } || { echo "FAIL ($fails)"; exit 1; }
