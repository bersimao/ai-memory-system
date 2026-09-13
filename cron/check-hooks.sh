#!/usr/bin/env bash
# Validate events structurally; a filename in an unrelated event is not enough.
set -euo pipefail
python3 - <<'PYCODE'
import json, os
from pathlib import Path
root = Path(os.environ.get('AI_MEMORY_HOME', Path.home() / '.claude'))
data = json.loads((root / 'settings.json').read_text())
missing = [event for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'PreCompact')
           if not any('memory-lifecycle.py' in h.get('command', '')
                      for group in data.get('hooks', {}).get(event, []) for h in group.get('hooks', []))]
if missing:
    raise SystemExit('Missing memory lifecycle events: ' + ', '.join(missing))
print('Memory lifecycle hooks registered.')
PYCODE
