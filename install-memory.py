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


def imported(path):
    """Files a Claude Code instructions file imports with a whole-line `@path` (fenced code skipped):
    `~` expanded, a relative path read from the file's own directory."""
    if not path.exists():
        return []
    text = re.sub(r'(?ms)^```.*?^```[^\n]*$', '', path.read_text())
    return [(path.parent / Path(raw).expanduser()).resolve() for raw in re.findall(r'(?m)^[ \t]*(?:-[ \t]+)?@(\S+)[ \t]*$', text)]


def instruction_writes(root, codex, body):
    """{target: bytes} for the instruction files. One shared file is a supported layout: when the
    Claude Code file imports a file that carries the block (the Codex one, about to get it, or any
    file that already has it), a block of its own would be loaded twice in every session. It then
    gets none, and one written by an earlier install is taken out."""
    writes = {}
    agents = (codex / 'AGENTS.md') if codex else None
    if agents:
        writes[agents] = instructions(agents.read_text() if agents.exists() else '', body).encode()
    claude = root / 'CLAUDE.md'
    old = claude.read_text() if claude.exists() else ''
    carried = False
    for target in imported(claude):
        if agents and target == agents.resolve():
            carried = True
        elif target.is_file() and BEGIN in target.read_text():
            # The block lives in a file this install would not otherwise touch (a shared file, or the
            # Codex one when it was not named): refresh it there, or an upgrade leaves the old text loaded.
            carried = True
            writes[target] = instructions(target.read_text(), body).encode()
    if not carried:
        writes[claude] = instructions(old, body).encode()
    elif BEGIN in old:
        if END not in old or old.index(END) < old.index(BEGIN):
            raise ValueError('unclosed memory instructions')
        writes[claude] = (old[:old.index(BEGIN)].rstrip() + '\n' + old[old.index(END) + len(END):].lstrip('\n')).encode()
    return writes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('CLAUDE_HOME', str(Path.home() / '.claude')))
    parser.add_argument('--codex-home')
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
    manifest = re.search(r'^MANIFEST=\(\n(.*?)\n\)', (HERE / 'sync-release.sh').read_text(), re.M | re.S).group(1)
    manifest = [name for line in manifest.splitlines() for name in line.split('#')[0].split()]  # bash comments are not files
    if any(not (HERE / name).is_file() for name in manifest):
        raise ValueError('release contains missing mechanism files')
    body = (HERE / 'docs/memory-instructions.md').read_text().split('\n---\n', 1)[1].replace('~/.claude', str(root))
    skill = (HERE / 'skills/memory-write/SKILL.md').read_text().replace('~/.claude', str(root))
    writes = {root / name: (HERE / name).read_bytes() for name in manifest}
    writes.update(instruction_writes(root, codex, body))
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
            if target.suffix in ('.sh', '.py') or target.name in ('mem', 'skill-grep'):
                target.chmod(0o755)
        command = ['python3', str(HERE / 'configure-memory.py'), '--root', str(root), '--scope', args.scope]
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
