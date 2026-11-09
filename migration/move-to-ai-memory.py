#!/usr/bin/env python3
"""Move memory out of ~/.claude into ~/.ai-memory (split layout). One-shot, re-runnable phases.

  move-to-ai-memory.py plan              what would move, sizes, symlinks; writes nothing
  move-to-ai-memory.py copy              copy under the writer lock + hash-verify (old copy stays live)
  move-to-ai-memory.py switch            write ~/.config/ai-memory/config.json (the cut-over), then delta pass
  move-to-ai-memory.py delta             carry over files the old location got after `copy`; never overwrites
  move-to-ai-memory.py resolve PATH...   mark old-location files as merged by hand (unblocks retire)
  move-to-ai-memory.py retire            move the old copies to the rollback dir (after you verified)
  move-to-ai-memory.py rollback          undo: put the old copies back, remove the config (reports new writes)

What moves (logical paths unchanged, so journal/usage/index entries stay valid):
  ~/.claude/context/**                    -> ~/.ai-memory/context/**
  ~/.claude/projects/<store>/context/**   -> ~/.ai-memory/projects/<store>/context/**
  ~/.claude/data/memory-system/**         -> ~/.ai-memory/state/**   (minus config + installation manifest)
  data/memory-system/config.json, installation.json, data/memory.env, data/store-anchors.json
                                          -> ~/.config/ai-memory/   (outside every agent-writable dir)
What stays: skills/, references/, Claude JSONL (~/.claude/projects/*/*.jsonl), code, demands.json.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

HOME = Path.home()
SRC = HOME / '.claude'
DST = HOME / '.ai-memory'
CFG_DIR = HOME / '.config' / 'ai-memory'
ROLLBACK = HOME / '.local' / 'share' / 'ai-memory-rollback'
MANIFEST = CFG_DIR / 'migration-manifest.json'
OLD_STATE = SRC / 'data' / 'memory-system'
TO_CONFIG = {OLD_STATE / 'config.json': 'config.json', OLD_STATE / 'installation.json': 'installation.json',
             SRC / 'data' / 'memory.env': 'memory.env', SRC / 'data' / 'store-anchors.json': 'store-anchors.json'}
SKIP_STATE = {'config.json', 'installation.json', 'writer.lock', 'index.lock', 'semantic.sock', 'semantic-daemon.lock'}
GITIGNORE = """# Local, per machine: journal, usage, index, locks, sockets. Never versioned.
state/
cache/
*.sqlite
*.sqlite-*
*.lock
*.sock
"""


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def pairs():
    """(source file, destination file) for every file that moves; symlinks reported, not followed."""
    out, links = [], []
    roots = [(SRC / 'context', DST / 'context')]
    roots += [(p, DST / 'projects' / p.parent.name / 'context') for p in sorted((SRC / 'projects').glob('*/context'))]
    roots.append((OLD_STATE, DST / 'state'))
    for src_root, dst_root in roots:
        if src_root.is_symlink():
            links.append(src_root)
            continue
        if not src_root.is_dir():
            continue
        for path in sorted(src_root.rglob('*')):
            if path.is_symlink():
                links.append(path)
            elif path.is_file():
                rel = path.relative_to(src_root)
                if src_root == OLD_STATE and (rel.parts[0] in SKIP_STATE or path.suffix == '.sock'):
                    continue
                out.append((path, dst_root / rel))
    return out, links


@contextlib.contextmanager
def writer_lock():
    """The same flock Memory.lock() takes: no journaled write runs while we copy."""
    OLD_STATE.mkdir(parents=True, exist_ok=True)
    with (OLD_STATE / 'writer.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def copy_file(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def plan(_):
    files, links = pairs()
    size = sum(s.stat().st_size for s, _ in files)
    print(f'files: {len(files)}  bytes: {size:,}')
    for label, root in (('context', SRC / 'context'), ('projects/*/context', SRC / 'projects'), ('state', OLD_STATE)):
        n = sum(1 for s, _ in files if s.is_relative_to(root) and (label != 'projects/*/context' or '/context/' in str(s)))
        print(f'  {label}: {n}')
    print(f'to config dir: {[str(p) for p in TO_CONFIG if p.exists()]}')
    print(f'symlinks (not moved; Memory rejects them anyway): {len(links)}')
    for link in links[:20]:
        print('   ', link)
    print(f'destination exists: {DST.exists()}  config exists: {(CFG_DIR / "config.json").exists()}')


def copy(args):
    if (CFG_DIR / 'config.json').exists():
        sys.exit('config already exists: already switched; use delta or rollback')
    if DST.exists() and any(DST.iterdir()):
        sys.exit(f'{DST} is not empty; refusing to copy over it (move it aside first)')
    started = time.time()
    with writer_lock():
        files, links = pairs()
        manifest = {}
        for src, dst in files:
            copy_file(src, dst)
            manifest[str(src)] = {'dst': str(dst), 'sha256': sha(src)}
        CFG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        for src, name in TO_CONFIG.items():
            if src.exists():
                copy_file(src, CFG_DIR / name)
                manifest[str(src)] = {'dst': str(CFG_DIR / name), 'sha256': sha(src)}
        os.chmod(CFG_DIR / 'memory.env', 0o600) if (CFG_DIR / 'memory.env').exists() else None
        bad = [s for s, m in manifest.items() if sha(m['dst']) != m['sha256']]
    if bad:
        sys.exit(f'hash mismatch after copy: {bad[:5]}')
    (DST / '.gitignore').write_text(GITIGNORE)
    MANIFEST.write_text(json.dumps({'copied_at': started, 'files': manifest, 'symlinks': [str(l) for l in links]}, indent=1))
    print(f'copied {len(manifest)} files, all hashes match; {len(links)} symlinks skipped; manifest {MANIFEST}')


def switch(args):
    """Writing the config file IS the cut-over: every resolver reads it on each call."""
    if not MANIFEST.exists():
        sys.exit('run copy first')
    old = json.loads((CFG_DIR / 'config.json').read_text()) if (CFG_DIR / 'config.json').exists() else {}
    if old.get('home'):
        sys.exit('already switched')
    old.update({'home': str(DST), 'state': str(DST / 'state'), 'sources': str(SRC), 'claude_projects': str(SRC / 'projects')})
    tmp = CFG_DIR / 'config.json.tmp'
    tmp.write_text(json.dumps(old, indent=2) + '\n')
    os.replace(tmp, CFG_DIR / 'config.json')
    print(f'switched: {CFG_DIR / "config.json"} -> home {DST}')
    time.sleep(args.settle)
    delta(args)


def delta(_):
    """Files the old location got after `copy` (hooks of live sessions mid-flight).

    Never overwrites: after the switch the new location is live and some writers (the Stop
    hook's transcripts) take no lock, so any check-then-copy could clobber a fresh write.
    A file absent in the new place is created exclusively (O_EXCL); one that exists there
    and differs is reported for a manual merge."""
    data = json.loads(MANIFEST.read_text())
    known = data['files']
    copied, conflicts = 0, []
    files, _ = pairs()
    for src, dst in files:
        entry = known.get(str(src))
        current = sha(src)
        if entry and entry['sha256'] == current:
            continue
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                conflicts.append(str(src))
                continue
            with os.fdopen(fd, 'wb') as out, open(src, 'rb') as inp:
                shutil.copyfileobj(inp, out)
            shutil.copystat(src, dst)
            known[str(src)] = {'dst': str(dst), 'sha256': current}
            copied += 1
        elif sha(dst) != current:
            conflicts.append(str(src))
    MANIFEST.write_text(json.dumps(data, indent=1))
    print(f'delta: {copied} new files carried over, {len(conflicts)} need a manual merge (nothing overwritten)')
    for c in conflicts:
        print('  MERGE (old copy changed after the copy, new copy exists):', c)
    if conflicts:
        print('merge each into the new copy (mem prepare/apply), then: move-to-ai-memory.py resolve <old path>...')
    return conflicts


def resolve(args):
    """Mark files as merged by hand: the old copy's current revision is recorded as seen, so
    delta stops flagging it. Only once the merged result exists in the new location."""
    data = json.loads(MANIFEST.read_text())
    targets = dict(pairs()[0])
    for raw in args.paths:
        src = Path(raw).expanduser().resolve()
        dst = targets.get(src)
        if dst is None or not dst.exists():
            sys.exit(f'{src}: not a migrated file with a copy in the new location')
        data['files'][str(src)] = {'dst': str(dst), 'sha256': sha(src), 'resolved_at': time.time()}
        print('resolved:', src)
    MANIFEST.write_text(json.dumps(data, indent=1))


def retire(args):
    cfg = json.loads((CFG_DIR / 'config.json').read_text()) if (CFG_DIR / 'config.json').exists() else {}
    if cfg.get('home') != str(DST):
        sys.exit('not switched')
    if delta(args):
        sys.exit('merge the files above first: retire would bury the only copy of those writes')
    target = ROLLBACK / time.strftime('%Y%m%dT%H%M%S')
    moves = [SRC / 'context', *sorted((SRC / 'projects').glob('*/context')), OLD_STATE,
             *[p for p in TO_CONFIG if p.parent != OLD_STATE]]
    moved = []
    for path in moves:
        if path.exists() or path.is_symlink():
            rel = path.relative_to(SRC)
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target / rel))
            moved.append(str(rel))
    (target / 'RETIRED.json').write_text(json.dumps({'at': time.time(), 'from': str(SRC), 'moved': moved}, indent=1))
    print(f'retired {len(moved)} paths to {target}')


def rollback(_):
    """Old copies back + config removed. Files changed in ~/.ai-memory since the copy are listed:
    reconcile them (journal records in ~/.ai-memory/state/journal) before deleting ~/.ai-memory."""
    data = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'files': {}}
    changed = [m['dst'] for m in data['files'].values() if Path(m['dst']).exists() and sha(m['dst']) != m['sha256']]
    retired = sorted(ROLLBACK.glob('*/RETIRED.json'))
    if retired:
        record = json.loads(retired[-1].read_text())
        clash = [rel for rel in record['moved'] if (SRC / rel).exists()]
        if clash:
            sys.exit(f'refusing to overwrite: {clash[:5]}')
        for rel in record['moved']:
            (SRC / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(retired[-1].parent / rel), str(SRC / rel))
        retired[-1].rename(retired[-1].with_name('RESTORED.json'))
    cfg = CFG_DIR / 'config.json'
    if cfg.exists():
        os.replace(cfg, CFG_DIR / f'config.json.rolled-back-{int(time.time())}')
    print('rolled back: legacy layout (~/.claude) active again')
    print(f'{len(changed)} copied files changed in {DST} since the copy; new files there are not listed:')
    for path in changed[:50]:
        print('  ', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('phase', choices=['plan', 'copy', 'switch', 'delta', 'resolve', 'retire', 'rollback'])
    parser.add_argument('paths', nargs='*', help='resolve: old-location files already merged by hand')
    parser.add_argument('--settle', type=float, default=5.0, help='seconds to wait after switch before the delta pass')
    args = parser.parse_args()
    globals()[args.phase](args)
