#!/usr/bin/env python3
"""Self-check for split-memory.py. The invariant is MOVE: nothing may vanish."""
import json, os, subprocess, sys, tempfile
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), "split-memory.py")
fails = []
def run(mem, plan, cap=2500):
    t = tempfile.mkdtemp()
    ctx = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx)
    p = os.path.join(ctx, "MEMORY.md"); open(p, "w").write(mem)
    pj = os.path.join(t, "plan.json"); open(pj, "w").write(json.dumps(plan))
    r = subprocess.run([sys.executable, S, p, str(cap), pj], capture_output=True, text=True)
    return r.returncode, open(p).read(), ctx, r.stderr
def chk(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)

MEM = """# Working Memory

## Index
- [Already there](topics/already-there.md) — something

## Pending
- item one, long enough to take up space
- item two

## Decided
- an old decision
"""
PLAN = {"moves": [{"heading": "## Pending", "slug": "pending",
                   "index_line": "- [Pending](topics/pending.md) — what is open"}]}

rc, new, ctx, err = run(MEM, PLAN)
chk("split applies and exits 0", rc == 0, err)
chk("section left MEMORY.md", "## Pending" not in new)
chk("index line went in", "topics/pending.md" in new)
tp = os.path.join(ctx, "topics", "pending.md")
chk("topic page created", os.path.exists(tp))
if os.path.exists(tp):
    body = open(tp).read()
    chk("content preserved in the topic page", "item one, long enough" in body and "item two" in body)
    chk("frontmatter present", body.startswith("---"))

# nothing may vanish: every line of the original lives somewhere
if os.path.exists(tp):
    combined = new + open(tp).read()
    gone = [l for l in MEM.splitlines() if l.strip() and l.strip() not in combined]
    chk("NO line of the original vanished", not gone, str(gone[:1]))

# legacy heading: a store written before the rename says "## Índice" — the
# index lines must land under it, never under a duplicate "## Index".
MEM_LEGACY = MEM.replace("## Index", "## Índice")
rc, new, ctx, err = run(MEM_LEGACY, PLAN)
chk("legacy '## Índice' heading still receives the index line",
    rc == 0 and "topics/pending.md" in new and "## Index\n" not in new, err)

# guards
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Does Not Exist", "slug": "x",
                                     "index_line": "- [x](topics/x.md) — y"}]})
chk("missing heading -> refuses", rc == 2)
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Pending", "slug": "../escape",
                                     "index_line": "- [x](topics/../escape.md) — y"}]})
chk("slug with path traversal -> refuses", rc == 2)
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Pending", "slug": "pending",
                                     "index_line": "- [x](topics/OTHER.md) — y"}]})
chk("index_line pointing at the wrong file -> refuses", rc == 2)
rc, _, _, err = run(MEM, {"moves": []})
chk("empty plan -> refuses", rc == 2)

# global guard: a path outside projects/ must be refused
t = tempfile.mkdtemp(); g = os.path.join(t, "context"); os.makedirs(g)
gp = os.path.join(g, "MEMORY.md"); open(gp, "w").write(MEM)
pj = os.path.join(t, "p.json"); open(pj, "w").write(json.dumps(PLAN))
r = subprocess.run([sys.executable, S, gp, "4000", pj], capture_output=True, text=True)
chk("global MEMORY.md -> refuses (has no topics/)", r.returncode == 2, r.stderr[:60])

# A plan that LOSES content must not write a single byte: verification happens
# before writing, so neither MEMORY.md nor any topic page changes.
t = tempfile.mkdtemp(); ctx2 = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx2)
p2 = os.path.join(ctx2, "MEMORY.md"); open(p2, "w").write(MEM)
# moving the Index section itself is the degenerate case: its heading exists,
# but pulling it out interacts with the index-line insertion
pj2 = os.path.join(t, "plan.json")
open(pj2, "w").write(json.dumps({"moves": [{"heading": "## Index", "slug": "index",
      "index_line": "- [Index](topics/index.md) — x"}]}))
before = open(p2).read()
r = subprocess.run([sys.executable, S, p2, "2500", pj2], capture_output=True, text=True)
after = open(p2).read()
tp2 = os.path.join(ctx2, 'topics', 'index.md')
moved = open(tp2).read() if os.path.exists(tp2) else ""
chk("moving the Index itself does not corrupt (refuses, or preserves all)",
    r.returncode != 0 or all(l.strip() in after + moved
                             for l in before.splitlines() if l.strip()))

# ISOLATION: a plan made from a snapshot must not be applied after another
# session wrote — it would move a section nobody inspected.
import hashlib
t = tempfile.mkdtemp(); ctx3 = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx3)
p3 = os.path.join(ctx3, "MEMORY.md"); open(p3, "w").write(MEM)
h = hashlib.sha256(MEM.encode()).hexdigest()
pj3 = os.path.join(t, "plan.json"); open(pj3, "w").write(json.dumps(PLAN))
open(p3, "w").write(MEM.replace("- item two", "- item two\n- CONCURRENT WRITE"))
r = subprocess.run([sys.executable, S, p3, "2500", pj3, h], capture_output=True, text=True)
chk("stale plan -> refuses", r.returncode == 2, r.stderr[:70])
chk("concurrent write survives", "CONCURRENT WRITE" in open(p3).read())

# correct hash -> applies normally
open(p3, "w").write(MEM)
r = subprocess.run([sys.executable, S, p3, "2500", pj3, h], capture_output=True, text=True)
chk("correct hash -> applies", r.returncode == 0, r.stderr[:70])

print("PASS" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
