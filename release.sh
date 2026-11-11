#!/usr/bin/env bash
# The only way a version reaches GitHub: the CHANGELOG section, the git tag and
# the GitHub Release are three copies of one thing, and this script keeps them
# one thing.
#
#   ./release.sh check [--offline]  fail when headings, tags and Releases disagree
#   ./release.sh cut vX.Y.Z         annotated tag from the top CHANGELOG section
#   ./release.sh publish vX.Y.Z     GitHub Release from an already pushed tag
#
# Why it exists (2026-11-11): releases were cut by hand. The CHANGELOG had dated
# headings and no versions, v0.4.0 was tagged without a Release (the Releases
# page still called v0.3.0 the latest), and the 2026-11-09 entry shipped on
# main with no tag at all. Nothing failed, so nobody noticed.
#
# The script never pushes. `cut` prints the push; `publish` refuses until the
# tag is on the remote and calls `gh release create --verify-tag`, because
# without that flag gh creates the remote tag itself and bypasses the push.
set -uo pipefail
cd "$(dirname "$0")" || exit 1

CHANGELOG=docs/CHANGELOG.md
REMOTE=${RELEASE_REMOTE:-origin}
BRANCH=main
# Tags older than the CHANGELOG; their notes live only on their GitHub Release.
PRE_CHANGELOG_TAGS=(v0.1.0 v0.2.0)

SEMVER='v[0-9]+\.[0-9]+\.[0-9]+'
RELEASE_HEADING="^## ($SEMVER) — [0-9]{4}-[0-9]{2}-[0-9]{2}( — .+)?$"
UNRELEASED_HEADING='^## Unreleased'

die() { echo "release: $*" >&2; exit 1; }
errors=0
err() { echo "MISMATCH $*" >&2; errors=$((errors+1)); }

# "## " lines of the CHANGELOG on stdin, skipping fenced code: a "## " comment
# inside an upgrade snippet is not a section.
headings() { awk '/^```/ { f = !f; next } !f && /^## /'; }

# Versions of the release headings, top to bottom. Reads stdin.
heading_versions() { headings | sed -nE "s/$RELEASE_HEADING/\1/p"; }

# Body of version $1's section (heading excluded) from the CHANGELOG on stdin.
section_body() {
  awk -v v="$1" '
    /^```/ { f = !f }
    !f && /^## / { if (on) exit; if (index($0, "## " v " ") == 1) { on = 1; next } }
    on { print }
  ' | sed -e '/./,$!d'
}

section_title() { headings | grep -m1 -E "^## $1 " | sed 's/^## //'; }

is_pre() { local t; for t in "${PRE_CHANGELOG_TAGS[@]}"; do [ "$t" = "$1" ] && return 0; done; return 1; }

highest() { sort -V | tail -1; }

# "<tag> <object sha>" per remote v* tag. The object (not the peeled commit) is
# compared, so a tag re-cut locally with new notes on the same commit still
# shows as different. 'refs/tags/v*', not 'v*': ls-remote matches the tail, and
# 'v*' would also list a namespaced tag like backup/v0.4.0.
remote_tags() {
  git ls-remote --tags "$REMOTE" 'refs/tags/v*' \
    | awk '$2 !~ /\^\{\}$/ { sub("^refs/tags/", "", $2); print $2, $1 }'
}

# Published (non-draft) Release tags: a draft left by a failed publish is not a Release.
release_tags() { gh release list --limit 1000 --json tagName,isDraft -q '.[] | select(.isDraft | not) | .tagName'; }

cmd_check() {
  local offline=0
  [ "${1:-}" = "--offline" ] && offline=1
  [ -f "$CHANGELOG" ] || die "$CHANGELOG not found"

  # 1. Every heading is a release or the top Unreleased block.
  local n=0 line
  while IFS= read -r line; do
    n=$((n+1))
    if [[ $line =~ $UNRELEASED_HEADING ]]; then
      [ $n -eq 1 ] || err "'$line' is not the first section"
    elif ! [[ $line =~ $RELEASE_HEADING ]]; then
      err "heading '$line' is not '## vX.Y.Z — YYYY-MM-DD' (a release with no version is how 2026-11-09 went untagged)"
    fi
  done < <(headings < "$CHANGELOG")

  local versions; versions=$(heading_versions < "$CHANGELOG")
  [ "$versions" = "$(sort -rV <<<"$versions")" ] || err "release headings are not newest-first"
  [ -z "$(uniq -d <<<"$versions")" ] || err "duplicate release heading: $(uniq -d <<<"$versions" | tr '\n' ' ')"

  # 2. Headings <-> local tags.
  local tags v; tags=$(git tag -l 'v*')
  for v in $versions; do
    grep -qxF "$v" <<<"$tags" || err "$v has a CHANGELOG section but no tag"
  done
  for v in $tags; do
    is_pre "$v" && continue
    grep -qxF "$v" <<<"$versions" || err "tag $v has no CHANGELOG section"
  done

  if [ $offline -eq 0 ]; then
    # 3. Local tags <-> remote tags (same name, same commit).
    local rt name sha; rt=$(remote_tags) || die "cannot list tags on $REMOTE"
    for v in $tags; do
      sha=$(awk -v v="$v" '$1 == v { print $2 }' <<<"$rt")
      if [ -z "$sha" ]; then err "tag $v is not on $REMOTE (push it: git push $REMOTE $v)"
      elif [ "$sha" != "$(git rev-parse "refs/tags/$v")" ]; then err "tag $v differs on $REMOTE (commit or notes)"
      fi
    done
    while read -r name _; do
      [ -n "$name" ] || continue
      grep -qxF "$name" <<<"$tags" || err "tag $name is on $REMOTE but not local (git fetch --tags)"
    done <<<"$rt"

    # 4. Tags <-> GitHub Releases, and the latest Release is the highest tag.
    local rels latest top
    rels=$(release_tags) || die "gh release list failed (auth? network?)"
    for v in $tags; do
      grep -qxF "$v" <<<"$rels" || err "tag $v has no GitHub Release (./release.sh publish $v)"
    done
    for v in $rels; do
      grep -qxF "$v" <<<"$tags" || err "GitHub Release $v has no tag"
    done
    top=$(highest <<<"$tags")
    latest=""
    if [ -n "$rels" ]; then latest=$(gh release view --json tagName -q .tagName) || die "gh release view failed"; fi
    [ -z "$top" ] || [ "$latest" = "$top" ] || err "GitHub marks '${latest:-none}' as latest, highest tag is $top"
  fi

  if [ $errors -gt 0 ]; then echo "release check: $errors mismatch(es)" >&2; return 1; fi
  echo "release check: CHANGELOG, tags$([ $offline -eq 0 ] && echo ', remote and GitHub Releases') agree"
}

need_version() { [[ ${1:-} =~ ^$SEMVER$ ]] || die "usage: $0 $2 vX.Y.Z"; }

cmd_cut() {
  local v=${1:-}; need_version "$v" cut
  [ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || die "cut runs on $BRANCH (release what shipped, not a work branch)"
  [ -z "$(git status --porcelain)" ] || die "working tree is not clean"
  git fetch -q "$REMOTE" "$BRANCH" --tags \
    || die "cannot fetch $REMOTE (a local tag that differs from $REMOTE also fails here)"
  [ "$(git rev-parse HEAD)" = "$(git rev-parse "$REMOTE/$BRANCH")" ] \
    || die "HEAD is not $REMOTE/$BRANCH: push $BRANCH first, so the tag names a published commit"
  git rev-parse -q --verify "refs/tags/$v" >/dev/null && die "tag $v already exists"

  local top; top=$(headings < "$CHANGELOG" | head -1)
  [[ $top =~ $RELEASE_HEADING ]] && [ "${BASH_REMATCH[1]}" = "$v" ] \
    || die "top CHANGELOG section is '$top'; rename it to '## $v — $(date +%F)' and commit first"
  local prev; prev=$(git tag -l 'v*' | highest)
  if [ -n "$prev" ]; then
    [ "$prev" != "$v" ] && [ "$(printf '%s\n%s\n' "$prev" "$v" | highest)" = "$v" ] \
      || die "$v is not above the highest tag $prev"
  fi

  local body; body=$(section_body "$v" < "$CHANGELOG")
  [ -n "$body" ] || die "section $v is empty"
  # verbatim: the default cleanup strips every line starting with "#", which
  # would drop the "### YYYY-MM-DD" day headings from the tag message.
  git tag -a --cleanup=verbatim "$v" -F - <<<"$(section_title "$v" < "$CHANGELOG")"$'\n\n'"$body" || die "git tag failed"
  echo "tagged $v at $(git rev-parse --short HEAD). Next:"
  echo "  git push $REMOTE $v"
  echo "  ./release.sh publish $v"
}

cmd_publish() {
  local v=${1:-}; need_version "$v" publish
  git rev-parse -q --verify "refs/tags/$v" >/dev/null || die "no local tag $v (./release.sh cut $v)"
  local rsha; rsha=$(remote_tags | awk -v v="$v" '$1 == v { print $2 }')
  [ -n "$rsha" ] || die "tag $v is not on $REMOTE yet: git push $REMOTE $v"
  [ "$rsha" = "$(git rev-parse "refs/tags/$v")" ] || die "tag $v differs on $REMOTE (commit or notes)"
  local rels; rels=$(release_tags) || die "gh release list failed (auth? network?)"
  grep -qxF "$v" <<<"$rels" && die "GitHub Release $v already exists"

  # Notes come from the CHANGELOG as tagged. A tag cut before the CHANGELOG had
  # version headings (v0.3.0, v0.4.0) falls back to the current one.
  local log; log=$(git show "$v:$CHANGELOG" 2>/dev/null)
  if [ -z "$(section_body "$v" <<<"$log")" ]; then
    echo "note: $CHANGELOG at $v has no '## $v' section; using the one in HEAD" >&2
    log=$(cat "$CHANGELOG")
  fi
  local title body; title=$(section_title "$v" <<<"$log"); body=$(section_body "$v" <<<"$log")
  [ -n "$body" ] || die "no '## $v' section in $CHANGELOG"

  local latest=false
  [ "$(git tag -l 'v*' | highest)" = "$v" ] && latest=true
  gh release create "$v" --verify-tag --title "$title" --notes-file - --latest="$latest" <<<"$body" \
    || die "gh release create failed"
  cmd_check
}

case "${1:-}" in
  check)   shift; cmd_check "$@" ;;
  cut)     shift; cmd_cut "$@" ;;
  publish) shift; cmd_publish "$@" ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
