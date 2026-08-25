#!/usr/bin/env python3
"""Merge the split per-project memory stores under ~/.claude/projects/.

Until the hooks were fixed, memory-inject.js and transcript-capture.js encoded the
project path with [/\\.: ] -> '-', which preserves '_'. Claude Code itself maps
every character outside [A-Za-z0-9-] to '-'. So every project whose path held an
underscore (my_repos/, criacao_nfs, GRUPO_EXEMPLO, ...) ended up with two
stores: the hooks read and wrote one, agents wrote the other.

This moves each mis-encoded store into the directory Claude Code actually uses.

    ./merge-project-stores.py            # dry run, prints the plan
    ./merge-project-stores.py --apply    # perform the moves

Second job (2026-08-21): collapse per-subdirectory stores into their repo store.
Until the hooks anchored the store to the git repo root, a session started in
.../sap-api/3-Queue/AcademicQueue got its own memory, invisible to a session at
the repo root. --collapse finds those and folds them back:

    ./merge-project-stores.py --collapse           # dry run
    ./merge-project-stores.py --collapse --apply

Anchors come from the demand registry (~/.claude/data/demands.json): each
workdir's git root. Subdirectory stores are matched by walking the real
subtree and encoding each directory, so a sibling repo whose name merely starts
with the anchor's name is never swallowed. Stores whose directory no longer
exists fall back to a name-prefix match and are flagged for review.

Files that exist on both sides with identical bytes are dropped from the source.
Files that exist on both sides with *different* bytes are left untouched and
reported as collisions; the script exits non-zero so they get resolved by hand
rather than silently overwritten.

Python rather than bash: these directory names begin with '-', which most shell
tools parse as option flags.
"""

import argparse
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")


def encode(name: str) -> str:
    """Claude Code's project-dir encoding, applied to an already-encoded name.

    Idempotent: names it already produced are returned unchanged.
    """
    return re.sub(r"[^a-zA-Z0-9-]", "-", name)


def relfiles(root: str, only: str = ""):
    for dirpath, _, names in os.walk(root):
        for n in names:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root)
            if only and not rel.startswith(only):
                continue
            yield rel, full


def prune_empty(root: str) -> None:
    for dirpath, _, _ in sorted(os.walk(root), reverse=True):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


def merge(name: str, target: str, apply: bool, note: str = "", only: str = "") -> tuple:
    """Move store `name` into store `target`. Returns (moved, identical, collisions).

    `only` restricts the move to a subtree — collapse passes "context" so Claude
    Code's own <session>.jsonl files stay where the harness put them.
    """
    src = os.path.join(PROJECTS, name)
    dst = os.path.join(PROJECTS, target)
    moved = identical = collisions = 0

    print(f"\n{name}\n  -> {target}"
          f"  [target {'exists' if os.path.isdir(dst) else 'new'}]{note}")

    for rel, srcfile in sorted(relfiles(src, only)):
        dstfile = os.path.join(dst, rel)

        if not os.path.exists(dstfile):
            print(f"  move       {rel}")
            moved += 1
            if apply:
                os.makedirs(os.path.dirname(dstfile), exist_ok=True)
                shutil.move(srcfile, dstfile)
        elif filecmp.cmp(srcfile, dstfile, shallow=False):
            print(f"  identical  {rel}  (drop source)")
            identical += 1
            if apply:
                os.remove(srcfile)
        elif rel.startswith(os.path.join("context", "transcripts")):
            # Transcripts are append-only day files: two stores holding the same
            # day means two sessions, not a conflict. Concatenate, don't ask.
            print(f"  append     {rel}  (same day in both stores)")
            moved += 1
            if apply:
                with open(dstfile, "a", encoding="utf-8") as out:
                    out.write(f"\n<!-- merged from {name} -->\n")
                    out.write(open(srcfile, encoding="utf-8").read())
                os.remove(srcfile)
        else:
            print(f"  COLLISION  {rel}  (differs; left in place)")
            collisions += 1

    if apply:
        prune_empty(src)
        if os.path.isdir(src):
            print(f"  source kept: {name} (still has files)")
    return moved, identical, collisions


def git_root(path_: str) -> str:
    try:
        out = subprocess.run(["git", "-C", path_, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def anchors_from_registry() -> dict:
    """{encoded anchor name: real anchor path} — one per registered demand repo."""
    reg = os.path.expanduser("~/.claude/data/demands.json")
    try:
        demands = json.load(open(reg, encoding="utf-8"))["demands"]
    except Exception:
        return {}
    out = {}
    for d in demands:
        wd = d.get("workdir")
        if not wd or not os.path.isdir(wd):
            continue
        root = git_root(wd) or wd
        out[encode(root)] = root
    return out


SKIP_DIRS = {".git", "node_modules", "bin", "obj", ".vs", "dist", "__pycache__", ".venv"}


def subdir_names(root: str, max_depth: int = 5):
    """Encoded names of every directory under `root` — the stores that a session
    started there would have created before the anchor fix."""
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        for d in dirnames:
            yield encode(os.path.join(dirpath, d))


def collapse_plan() -> list:
    """[(store, anchor_store, note)] for stores that are subdirectories of a repo."""
    anchors = anchors_from_registry()
    stores = {n for n in os.listdir(PROJECTS) if os.path.isdir(os.path.join(PROJECTS, n))}
    plan, claimed = [], set()

    for enc, root in sorted(anchors.items()):
        for name in subdir_names(root):
            if name in stores and name != enc and name not in claimed:
                claimed.add(name)
                plan.append((name, enc, ""))

    # Stores whose directory is gone can't be walked to; fall back to the name
    # prefix. Flagged, because a sibling repo can share the prefix.
    for name in sorted(stores - claimed):
        best = max((a for a in anchors if name.startswith(a + "-")), key=len, default="")
        if best:
            plan.append((name, best, "  [path gone — matched by name prefix, review]"))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the moves")
    ap.add_argument("--collapse", action="store_true",
                    help="fold per-subdirectory stores into their repo store")
    args = ap.parse_args()

    moved = identical = collisions = 0

    if args.collapse:
        plan = collapse_plan()
        if not plan:
            print("No subdirectory stores found.")
            return 0
        for name, target, note in plan:
            m, i, c = merge(name, target, args.apply, note, only="context")
            moved, identical, collisions = moved + m, identical + i, collisions + c
    else:
        moved, identical, collisions = legacy_pass(args.apply)

    verb = "moved" if args.apply else "to move"
    print(f"\n{moved} {verb}, {identical} identical, {collisions} collisions")
    if collisions:
        print("Resolve collisions by hand, then re-run.", file=sys.stderr)
        return 1
    if not args.apply:
        print("Dry run. Re-run with --apply.")
    return 0


def legacy_pass(apply: bool) -> tuple:
    """The original job: re-encode stores written with the old [/\\.: ] regex."""
    moved = identical = collisions = 0
    for name in sorted(os.listdir(PROJECTS)):
        src = os.path.join(PROJECTS, name)
        target = encode(name)
        if target == name or not os.path.isdir(src):
            continue
        m, i, c = merge(name, target, apply)
        moved, identical, collisions = moved + m, identical + i, collisions + c
    return moved, identical, collisions


if __name__ == "__main__":
    sys.exit(main())
