#!/usr/bin/env python3
"""Layout resolution: memory_core.layout() and project-store.js must agree, and in the split
layout every memory write lands under the configured home, never under sources (~/.claude).

Runs in a throwaway HOME: no real config, store or skill is read or written.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

CODE = Path(__file__).resolve().parent.parent
fails = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f': {detail}' if detail and not ok else ''))
    if not ok:
        fails.append(name)


FAKE = CODE


def py_layout(env):
    out = subprocess.run([sys.executable, str(FAKE / 'scripts/memory-paths.py'), '--json'],
                         env=env, text=True, capture_output=True, check=True).stdout
    return json.loads(out)


def js_layout(env):
    out = subprocess.run(['node', '-e', 'const {LAYOUT}=require(process.argv[1]);console.log(JSON.stringify(LAYOUT))',
                          str(FAKE / 'hooks/project-store.js')], env=env, text=True, capture_output=True, check=True).stdout
    return json.loads(out)


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp).resolve()
    home, sources, mem_home = tmp / 'home', tmp / 'home/.claude', tmp / 'home/.ai-memory'
    for d in (sources / 'skills/demo/knowledge', sources / 'projects', mem_home / 'context', mem_home / 'state',
              home / '.config/ai-memory'):
        d.mkdir(parents=True)
    (sources / 'skills/demo/knowledge/k.md').write_text('# Demo\n\n## Gizmo\nthe gizmo flag lives in OADM\n')
    (mem_home / 'context/MEMORY.md').write_text('# Working memory\n')
    # The fake ~/.claude carries its own copy of the code: `sources` must be the code root
    # for the split layout to apply, and a copy keeps every write out of the real one.
    for part in ('scripts', 'hooks'):
        shutil.copytree(CODE / part, sources / part, ignore=shutil.ignore_patterns('node_modules', '__pycache__', '*.test.*'))
    FAKE = sources
    base = {k: v for k, v in os.environ.items() if not k.startswith(('AI_MEMORY', 'XDG_CONFIG'))}
    base['HOME'] = str(home)

    # 1. No config: legacy single root under HOME/.claude, both sides.
    py, js = py_layout(base), js_layout(base)
    check('no config: legacy layout (python)', py['split'] is False)
    # Pre-split rule, unchanged: script entry points (mem, cron) use their own code root,
    # hooks use HOME/.claude. Identical on a normal install, where the code IS ~/.claude.
    check('no config: scripts use the code root', Path(py['root']) == sources, py['root'])
    check('no config: js uses HOME/.claude', Path(js['root']) == sources, js['root'])

    # 2. Split config.
    cfg = home / '.config/ai-memory/config.json'
    cfg.write_text(json.dumps({'home': str(mem_home), 'sources': str(sources), 'claude_projects': str(sources / 'projects')}))
    py, js = py_layout(base), js_layout(base)
    check('split: python root is the configured home', Path(py['root']) == mem_home, py['root'])
    check('split: python state is home/state', Path(py['state']) == mem_home / 'state', py['state'])
    check('split: js agrees on root/state/anchors',
          (Path(js['root']), Path(js['state']), Path(js['anchors'])) ==
          (mem_home, mem_home / 'state', home / '.config/ai-memory/store-anchors.json'), json.dumps(js))
    check('split: config file is outside the home', not Path(py['config']).is_relative_to(mem_home))

    # 3. Legacy hook pin AI_MEMORY_HOME=<sources> maps to the live home, on both sides.
    pinned = {**base, 'AI_MEMORY_HOME': str(sources)}
    check('pin ~/.claude: python maps to home', Path(py_layout(pinned)['root']) == mem_home)
    check('pin ~/.claude: js maps to home', Path(js_layout(pinned)['root']) == mem_home)

    # 4. Any other explicit root stays an isolated legacy root (tests, --root installs).
    other = tmp / 'other'
    other.mkdir()
    iso = {**base, 'AI_MEMORY_HOME': str(other)}
    py = py_layout(iso)
    check('explicit other root: legacy, not the configured home', Path(py['root']) == other and py['split'] is False)
    check('explicit other root: js agrees', Path(js_layout(iso)['root']) == other)

    # 4b. A second install whose code root is not the configured `sources` keeps its own root.
    check('other install: real code root stays legacy under a foreign split config',
          Path(json.loads(subprocess.run([sys.executable, str(CODE / 'scripts/memory-paths.py'), '--json'], env=base,
                                         text=True, capture_output=True, check=True).stdout)['root']) == CODE)

    # 5. Split writes and search: nothing lands under sources; skills are still searchable.
    before = tree(sources)
    work = tmp / 'repo'
    (work / '.git').mkdir(parents=True)
    mem = [str(FAKE / 'scripts/mem')]
    env = {**base}
    store = json.loads(subprocess.run(['node', str(FAKE / 'hooks/project-store.js'), '--json', '--resolve', str(work)],
                                      env=env, text=True, capture_output=True, check=True).stdout)
    check('split: store dir under home/projects', Path(store['dir']).parent == mem_home / 'projects', store['dir'])
    rel = str(Path(store['dir']).relative_to(mem_home)) + '/context/MEMORY.md'
    note = tmp / 'note.md'
    note.write_text('\n- widget decision recorded for layout test\n')
    prop = subprocess.run(mem + ['prepare', rel, '--text-file', str(note), '--operation', 'create', '--reason', 'layout test'],
                          env=env, cwd=work, text=True, capture_output=True)
    check('split: prepare works', prop.returncode == 0, prop.stderr)
    (tmp / 'p.json').write_text(prop.stdout)
    applied = subprocess.run(mem + ['apply', str(tmp / 'p.json')], env=env, cwd=work, text=True, capture_output=True)
    check('split: apply works', applied.returncode == 0, applied.stderr)
    check('split: page written under home', (mem_home / rel).is_file())
    check('split: journal under home/state', any((mem_home / 'state/journal').rglob('*.json')))
    found = subprocess.run(mem + ['search', 'gizmo flag OADM', '--scope', 'all', '--json'], env=env, cwd=work,
                           text=True, capture_output=True)
    hits = json.loads(found.stdout or '{}').get('results', []) if found.returncode == 0 else []
    check('split: skill knowledge still found by logical name',
          any(h['source'] == 'skills/demo/knowledge/k.md' for h in hits), found.stderr or json.dumps(hits)[:300])
    if hits:
        rid = json.loads(found.stdout)['request_id']
        exp = subprocess.run(mem + ['expand', f"{rid}:{hits[0]['id']}"], env=env, cwd=work, text=True, capture_output=True)
        check('split: expand reads the skill page', exp.returncode == 0 and 'gizmo' in exp.stdout, exp.stderr)
    check('split: nothing written under sources (~/.claude)', tree(sources) == before,
          str(sorted(set(tree(sources)) - set(before))))

print('PASS' if not fails else f'FAILED: {len(fails)}')
sys.exit(1 if fails else 0)
