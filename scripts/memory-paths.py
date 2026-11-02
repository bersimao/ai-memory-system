#!/usr/bin/env python3
"""Print memory_core.layout() for shell scripts and hooks.

  eval "$(python3 ~/.claude/scripts/memory-paths.py --sh)"   # AI_MEMORY_HOME, AI_MEMORY_STATE, ...
  python3 memory-paths.py --json
"""
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_core import script_layout  # noqa: E402

KEYS = {'root': 'AI_MEMORY_HOME', 'state': 'AI_MEMORY_STATE', 'config': 'AI_MEMORY_CONFIG_FILE',
        'config_dir': 'AI_MEMORY_CONFIG_DIR', 'sources': 'AI_MEMORY_SOURCES', 'claude_projects': 'AI_MEMORY_CLAUDE_PROJECTS',
        'env': 'AI_MEMORY_ENV', 'code': 'AI_MEMORY_CODE'}

if __name__ == '__main__':
    found = script_layout()
    if '--json' in sys.argv:
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in found.items()}, indent=2))
    else:
        for key, var in KEYS.items():
            print(f'export {var}={shlex.quote(str(found[key]))}')
