#!/usr/bin/env python3
"""Import active legacy SQLite records without guessing repository anchors.

Preview by default. The source database is opened read-only and is never changed.
Legacy project namespaces preserve original labels until explicitly reconciled.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
from memory_core import Memory, VERSION, digest, read, redact


def proposal(memory, database):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = [dict(row) for row in connection.execute("SELECT * FROM memories WHERE status = 'active' ORDER BY id")]
    finally:
        connection.close()
    grouped = {}
    for row in records:
        label = row.get('project') or row.get('client') or 'shared'
        slug = re.sub(r'[^a-zA-Z0-9_-]', '-', label)[:60] + '-' + digest(label)[:8]
        relative = 'context/legacy-codex.md' if row['scope'] == 'global' else 'projects/legacy-codex-' + slug + '/context/topics/imported.md'
        grouped.setdefault(relative, []).append(row)
    changes = []
    for relative, rows in grouped.items():
        old = read(memory.path(relative)) or ''
        addition = ''
        for row in rows:
            marker = '<!-- legacy-codex:' + digest(json.dumps(row, sort_keys=True)) + ' -->'
            if marker in old:
                continue
            addition += '\n' + marker + '\n## ' + row['title'] + '\n'
            addition += 'Legacy project: ' + str(row.get('project')) + '; scope: ' + row['scope'] + '\n'
            addition += 'Original record: ' + row['id'] + '; updated: ' + row['updated_at'] + '\n'
            addition += row['content'] + '\n'
            addition += '\n<details><summary>Original record metadata</summary>\n\n```json\n' + json.dumps({k:v for k,v in row.items() if k != 'content'}, ensure_ascii=False, indent=2) + '\n```\n</details>\n'
        if addition:
            if redact(addition) != addition:
                raise ValueError('possible secret in legacy data; review before importing')
            changes.append(memory.change(relative, old + addition, 'append', 'preserve legacy Codex records; original database retained'))
    return {'version': VERSION, 'changes': changes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--root')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    memory = Memory(args.root)
    proposed = proposal(memory, args.database)
    if args.apply and proposed['changes']:
        print(json.dumps(memory.apply(proposed)))
    else:
        print(json.dumps({'targets': [c['path'] for c in proposed['changes']], 'changes': len(proposed['changes']), 'applied': False}, indent=2))
