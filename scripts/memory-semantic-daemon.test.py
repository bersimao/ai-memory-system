#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_core import Memory, atomic, digest, json_read


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BASE = Path(__file__).resolve().parent.parent
daemon = module('semantic_daemon', BASE / 'scripts/memory-semantic-daemon.py')
hooks = module('lifecycle_semantic', BASE / 'hooks/memory-lifecycle.py')


class SemanticDaemonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'store'
        self.cwd = Path(self.tmp.name) / 'repo'
        (self.cwd / '.git').mkdir(parents=True)
        self.memory = Memory(self.root)
        self.memory.state.mkdir(parents=True)
        self.rel = 'projects/other/context/topics/retry.md'
        self.text = '# Retry\nA chave de idempotencia fica persistida.\n'
        atomic(self.memory.path(self.rel), self.text)
        self.calls = []

    async def fake_search(self, memory, query, cwd, scope, domain, collection, limit):
        self.calls.append(query)
        return [{'id': 'h1', 'source': self.rel, 'project': 'other', 'kind': 'topic', 'line': 2,
                 'revision': digest(self.text), 'score': 0.9, 'content': 'A chave de idempotencia fica persistida.'}]

    def start(self, idle=5):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(daemon.socket_path(self.memory)))
        server.listen(1)
        thread = threading.Thread(target=daemon.serve, args=(self.memory, server, self.fake_search, idle))
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.close)
        return thread

    def ask(self, prompt):
        return hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': prompt, 'session_id': 's'})

    def test_answer_validates_and_logs_an_expandable_search(self):
        with self.assertRaisesRegex(ValueError, 'invalid'):
            daemon.answer(self.memory, {'query': ' ', 'cwd': str(self.cwd)}, self.fake_search)
        with self.assertRaisesRegex(ValueError, 'invalid'):
            daemon.answer(self.memory, {'query': 'x', 'cwd': str(self.cwd), 'limit': 50}, self.fake_search)
        result = daemon.answer(self.memory, {'query': 'retry key', 'cwd': str(self.cwd)}, self.fake_search)
        self.assertIn('persistida', self.memory.expand(result['request_id'], 'h1')['content'])

    def test_cue_prompt_gets_semantic_hit_and_plain_prompt_does_not_ask(self):
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_semantic': True, 'recall_min_score': 1000})
        self.start(idle=1)
        self.assertEqual(self.ask('how did we handle the retry key'), '')
        self.assertEqual(self.calls, [])
        output = self.ask('remember how we handled the retry key?')
        self.assertIn('Engine: semantic', output)
        self.assertIn(self.rel + ':2', output)
        self.assertNotIn('not proof of absence', output)
        recall = [e for e in map(json_read, (self.memory.state / 'usage').glob('*.json')) if e['event'] == 'recall']
        self.assertEqual(sorted(str(e['semantic']) for e in recall), ['1', 'None'])

    def test_semantic_skips_source_already_injected_lexically(self):
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_semantic': True})
        self.start(idle=1)
        output = self.ask('remember the idempotencia persistida?')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('Engine: semantic', output)
        self.assertIn(self.rel, output)

    def test_missing_daemon_starts_it_and_answers_lexically(self):
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_semantic': True, 'recall_min_score': 1000})
        real, spawned = hooks.subprocess.Popen, []

        def popen(args, **kwargs):
            # memory_core resolves stores through subprocess too; only fake the daemon.
            if 'memory-semantic-daemon.py' in str(args):
                spawned.append(kwargs)
                return None
            return real(args, **kwargs)
        with patch.object(hooks.importlib.util, 'find_spec', return_value=object()), \
             patch.object(hooks.subprocess, 'Popen', side_effect=popen):
            output = self.ask('remember the retry key?')
        self.assertIn('not proof of absence', output)
        self.assertEqual(len(spawned), 1)
        self.assertTrue(spawned[0]['start_new_session'])
        self.assertEqual(spawned[0]['env']['AI_MEMORY_HOME'], str(self.root))

    def test_idle_daemon_exits(self):
        thread = self.start(idle=0.2)
        thread.join(3)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
