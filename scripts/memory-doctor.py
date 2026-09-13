#!/usr/bin/env python3
"""Read-only installation/corpus health. No network and no model invocation."""
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
from memory_core import Memory, CODE, VERSION, digest, json_read, read


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
        report['files'][str(path.relative_to(CODE))] = digest(read(path))
    for path in sorted((memory.root / 'projects').glob('*/context/MEMORY.md')):
        text = read(path) or ''
        cap_text = (read(path.parent / '.cap') or '').strip()
        cap = int(cap_text) if re.fullmatch('[0-9]{1,9}', cap_text) and int(cap_text) else 2500
        if len(text) > cap:
            report['issues'].append({'type': 'over_cap', 'path': str(path), 'characters': len(text), 'cap': cap})
    for path in sorted((memory.root / 'projects').glob('*/context/*.md')):
        text = read(path) or ''
        for target in re.findall(r'\]\((topics/[^)]+)\)', text):
            if not (path.parent / target.split('#')[0]).exists():
                report['issues'].append({'type': 'broken_link', 'path': str(path), 'target': target})
    for path in (memory.state / 'journal').glob('*.json'):
        if json_read(path)['status'] != 'committed':
            report['issues'].append({'type': 'pending_transaction', 'path': str(path)})
    installed = json_read(memory.state / 'installation.json', {})
    for relative, expected in installed.get('files', {}).items():
        current = read(memory.root / relative)
        if current is None or digest(current) != expected:
            report['issues'].append({'type': 'installation_drift', 'path': relative})
    report['pending_semantic_updates'] = len(list((memory.state / 'dirty').glob('*.json')))
    return report


if __name__ == '__main__':
    print(json.dumps(diagnose(Memory()), ensure_ascii=False, indent=2))
