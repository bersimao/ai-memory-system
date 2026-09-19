#!/usr/bin/env python3
"""Install mechanism only, with backups. Private content never enters the release."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BEGIN = '<!-- memory-system:instructions -->'
END = '<!-- /memory-system:instructions -->'


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, path)


def instructions(old, body):
    block = BEGIN + '\n\n' + body.strip() + '\n' + END
    if BEGIN in old:
        if END not in old or old.index(END) < old.index(BEGIN):
            raise ValueError('unclosed memory instructions')
        return old[:old.index(BEGIN)] + block + old[old.index(END) + len(END):]
    return old.rstrip() + '\n\n' + block + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('CLAUDE_HOME', str(Path.home() / '.claude')))
    parser.add_argument('--codex-home')
    parser.add_argument('--semantic-updates', action='store_true')
    parser.add_argument('--scope', choices=['project', 'all'], default='project')
    parser.add_argument('--yes', '-y', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # node runs the hooks, git anchors the per-repo store, bash runs the cron
    # wrappers. Without this check a machine lacking node printed "Installed"
    # and every hook then failed at session start.
    missing = [dep for dep in ('node', 'git', 'bash') if shutil.which(dep) is None]
    if missing:
        sys.exit('missing dependency: ' + ', '.join(missing) + ' (install it and re-run)')
    root = Path(args.root).expanduser().resolve()
    codex = Path(args.codex_home).expanduser().resolve() if args.codex_home else None
    manifest = re.search(r'^MANIFEST=\(\n(.*?)\n\)', (HERE / 'sync-release.sh').read_text(), re.M | re.S).group(1).split()
    if any(not (HERE / name).is_file() for name in manifest):
        raise ValueError('release contains missing mechanism files')
    body = (HERE / 'docs/memory-instructions.md').read_text().split('\n---\n', 1)[1].replace('~/.claude', str(root))
    skill = (HERE / 'skills/memory-write/SKILL.md').read_text().replace('~/.claude', str(root))
    writes = {root / name: (HERE / name).read_bytes() for name in manifest}
    for target in [root / 'CLAUDE.md'] + ([codex / 'AGENTS.md'] if codex else []):
        writes[target] = instructions(target.read_text() if target.exists() else '', body).encode()
    writes[root / 'skills/memory-write/SKILL.md'] = skill.encode()
    if codex:
        writes[codex / 'skills/memory/SKILL.md'] = skill.replace('name: memory-write', 'name: memory', 1).encode()
        writes[codex / 'skills/memory-write/SKILL.md'] = skill.encode()
    configs = [root / 'settings.json', root / 'data/memory-system/config.json'] + ([codex / 'hooks.json'] if codex else [])
    for target in configs:
        if target.exists() and not isinstance(json.loads(target.read_text()), dict):
            raise ValueError('invalid config: ' + str(target))
    print(json.dumps({'root': str(root), 'codex_home': str(codex) if codex else None,
                      'scope': args.scope, 'files': len(writes), 'schedules': 'preserved'}, indent=2))
    if args.dry_run or not args.yes:
        print('Preview only. Use --yes to install.')
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = root / 'data/memory-system/install-backups' / stamp
    backup.mkdir(parents=True, mode=0o700)
    originals = {}
    for i, target in enumerate([*writes, *configs]):
        saved = backup / str(i)
        if target.exists():
            shutil.copy2(target, saved)
        originals[str(target)] = str(saved) if saved.exists() else None
    atomic(backup / 'manifest.json', json.dumps(originals, indent=2).encode())
    try:
        for target, content in writes.items():
            atomic(target, content)
            if target.suffix in ('.sh', '.py') or target.name in ('mem', 'skill-grep', 'llm-run'):
                target.chmod(0o755)
        command = ['python3', str(HERE / 'configure-memory.py'), '--root', str(root), '--scope', args.scope]
        if args.semantic_updates:
            command += ['--semantic-updates']
        if codex:
            command += ['--codex-home', str(codex)]
        subprocess.run(command, check=True)
        installed = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in manifest}
        atomic(root / 'data/memory-system/installation.json', json.dumps({'installed_at': stamp, 'files': installed,
               'backup_manifest': str(backup / 'manifest.json')}, indent=2).encode())
    except Exception:
        for target, saved in originals.items():
            path = Path(target)
            if saved:
                atomic(path, Path(saved).read_bytes())
                shutil.copymode(saved, path)
            elif path.exists():
                path.unlink()
        raise
    print('Installed. Restart sessions; review Codex hook trust in /hooks. Backup: ' + str(backup))


if __name__ == '__main__':
    main()
