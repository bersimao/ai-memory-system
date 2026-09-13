#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='memory-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'claude'
        self.codex = Path(self.temp.name) / 'codex'

    def run_install(self, *args):
        return subprocess.run(['python3', str(HERE / 'install-memory.py'), '--root', str(self.root),
            '--codex-home', str(self.codex), '--scope', 'all', *args], capture_output=True, text=True)

    def test_preview_has_no_writes(self):
        result = self.run_install('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.codex.exists())

    def test_install_preserves_unrelated_hooks_content_and_is_idempotent(self):
        self.root.mkdir()
        unrelated = {'type': 'command', 'command': 'echo unrelated'}
        settings = {'permissions': {'allow': ['Read']}, 'hooks': {'Stop': [{'matcher': '*', 'hooks': [unrelated,
                    {'type': 'command', 'command': 'node memory-inject.js'}]}]}}
        (self.root / 'settings.json').write_text(json.dumps(settings))
        (self.root / 'CLAUDE.md').write_text('My own instructions.\n')
        private = self.root / 'context/MEMORY.md'
        private.parent.mkdir()
        private.write_bytes(b'Private facts\r\nNever reverse this.\r\n')
        for _ in range(2):
            result = self.run_install('--yes')
            self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.root / 'settings.json').read_text())
        self.assertEqual(data['permissions'], settings['permissions'])
        handlers = [h for groups in data['hooks'].values() for group in groups for h in group['hooks']]
        self.assertIn(unrelated, handlers)
        self.assertEqual(sum('memory-lifecycle.py' in h['command'] for h in handlers), 4)
        self.assertEqual(private.read_bytes(), b'Private facts\r\nNever reverse this.\r\n')
        self.assertTrue((self.root / 'CLAUDE.md').read_text().startswith('My own instructions.'))
        self.assertEqual((self.root / 'CLAUDE.md').read_text().count('<!-- memory-system:instructions -->'), 1)
        self.assertEqual(json.loads((self.root / 'data/memory-system/config.json').read_text())['automatic_recall_scope'], 'all')
        self.assertIn(str(self.root), (self.codex / 'AGENTS.md').read_text())
        self.assertTrue(list((self.root / 'data/memory-system/install-backups').glob('*/manifest.json')))

    def test_invalid_config_does_not_write_runtime(self):
        self.codex.mkdir()
        (self.codex / 'hooks.json').write_text('{broken')
        result = self.run_install('--yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'scripts/mem').exists())

    def test_failed_merge_rolls_back_written_files(self):
        self.root.mkdir()
        (self.root / 'settings.json').write_text('{"hooks": []}')
        (self.root / 'CLAUDE.md').write_text('original')
        result = self.run_install('--yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'CLAUDE.md').read_text(), 'original')
        self.assertFalse((self.root / 'scripts/mem').exists())
        self.assertEqual((self.root / 'settings.json').read_text(), '{"hooks": []}')

if __name__ == '__main__':
    unittest.main()
