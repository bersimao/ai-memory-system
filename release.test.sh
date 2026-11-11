#!/usr/bin/env bash
# Self-check for release.sh. Each case is one way the CHANGELOG, the tags and
# the GitHub Releases drifted apart (or could), and must make `check` fail.
# A throwaway repo + bare remote stand in for the checkout; a fake `gh` reads
# its Releases from a file.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
SUT="$PWD/release.sh"
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

# Fake gh: `release list` and `release view` read $tmp/releases ("tag latest?"
# per line); `release create` appends to it and logs its args.
mkdir -p "$tmp/bin"
cat > "$tmp/bin/gh" <<'EOF'
#!/usr/bin/env bash
db="$FAKE_GH_DB"; touch "$db"
case "$1 $2" in
  "release list") cut -d' ' -f1 "$db" ;;
  "release view")
    if [ "${3:-}" = "--json" ]; then awk '$2 == "latest" { print $1 }' "$db"
    else grep -q "^$3\b" "$db"; fi ;;
  "release create")
    echo "$*" >> "$FAKE_GH_DB.log"
    cat > /dev/null
    if [[ " $* " == *" --latest=true "* ]]; then sed -i 's/ latest$//' "$db"; echo "$3 latest" >> "$db"
    else echo "$3" >> "$db"; fi ;;
esac
EOF
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"

# A repo whose CHANGELOG, tags, remote and Releases all agree on v0.3.0..v0.4.0.
fresh() {
  rm -rf "$tmp/r" "$tmp/o.git" "$tmp/releases"*
  git init -q --bare "$tmp/o.git"
  git init -q -b main "$tmp/r"
  cp "$SUT" "$tmp/r/release.sh"
  mkdir -p "$tmp/r/docs"
  cat > "$tmp/r/docs/CHANGELOG.md" <<'EOF'
# Changelog

## v0.4.0 — 2026-11-08

Body four.

```bash
## not a heading
```

## v0.3.0 — 2026-09-10

Body three.
EOF
  ( cd "$tmp/r" && git add -A && git commit -qm init && git remote add origin "$tmp/o.git" \
    && git tag v0.1.0 && git tag v0.2.0 && git tag v0.3.0 && git tag -a v0.4.0 -m four \
    && git push -q origin main --tags )
  printf 'v0.1.0\nv0.2.0\nv0.3.0\nv0.4.0 latest\n' > "$tmp/releases"
  export FAKE_GH_DB="$tmp/releases"
}
run() { ( cd "$tmp/r" && ./release.sh "$@" ) > "$tmp/out" 2>&1; }
expect_fail() { # name, grep pattern, args...
  local name=$1 pat=$2; shift 2
  if run "$@"; then bad "$name (passed)"; elif grep -q -- "$pat" "$tmp/out"; then ok "$name"
  else bad "$name (wrong reason: $(head -2 "$tmp/out" | tr '\n' ' '))"; fi
}
commit_log() { ( cd "$tmp/r" && git commit -qam "$1" && git push -q origin main ); }

# 1. Consistent state passes, online and offline.
fresh
if run check && run check --offline; then ok "consistent state passes"; else bad "consistent state passes: $(cat "$tmp/out")"; fi

# 2. A dated heading with no version: the 2026-11-09 shape.
fresh; sed -i 's/^# Changelog$/# Changelog\n\n## 2026-11-09\n\nUntagged./' "$tmp/r/docs/CHANGELOG.md"
expect_fail "dated heading without version fails" "is not '## vX.Y.Z" check --offline

# 3. A release section that was never tagged.
fresh; sed -i 's/^# Changelog$/# Changelog\n\n## v0.5.0 — 2026-11-09\n\nFive./' "$tmp/r/docs/CHANGELOG.md"
expect_fail "section without tag fails" "v0.5.0 has a CHANGELOG section but no tag" check --offline

# 4. A tag with no section; the pre-CHANGELOG tags stay exempt.
fresh; ( cd "$tmp/r" && git tag v0.4.1 )
expect_fail "tag without section fails" "tag v0.4.1 has no CHANGELOG section" check --offline
grep -q 'v0.1.0\|v0.2.0' "$tmp/out" && bad "pre-CHANGELOG tags are exempt" || ok "pre-CHANGELOG tags are exempt"

# 5. A tag pushed but never published: the v0.4.0 shape.
fresh; printf 'v0.1.0\nv0.2.0\nv0.3.0 latest\n' > "$tmp/releases"
expect_fail "tag without GitHub Release fails" "tag v0.4.0 has no GitHub Release" check
grep -q "marks 'v0.3.0' as latest" "$tmp/out" && ok "stale 'latest' Release fails" || bad "stale 'latest' Release fails"

# 6. A local tag never pushed.
fresh; ( cd "$tmp/r" && git tag -d v0.4.0 >/dev/null && git push -q origin :refs/tags/v0.4.0 && git tag -a v0.4.0 -m again )
expect_fail "unpushed tag fails" "tag v0.4.0 is not on origin" check

# 6b. A tag re-cut locally with new notes on the same commit.
fresh; ( cd "$tmp/r" && git tag -d v0.4.0 >/dev/null && git tag -a v0.4.0 -m edited )
expect_fail "re-cut tag notes differ from remote" "tag v0.4.0 differs on origin" check

# 6c. A namespaced remote tag is not a release tag.
fresh; ( cd "$tmp/r" && git push -q origin HEAD:refs/tags/backup/v0.4.0 )
run check && ok "namespaced remote tag ignored" || bad "namespaced remote tag: $(cat "$tmp/out")"

# 6d. A heading-looking line inside a code fence is not a heading (fixture has one).
fresh; run check --offline && ok "fenced '## ' is not a heading" || bad "fenced ##: $(cat "$tmp/out")"

# 7. Unreleased is allowed only on top; headings must be newest-first.
fresh; sed -i 's/^# Changelog$/# Changelog\n\n## Unreleased (on `hmg`)\n\nWip./' "$tmp/r/docs/CHANGELOG.md"
run check --offline && ok "Unreleased on top passes" || bad "Unreleased on top passes: $(cat "$tmp/out")"
fresh; printf '\n## Unreleased\n\nLate.\n' >> "$tmp/r/docs/CHANGELOG.md"
expect_fail "Unreleased below a release fails" "is not the first section" check --offline

# 8. cut refuses: top still Unreleased, dirty tree, off main, unpushed HEAD, existing tag.
fresh; sed -i 's/^# Changelog$/# Changelog\n\n## Unreleased\n\nWip./' "$tmp/r/docs/CHANGELOG.md"; commit_log wip
expect_fail "cut refuses Unreleased top" "top CHANGELOG section is '## Unreleased'" cut v0.5.0
fresh; echo x >> "$tmp/r/docs/CHANGELOG.md"
expect_fail "cut refuses dirty tree" "not clean" cut v0.5.0
fresh; ( cd "$tmp/r" && git checkout -qb hmg )
expect_fail "cut refuses off main" "cut runs on main" cut v0.5.0
fresh; expect_fail "cut refuses existing tag" "already exists" cut v0.4.0
fresh; sed -i 's/^# Changelog$/# Changelog\n\n## v0.5.0 — 2026-11-09\n\n### 2026-11-09\n\nFive.\n\n```\n## fenced\n```\n\nTail./' "$tmp/r/docs/CHANGELOG.md"
( cd "$tmp/r" && git commit -qam five )
expect_fail "cut refuses unpushed HEAD" "push main first" cut v0.5.0

# 9. cut: annotated tag whose message is the section; nothing pushed.
( cd "$tmp/r" && git push -q origin main )
if run cut v0.5.0; then
  msg=$(cd "$tmp/r" && git tag -l --format='%(contents)' v0.5.0)
  [ "$(cd "$tmp/r" && git cat-file -t v0.5.0)" = tag ] && grep -q '^Five\.$' <<<"$msg" && grep -q '^v0.5.0 — 2026-11-09$' <<<"$msg" \
    && ok "cut makes an annotated tag from the section" || bad "cut tag message: $msg"
  grep -q '^### 2026-11-09$' <<<"$msg" && ok "cut keeps ### day headings" || bad "cut drops ### lines: $msg"
  grep -q '^Tail\.$' <<<"$msg" && ok "a fenced '## ' does not end the section" || bad "fenced ## cut the section: $msg"
  git -C "$tmp/r" ls-remote --tags origin v0.5.0 | grep -q . && bad "cut does not push" || ok "cut does not push"
else bad "cut v0.5.0: $(cat "$tmp/out")"; fi

# 10. publish refuses an unpushed tag, then publishes with --verify-tag and fixes 'latest'.
expect_fail "publish refuses unpushed tag" "not on origin yet" publish v0.5.0
( cd "$tmp/r" && git push -q origin v0.5.0 )
if run publish v0.5.0; then
  grep -q -- '--verify-tag' "$tmp/releases.log" && ok "publish uses --verify-tag" || bad "publish uses --verify-tag"
  grep -q '^v0.5.0 latest$' "$tmp/releases" && ok "publish marks the highest tag latest" || bad "latest: $(cat "$tmp/releases")"
  grep -q 'agree' "$tmp/out" && ok "publish ends with a passing check" || bad "publish check: $(cat "$tmp/out")"
else bad "publish v0.5.0: $(cat "$tmp/out")"; fi
expect_fail "publish refuses an existing Release" "already exists" publish v0.5.0

# 11. Backfill: a tag cut before version headings takes its notes from HEAD, not latest.
fresh; printf 'v0.1.0\nv0.2.0\nv0.3.0 latest\n' > "$tmp/releases"
( cd "$tmp/r" && git tag -d v0.4.0 >/dev/null && git push -q origin :refs/tags/v0.4.0 \
  && sed -i 's/^## v0.4.0 — 2026-11-08$/## 2026-11-08/' docs/CHANGELOG.md && git commit -qam old && git tag v0.4.0 \
  && sed -i 's/^## 2026-11-08$/## v0.4.0 — 2026-11-08/' docs/CHANGELOG.md && git commit -qam new \
  && git push -q origin main v0.4.0 )
if run publish v0.4.0 && grep -q 'using the one in HEAD' "$tmp/out" && grep -q '^v0.4.0 latest$' "$tmp/releases"; then
  ok "backfill publish takes notes from HEAD"
else bad "backfill publish: $(cat "$tmp/out")"; fi

echo
[ $fails -eq 0 ] && echo "release.test.sh: all passed" || echo "release.test.sh: $fails failed"
exit $((fails > 0))
