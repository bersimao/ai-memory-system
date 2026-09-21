#!/usr/bin/env python3
"""Read-only installation/corpus health. No network and no model invocation."""
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from memory_core import Memory, CODE, VERSION, digest, json_read, read


def contexts(root):
    """The real context dirs, found without following links: (links met on the way, dirs).
    Everything below walks only these, so no check reads or lists through a symlink."""
    links, dirs, projects = [], [], root / 'projects'
    candidates = [root / 'context']
    # A linked projects/ puts every project page behind it: read_stored() hides ALL project memory.
    if projects.is_symlink():
        links.append(projects)
    elif projects.is_dir():
        for project in sorted(projects.iterdir()):
            if project.is_symlink():
                links.append(project)
            elif project.is_dir():
                candidates.append(project / 'context')
    for ctx in candidates:
        if ctx.is_symlink():
            links.append(ctx)
        elif ctx.is_dir():
            dirs.append(ctx)
    return links, dirs


def markdown(ctx):
    """Every file under a real context dir; os.walk does not descend into linked dirs."""
    for directory, _, files in os.walk(ctx):
        yield from (Path(directory) / name for name in files)


def symlinks(root):
    """Links inside the store: Memory.read_stored() skips them as if absent, so they surface here."""
    found, dirs = contexts(root)
    for ctx in dirs:
        for directory, subdirs, files in os.walk(ctx):
            found += [p for p in (Path(directory) / n for n in subdirs + files) if p.is_symlink()]
    return sorted(found)


def orphans(root):
    """Pages no index reaches: written, but no session will ever be led to them.
    Walks links from the index roots; checkpoints and daily logs don't count as links.
    Only pages Memory.stored() accepts: one behind a symlink is never read, and symlinks() reports it.
    topics/archive/ is unindexed on purpose (still searchable), so it is never an orphan."""
    memory = Memory(root)
    root = memory.root
    pages = set()
    for ctx in contexts(root)[1]:
        for p in markdown(ctx):
            parts = p.relative_to(ctx).parts
            if (p.suffix == '.md' and memory.stored(p) and 'archive' not in parts
                    and (len(parts) == 1 or parts[0] == 'topics')):
                pages.add(p)
    store = lambda p: p.relative_to(root).parts[:2] if p.relative_to(root).parts[0] == 'projects' else ('context',)
    # [[stem]] resolves in the linking page's own store, else global context/ — never another
    # project's, or an orphan there would count as reached and go unreported.
    by_stem = {}
    for page in pages:
        by_stem.setdefault((store(page), page.stem), []).append(page)
    seen = set()
    todo = [p for p in [root / 'context/USER.md', *(ctx / 'MEMORY.md' for ctx in contexts(root)[1])] if p in pages]
    while todo:
        page = todo.pop()
        if page in seen:
            continue
        seen.add(page)
        text = memory.read_stored(page) or ''
        todo += [q for q in ((page.parent / t).resolve() for t in re.findall(r'\]\(([^)#\s]+\.md)', text)) if q in pages]
        for stem in re.findall(r'\[\[([^\]|#]+)', text):
            todo += by_stem.get((store(page), stem.strip())) or by_stem.get((('context',), stem.strip()), [])
    return sorted(pages - seen)


def diagnose(memory):
    report = {'schema': VERSION, 'root': str(memory.root), 'runtime': {}, 'files': {}, 'issues': []}
    for executable in ['python3', 'node', 'claude', 'codex']:
        if shutil.which(executable):
            p = subprocess.run([executable, '--version'], text=True, capture_output=True, timeout=10)
            report['runtime'][executable] = p.stdout.strip()
    try:
        report['runtime']['memsearch'] = importlib.metadata.version('memsearch')
    except importlib.metadata.PackageNotFoundError:
        report['runtime']['memsearch'] = 'not installed (lexical recall works)'
    for path in sorted((CODE / 'scripts').glob('memory*.py')):
        report['files'][str(path.relative_to(CODE))] = 'symlink (not read)' if path.is_symlink() else digest(read(path))
    projects = [ctx for ctx in contexts(memory.root)[1] if ctx.parent != memory.root]
    for path in (ctx / 'MEMORY.md' for ctx in projects):
        text = memory.read_stored(path) or ''
        cap_text = (memory.read_stored(path.parent / '.cap') or '').strip()
        cap = int(cap_text) if re.fullmatch('[0-9]{1,9}', cap_text) and int(cap_text) else 2500
        if len(text) > cap:
            report['issues'].append({'type': 'over_cap', 'path': str(path), 'characters': len(text), 'cap': cap})
    for path in sorted(p for ctx in projects for p in ctx.glob('*.md') if memory.stored(p)):
        text = memory.read_stored(path) or ''
        for target in re.findall(r'\]\((topics/[^)]+)\)', text):
            if not (path.parent / target.split('#')[0]).exists():
                report['issues'].append({'type': 'broken_link', 'path': str(path), 'target': target})
    for path in symlinks(memory.root):
        report['issues'].append({'type': 'symlink_in_store', 'path': str(path)})
    for path in orphans(memory.root):
        report['issues'].append({'type': 'orphan_page', 'path': str(path)})
    for path in (memory.state / 'journal').glob('*.json'):
        if json_read(path)['status'] != 'committed':
            report['issues'].append({'type': 'pending_transaction', 'path': str(path)})
    installed = json_read(memory.state / 'installation.json', {})
    for relative, expected in installed.get('files', {}).items():
        # install.sh copies, never links: a link (or a manifest path escaping the root) is drift, not something to follow.
        current = memory.read_stored(memory.root / relative)
        if current is None or digest(current) != expected:
            report['issues'].append({'type': 'installation_drift', 'path': relative})
    report['pending_semantic_updates'] = len(list((memory.state / 'dirty').glob('*.json')))
    return report


if __name__ == '__main__':
    print(json.dumps(diagnose(Memory()), ensure_ascii=False, indent=2))
