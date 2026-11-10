#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
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

    def test_missing_node_fails_before_any_write(self):
        bin_dir = Path(self.temp.name) / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'python3').symlink_to(sys.executable)
        result = subprocess.run(['python3', str(HERE / 'install-memory.py'), '--root', str(self.root), '--yes'],
                                capture_output=True, text=True, env={'PATH': str(bin_dir)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('node', result.stderr)
        self.assertFalse(self.root.exists())

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
        reads = [g for g in data['hooks']['PostToolUse'] if any('memory-read.py' in h['command'] for h in g['hooks'])]
        self.assertEqual([g.get('matcher') for g in reads], ['Read'])  # once after two installs, on the Read tool only
        self.assertNotIn('memory-read.py', (self.codex / 'hooks.json').read_text())
        self.assertEqual(private.read_bytes(), b'Private facts\r\nNever reverse this.\r\n')
        self.assertTrue((self.root / 'CLAUDE.md').read_text().startswith('My own instructions.'))
        self.assertEqual((self.root / 'CLAUDE.md').read_text().count('<!-- memory-system:instructions -->'), 1)
        self.assertEqual(json.loads((self.root / 'data/memory-system/config.json').read_text())['automatic_recall_scope'], 'all')
        self.assertIn(str(self.root), (self.codex / 'AGENTS.md').read_text())
        self.assertTrue(list((self.root / 'data/memory-system/install-backups').glob('*/manifest.json')))

    def test_instructions_imported_from_the_shared_file_are_not_written_twice(self):
        self.root.mkdir()
        mine = 'My rules.\n\n@' + str(self.codex / 'AGENTS.md') + '\n\nClaude only.\n'
        (self.root / 'CLAUDE.md').write_text(mine)
        for _ in range(2):
            self.assertEqual(self.run_install('--yes').returncode, 0)
        self.assertEqual((self.root / 'CLAUDE.md').read_text(), mine)
        self.assertEqual((self.codex / 'AGENTS.md').read_text().count('<!-- memory-system:instructions -->'), 1)
        # A block left by an install made before the import existed is taken out, the rest kept.
        block = (self.codex / 'AGENTS.md').read_text()
        (self.root / 'CLAUDE.md').write_text(mine + '\n' + block[block.index('<!-- memory-system'):] + '\nAfter.\n')
        self.assertEqual(self.run_install('--yes').returncode, 0)
        text = (self.root / 'CLAUDE.md').read_text()
        self.assertNotIn('memory-system:instructions', text)
        self.assertTrue(text.startswith(mine) and text.endswith('After.\n'))

    def test_a_block_carried_by_another_imported_file_is_refreshed_there(self):
        self.root.mkdir()
        shared = Path(self.temp.name) / 'shared.md'
        shared.write_text('Shared rules.\n\n<!-- memory-system:instructions -->\nOLD RELEASE TEXT\n<!-- /memory-system:instructions -->\n')
        (self.root / 'CLAUDE.md').write_text('@' + str(shared) + '\n')
        self.assertEqual(self.run_install('--yes').returncode, 0)
        self.assertNotIn('memory-system', (self.root / 'CLAUDE.md').read_text())
        self.assertNotIn('OLD RELEASE TEXT', shared.read_text())
        self.assertTrue(shared.read_text().startswith('Shared rules.'))
        self.assertEqual(shared.read_text().count('<!-- memory-system:instructions -->'), 1)

    def test_an_import_that_carries_no_block_does_not_stop_the_write(self):
        self.root.mkdir()
        (self.root / 'other.md').write_text('unrelated notes\n')
        (self.root / 'CLAUDE.md').write_text('@other.md\n```\n@' + str(self.codex / 'AGENTS.md') + '\n```\n')
        self.assertEqual(self.run_install('--yes').returncode, 0)
        self.assertEqual((self.root / 'CLAUDE.md').read_text().count('<!-- memory-system:instructions -->'), 1)

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
