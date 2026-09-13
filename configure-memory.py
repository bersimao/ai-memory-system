#!/usr/bin/env python3
"""Merge only owned lifecycle hooks; preserve unrelated host configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

OWNED = ('memory-inject.js', 'daily-log-nudge.js', 'memory-lifecycle.py')


def merge(target, command, events):
    data = json.loads(target.read_text()) if target.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
        raise ValueError('invalid hook configuration')
    before = json.dumps(data, sort_keys=True)
    groups = data.setdefault('hooks', {})
    for event, matchers in list(groups.items()):
        kept = []
        for group in matchers:
            handlers = [h for h in group.get('hooks', []) if not any(name in h.get('command', '') for name in OWNED)]
            if handlers:
                kept.append({**group, 'hooks': handlers})
        groups[event] = kept
    for event in events:
        groups.setdefault(event, []).append({'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]})
    if before == json.dumps(data, sort_keys=True):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + '.memory-backup-' + hashlib.sha256(target.read_bytes()).hexdigest()[:12])
        if not backup.exists():
            shutil.copy2(target, backup)
    fd, name = tempfile.mkstemp(dir=target.parent)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    os.replace(name, target)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--codex-home')
    parser.add_argument('--semantic-updates', action='store_true')
    parser.add_argument('--scope', choices=['all', 'project'], default='project')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    command = 'env AI_MEMORY_HOME=' + shlex.quote(str(root)) + ' python3 ' + shlex.quote(str(root / 'hooks/memory-lifecycle.py'))
    events = ['SessionStart', 'UserPromptSubmit', 'Stop', 'PreCompact']
    merge(root / 'settings.json', command, events)
    if args.codex_home:
        merge(Path(args.codex_home) / 'hooks.json', command, events)
    cfg = root / 'data/memory-system/config.json'
    current = json.loads(cfg.read_text()) if cfg.exists() else {}
    current['automatic_recall_scope'] = args.scope
    if args.semantic_updates:
        current['post_write_index'] = True
    cfg.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=cfg.parent)
    with os.fdopen(fd, 'w') as stream:
        json.dump(current, stream, indent=2)
        stream.write('\n')
    os.replace(name, cfg)
    print('Memory hooks configured. In Codex, review/trust their definitions in /hooks if requested.')
