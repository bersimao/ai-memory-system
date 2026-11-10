#!/usr/bin/env python3
"""PostToolUse(Read): log that the agent actually read a curated memory page.

Recall prints `Source: <abs path>:<line>` and the agent opens it with Read; nothing recorded that, so
salience counted "was injected" as use and missed real reading (83 open events against 2,604 injected
hits, 2026-11-10). One `read` event per curated page read. Silent always: no stdout, exit 0.
"""
import json
import os
import re
import sys
from pathlib import Path

# Runs on every Read of every session: decide from the path string and the small config file before
# importing the core (which compiles regexes and may start node to resolve the store).
SKILL_PAGE = re.compile(r'/skills/[^/]+/(?:knowledge|references)/.+\.md$')


def memory_home():
    """The configured home (split layout). Not AI_MEMORY_HOME first: hook commands pin it to ~/.claude,
    and that prefix would send every Read of a script through the full import. Only without a config
    (the legacy single-root layout the tests use) does AI_MEMORY_HOME name the store."""
    explicit = os.environ.get('AI_MEMORY_CONFIG')
    config = Path(explicit).expanduser() if explicit else \
        Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'ai-memory' / 'config.json'
    try:
        home = json.loads(config.read_text(encoding='utf-8')).get('home')
    except (OSError, ValueError, AttributeError):
        home = None
    home = home or os.environ.get('AI_MEMORY_HOME')
    return str(Path(home).expanduser()) if home else None


def candidate(path):
    if not isinstance(path, str) or not path.endswith('.md'):
        return False
    home = memory_home()
    return bool(home and path.startswith(home.rstrip('/') + '/') and '/context/' in path) or bool(SKILL_PAGE.search(path))


def spawned_by_claude_code(event):
    # Same guard as memory-lifecycle.py: a headless Codex review run from Claude Code is not a reader.
    try:
        with open(event.get('transcript_path'), 'rb') as stream:
            first = json.loads(stream.readline())
    except (TypeError, OSError, ValueError):
        return False
    return (isinstance(first, dict) and first.get('type') == 'session_meta'
            and (first.get('payload') or {}).get('originator') == 'Claude Code')


CURATED = re.compile(r'(?:context|projects/[^/]+/context)/.+\.md|skills/[^/]+/(?:knowledge|references)/.+\.md')


def handle(event):
    path = (event.get('tool_input') or {}).get('file_path')
    session = event.get('session_id')
    if not session or not candidate(path) or spawned_by_claude_code(event):
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
    from memory_core import Memory
    memory = Memory()
    if not Path(path).is_file() or not memory.stored(path):
        return
    rel = memory.logical(path)
    if not CURATED.fullmatch(rel) or {'transcripts', 'checkpoints'} & set(Path(rel).parts):
        return
    event_out = {'event': 'read', 'source': rel, 'session': session}
    if event.get('cwd'):  # whose store read it: salience weighs a read from another project like a cross recall
        try:
            event_out['store'] = memory.resolve(event['cwd'])['relative']
        except Exception:
            pass
    memory.event(event_out)


if __name__ == '__main__':
    try:
        handle(json.load(sys.stdin))
    except Exception:  # a memory log must never cost the user's tool call anything
        pass
    sys.exit(0)
