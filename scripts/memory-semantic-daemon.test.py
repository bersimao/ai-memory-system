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
from memory_core import Memory, atomic, digest, json_read, read


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
        self.other = 'projects/other/context/topics/vizinho.md'
        atomic(self.memory.path(self.other), 'Pagina que so o vetor acha.\n')
        self.calls = []

    async def fake_search(self, memory, query, cwd, scope, domain, collection, limit):
        self.calls.append(query)
        return [{'id': 'h1', 'source': self.rel, 'project': 'other', 'kind': 'topic', 'line': 2,
                 'revision': digest(self.text), 'score': 0.9, 'content': 'A chave de idempotencia fica persistida.'}]

    def fake_vectors(self, memory, query, cwd, scope, domain, collection, limit):
        self.calls.append(('vector', query, limit))
        text = read(self.memory.path(self.other))
        return [{'id': 'v1', 'source': self.other, 'project': 'other', 'kind': 'topic', 'line': 1,
                 'revision': digest(text), 'score': 0.0167, 'content': text.strip()}]

    def start(self, idle=5):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(daemon.socket_path(self.memory)))
        server.listen(1)
        thread = threading.Thread(target=daemon.serve, args=(self.memory, server, self.fake_search, idle),
                                  kwargs={'vectors': self.fake_vectors})
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
            daemon.answer(self.memory, {'query': 'x', 'cwd': str(self.cwd), 'limit': 51}, self.fake_search)
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

    def test_hybrid_keeps_gate_hit_and_fills_from_vectors(self):
        # 2026-09-28: lexical #1 opened the gate and stays first; a page only the vectors rank fills a slot,
        # past the lexical floor, and stays expandable under the daemon's own request id.
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_hybrid': True, 'recall_min_score': 0.5})
        self.start(idle=1)
        output = self.ask('a chave de idempotencia persistida')
        self.assertIn('all, hybrid', output)
        self.assertLess(output.index(self.rel), output.index(self.other))
        self.assertIn('Engine: vector (hybrid fill)', output)
        vector_id = output.split(self.other)[1].split('Expand: ')[1].split(':')[0]
        self.assertIn('vetor', self.memory.expand(vector_id, 'v1')['content'])

    def auto_page_of_another_project(self):
        rel, text = 'projects/other/context/memory/2026-01-01.md', 'Chave persistida no log do dia.\n'
        atomic(self.memory.path(rel), text)
        return rel, {'id': 'v2', 'source': rel, 'project': 'other', 'kind': 'project', 'line': 1,
                     'revision': digest(text), 'score': 0.016, 'content': text.strip()}

    def test_hybrid_does_not_refill_a_page_the_cross_project_floor_dropped(self):
        # 2026-10-11 (Codex stop review): the floor filtered the lexical hits only, so the vector ranking
        # put the same daily log back as a fill slot.
        log, hit = self.auto_page_of_another_project()
        vectors = self.fake_vectors
        self.fake_vectors = lambda *args: vectors(*args) + [hit]
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_hybrid': True, 'recall_min_score': 0.5})
        self.start(idle=1)
        self.assertIn(log, self.ask('a chave de idempotencia persistida'))      # off by default
        self.memory.config['recall_cross_auto_min_score'] = 1000
        output = self.ask('a chave de idempotencia persistida')
        self.assertNotIn(log, output)
        self.assertIn(self.other, output)                                       # the other fill still arrives

    def test_semantic_does_not_bring_back_a_page_the_cross_project_floor_dropped(self):
        log, hit = self.auto_page_of_another_project()

        async def found(*args):
            return [hit]
        self.fake_search = found
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_semantic': True})
        self.start(idle=1)
        self.assertIn(log, self.ask('remember a chave persistida?'))            # off by default
        self.memory.config['recall_cross_auto_min_score'] = 1000
        output = self.ask('remember a chave persistida?')
        self.assertNotIn(log, output)
        self.assertIn(self.rel, output)

    def test_hybrid_asks_nothing_when_the_gate_is_closed(self):
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_hybrid': True, 'recall_min_score': 1000})
        self.start(idle=1)
        self.assertEqual(self.ask('a chave de idempotencia persistida'), '')
        self.assertEqual(self.calls, [])

    def test_hybrid_without_daemon_answers_lexically_and_starts_the_sqlite_engine(self):
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_hybrid': True, 'recall_min_score': 0.5,
                                   'semantic_engine': 'sqlite'})
        real, spawned, asked = hooks.subprocess.Popen, [], []

        def popen(args, **kwargs):
            if 'memory-semantic-daemon.py' in str(args):
                spawned.append(kwargs)
                return None
            return real(args, **kwargs)
        with patch.object(hooks.importlib.util, 'find_spec', side_effect=lambda name: asked.append(name) or object()), \
             patch.object(hooks.subprocess, 'Popen', side_effect=popen):
            output = self.ask('a chave de idempotencia persistida')
        self.assertIn('all, lexical', output)
        self.assertIn(self.rel, output)
        self.assertEqual((len(spawned), 'memsearch' in asked, 'onnxruntime' in asked), (1, False, True))

    def test_hybrid_pick_keeps_lexical_first_then_fuses(self):
        hit = lambda s: {'source': s}
        picks = hooks.hybrid_pick([hit('A'), hit('B'), hit('C')], [hit('D'), hit('B'), hit('E')], 3)
        # plain RRF would put B (both lists) over A (lexical #1 only)
        self.assertEqual([(h['source'], v) for h, v in picks], [('A', False), ('B', False), ('D', True)])

    def test_vector_mode_needs_sqlite_and_allows_a_deeper_limit(self):
        request = {'query': 'x', 'cwd': str(self.cwd), 'mode': 'vector', 'limit': 30}
        with self.assertRaisesRegex(ValueError, 'sqlite'):
            daemon.answer(self.memory, request, self.fake_search)
        self.assertEqual(daemon.answer(self.memory, request, self.fake_search, self.fake_vectors)['engine'], 'vector')
        with self.assertRaisesRegex(ValueError, 'invalid'):
            daemon.answer(self.memory, {**request, 'limit': 31}, self.fake_search, self.fake_vectors)

    def test_cli_semantic_asks_the_resident_process_and_falls_back(self):
        import types
        cli = module('memory_cli', BASE / 'scripts/memory-cli.py')
        args = types.SimpleNamespace(query='retry key', cwd=str(self.cwd), scope='all', domain=None, top_k=20, session='s')
        seen, loads, real_run, real_answer, canned = [], [], cli.subprocess.run, daemon.answer, self.fake_search

        async def search(memory, query, cwd, scope, domain, collection, limit):
            seen.append((collection, domain, limit))
            return await canned(memory, query, cwd, scope, domain, collection, limit)

        def run(command, **kwargs):  # only the model-loading subprocess is faked; `resolve` still runs node
            if 'memory-search.py' not in str(command):
                return real_run(command, **kwargs)
            loads.append(command)
            return types.SimpleNamespace(returncode=0, stdout='{"results": []}', stderr='')
        self.fake_search = search
        thread = self.start()
        events = lambda: len(list((self.memory.state / 'usage').glob('*.json')))
        with patch.object(cli.subprocess, 'run', side_effect=run):
            found = cli.semantic(self.memory, args, 'transcripts')
            self.assertEqual((seen, loads, found['engine'], found['results'][0]['source']),
                             ([('transcripts', None, 20)], [], 'semantic-sqlite', self.rel))   # no second process
            self.assertEqual(events(), 1)                             # logged once, by the CLI
            self.memory.expand(found['request_id'], 'h1')             # and expandable
            cli.semantic(self.memory, args, 'transcripts', log=False)  # --auto logs the result it keeps
            self.assertEqual((events(), loads), (2, []))               # 2 = the search + the expand above
            # A process started from older code ignores `collection`: its answer must not be used.
            with patch.object(daemon, 'answer', side_effect=lambda *a, **k: {**real_answer(*a, **k), 'collection': 'curated'}):
                self.assertEqual(cli.semantic(self.memory, args, 'transcripts'), {'results': []})
            self.assertEqual(len(loads), 1)
            daemon.socket_path(self.memory).unlink()                  # nobody listening
            self.assertEqual(cli.semantic(self.memory, args, 'curated'), {'results': []})
            self.assertEqual(len(loads), 2)
            # Listening but silent (busy with another request, or stuck): give up after RESIDENT_WAIT.
            import time
            silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(silent.close)
            silent.bind(str(daemon.socket_path(self.memory)))
            silent.listen(1)
            started = time.monotonic()
            with patch.object(cli, 'RESIDENT_WAIT', 0.3):
                self.assertEqual(cli.semantic(self.memory, args, 'curated'), {'results': []})
            self.assertEqual(len(loads), 3)
            self.assertLess(time.monotonic() - started, 3)
            # Answering, but a byte at a time: the bound is on the whole exchange, not on each read.
            def trickle():
                conn, _ = silent.accept()                             # the silent case above left one queued
                conn.close()
                conn, _ = silent.accept()
                with conn:
                    for _ in range(40):
                        try:
                            conn.sendall(b' ')
                        except OSError:
                            return
                        time.sleep(0.1)
            slow = threading.Thread(target=trickle, daemon=True)
            slow.start()
            started = time.monotonic()
            with patch.object(cli, 'RESIDENT_WAIT', 0.5):
                self.assertEqual(cli.semantic(self.memory, args, 'curated'), {'results': []})
            self.assertEqual(len(loads), 4)
            self.assertLess(time.monotonic() - started, 2)
            slow.join(timeout=10)
        self.assertLessEqual(cli.RESIDENT_WAIT, 5)                    # the real bound stays a few seconds

    def test_idle_daemon_exits(self):
        thread = self.start(idle=0.2)
        thread.join(3)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
