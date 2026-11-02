#!/usr/bin/env python3
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


weekly = module('weekly_check', Path(__file__).resolve().parent / 'memory-weekly-check.py')


class WeeklyCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_summary_flags_absent_case_over_floor(self):
        rows = [{'id': 'h1', 'kind': 'prose', 'rank': 1, 'top_score': 9, 'elapsed_ms': 10},
                {'id': 'h2', 'kind': 'prose', 'rank': None, 'top_score': 3, 'elapsed_ms': 30},
                {'id': 'a1', 'kind': 'absent', 'rank': None, 'top_score': 7.22, 'elapsed_ms': 20},
                {'id': 'a2', 'kind': 'absent', 'rank': None, 'top_score': 2.0, 'elapsed_ms': 20},
                {'id': 'e1', 'kind': 'absent', 'error': 'busy'}]
        summary = weekly.summarize(rows, 7.17)
        self.assertEqual(summary['absent_over_floor'], [('a1', 7.22)])
        self.assertEqual((summary['recall@1'], summary['recall@5'], summary['absent_ok'], summary['errors']), (0.5, 0.5, 0.5, 1))

    def test_known_leak_is_listed_but_not_flagged(self):
        # a09 (2026-09-28) is an accepted leak; flagging it every week would hide a new crossing.
        rows = [{'id': 'a1', 'kind': 'absent', 'rank': None, 'top_score': 9.4, 'elapsed_ms': 20},
                {'id': 'a2', 'kind': 'absent', 'rank': None, 'top_score': 7.5, 'elapsed_ms': 20}]
        summary = weekly.summarize(rows, 7.42, known={'a1'})
        self.assertEqual((summary['absent_over_floor'], summary['absent_over_floor_known']), ([('a2', 7.5)], [('a1', 9.4)]))
        self.assertEqual(summary['absent_ok'], 0.0)  # the metric still counts both

    def test_recall_drop_compares_shared_cases_only(self):
        # 37 -> 60 cases (2026-09-28) would have read as recall@5 89% -> 84% with no real change.
        hit = lambda i, rank: {'id': i, 'kind': 'prose', 'rank': rank, 'top_score': 9, 'elapsed_ms': 10}
        old = [hit('h1', 1), hit('h2', 2)]
        self.assertIsNone(weekly.recall_drop([hit('h1', 1), hit('h2', 2), hit('h3', None)], old))
        self.assertEqual(weekly.recall_drop([hit('h1', 1), hit('h2', None), hit('h3', 1)], old), (1.0, 0.5, 2))

    def test_recall_stats_only_counts_the_window(self):
        events = [{'event': 'recall', 'at': '2026-09-20', 'injected_bytes': 900, 'hits': ['a'], 'elapsed_ms': 5},
                  {'event': 'recall', 'at': '2026-09-25', 'injected_bytes': 0, 'hits': [], 'elapsed_ms': 100},
                  {'event': 'recall', 'at': '2026-09-26', 'injected_bytes': 700, 'hits': ['b'], 'elapsed_ms': 300},
                  {'event': 'recall', 'at': '2026-09-26', 'injected_bytes': 260, 'hits': [], 'elapsed_ms': 50},  # absence notice
                  {'event': 'search', 'at': '2026-09-26'}]
        stats = weekly.recall_stats(events, '2026-09-21')
        self.assertEqual((stats['recalls'], stats['injected'], stats['injection_rate']), (3, 1, 0.333))
        self.assertEqual(stats['p95_ms'], 300)

    def test_sample_is_stratified_around_the_floor(self):
        recalls = [{'top_score': s} for s in [1, 2, 3, 7.5, 8, 12, 15, 20]]
        picked = weekly.sample(recalls, 7.17, per_bin=2, seed=1)
        self.assertEqual(sorted(sum(low <= r['top_score'] < high for r in picked) for low, high in
                                [(0, 7.17), (7.17, 9.17), (9.17, 99)]), [2, 2, 2])

    def test_injected_row_shows_the_opener_not_the_gated_top(self):
        # 2026-10-17 #18: the cross-project floor dropped the top hit and the hook re-opened with
        # another page; the sheet labelled the dropped page.
        top = {'id': 't', 'score': 9.31, 'source': 'other/learned.md', 'line': 1}
        opener = {'id': 'o', 'score': 8.11, 'source': 'skills/x.md', 'line': 4, 'matched_terms': ['log']}
        events = [{'event': 'search', 'request_id': 'r1', 'query_hash': 'h1', 'hits': [top, opener]},
                  {'event': 'recall', 'request_id': 'r1', 'at': '2026-10-15', 'hits': ['o']},
                  {'event': 'search', 'request_id': 'r2', 'query_hash': 'h2', 'hits': [dict(top)]},
                  {'event': 'recall', 'request_id': 'r2', 'at': '2026-10-15', 'hits': ['vector-only']},
                  {'event': 'search', 'request_id': 'r3', 'query_hash': 'h3', 'hits': [dict(top)]},
                  {'event': 'recall', 'request_id': 'r3', 'at': '2026-10-15', 'hits': []}]
        rows = {r['hash']: r for r in weekly.recall_rows(events, '2026-10-10')}
        self.assertEqual((rows['h1']['source'], rows['h1']['line'], rows['h1']['top_score'], rows['h1']['opener_score']),
                         ('skills/x.md', 4, 9.31, 8.11))
        self.assertEqual((rows['h2']['source'], rows['h2']['opener_found']), ('other/learned.md', False))
        self.assertEqual((rows['h3']['source'], rows['h3']['injected'], rows['h3']['opener_score']),
                         ('other/learned.md', False, False))

    def test_prompt_recovered_from_session_log_by_hash(self):
        log = self.dir / 'projects' / 'p' / 's.jsonl'
        log.parent.mkdir(parents=True)
        text = 'remember the retry key?'
        log.write_text(json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': text}]}}) + '\n{broken\n')
        key = hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(weekly.prompts_by_hash(self.dir, {key}), {key: text})

    def test_env_and_park_does_not_stack_open_items(self):
        env = self.dir / 'memory.env'
        env.write_text('export CLAUDE_MEM_INBOX="/vault/INBOX.md"\n# comment\n')
        self.assertEqual(weekly.read_env(env)['CLAUDE_MEM_INBOX'], '/vault/INBOX.md')
        note = self.dir / 'SAVE-FOR-LATER.md'
        note.write_text('- [ ] #parked **[CLARK] outro problema**')
        entry = '- [ ] #parked **[memoria] ' + weekly.TITLE + ' 2026-10-02** ^sfl-x\n'
        self.assertTrue(weekly.park(note, entry))
        self.assertFalse(weekly.park(note, entry.replace('10-02', '10-09')))
        self.assertIn('outro problema**\n- [ ] #parked **[memoria]', note.read_text())
        note.write_text(note.read_text().replace('- [ ] #parked **[memoria]', '- [x] #parked **[memoria]'))
        self.assertTrue(weekly.park(note, entry.replace('10-02', '10-09')))


if __name__ == '__main__':
    unittest.main()
