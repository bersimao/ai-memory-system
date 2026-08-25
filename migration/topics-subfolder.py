#!/usr/bin/env python3
"""Move each project store's topic notes into context/topics/.

MEMORY.md is an index; the pages it indexes used to sit flat next to it, so
`context/` filled with a dozen loose .md files and the two real subdirectories
(memory/, transcripts/) got lost among them. Topic pages now live in
context/topics/ and the index links there.

    ./topics-subfolder.py            # dry run, prints the plan
    ./topics-subfolder.py --apply

Links inside MEMORY.md are rewritten `](slug.md)` -> `](topics/slug.md)`.
Cross-links BETWEEN topic pages need no rewrite: they move together, so a bare
`slug.md` still resolves. Nothing else in the store is touched.
"""

import argparse
import glob
import os
import re
import shutil
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the moves")
    args = ap.parse_args()

    moved = 0
    for store in sorted(os.listdir(PROJECTS)):
        ctx = os.path.join(PROJECTS, store, "context")
        if not os.path.isdir(ctx):
            continue
        pages = [p for p in sorted(glob.glob(os.path.join(ctx, "*.md")))
                 if os.path.basename(p) != "MEMORY.md"]
        if not pages:
            continue

        topics = os.path.join(ctx, "topics")
        print(f"\n{store}\n  -> context/topics/")
        names = []
        for p in pages:
            name = os.path.basename(p)
            names.append(name)
            dst = os.path.join(topics, name)
            if os.path.exists(dst):
                print(f"  SKIP       {name}  (already in topics/)")
                continue
            print(f"  move       {name}")
            moved += 1
            if args.apply:
                os.makedirs(topics, exist_ok=True)
                shutil.move(p, dst)

        index = os.path.join(ctx, "MEMORY.md")
        if not os.path.exists(index):
            continue
        text = open(index, encoding="utf-8").read()
        # ](slug.md) -> ](topics/slug.md), only for pages that actually moved
        new = re.sub(r"\]\((?!topics/)([^)/]+\.md)\)",
                     lambda m: f"]({'topics/' if m.group(1) in names else ''}{m.group(1)})",
                     text)
        if new != text:
            print(f"  relink     MEMORY.md ({len(re.findall(r'\]\(topics/', new))} links)")
            if args.apply:
                open(index, "w", encoding="utf-8").write(new)

    print(f"\n{moved} {'moved' if args.apply else 'to move'}")
    if not args.apply:
        print("Dry run. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
