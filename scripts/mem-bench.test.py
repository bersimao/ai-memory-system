#!/usr/bin/env python3
"""Tests for mem-bench.py. Never touches the real corpus: MEM_BIN points at a stub
that prints canned JSON, so every number here is deterministic."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HERE = pathlib.Path(__file__).resolve().parent
STUB = r'''#!/usr/bin/env python3
# Canned `mem search` stub. Answers by query text so cases stay independent.
import json, os, sys
assert sys.argv[1] == "search", sys.argv  # the runner must pass the subcommand
q = sys.argv[2]
def hit(src, score):
    return {"source": src, "score": score, "project": "shared/x", "line": "1", "content": "c"}
if "upsert" in q.lower():
    results = [hit("skills/db/references/reference-hana-upsert-with-primary-key.md", 9.5),
               hit("skills/db/references/other.md", 2.0)]
elif "matrix" in q.lower():
    results = [hit("skills/db/references/other.md", 4.0),
               hit("skills/di/references/reference-matrix-cell-read.md", 3.1)]
elif "kubernetes" in q.lower():
    results = [hit("skills/db/references/noise.md", 0.4)]
else:
    results = []
engine = "semantic" if "--semantic" in sys.argv else "lexical"
print(json.dumps({"results": results, "elapsed_ms": 12, "engine": engine,
                  "scope": "all", "collection": "curated"}))
'''

CASES = [
    {"id": "t1", "query": "UPSERT no HANA", "lang": "pt", "kind": "identifier",
     "expected": "skills/db/references/reference-hana-upsert-with-primary-key.md", "held_out": False},
    {"id": "t2", "query": "matrix cells", "lang": "en", "kind": "prose",
     "expected": "skills/di/references/reference-matrix-cell-read.md", "held_out": True},
    {"id": "t3", "query": "kubernetes ingress", "lang": "en", "kind": "absent",
     "expected": None, "held_out": False},
]


class Bench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(cls.tmp.name)
        cls.stub = d / 'mem-stub.py'
        cls.stub.write_text(STUB)
        cls.stub.chmod(0o755)
        cls.cases = d / 'cases.jsonl'
        cls.cases.write_text('\n'.join(json.dumps(c) for c in CASES) + '\n')
        os.environ['MEM_BIN'] = str(cls.stub)
        cls.bench = module('mem_bench', HERE / 'mem-bench.py')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, mode='lexical', include_held_out=True):
        cases = [c for c in CASES if include_held_out or not c['held_out']]
        return [self.bench.run_case(c, mode, 5, 30) for c in cases]

    def test_rank_found_at_one(self):
        self.assertEqual(self.rows()[0]['rank'], 1)

    def test_rank_found_below_top(self):
        self.assertEqual(self.rows()[1]['rank'], 2)

    def test_absent_case_has_no_rank_and_low_score(self):
        r = self.rows()[2]
        self.assertIsNone(r['rank'])
        self.assertLess(r['top_score'], 1.0)

    def test_expected_any_accepts_each_listed_page(self):
        # Real long prompts (2026-09-28) have more than one right page.
        case = {'id': 't4', 'query': 'matrix cells', 'lang': 'en', 'kind': 'prose', 'held_out': False,
                'expected': 'skills/x/none.md',
                'expected_any': ['skills/x/none.md', 'skills/di/references/reference-matrix-cell-read.md']}
        self.assertEqual(self.bench.run_case(case, 'lexical', 5, 30)['rank'], 2)

    def test_held_out_is_excluded_by_default(self):
        ids = [r['id'] for r in self.rows(include_held_out=False)]
        self.assertEqual(ids, ['t1', 't3'])

    def test_semantic_modes_reach_the_subprocess(self):
        # Proves the flag actually reaches the subprocess, not just the row label.
        self.assertEqual(self.rows('semantic')[0]['engine'], 'semantic')
        self.assertEqual(self.rows('semantic-sqlite')[0]['engine'], 'semantic')
        self.assertEqual(self.rows('lexical')[0]['engine'], 'lexical')

    def test_summary_counts(self):
        s = self.bench.summarise(self.rows(), floor=1.0)
        self.assertEqual(s['recall@1'], '50%')    # t1 at rank 1, t2 at rank 2
        self.assertEqual(s['recall@5'], '100%')
        self.assertEqual(s['absent_ok'], '100%')
        self.assertEqual(s['errors'], 0)

    def test_absent_ok_fails_when_floor_is_below_the_noise(self):
        # POSITIVE CONTROL: with a floor under the stub's 0.4 noise score, the absent
        # case must be scored WRONG. A floor that always passes proves nothing.
        s = self.bench.summarise(self.rows(), floor=0.1)
        self.assertEqual(s['absent_ok'], '0%')

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.bench.main(['--cases', str(self.cases), '--modes', 'telepathy'])

    def test_end_to_end_writes_raw_rows(self):
        out = pathlib.Path(self.tmp.name) / 'run.json'
        rc = self.bench.main(['--cases', str(self.cases), '--modes', 'lexical',
                              '--include-held-out', '--json', str(out)])
        self.assertEqual(rc, 0)
        payload = json.loads(out.read_text())
        self.assertEqual(len(payload['rows']), 3)
        self.assertEqual({r['id'] for r in payload['rows']}, {'t1', 't2', 't3'})

    def test_warm_up_runs_once_and_is_not_a_row(self):
        out = pathlib.Path(self.tmp.name) / 'warm.json'
        real, seen = self.bench.run_case, []
        self.bench.run_case = lambda c, *a: (seen.append(c['id']), real(c, *a))[1]
        try:
            self.bench.main(['--cases', str(self.cases), '--modes', 'lexical',
                             '--include-held-out', '--json', str(out)])
        finally:
            self.bench.run_case = real
        self.assertEqual(seen, ['t1', 't1', 't2', 't3'])
        self.assertEqual(len(json.loads(out.read_text())['rows']), 3)

    def test_bad_json_from_mem_is_recorded_not_raised(self):
        broken = pathlib.Path(self.tmp.name) / 'broken.py'
        broken.write_text('#!/usr/bin/env python3\nprint("not json")\n')
        broken.chmod(0o755)
        old = os.environ['MEM_BIN']
        os.environ['MEM_BIN'] = str(broken)
        try:
            r = self.bench.run_case(CASES[0], 'lexical', 5, 30)
            self.assertIsNotNone(r['error'])
            self.assertIsNone(r['rank'])
        finally:
            os.environ['MEM_BIN'] = old


if __name__ == '__main__':
    unittest.main(verbosity=2)
