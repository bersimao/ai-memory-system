#!/usr/bin/env python3
"""Executes a MEMORY.md split plan — the CODE moves, the LLM only decides.

Why it exists: when a MEMORY.md goes over its cap, the only tool the cron had
was COMPRESSING, which is lossy — that is how a store went from 6534 to 2568
chars with no record. Splitting into `topics/` + an index line is lossless, but
takes judgment (what groups together, what to name it). The division here is
deliberate:

    LLM  -> returns a PLAN (which sections to move, slug, index line)
    code -> does the move, verifies, and reverts if anything was lost

That makes losing content impossible by CONSTRUCTION, not because the prompt
asked to preserve it — a request that had already failed 3 times out of 16 in a
single day ([[2026-08-24]]).

Usage: split-memory.py <MEMORY.md> <cap> <plan.json> [expected-sha256]
Plan: {"moves":[{"heading":"## X","slug":"x","index_line":"- [X](topics/x.md) — ..."}]}
Exits 0 if applied, 2 if refused/reverted.
"""
import hashlib, json, os, re, sys, tempfile, datetime

# The index heading this script emits, plus the legacy spelling ("## Índice")
# it still recognizes in stores written before the rename.
INDEX_HEADING = '## Index'
INDEX_LINE_RE = r'(?m)^(## (?:Index|Índice)[ \t]*\n)'

def sections(text):
    """-> (preamble, [(heading, body_including_heading)])"""
    parts = re.split(r'(?m)^(?=## )', text)
    pre = parts[0] if parts and not parts[0].startswith('## ') else ''
    out = []
    for p in parts:
        if p.startswith('## '):
            out.append((p.splitlines()[0].strip(), p))
    return pre, out

def norm(line):
    return re.sub(r'\s+', ' ', line).strip()

def main():
    if len(sys.argv) not in (4, 5):
        print("usage: split-memory.py <MEMORY.md> <cap> <plan.json> [sha256]", file=sys.stderr); return 2
    path, cap, planpath = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    expected = sys.argv[4] if len(sys.argv) == 5 else None

    # Guard 1: PROJECT stores only. The global MEMORY.md has no `topics/` and
    # should not — there the overflow is reusable knowledge that belongs
    # elsewhere. abspath BEFORE the guard: a relative path
    # ("projects/x/context/MEMORY.md") does not match "/projects/" and was
    # silently refused even though it was valid.
    path = os.path.abspath(path)
    norm_path = path.replace('\\', '/')
    if '/projects/' not in norm_path or not norm_path.endswith('/context/MEMORY.md'):
        print(f"refused: {path} is not a project store", file=sys.stderr); return 2
    if not os.path.exists(planpath):
        print("refused: plan does not exist (the LLM never wrote it)", file=sys.stderr); return 2

    original = open(path, encoding='utf-8').read()

    # ISOLATION: the plan was made from a snapshot; the LLM call takes seconds
    # or minutes, and in the meantime an interactive session may have written
    # to the file. Applying an old plan over new content moves sections nobody
    # inspected. If the content is not what the plan saw, refuse.
    if expected:
        current_hash = hashlib.sha256(original.encode('utf-8')).hexdigest()
        if current_hash != expected:
            print("refused: file changed after the plan was made "
                  "(stale plan — another session wrote to it?)", file=sys.stderr)
            return 2
    try:
        plan = json.load(open(planpath, encoding='utf-8'))
    except Exception as e:
        print(f"refused: invalid plan ({e})", file=sys.stderr); return 2
    moves = plan.get('moves') or []
    if not moves:
        print("empty plan: nothing to move"); return 2

    pre, secs = sections(original)
    by_head = {h: b for h, b in secs}
    ctx = os.path.dirname(path)
    tdir = os.path.join(ctx, 'topics')
    os.makedirs(tdir, exist_ok=True)          # most stores have no topics/ yet

    created, moved_heads, index_lines = [], [], []
    for m in moves:
        head, slug = (m.get('heading') or '').strip(), (m.get('slug') or '').strip()
        idx = (m.get('index_line') or '').strip()
        if head not in by_head:
            print(f"refused: heading not present in the file: {head!r}", file=sys.stderr); return 2
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,60}', slug):
            print(f"refused: invalid slug: {slug!r}", file=sys.stderr); return 2
        dest = os.path.join(tdir, f"{slug}.md")
        if os.path.exists(dest):
            print(f"refused: {slug}.md already exists (never overwrite)", file=sys.stderr); return 2
        if not idx.startswith('- ') or f'topics/{slug}.md' not in idx:
            print(f"refused: index_line does not point at topics/{slug}.md", file=sys.stderr); return 2
        created.append((dest, by_head[head], slug)); moved_heads.append(head); index_lines.append(idx)

    today = datetime.date.today().isoformat()
    proj = os.path.basename(os.path.dirname(ctx))

    # Build EVERYTHING in memory and verify BEFORE touching disk. Writing first
    # and checking afterwards leaves a window where a crash interrupts the
    # process with MEMORY.md already split and nothing to revert. Verified-
    # before-written has no such window: if the plan loses content, not a
    # single byte was written.
    contents = []
    for dest, body, slug in created:
        fm = f"---\nname: {slug}\ndate: {today}\nproject: {proj}\ntags: [auto-split]\n---\n\n"
        contents.append((dest, fm + body.rstrip() + "\n"))

    keep = [b for h, b in secs if h not in moved_heads]
    new = pre + ''.join(keep)
    if re.search(INDEX_LINE_RE, new):
        new = re.sub(INDEX_LINE_RE, r'\1' + '\n'.join(index_lines) + '\n', new, count=1)
    else:
        lines = new.splitlines(True)
        pos = next((i + 1 for i, l in enumerate(lines) if l.startswith('# ')), 0)
        lines.insert(pos, f"\n{INDEX_HEADING}\n" + '\n'.join(index_lines) + "\n")
        new = ''.join(lines)

    # VERIFICATION: this is a MOVE. Every non-empty line of the original must
    # survive somewhere.
    combined = new + ''.join(c for _, c in contents)
    alive = {norm(l) for l in combined.splitlines() if norm(l)}
    lost = [l for l in original.splitlines() if norm(l) and norm(l) not in alive]
    if lost:
        print(f"REFUSED (nothing was written): {len(lost)} line(s) would vanish, "
              f"e.g.: {lost[0][:60]!r}", file=sys.stderr)
        return 2

    # CRASH-SAFETY: `open(path,'w')` truncates immediately — dying mid-write
    # leaves MEMORY.md truncated or empty, which is real loss. Write to a temp
    # file in the SAME directory and swap with os.replace(), which is atomic on
    # POSIX: the file is either the old content or the new, never half. The
    # topic pages go first; if the process dies between them and the swap,
    # MEMORY.md stays intact (the orphan pages are inert and the next run
    # refuses them with "already exists").
    def write_atomic(dest, content):
        d = os.path.dirname(dest) or '.'
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.split-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(content); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, dest)
        except BaseException:
            try: os.remove(tmp)
            except OSError: pass
            raise

    written = []
    try:
        for dest, content in contents:
            write_atomic(dest, content); written.append(dest)
        write_atomic(path, new)
    except OSError as e:
        for d in written:
            try: os.remove(d)
            except OSError: pass
        write_atomic(path, original)
        print(f"REVERTED: write failure ({e})", file=sys.stderr)
        return 2

    new_n = len(new)
    print(f"split OK: {len(original)} -> {new_n} chars (cap {cap}) | "
          f"{len(created)} topic page(s): {', '.join(s for _, _, s in created)}")
    if new_n > cap:
        print(f"  still over cap by {new_n - cap} chars — nothing lost, it just did not fit")
    return 0

if __name__ == '__main__':
    sys.exit(main())
