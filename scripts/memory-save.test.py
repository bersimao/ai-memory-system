#!/usr/bin/env python3
"""memory-save.py: the headless save. The model call is replaced by a fixed reply, so these
cases cover what the code decides on its own: allowed paths, caps, unlinked pages, the cursor,
an interrupted batch, the optional config commands, and dry runs."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from memory_core import Memory, VERSION, atomic, json_read, json_write, read  # noqa: E402

spec = importlib.util.spec_from_file_location('memory_save', HERE / 'memory-save.py')
save = importlib.util.module_from_spec(spec)
spec.loader.exec_module(save)

SID = 'sess-1'


def turn(role, text):
    return json.dumps({'type': role, 'message': {'role': role, 'content': text}})


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root, self.cwd = base / 'root', base / 'repo'
        (self.cwd / '.git').mkdir(parents=True)
        env = patch.dict(os.environ, {'AI_MEMORY_HOME': str(self.root), 'AI_MEMORY_CONFIG': str(base / 'none.json')})
        env.start()
        self.addCleanup(env.stop)
        self.memory = Memory()
        self.store = self.memory.resolve(self.cwd)['relative']
        self.index = self.store + '/context/MEMORY.md'
        atomic(self.memory.path(self.index), '# Working memory\n- old line\n')
        self.jsonl = save.projects_dir(self.memory) / 'p' / (SID + '.jsonl')
        self.jsonl.parent.mkdir(parents=True)
        self.jsonl.write_text(turn('user', 'we decided X') + '\n' + turn('assistant', 'ok, X because Y') + '\n')
        self.cursor = self.memory.state / 'save-cursor' / (SID + '.json')

    def config(self, **keys):
        json_write(self.memory.state / 'config.json', keys)  # single-root config

    def run_save(self, reply, *extra, fail=None):
        def fake(memory, source, instruction, **kw):
            self.instruction = instruction
            if fail:
                raise RuntimeError(fail)
            kw['meta'].update({'total_cost_usd': 0.01})
            return reply
        argv = ['memory-save.py', '--session', SID, '--cwd', str(self.cwd), *extra]
        out = io.StringIO()
        with patch.object(save._maintain, 'generate', fake), patch.object(sys, 'argv', argv), redirect_stdout(out):
            code = save.main()
        return code, out.getvalue()

    def reply(self, saves=(), main=(), left=()):
        return {'saves': list(saves), 'main_session': list(main), 'left_out': list(left)}

    def test_allowed_paths(self):
        s = self.store
        for ok in ('context/USER.md', 'context/MEMORY.md', s + '/context/MEMORY.md', s + '/context/topics/a-b.md'):
            self.assertTrue(save.allowed(ok, s), ok)
        for bad in ('projects/other/context/MEMORY.md', s + '/context/topics/../../x.md',
                    s + '/context/topics/sub/a.md', 'skills/db/SKILL.md', s + '/context/notes.md'):
            self.assertFalse(save.allowed(bad, s), bad)

    def test_append_lands_and_cursor_advances(self):
        code, out = self.run_save(self.reply([{'path': self.index, 'operation': 'append', 'text': '- X because Y',
                                               'reason': 'decision'}]))
        self.assertEqual(code, 0, out)
        self.assertEqual(read(self.memory.path(self.index)), '# Working memory\n- old line\n- X because Y\n')
        self.assertIn('saved  ' + self.index, out)
        self.assertEqual(json_read(self.cursor)['line'], 2)
        code, out = self.run_save(self.reply())
        self.assertIn('nothing new since the last /save', out)

    def test_outside_path_is_handed_back_not_written(self):
        code, out = self.run_save(self.reply([{'path': 'projects/other/context/MEMORY.md', 'operation': 'append',
                                               'text': '- leak', 'reason': 'r'}]))
        self.assertIn('MAIN [other]', out)
        self.assertIn('path outside this store', out)
        self.assertIsNone(read(self.memory.path('projects/other/context/MEMORY.md')))

    def test_over_cap_goes_back_whole_and_the_rest_lands(self):
        cap = self.memory.cap(self.index)
        big = '- ' + 'z' * cap
        code, out = self.run_save(self.reply([
            {'path': self.index, 'operation': 'append', 'text': big, 'reason': 'r'},
            {'path': 'context/USER.md', 'operation': 'append', 'text': '- likes tests', 'reason': 'r'}]))
        self.assertEqual(code, 0, out)
        self.assertIn('MAIN [cap]', out)
        self.assertIn('z' * 50, json.dumps(json_read(self.cursor)['pending']))  # full text kept, not truncated
        self.assertEqual(read(self.memory.path(self.index)), '# Working memory\n- old line\n')
        self.assertIn('- likes tests', read(self.memory.path('context/USER.md')))

    def test_new_page_needs_a_markdown_link(self):
        page = self.store + '/context/topics/thing.md'
        code, out = self.run_save(self.reply([
            {'path': page, 'operation': 'create', 'text': '# Thing\nbody', 'reason': 'r'},
            {'path': self.index, 'operation': 'append', 'text': '- mentions thing.md without a link', 'reason': 'r'}]))
        self.assertIsNone(read(self.memory.path(page)))
        self.assertIn('new page without a link', out)
        self.jsonl.write_text(self.jsonl.read_text() + turn('user', 'again') + '\n')
        code, out = self.run_save(self.reply([
            {'path': page, 'operation': 'create', 'text': '# Thing\nbody', 'reason': 'r'},
            {'path': self.index, 'operation': 'append', 'text': '- [Thing](topics/thing.md)', 'reason': 'r'}]),
            '--scope', 'x')
        self.assertEqual(read(self.memory.path(page)), '# Thing\nbody\n')

    def test_model_failure_writes_nothing_and_keeps_the_cursor(self):
        code, out = self.run_save(None, fail='quota')
        self.assertEqual(code, 1)
        self.assertIn('headless save FAILED, nothing written: quota', out)
        self.assertIn('fallback: run the in-session /save', out)
        self.assertIsNone(json_read(self.cursor))

    def test_dry_run_writes_nothing(self):
        code, out = self.run_save(self.reply([{'path': self.index, 'operation': 'append', 'text': '- X',
                                               'reason': 'r'}]), '--dry-run')
        self.assertTrue(out.startswith('DRY RUN'), out)
        self.assertEqual(read(self.memory.path(self.index)), '# Working memory\n- old line\n')
        self.assertIsNone(json_read(self.cursor))

    def test_inflight_batch_that_landed_moves_the_cursor(self):
        batch = 'b' * 32
        self.memory.apply({'version': VERSION, 'changes': [self.memory.change(
            self.index, '# Working memory\n- old line\n- landed\n', 'append', '/save-hl ' + batch + ': r')]})
        item = {'kind': 'other', 'target': '', 'what': 'kept', 'why': 'w'}
        json_write(self.cursor, {'line': 0, 'inflight': {'to_line': 2, 'batch': batch, 'pending': [item]}})
        save.settle_inflight(self.memory, self.cursor)
        state = json_read(self.cursor)
        self.assertEqual(state['line'], 2)
        self.assertNotIn('inflight', state)
        self.assertEqual(state['pending'], [item])

    def test_inflight_batch_never_journaled_rereads_the_turns(self):
        json_write(self.cursor, {'line': 0, 'inflight': {'to_line': 2, 'batch': 'c' * 32, 'pending': []}})
        save.settle_inflight(self.memory, self.cursor)
        self.assertEqual(json_read(self.cursor), {'line': 0})

    def test_rejected_batch_is_not_marked_inflight(self):
        def boom(*a, **k):
            raise ValueError('rejected')
        with patch.object(Memory, 'apply', boom):
            code, out = self.run_save(self.reply([{'path': self.index, 'operation': 'append', 'text': '- X',
                                                   'reason': 'r'}]))
        self.assertEqual(code, 1)
        self.assertIn('batch not written (all or nothing)', out)
        self.assertNotIn('inflight', json_read(self.cursor, {}))

    def test_protected_skills_only_with_config(self):
        self.run_save(self.reply())
        self.assertNotIn('protected =', self.instruction)
        self.jsonl.write_text(self.jsonl.read_text() + turn('user', 'more') + '\n')
        self.config(save_protected_command=['echo', 'alpha beta'], save_protected_hint='ask the team')
        self.run_save(self.reply())
        self.assertIn('protected = a fact owned by one of these skills: alpha beta', self.instruction)
        self.assertIn('ask the team', self.instruction)

    def test_failing_protected_command_fails_the_save(self):
        self.config(save_protected_command=['false'])
        code, out = self.run_save(self.reply())
        self.assertEqual(code, 1)
        self.assertIn('headless save FAILED', out)

    def test_old_synced_skill_items_read_as_protected(self):
        json_write(self.cursor, {'line': 2, 'pending': [{'kind': 'synced-skill', 'target': 'db', 'what': 'f', 'why': 'w'}, 'legacy']})
        code, out = self.run_save(self.reply())
        self.assertIn('MAIN [protected] - NEEDS THE USER', out)
        self.assertIn('MAIN [other]', out)

    def test_clear_pending_by_id(self):
        a = {'kind': 'protected', 'target': 'db', 'what': 'a', 'why': 'w'}
        b = {'kind': 'other', 'target': '', 'what': 'b', 'why': 'w'}
        json_write(self.cursor, {'line': 2, 'pending': [a, b]})
        self.run_save(None, '--clear-pending', save.item_id(b))
        self.assertEqual(json_read(self.cursor)['pending'], [a])

    def test_status_command_is_optional_and_printed_last(self):
        code, out = self.run_save(self.reply())
        self.assertNotIn('status', out.splitlines()[-1])
        self.jsonl.write_text(self.jsonl.read_text() + turn('user', 'more') + '\n')
        self.config(save_status_command=['echo', 'notes clean for'])
        code, out = self.run_save(self.reply())
        self.assertEqual(out.splitlines()[-1], 'notes clean for ' + str(self.cwd.resolve()))
        self.config(save_status_command=['false'])
        self.assertIn('status: command failed', save.status_line(Memory(), self.cwd))

    def test_possible_secret_goes_back_and_the_rest_lands(self):
        code, out = self.run_save(self.reply([
            {'path': self.index, 'operation': 'append', 'text': '- SL auth uses Bearer tokens', 'reason': 'r'},
            {'path': 'context/USER.md', 'operation': 'append', 'text': '- likes tests', 'reason': 'r'}]))
        self.assertEqual(code, 0, out)
        self.assertIn('looks like a secret', out)
        self.assertEqual(read(self.memory.path(self.index)), '# Working memory\n- old line\n')
        self.assertIn('- likes tests', read(self.memory.path('context/USER.md')))

    def test_link_in_the_global_index_does_not_count(self):
        atomic(self.memory.path('context/MEMORY.md'), '# Global\n')
        page = self.store + '/context/topics/x.md'
        self.run_save(self.reply([
            {'path': page, 'operation': 'create', 'text': '# X', 'reason': 'r'},
            {'path': 'context/MEMORY.md', 'operation': 'append', 'text': '- [X](topics/x.md)', 'reason': 'r'}]))
        self.assertIsNone(read(self.memory.path(page)))

    def test_link_from_a_hub_uses_the_bare_name(self):
        hub = self.store + '/context/topics/hub.md'
        atomic(self.memory.path(hub), '# Hub\n')
        page = self.store + '/context/topics/x.md'
        self.run_save(self.reply([
            {'path': page, 'operation': 'create', 'text': '# X', 'reason': 'r'},
            {'path': hub, 'operation': 'append', 'text': '- [X](x.md)', 'reason': 'r'}]))
        self.assertEqual(read(self.memory.path(page)), '# X\n')

    def test_create_on_an_existing_page_is_handed_back(self):
        page = self.store + '/context/topics/x.md'
        atomic(self.memory.path(page), '# X\nold\n')
        code, out = self.run_save(self.reply([{'path': page, 'operation': 'create', 'text': '# X\nnew', 'reason': 'r'}]))
        self.assertEqual(read(self.memory.path(page)), '# X\nold\n')
        self.assertIn('create asked but the page exists', out)

    def test_big_delta_is_read_in_parts(self):
        self.jsonl.write_text('\n'.join(turn('user', str(i) * 60) for i in range(10)) + '\n')
        with patch.object(save, 'MAX_CHARS', 200):
            code, out = self.run_save(self.reply())
            self.assertIn('PARTIAL: lines 0-', out)
            first = json_read(self.cursor)['line']
            self.assertTrue(0 < first < 10, first)
            self.run_save(self.reply())
            self.assertGreater(json_read(self.cursor)['line'], first)

    def test_malformed_reply_fails_cleanly(self):
        code, out = self.run_save({'saves': [{'path': self.index, 'content': 'x'}], 'main_session': [], 'left_out': []})
        self.assertEqual(code, 1)
        self.assertIn('headless save FAILED, nothing written: KeyError', out)
        self.assertIsNone(json_read(self.cursor))

    def test_session_id_must_be_plain(self):
        for bad in ('', '*', '../x'):
            with patch.object(sys, 'argv', ['m', '--session', bad, '--cwd', str(self.cwd)]), \
                    redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit):
                    save.main()

    def test_unsettled_batch_says_not_to_save_in_session(self):
        json_write(self.cursor, {'line': 0, 'inflight': {'to_line': 2, 'batch': 'd' * 32, 'pending': []}})
        with patch.object(save, 'journal_status', lambda m, b: 'prepared'):
            code, out = self.run_save(self.reply())
        self.assertEqual(code, 1)
        self.assertIn('do NOT run the in-session /save', out)


    def test_one_huge_turn_says_it_was_clipped(self):
        self.jsonl.write_text(turn('user', 'x' * 500) + '\n')
        with patch.object(save, 'MAX_CHARS', 200):
            code, out = self.run_save(self.reply())
        self.assertIn('CLIPPED: one turn over 200 chars', out)

if __name__ == '__main__':
    unittest.main()
