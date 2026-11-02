#!/usr/bin/env python3
"""Backfill registry/remotes/: git remote -> store, for the stores that existed before it.

A store's name is its encoded anchor path, which is not reversible, so the anchor dirs are
recovered from what recorded a cwd: Claude Code JSONL (`cwd` field), Codex rollouts
(`session_meta`), demands.json workdirs, store pins, and git repos under ~/ai, ~/repos and
repos_local. Each candidate is resolved with
project-store.js on THIS machine; it is registered only when it resolves to an existing store.
First writer wins, so re-running is safe.

  register-remotes.py            register and report
  register-remotes.py --dry-run  report only
"""
import glob
import json
import os
from pathlib import Path
import subprocess
import sys

HOOK = Path.home() / '.claude' / 'hooks' / 'project-store.js'


def node(*args):
    out = subprocess.run(['node', str(HOOK), *args], capture_output=True, text=True, timeout=20)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip().startswith('{') else None


def first_cwd(path, key):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for _, line in zip(range(400), f):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                cwd = key(event)
                if isinstance(cwd, str) and cwd.startswith('/'):
                    return cwd
    except OSError:
        pass
    return None


def candidates(layout):
    found = set()
    for jsonl in glob.glob(os.path.join(layout['claude_projects'], '*', '*.jsonl')):
        cwd = first_cwd(jsonl, lambda e: e.get('cwd'))
        if cwd:
            found.add(cwd)
    for roll in glob.glob(os.path.expanduser('~/.codex/sessions/*/*/*/rollout-*.jsonl')):
        cwd = first_cwd(roll, lambda e: (e.get('payload') or {}).get('cwd') if e.get('type') == 'session_meta' else None)
        if cwd:
            found.add(cwd)
    demands = json.loads(Path(layout['demands']).read_text()) if Path(layout['demands']).is_file() else {}
    for d in demands.get('demands', []):
        for w in [d.get('workdir'), *[x.get('workdir') for x in d.get('workdirs') or []]]:
            if w:
                found.add(w)
    anchors = json.loads(Path(layout['anchors']).read_text()) if Path(layout['anchors']).is_file() else {}
    found.update(anchors)
    # Repos with no recent session log: any git repo under the usual roots (depth 4).
    for base in (Path.home() / 'ai', Path.home() / 'repos', *Path('/mnt/c/Users').glob('*/repos_local')):
        for git in [*base.glob('*/.git'), *base.glob('*/*/.git'), *base.glob('*/*/*/.git'), *base.glob('*/*/*/*/.git')]:
            found.add(str(git.parent))
    return sorted(c for c in found if os.path.isdir(c) and not c.startswith('/tmp/'))


def main():
    dry = '--dry-run' in sys.argv
    layout = json.loads(subprocess.run([sys.executable, str(HOOK.parent.parent / 'scripts/memory-paths.py'), '--json'],
                                       capture_output=True, text=True, check=True).stdout)
    projects = Path(layout['root']) / 'projects'
    stores = {p.name for p in projects.iterdir() if p.is_dir()}
    covered, results = set(), []
    for cwd in candidates(layout):
        r = node('--json', '--resolve', cwd)
        if not r:
            continue
        store = Path(r['dir']).name
        if store not in stores or r['anchor']['source'] == 'cwd':
            continue
        if dry:
            res = {'status': 'dry-run', 'remote': r.get('remote') or ''}
        else:
            res = node('--register', cwd) or {}
        results.append((store, r['anchor']['dir'], res.get('status'), res.get('remote'), res.get('registered')))
        if res.get('remote'):
            covered.add(store)
    seen = set()
    for store, anchor, status, remote, other in sorted(results):
        if (store, anchor) in seen:
            continue
        seen.add((store, anchor))
        print(f'{status:10} {store}  <- {anchor}  {remote or ""}{"  (registered to " + other + ")" if other else ""}')
    rest = sorted(s for s in stores - covered if not s.startswith('legacy-codex-'))
    print(f'\n{len(covered)} of {len(stores)} stores reachable by remote; {len(rest)} need `--bind` on another PC:')
    for s in rest:
        print('  ', s)


if __name__ == '__main__':
    main()
