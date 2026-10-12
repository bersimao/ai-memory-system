#!/usr/bin/env python3
import importlib.util
import io
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from memory_core import Memory, VERSION, atomic, digest, json_read, json_write, read, redact


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BASE = Path(__file__).resolve().parent.parent
hooks = module('lifecycle', BASE / 'hooks/memory-lifecycle.py')
maintain = module('maintain', BASE / 'scripts/memory-maintain.py')


import memory_core


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'store'
        self.cwd = Path(self.tmp.name) / 'repo'
        (self.cwd / '.git').mkdir(parents=True)
        self.memory = Memory(self.root)
        self.ctx = self.memory.resolve(self.cwd)['relative'] + '/context'
        self.rel = self.ctx + '/MEMORY.md'
        atomic(self.memory.path(self.rel), '# Context\nNever disable validation.\n')

    def apply(self, change, reviewed=False):
        return self.memory.apply({'version': VERSION, 'changes': [change]}, reviewed)

    def test_append_journals_exact_original(self):
        before = read(self.memory.path(self.rel))
        result = self.apply(self.memory.change(self.rel, before + 'New fact.\n'))
        record = json_read(self.memory.state / 'journal' / (result['transaction'] + '.json'))
        self.assertEqual(record['changes'][0]['before'], before)
        self.assertEqual(record['status'], 'committed')
        self.assertEqual(read(self.memory.path(self.rel)), before + 'New fact.\n')

    def test_small_semantic_rewrite_is_rejected(self):
        before = read(self.memory.path(self.rel))
        change = self.memory.change(self.rel, before.replace('Never', 'Always'))
        with self.assertRaisesRegex(ValueError, 'append'):
            self.apply(change)
        change['operation'] = 'replace'
        with self.assertRaisesRegex(ValueError, 'reviewed'):
            self.apply(change)
        self.assertEqual(read(self.memory.path(self.rel)), before)

    def test_reviewed_replacement_keeps_old_revision(self):
        before = read(self.memory.path(self.rel))
        result = self.apply(self.memory.change(self.rel, 'A corrected fact.\n', 'replace'), True)
        self.assertEqual(json_read(self.memory.state / 'journal' / (result['transaction'] + '.json'))['changes'][0]['before'], before)

    def test_stale_proposal_preserves_concurrent_edit(self):
        change = self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'proposal')
        atomic(self.memory.path(self.rel), 'user concurrent edit')
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            self.apply(change)
        self.assertEqual(read(self.memory.path(self.rel)), 'user concurrent edit')

    def test_all_changes_validated_before_first_write(self):
        old = read(self.memory.path(self.rel))
        changes = [self.memory.change(self.rel, old + 'addition'),
                   {'path': '../escape.md', 'before_sha256': None, 'text': 'bad'}]
        with self.assertRaises(ValueError):
            self.memory.apply({'version': VERSION, 'changes': changes})
        self.assertEqual(read(self.memory.path(self.rel)), old)

    def test_symlink_and_nonmemory_targets_rejected(self):
        outside = Path(self.tmp.name) / 'outside.md'
        outside.write_text('private')
        alias = self.root / self.ctx / 'alias.md'
        alias.symlink_to(outside)
        for relative in [self.ctx + '/alias.md', 'hooks/file.md', 'context/../else.md']:
            with self.assertRaises(ValueError):
                self.memory.path(relative)

    def test_secret_rejected_before_journal(self):
        with self.assertRaisesRegex(ValueError, 'secret'):
            self.apply(self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'PASSWORD=secret-value'))
        self.assertEqual(list((self.memory.state / 'journal').glob('*')), [])

    def test_redact_short_keywords_without_prose_false_positives(self):
        for secret in ('DB_PASS=hunter2', 'export PWD_DB=abc', 'Uid=sa;Pwd=S3cret;Db=y',
                       'CREDENTIALS=abc', 'Authorization: Basic dXNlcjpwYXNz'):
            self.assertIn('[REDACTED]', redact(secret), secret)
        self.assertNotIn('S3cret', redact('Uid=sa;Pwd=S3cret;Db=y'))
        self.assertIn(';Db=y', redact('Uid=sa;Pwd=S3cret;Db=y'))
        for prose in ('bypass: no', 'first pass: we split', 'passo: 1', 'compass=north', '"first pass": "ok"'):
            self.assertEqual(redact(prose), prose)
        for quoted, leak in (('DB_PASS="alpha beta" tail', 'beta'), ('{"DB_PASS": "synthetic secret"}', 'synthetic'),
                             ('{"password": "s3cret", "n": 1}', 's3cret'), ("token: 'a b c'", 'b c'),
                             ('Uid=sa;Pwd="x y";Db=z', 'x y'), ('PASS="a\\"b escapedtail"', 'escapedtail'),
                             ("token: 'it\\'s hidden'", 'hidden')):
            self.assertNotIn(leak, redact(quoted), quoted)
        self.assertIn('tail', redact('DB_PASS="alpha beta" tail'))

    def test_mem_wrapper_uses_its_own_root_even_via_symlink(self):
        root = Path(self.tmp.name) / 'custom-root'
        (root / 'scripts').mkdir(parents=True)
        (root / 'scripts/mem').write_bytes((BASE / 'scripts/mem').read_bytes())
        (root / 'scripts/mem').chmod(0o755)
        (root / 'scripts/memory-cli.py').write_text('import os\nprint(os.environ["AI_MEMORY_HOME"])\n')
        link = Path(self.tmp.name) / 'bin-mem'
        link.symlink_to(root / 'scripts/mem')
        env = {k: v for k, v in os.environ.items() if k != 'AI_MEMORY_HOME'}
        for entry in (root / 'scripts/mem', link):
            out = subprocess.run(['bash', str(entry)], capture_output=True, text=True, env=env).stdout.strip()
            self.assertEqual(Path(out).resolve(), root.resolve(), entry)

    def test_recovery_completes_prepared_transaction(self):
        old = read(self.memory.path(self.rel))
        change = self.memory.change(self.rel, old + 'new')
        prepared = self.memory._validate([change], False)
        journal = self.memory.state / 'journal/t.json'
        json_write(journal, {'id': 't', 'status': 'prepared', 'changes': prepared})
        with self.memory.lock():
            self.memory._recover()
        self.assertEqual(read(self.memory.path(self.rel)), old + 'new')
        self.assertEqual(json_read(journal)['status'], 'committed')

    def test_recovery_does_not_clobber_external_edit(self):
        change = self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'new')
        prepared = self.memory._validate([change], False)
        json_write(self.memory.state / 'journal/t.json', {'id': 't', 'status': 'prepared', 'changes': prepared})
        atomic(self.memory.path(self.rel), 'external')
        with self.assertRaisesRegex(ValueError, 'recovery conflict'):
            self.memory._recover()
        self.assertEqual(read(self.memory.path(self.rel)), 'external')

    def test_fault_after_prepare_recoverable(self):
        old = read(self.memory.path(self.rel))
        import memory_core
        real = memory_core.atomic
        def fail(path, text):
            if Path(path) == self.memory.path(self.rel):
                raise OSError('simulated crash')
            real(path, text)
        with patch.object(memory_core, 'atomic', fail):
            with self.assertRaises(OSError):
                self.apply(self.memory.change(self.rel, old + 'new'))
        self.memory._recover()
        self.assertEqual(read(self.memory.path(self.rel)), old + 'new')

    def test_lossless_cap_split(self):
        original = '# Important decisions\n' + 'Never discard this reasoning.\n' * 200
        atomic(self.memory.path(self.rel), original)
        result = maintain.split(self.memory, self.rel)
        self.assertLess(len(read(self.memory.path(self.rel))), 2500)
        target = next(p for p in result['changed'] if '/index-' in p)
        self.assertEqual(read(self.memory.path(target)), original)

    def test_fake_split_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'complete original'):
            self.apply(self.memory.change(self.rel, 'lost everything', 'split'))

    def test_facts_need_matching_evidence_and_dedup(self):
        source = self.ctx + '/memory/2026-09-12.md'
        atomic(self.memory.path(source), 'The retry bug was fixed using an idempotency key.')
        # One unquoted fact must not sink the batch (haiku paraphrased in 4 of 8
        # sources, 2026-09-16, and the nightly run aborted on the same file forever).
        mixed = self.memory.facts(source, [{'text': 'Guess', 'quote': 'not in source'}, 'not a fact',
                                           {'text': 'Retries use a key.', 'quote': 'The retry bug was fixed'}])
        self.assertEqual(mixed['rejected'], 2)
        learned = read(self.memory.path(next(p for p in mixed['changed'] if '/learned-' in p)))
        self.assertIn('Retries use a key.', learned)
        self.assertNotIn('Guess', learned)
        facts = [{'text': 'Use an idempotency key for retries.', 'quote': 'fixed using an idempotency key'}]
        first = self.memory.facts(source, facts)
        self.assertTrue(first['changed'])
        self.assertEqual(self.memory.facts(source, facts)['changed'], [])

    def test_secret_flagged_fact_is_dropped_not_raised(self):
        # A checkpoint ABOUT auth headers makes `redact` fire on prose, and raising
        # failed the source every night for 35 days (a Node integration project, 2026-09-19).
        source = self.ctx + '/memory/2026-09-18.md'
        atomic(self.memory.path(source), 'O WMS exige o token em Authorization: Bearer <token>, '
                                         'e aceitaria X-API-Token: <valor> no lugar.')
        result = self.memory.facts(source, [
            {'text': 'O WMS exige `Bearer <token>`.', 'quote': 'Authorization: Bearer <token>'},
            {'text': 'A alternativa e um cabecalho proprio.', 'quote': 'aceitaria X-API-Token'},
        ])
        self.assertEqual(result['rejected'], 1)
        learned = read(self.memory.path(next(p for p in result['changed'] if '/learned-' in p)))
        self.assertIn('cabecalho proprio', learned)
        self.assertNotIn('Bearer', learned)

    def test_evidence_revision_checked(self):
        source = self.ctx + '/topics/evidence.md'
        atomic(self.memory.path(source), 'evidence')
        change = self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'fact', evidence=[{'path': source, 'sha256': digest('evidence')}])
        atomic(self.memory.path(source), 'changed')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.apply(change)

    def test_snapshot_bounded_utf8_manifest_and_no_transcript(self):
        atomic(self.memory.path(self.rel), 'á🚀' * 10000)
        transcript = self.root / self.ctx / 'transcripts/today.md'
        atomic(transcript, 'RAW SECRET DIALOGUE')
        result = self.memory.snapshot(self.cwd, 600)
        self.assertLessEqual(len(result['text'].encode()), 600)
        self.assertEqual(result['manifest']['omitted'][0]['source'], self.rel)
        self.assertNotIn('RAW SECRET', result['text'])

    def seed_other(self):
        rel = 'projects/other/context/topics/retry.md'
        atomic(self.memory.path(rel), '# Retry design\nThe idempotency defect was resolved by persisting the request identifier.')
        return rel

    def test_all_private_recall_labels_project(self):
        other = self.seed_other()
        result = self.memory.search('Considering the idempotency defect was solved, how would we handle retries?', self.cwd, 'all')
        self.assertEqual(result['results'][0]['source'], other)
        self.assertEqual(result['results'][0]['project'], 'other')

    def test_project_scope_excludes_other_project(self):
        self.seed_other()
        result = self.memory.search('idempotency', self.cwd, 'project')
        self.assertEqual(result['results'], [])

    def test_domain_scope_and_no_transcripts(self):
        atomic(self.root / 'skills/db/knowledge/sql.md', 'idempotency hana')
        atomic(self.root / 'skills/sl/knowledge/requests.md', 'idempotency endpoint')
        atomic(self.root / self.ctx / 'transcripts/raw.md', 'idempotency secret')
        result = self.memory.search('idempotency', self.cwd, domain='db')
        self.assertEqual([r['source'] for r in result['results']], ['skills/db/knowledge/sql.md'])

    def test_search_reads_latest_disk_without_index(self):
        self.assertFalse(self.memory.search('zebracache', self.cwd)['results'])
        self.apply(self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'zebracache fixed'))
        self.assertTrue(self.memory.search('zebracache', self.cwd)['results'])

    def test_expand_belongs_to_request_and_revision(self):
        first = self.memory.search('validation', self.cwd)
        hit = first['results'][0]
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            self.memory.expand(first['request_id'], 'wrong')
        self.memory.expand(first['request_id'], hit['id'])
        atomic(self.memory.path(self.rel), 'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.memory.expand(first['request_id'], hit['id'])

    def test_prompt_hook_does_not_need_remember_keyword(self):
        self.seed_other()
        self.memory.config['automatic_recall_scope'] = 'all'
        output = hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': 'Considering idempotency was solved, what next?', 'session_id': 's'})
        self.assertIn('Project: other', output)
        self.assertIn('request identifier', output)

    def test_prompt_hook_floor_cue_snippet_and_recall_event(self):
        # RAG plan T2.2.
        rel = 'projects/other/context/topics/retry.md'
        atomic(self.memory.path(rel), '# Retry\n' + 'filler line\n' * 20 + 'The idempotency key is persisted.\n')
        self.memory.config['automatic_recall_scope'] = 'all'
        ask = lambda prompt: hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': prompt, 'session_id': 's'})
        output = ask('how is idempotency handled')
        self.assertIn(rel + ':22\n', output)
        self.assertNotIn('filler', output)
        self.memory.config['recall_min_score'] = 1000
        self.assertEqual(ask('how is idempotency handled'), '')
        self.assertIn('not proof of absence', ask('remember how idempotency was handled?'))
        self.assertIn('not proof of absence', ask('como era a idempotency antes?'))
        self.assertEqual(ask('quais campos importantes e restantes na idempotency'), '')
        self.memory.config.update({'recall_min_score': 0, 'recall_min_terms': 2})
        self.assertEqual(ask('idempotency'), '')
        self.assertIn('retry.md', ask('idempotency persisted'))
        recalls = [e for e in map(json_read, (self.memory.state / 'usage').glob('*.json')) if e['event'] == 'recall']
        self.assertEqual(sorted(e['skipped'] for e in recalls), [0, 0, 0, 1, 1, 1, 1])
        self.assertTrue(all(isinstance(e['elapsed_ms'], int) for e in recalls))
        self.assertEqual(sorted(e['injected_bytes'] == 0 for e in recalls), [False, False, False, False, True, True, True])

    def test_prompt_hook_skips_task_notices_and_two_plain_words(self):
        # 2026-10-10, logged traffic: harness notices and two-word replies opened the gate on noise.
        atomic(self.memory.path('projects/other/context/topics/deploy.md'),
               'Commit and push before the deploy; ticket 85789 completed, status ok.\n')
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_min_terms': 2})
        ask = lambda prompt: hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': prompt, 'session_id': 's'})
        self.assertEqual(ask('<task-notification>\n<status>completed</status>'), '')
        # 2026-10-12: a sub-agent's final report reaches the hook in this shape (12 of 12 logged hashes).
        self.assertEqual(ask('<agent-message from="a0aa9403a96a697d0">\n[Subagent hand-back] The text below is the '
                             'final report of a subagent.\n  status completed, commit and push done\n</agent-message>'), '')
        self.assertIn('deploy.md', ask('status completed'))      # the same words, typed, still recall
        self.assertIn('deploy.md', ask('commit and push'))       # off by default
        self.memory.config['recall_plain_terms'] = 3
        self.assertEqual(ask('commit and push'), '')
        self.assertIn('deploy.md', ask('commit push deploy'))
        self.assertIn('deploy.md', ask('ticket 85789'))          # an identifier keeps a two-word match

    def test_prompt_hook_floors_another_projects_auto_pages(self):
        # 2026-10-10: 87 logged openers judged against the injected text; under 10, 1 of 51 helped.
        pages = {'projects/other/context/memory/2026-01-01.md': 'zebra quokka lemur',
                 'projects/other/context/topics/learned-2026-01.md': 'falcon heron osprey',
                 'projects/other/context/topics/birds.md': 'penguin puffin albatross',
                 self.ctx + '/memory/2026-01-01.md': 'walrus narwhal beluga'}
        for rel, words in pages.items():
            atomic(self.memory.path(rel), words + ' notes\n')
        self.memory.config['automatic_recall_scope'] = 'all'
        ask = lambda prompt: hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': prompt, 'session_id': 's'})
        seen = lambda: [rel in ask(words) for rel, words in pages.items()]
        self.assertEqual(seen(), [True, True, True, True])       # off by default
        self.memory.config['recall_cross_auto_min_score'] = 1000
        self.assertEqual(seen(), [False, False, True, True])     # a curated topic and the own daily log stay
        self.memory.config['recall_cross_auto_min_score'] = 0.01
        self.assertEqual(seen(), [True, True, True, True])       # at or above the floor nothing is cut

    def test_prompt_hook_clips_only_the_rarest_terms(self):
        # 2026-09-28: a 36-term prompt matched a dozen common words and excerpt() kept one clip
        # per term, so the snippet arrived as twelve 40-byte shreds joined by '…'.
        common = ['alpha', 'beta', 'gamma', 'delta', 'epsilon']
        for i in range(5):
            atomic(self.memory.path(f'projects/other/context/topics/n{i}.md'), ' '.join(common) + '\n')
        target = 'projects/other/context/topics/target.md'
        atomic(self.memory.path(target), ''.join(t + ' here\n' + 'x' * 150 + '\n' for t in common + ['zebrakey']))
        self.memory.config['automatic_recall_scope'] = 'all'
        output = hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd),
                                            'prompt': ' '.join(common + ['zebrakey']), 'session_id': 's'})
        block = next(b for b in output.split('\nProject: ') if target in b)
        self.assertIn('zebrakey', block)
        self.assertLessEqual(block.count('\n…\n'), 2, block)

    def test_verified_frontmatter_reaches_hit_and_hook_only_when_present(self):
        # RAG plan T1.4.
        dated, plain = 'projects/other/context/topics/dated.md', 'projects/other/context/topics/plain.md'
        atomic(self.memory.path(dated), '---\nname: x\nverified: 2026-09-01\n---\nThe quokkarule holds.\n')
        atomic(self.memory.path(plain), 'The quokkarule also shows up here, unverified quokkarule.\n---\nverified: 2020-01-01\n---\n')
        hits = {h['source']: h for h in self.memory.search('quokkarule', self.cwd, 'all', log=False)['results']}
        self.assertEqual((hits[dated]['verified'], hits[plain]['verified']), ('2026-09-01', None))
        self.memory.config['automatic_recall_scope'] = 'all'
        output = hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': 'quokkarule', 'session_id': 's'})
        self.assertEqual(output.count('Verified: '), 1)
        self.assertIn('dated.md:', output.split('Verified: 2026-09-01')[0].rsplit('Source: ', 1)[1])

    def test_excerpt_picks_the_cut_with_most_matched_terms(self):
        text = 'alpha here\n' + 'pad\n' * 200 + 'alpha and rs.beta\n' + 'tail\n'
        start, cut = memory_core.excerpt(text, ['alpha', 'beta'], 100)
        self.assertEqual(start, 201)
        self.assertIn('rs.beta', cut)
        self.assertEqual(memory_core.excerpt('a\nalpha x\nalpha y', ['alpha'], 100)[0], 1)  # tie -> earliest
        self.assertEqual(memory_core.excerpt('none here', ['alpha'], 5), (0, 'none '))
        # Terms too far apart for one cut: each term's line survives.
        self.assertEqual(memory_core.excerpt('alpha\n' + 'pad\n' * 100 + 'alpha again\n' + 'pad\n' * 100 + 'rs.beta', ['alpha', 'beta'], 50), (0, 'alpha\n…\nrs.beta'))
        # A long first line must not eat the budget of the next term's line.
        start, cut = memory_core.excerpt('x' * 300 + ' alpha ' + 'y' * 300 + '\n' + 'pad\n' * 100 + 'rs.beta', ['alpha', 'beta'], 100)
        self.assertEqual(start, 0)
        self.assertIn('alpha', cut)
        self.assertIn('rs.beta', cut)
        self.assertLessEqual(len(cut.encode()), 100)
        # Two terms on one line plus a later term: all three fit, so all three stay (Codex repro).
        line = 'x' * 80 + ' alpha ' + 'z' * 30 + ' beta ' + 'y' * 80  # beta beyond alpha's padding
        cut = memory_core.excerpt(line + '\n' + 'pad\n' * 100 + 'rs.gamma', ['alpha', 'beta', 'gamma'], 100)[1]
        self.assertTrue(all(t in cut for t in ('alpha', 'beta', 'rs.gamma')), cut)
        self.assertLessEqual(len(cut.encode()), 100)
        # Terms too far apart on one line: fall back to each line's first term, never lose the next line.
        line = 'alpha ' + 'z' * 300 + ' beta'
        cut = memory_core.excerpt(line + '\n' + 'pad\n' * 100 + 'rs.gamma', ['alpha', 'beta', 'gamma'], 100)[1]
        self.assertTrue('alpha' in cut and 'rs.gamma' in cut and len(cut.encode()) <= 100, cut)
        # A term inside a longer word is not a match: the standalone `beta` must be the one kept (Codex repro).
        # Gap wider than the context padding, or the padding would reach the real `beta` by luck.
        cut = memory_core.excerpt('alpha betamax ' + 'x' * 60 + ' beta\n' + 'pad\n' * 100 + 'rs.gamma', ['alpha', 'beta', 'gamma'], 100)[1]
        self.assertTrue({'alpha', 'beta', 'gamma'} <= memory_core.code_words(cut), cut)
        self.assertEqual(memory_core._word_at('Ação beta', 'acao'), (0, 4))  # folded like tokens()
        # Accents count in bytes, not chars: the cut still holds both terms within budget.
        cut = memory_core.excerpt('é' * 200 + ' alpha\n' + 'pad\n' * 100 + 'ção beta', ['alpha', 'beta'], 40)[1]
        self.assertTrue('alpha' in cut and 'beta' in cut and len(cut.encode()) <= 40, cut)
        # A term only in the heading (sdk matched_terms) must not force the fallback.
        self.assertEqual(memory_core.excerpt('x\nalpha y\nctx', ['alpha', 'fromheading'], 50), (1, 'alpha y\nctx'))

    def test_word_at_agrees_with_code_words(self):
        # Equivalence, not cases: the locator must find a term exactly when code_words() has it.
        import random
        rng = random.Random(11)
        for _ in range(3000):
            line = ''.join(rng.choice(['beta', 'x', '-', '.', ' ', 'é', 'B', '_']) for _ in range(rng.randint(0, 12)))
            spot = memory_core._word_at(line, 'beta')
            self.assertEqual(spot is not None, 'beta' in memory_core.code_words(line), line)
            if spot:
                self.assertEqual(line[spot[0]:spot[1]].casefold(), 'beta', line)
        for line in ('-beta', 'beta-', 'x.beta', '.beta.', 'Béta'):
            self.assertIsNotNone(memory_core._word_at(line, 'beta'), line)
        self.assertIsNone(memory_core._word_at('beta-x betamax', 'beta'))

    def test_recent_rag_hits_latest_event_for_that_path_wins(self):
        # RAG plan T4.3. `at` decides, not file order.
        for at, path, refs in (('2026-09-26T10:00:01', '/r/a.cs', ['old:1']), ('2026-09-26T10:00:05', '/r/a.cs', ['new:1']),
                               ('2026-09-26T10:00:09', '/r/b.cs', ['other:1'])):
            json_write(self.memory.state / 'usage' / (at[-2:] + '.json'), {'at': at, 'event': 'rag_edit', 'file_path': path, 'refs': refs})
        self.assertEqual(self.memory.recent_rag_hits('/r/a.cs'), ['new:1'])
        self.assertEqual(self.memory.recent_rag_hits('/r/none.cs'), [])

    def test_explicit_feedback_excludes_automatic_lint_feedback(self):
        for outcome, auto in (('code_failed', True), ('code_passed', True), ('relevant', False)):
            self.memory.event({'event': 'feedback', 'outcome': outcome, 'automatic': auto})
        cli = subprocess.run([sys.executable, str(BASE / 'scripts/memory_core.py'), '--root', str(self.root), 'report'],
                             capture_output=True, text=True)
        explicit = json.loads(cli.stdout)['explicit_feedback']
        self.assertEqual((explicit['code_failed'], explicit['code_passed'], explicit['relevant']), (0, 0, 1))

    def test_rag_report_counts_fires_bytes_and_failed_share(self):
        events = [{'event': 'rag_edit', 'injected_bytes': 0}, {'event': 'rag_edit', 'injected_bytes': 900},
                  {'event': 'rag_edit', 'injected_bytes': 1200}, {'event': 'rag_edit', 'injected_bytes': 1500},
                  {'event': 'feedback', 'outcome': 'code_failed', 'automatic': True},
                  {'event': 'feedback', 'outcome': 'code_passed', 'automatic': True},
                  {'event': 'feedback', 'outcome': 'code_passed', 'automatic': True},
                  {'event': 'feedback', 'outcome': 'code_failed'}]  # manual: not part of the share
        self.assertEqual(memory_core.rag_report(events),
                         {'fired': 4, 'injected': 3, 'injected_bytes_p50': 1200, 'code_failed_share': 0.333})

    def test_excerpt_contract_on_random_chunks(self):
        # Contract, not cases: never over budget; when the fallback's spans fit, every term shows.
        import random
        rng, terms = random.Random(7), ['alpha', 'beta', 'gamma', 'delta']
        for _ in range(500):
            lines = [''.join(rng.choice('xyé ') for _ in range(rng.randint(0, 120))) for _ in range(rng.randint(1, 60))]
            for t in rng.sample(terms, rng.randint(1, 4)):
                i = rng.randrange(len(lines))
                at = rng.randint(0, len(lines[i]))
                lines[i] = lines[i][:at] + ' ' + t + ' ' + lines[i][at:]
                j = rng.randrange(len(lines))  # decoy: the term inside a longer word
                lines[j] = t + 'max ' + lines[j]
            content, budget = '\n'.join(lines), rng.randint(20, 400)
            present = [t for t in terms if t in memory_core.code_words(content)]
            cut = memory_core.excerpt(content, present, budget)[1]
            self.assertLessEqual(len(cut.encode()), budget)
            if len('\n…\n'.join(present).encode()) <= budget // 3 and all(
                    len(l.encode()) <= budget // (2 * len(present)) for l in lines if any(t in l for t in present)):
                self.assertTrue(set(present) <= memory_core.code_words(cut), (budget, cut))

    def test_min_terms_applies_before_the_limit(self):
        # Codex review 2026-09-26: filtering after truncation let three one-term hits
        # hide a valid two-term fourth hit and produced a false "no recall".
        for name in ('a', 'b', 'c'):
            atomic(self.memory.path('projects/other/context/topics/' + name + '.md'), 'quokkaflag ' * 5 + '\n')
        two = 'projects/other/context/topics/two.md'
        atomic(self.memory.path(two), 'zebracache quokkaflag ' + 'filler words here ' * 400 + '\n')
        query = 'zebracache quokkaflag'
        top = self.memory.search(query, self.cwd, 'all', limit=3)['results']
        self.assertNotIn(two, [h['source'] for h in top])  # precondition: it really is hidden
        self.assertEqual([h['source'] for h in self.memory.search(query, self.cwd, 'all', limit=3, min_terms=2)['results']], [two])
        self.memory.config.update({'automatic_recall_scope': 'all', 'recall_min_terms': 2})
        output = hooks.handle(self.memory, {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.cwd), 'prompt': query, 'session_id': 's'})
        self.assertIn(two, output)

    def test_one_turn_checkpoint_despite_existing_daily_log(self):
        atomic(self.memory.path(self.ctx + '/memory/2026-09-12.md'), 'An earlier session')
        transcript = Path(self.tmp.name) / 'session.jsonl'
        transcript.write_text(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Fixed with idempotency.'}]}}) + '\n')
        event = {'hook_event_name': 'Stop', 'cwd': str(self.cwd), 'session_id': 's', 'transcript_path': str(transcript)}
        hooks.handle(self.memory, event)
        hooks.handle(self.memory, event)
        checkpoints = list((self.root / self.ctx / 'checkpoints').glob('*.md'))
        self.assertEqual(len(checkpoints), 1)
        self.assertIn('Fixed with idempotency.', read(checkpoints[0]))

    def test_codex_checkpoint_same_project_and_partial_line(self):
        path = Path(self.tmp.name) / 'rollout.jsonl'
        first = json.dumps({'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Codex decision'}})
        path.write_text(first)
        event = {'hook_event_name': 'Stop', 'cwd': str(self.cwd), 'session_id': 'codex', 'transcript_path': str(path)}
        hooks.handle(self.memory, event)
        self.assertEqual(list((self.root / self.ctx / 'checkpoints').glob('*.md')), [])
        path.write_text(first + '\n')
        hooks.handle(self.memory, event)
        self.assertEqual(len(list((self.root / self.ctx / 'checkpoints').glob('*.md'))), 1)

    def test_codex_sessions_spawned_by_claude_code_get_no_memory(self):
        # 2026-09-17: once Codex trusted the hooks, the Claude Code codex plugin's
        # headless review sessions (424 of 431 rollouts in a week) received the
        # snapshot and would have filed review verdicts as project checkpoints.
        path = Path(self.tmp.name) / 'rollout.jsonl'
        meta = {'type': 'session_meta', 'payload': {'id': 'r', 'originator': 'Claude Code', 'source': 'vscode', 'cwd': str(self.cwd)}}
        reply = {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Review verdict'}}
        path.write_text(json.dumps(meta) + '\n' + json.dumps(reply) + '\n')
        base = {'cwd': str(self.cwd), 'session_id': 'r', 'transcript_path': str(path)}
        for kind in ('SessionStart', 'UserPromptSubmit', 'Stop'):
            self.assertEqual(hooks.handle(self.memory, {**base, 'hook_event_name': kind, 'prompt': 'review the retry decision'}), '')
        self.assertEqual(list((self.root / self.ctx / 'checkpoints').glob('*.md')), [])
        meta['payload']['originator'] = 'codex-tui'
        path.write_text(json.dumps(meta) + '\n' + json.dumps(reply) + '\n')
        self.assertIn('Memory snapshot', hooks.handle(self.memory, {**base, 'hook_event_name': 'SessionStart'}))
        hooks.handle(self.memory, {**base, 'hook_event_name': 'Stop'})
        self.assertEqual(len(list((self.root / self.ctx / 'checkpoints').glob('*.md'))), 1)

    def test_model_has_no_tools_or_mcp_and_no_permission_bypass(self):
        with patch.object(maintain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"result":"{\\"facts\\":[]}"}', '')) as run:
            self.assertEqual(maintain.generate(self.memory, 'evidence', 'instruction'), {'facts': []})
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--tools') + 1], '')
        self.assertIn('--strict-mcp-config', argv)
        self.assertNotIn('--dangerously-skip-permissions', argv)

    def test_fenced_model_result_is_parsed(self):
        # Real haiku output (2026-09-14): the JSON arrives inside a markdown fence.
        # The stub above never showed that shape, so the parser shipped broken.
        fenced = json.dumps({'result': '```json\n{"summary": "s"}\n```'})
        with patch.object(maintain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, fenced, '')):
            self.assertEqual(maintain.generate(self.memory, 'evidence', 'instruction'), {'summary': 's'})

    def test_structured_output_wins_over_result_text(self):
        wrapped = json.dumps({'result': 'not json at all', 'structured_output': {'facts': []}})
        with patch.object(maintain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, wrapped, '')):
            self.assertEqual(maintain.generate(self.memory, 'evidence', 'instruction', schema={'type': 'object'}), {'facts': []})

    def test_schema_flag_only_for_default_extractor(self):
        ok = subprocess.CompletedProcess([], 0, '{"summary": "s"}', '')
        with patch.object(maintain.subprocess, 'run', return_value=ok) as run:
            maintain.generate(self.memory, 'evidence', 'instruction', schema={'type': 'object'})
        self.assertIn('--json-schema', run.call_args.args[0])
        self.memory.config['extract_command'] = ['my-extractor', '--json']
        with patch.object(maintain.subprocess, 'run', return_value=ok) as run:
            maintain.generate(self.memory, 'evidence', 'instruction', schema={'type': 'object'})
        self.assertEqual(run.call_args.args[0], ['my-extractor', '--json'])

    def test_json_shaped_summary_is_rendered_as_markdown(self):
        # Real haiku output (2026-09-14): asked for a summary string, it returned its
        # own JSON object serialized inside that string. The daily log became a blob.
        blob = json.dumps({'goal': 'G', 'deliverables': ['A', 'B'],
                           'decisions': [{'decision': 'D', 'reason': 'R'}], 'open_threads': ['O']})
        text = maintain.summary_markdown(blob)
        self.assertFalse(text.lstrip().startswith('{'))
        for expected in ('**Goal**: G', '- A', '- D — R', '**Open threads**:', '- O'):
            self.assertIn(expected, text)
        prose = '**Goal**: already markdown.'
        self.assertEqual(maintain.summary_markdown(prose), prose)

    def test_backfill_skips_logged_day_before_size_check(self):
        # A day that already has a daily log must not be read or size-checked;
        # otherwise every logged-but-oversized transcript printed
        # "split/review required" every night, for nothing (2026-09-14).
        import contextlib, io
        name = '2026-01-01.md'
        atomic(self.root / self.ctx / 'transcripts' / name, 'x' * 180001)
        atomic(self.root / self.ctx / 'memory' / name, 'already logged\n')
        err = io.StringIO()
        with patch.object(maintain.subprocess, 'run') as run, contextlib.redirect_stderr(err):
            maintain.maintain(self.memory, 'backfill')
        self.assertNotIn('oversized', err.getvalue())
        run.assert_not_called()

    def test_real_haiku_quotes_survive_markdown_and_elision(self):
        # Measured 2026-09-17 on real logs: haiku quoted faithfully but dropped
        # `code`/**bold** marks, rendered → as ->, and joined two passages with
        # "...". Exact matching kept 0/13 facts from one client log and 1/12 from
        # claude-mem, so only markdown-free haiku summaries ever yielded facts.
        source = self.ctx + '/memory/2026-09-08.md'
        atomic(self.memory.path(source),
               '- Detecção de drift **REJEITADA** pelo usuário: clientes podem ter regras em `SqlText`.\n'
               '- Mecanismo: `MetadataCore.ExistsQuery:246` compara só SqlCode — query nunca atualizada.\n'
               '- Corpo não-OData → FlurlHttpException; nada tratava o null.\n')
        facts = [
            {'text': 'Drift rejected.', 'quote': 'Detecção de drift REJEITADA pelo usuário: clientes podem ter regras em SqlText'},
            {'text': 'Exists only by code.', 'quote': 'MetadataCore.ExistsQuery:246 compara só SqlCode - query nunca atualizada'},
            {'text': 'Non-OData body.', 'quote': 'Corpo não-OData -> FlurlHttpException... nada tratava o null'},
            {'text': 'Invented.', 'quote': 'clientes nunca têm regras customizadas no SqlText'},
            {'text': 'Out of order.', 'quote': 'nada tratava o null... Corpo não-OData'},
            {'text': 'Too short.', 'quote': 'só'},
        ]
        result = self.memory.facts(source, facts)
        self.assertEqual(result['rejected'], 3)
        learned = read(self.memory.path(next(p for p in result['changed'] if '/learned-' in p)))
        for kept in ('Drift rejected.', 'Exists only by code.', 'Non-OData body.'):
            self.assertIn(kept, learned)
        for dropped in ('Invented.', 'Out of order.', 'Too short.'):
            self.assertNotIn(dropped, learned)

    def test_distill_is_newest_first_and_one_bad_source_does_not_stop_the_batch(self):
        # Alphabetical store order spent every run on the home store (41/41
        # transactions); an exception in one source ended the run for all stores.
        import contextlib, io, time as _time
        paths = []
        for i, name in enumerate(('2026-01-01.md', '2026-01-02.md', '2026-01-03.md')):
            p = self.root / self.ctx / 'memory' / name
            atomic(p, 'Decision ' + name + ' because it is measured and durable.\n')
            stamp = _time.time() - 86400 * (3 - i)
            os.utime(p, (stamp, stamp))
            paths.append(p)
        calls = []

        def fake(memory, source, instruction, schema=None):
            calls.append(source)
            if '2026-01-02' in source:
                raise ValueError('malformed reply')
            return {'facts': []}
        err = io.StringIO()
        with patch.object(maintain, 'generate', side_effect=fake), contextlib.redirect_stderr(err):
            failures = maintain.maintain(self.memory, 'distill')
        self.assertEqual([c.split()[1] for c in calls], ['2026-01-03.md', '2026-01-02.md', '2026-01-01.md'])
        self.assertEqual(len(failures), 1)
        self.assertIn('2026-01-02.md', err.getvalue())
        receipts = list((self.memory.state / 'extracted').glob('*.json'))
        self.assertEqual(len(receipts), 2)  # the failed source stays eligible for retry

    def test_distill_skips_sources_still_changing_today(self):
        p = self.root / self.ctx / 'checkpoints' / 'session-today.md'
        atomic(p, 'Decision taken today because the session is still open.\n')
        with patch.object(maintain, 'generate') as gen:
            maintain.maintain(self.memory, 'distill')
        gen.assert_not_called()

    def test_temp_dir_stores_are_never_backfilled_or_distilled(self):
        # A session run under /tmp is a headless test. Whoever wrote its transcript or checkpoint
        # (Stop hook, jsonl extractor), it must not become a memory page: those were injected into
        # real sessions and came back the morning after being removed (2026-10-12).
        ctx = self.root / 'projects/-tmp-claude-1000-fixture/context'
        stamp = __import__('time').time() - 86400
        for folder in ('transcripts', 'checkpoints'):
            atomic(ctx / folder / '2026-01-01.md', 'Canary decision because the hook test said so.\n')
            os.utime(ctx / folder / '2026-01-01.md', (stamp, stamp))
        with patch.object(maintain, 'generate') as gen:
            maintain.maintain(self.memory, 'backfill')
            maintain.maintain(self.memory, 'distill')
        gen.assert_not_called()

    def test_quota_exhaustion_aborts_the_batch(self):
        for name in ('2026-01-01.md', '2026-01-02.md'):
            p = self.root / self.ctx / 'memory' / name
            atomic(p, 'Durable decision because reasons.\n')
            stamp = __import__('time').time() - 86400
            os.utime(p, (stamp, stamp))
        err = RuntimeError('extractor failed; no memory changed (exit 1): 5-hour limit reached ∙ resets 3pm')
        with patch.object(maintain, 'generate', side_effect=err) as gen:
            with self.assertRaisesRegex(RuntimeError, 'resets'):
                maintain.maintain(self.memory, 'distill')
        self.assertEqual(gen.call_count, 1)


class CheckpointCollectionTests(unittest.TestCase):
    """Checkpoints left `curated` on 2026-09-20: conversation prose overlaps every query and
    outranked curated pages (two absent benchmark queries moved 1.65/1.66 -> 3.30/3.07 in a
    day, topped by checkpoints of the session that wrote them)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'store'
        self.cwd = Path(self.tmp.name) / 'repo'
        (self.cwd / '.git').mkdir(parents=True)
        self.memory = Memory(self.root)
        ctx = self.memory.resolve(self.cwd)['relative'] + '/context'
        atomic(self.memory.path(ctx + '/MEMORY.md'), '# Context\nThe widget uses a lock.\n')
        atomic(self.memory.path(ctx + '/checkpoints/a-b.md'), '# Chat\nThe widget uses a lock.\n')
        # Transcripts are not a curated write, so the guard refuses memory.path() for them.
        raw = self.root / ctx / 'transcripts' / '2026-01-01.md'
        raw.parent.mkdir(parents=True, exist_ok=True)
        atomic(raw, '# Raw\nwidget lock\n')

    def sources(self, collection):
        return {str(p) for p, _project, _kind in
                self.memory.documents(self.cwd, 'all', None, collection)}

    def test_curated_excludes_checkpoints(self):
        self.assertFalse([s for s in self.sources('curated') if 'checkpoints' in s])

    def test_curated_still_has_the_real_page(self):
        self.assertTrue([s for s in self.sources('curated') if s.endswith('MEMORY.md')])

    def test_checkpoints_collection_returns_only_checkpoints(self):
        found = self.sources('checkpoints')
        self.assertTrue(found)
        self.assertTrue(all('checkpoints' in s for s in found))

    def test_checkpoints_collection_excludes_transcripts(self):
        self.assertFalse([s for s in self.sources('checkpoints') if 'transcripts' in s])

    def test_transcripts_collection_unaffected(self):
        found = self.sources('transcripts')
        self.assertTrue(all('transcripts' in s for s in found))

    def test_curated_fts_returns_only_allowed_sources(self):
        # T3.1: the index holds scope `all`; the caller's allow-list is the only scope guard.
        self.memory.index_collection('curated')
        page = next(str(p.relative_to(self.root)) for p, _, _ in self.memory.documents(self.cwd, 'all', None, 'curated'))
        hits = lambda allowed: [h['source'] for h in self.memory.search_fts(
            'widget lock', log=False, collection='curated', allowed=allowed)['results']]
        self.assertEqual(hits({page}), [page])
        self.assertEqual(hits({'other.md'}), [])
        self.assertEqual(hits(None), [])

    def test_unknown_collection_still_rejected(self):
        with self.assertRaises(ValueError):
            list(self.memory.documents(self.cwd, 'all', None, 'nonsense'))

    def test_writing_a_checkpoint_still_allowed(self):
        # The write path must keep working: only RETRIEVAL was demoted.
        self.memory.checkpoint(self.cwd, 'sess', 'a decision and why', 'cursor-1')
        written = self.sources('checkpoints')
        self.assertTrue([s for s in written if 'a-b.md' not in s],
                        'checkpoint() wrote nothing - the write path regressed')

    def test_positive_control_curated_would_include_them_without_the_guard(self):
        # Reverting the guard must make test_curated_excludes_checkpoints fail. Proven by
        # asking the SAME question through the checkpoints collection: the file is there,
        # indexable, and only the curated filter keeps it out.
        self.assertTrue([s for s in self.sources('checkpoints') if 'checkpoints' in s],
                        'fixture is empty - the exclusion test would pass vacuously')


class IdfScoringTests(unittest.TestCase):
    """RAG plan T2.1 (2026-09-21): a token present in most of the corpus must not weigh like a
    rare one. Before IDF an off-topic query scored above real hits on 'sap'/'b1' alone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'store'
        self.cwd = Path(self.tmp.name) / 'repo'
        (self.cwd / '.git').mkdir(parents=True)
        self.memory = Memory(self.root)
        ctx = self.memory.resolve(self.cwd)['relative'] + '/context'
        for i in range(6):
            atomic(self.memory.path(f'{ctx}/topics/filler{i}.md'), f'# Filler {i}\nsap b1 page {i}\n')
        # Generic tokens repeated: wins on raw term frequency when every term weighs the same.
        atomic(self.memory.path(ctx + '/topics/generic.md'), '# G\n' + 'sap b1 ' * 5 + '\n')
        atomic(self.memory.path(ctx + '/topics/rare.md'), '# R\nzebra notes\n')

    def test_rare_term_outranks_repeated_generic_terms(self):
        top = self.memory.search('sap b1 zebra', self.cwd, log=False)['results'][0]
        self.assertTrue(top['source'].endswith('rare.md'), top['source'])

    def test_term_in_every_document_still_retrieves(self):
        # The +1 inside the log keeps IDF positive: a term in every page must not zero out.
        hits = self.memory.search('sap', self.cwd, log=False)['results']
        self.assertTrue(hits and all(h['score'] > 0 for h in hits))


class SpanScoringTests(unittest.TestCase):
    """2026-09-28: a long prompt is scored as the best short query inside it. Whole-prompt coverage
    buried the page matching 3 of 36 terms ("...got my Jev API Key...") under one matching filler."""
    setUp = Tests.setUp
    COMMON = 'deploy server config user report build test cache queue token'.split()

    def seed(self):
        for i in range(20):
            atomic(self.memory.path(f'projects/other/context/topics/f{i}.md'), ' '.join(self.COMMON) + f' page{i}\n')
        atomic(self.memory.path('projects/other/context/topics/chatty.md'), ' '.join(self.COMMON) + '\n')
        atomic(self.memory.path('projects/other/context/topics/target.md'), 'zebra quokka marmot notes ' + 'lorem ' * 100 + '\n')

    def scores(self, query, discount):
        self.memory.config['span_discount'] = discount
        return {h['source'].rsplit('/', 1)[1]: h['score'] for h in self.memory.search(query, self.cwd, 'all', limit=30, log=False)['results']}

    def test_page_matching_one_topic_of_a_long_prompt_wins(self):
        self.seed()
        prompt = ' '.join(self.COMMON) + ' and by the way zebra quokka marmot arrived'
        off, on = self.scores(prompt, 0), self.scores(prompt, 0.84)
        self.assertGreater(off['chatty.md'], off['target.md'])  # the bug, reproduced with spans off
        self.assertGreater(on['target.md'], on['chatty.md'])

    def test_short_queries_and_weak_spans_are_untouched(self):
        self.seed()
        short = 'zebra quokka marmot unseen1 unseen2'  # <= SPAN_WIDTH terms; a 3/3 sub-span would beat 3/5
        self.assertEqual(self.scores(short, 0), self.scores(short, 0.84))
        for prompt in (' '.join(self.COMMON) + ' zebra quokka arrived',  # only 2 matched in the span
                       ' '.join(self.COMMON) + ' zebra x1 x2 x3 x4 x5 x6 x7 x8 quokka marmot'):  # wider than SPAN_WIDTH
            self.assertEqual(self.scores(prompt, 0)['target.md'], self.scores(prompt, 0.84)['target.md'], prompt)

    def test_function_words_dropped_but_sql_words_kept(self):
        self.assertEqual(memory_core.tokens('algo aqui onde from so if for while not nao on by'),
                         ['for', 'while', 'not', 'nao', 'on', 'by'])


class AutoEngineTests(unittest.TestCase):
    """RAG plan T2.3: `mem search --auto` pays for semantic only when lexical is weak."""
    setUp = Tests.setUp

    def auto(self, query, floor, semantic_result):
        cli = module('cli_auto', BASE / 'scripts/memory-cli.py')
        self.memory.config['recall_min_score'] = floor
        out = io.StringIO()
        argv = ['mem', 'search', query, '--auto', '--json', '--scope', 'project', '--cwd', str(self.cwd)]
        with patch.object(cli, 'Memory', return_value=self.memory), patch.object(sys, 'argv', argv), \
                patch.object(cli, 'semantic', side_effect=[semantic_result]) as sem, redirect_stdout(out):
            cli.run()
        # The mock stands in for memory-search.py; under --auto it must be asked not to log.
        self.assertTrue(all(c.kwargs.get('log') is False for c in sem.call_args_list))
        logged = [e for e in map(json_read, (self.memory.state / 'usage').glob('*.json')) if e['event'] == 'search']
        return json.loads(out.getvalue()), sem.call_count, len(logged)

    def test_strong_lexical_skips_semantic(self):
        output, calls, logged = self.auto('validation', 0.01, None)
        self.assertEqual((output['engine'], calls, logged), ('lexical', 0, 1))

    def test_weak_or_empty_lexical_runs_semantic(self):
        hit = {'id': 'h', 'source': 'x.md', 'score': 0.8}
        for query, floor in (('validation', 1e9), ('zzzabsent', 0)):
            with self.subTest(query=query):
                self.memory.state.joinpath('usage').mkdir(parents=True, exist_ok=True)
                for old in self.memory.state.joinpath('usage').glob('*.json'):
                    old.unlink()
                output, calls, logged = self.auto(query, floor, {'request_id': 'r' * 32, 'scope': 'all', 'engine': 'semantic',
                                                                 'elapsed_ms': 1, 'results': [hit]})
                self.assertEqual((output['engine'], calls, logged), ('semantic', 1, 1))

    def test_empty_semantic_keeps_weak_lexical_hits(self):
        # Codex stop review 2026-09-26: an empty semantic answer replaced valid lexical hits.
        output, calls, logged = self.auto('validation', 1e9, {'request_id': 'r' * 32, 'scope': 'all', 'engine': 'semantic',
                                                              'elapsed_ms': 1, 'results': []})
        self.assertEqual((output['engine'], calls, logged), ('lexical', 1, 1))
        self.assertTrue(output['results'])

    def test_semantic_failure_keeps_lexical(self):
        output, _, logged = self.auto('validation', 1e9, ValueError('semantic index is busy'))
        self.assertEqual((output['engine'], logged), ('lexical', 1))
        self.assertTrue(output['results'])
        self.assertEqual(output['semantic_error'], 'semantic index is busy')


class StubEmbedder:
    model = 'stub'

    def __init__(self):
        self.seen = []

    def embed(self, texts):
        import numpy as np
        self.seen.extend(texts)
        vecs = np.array([[1.0 if 'validation' in t.lower() else 0.0, 1.0] for t in texts], dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


class SqliteEngineTests(unittest.TestCase):
    """RAG plan T3.2: vectors in index.sqlite, fused with FTS, scoped before ranking."""

    def setUp(self):
        Tests.setUp(self)
        self.semantic = module('semantic_sqlite', BASE / 'scripts/memory-search.py')
        self.other = 'projects/other/context/MEMORY.md'
        atomic(self.memory.path(self.other), '# Private\nvalidation validation validation\n')
        self.memory.index_collection('curated')

    def test_batches_bound_padded_tokens_and_keep_every_row(self):
        rows = [(i, 'x' * n) for i, n in enumerate(sorted([10] * 100 + [900] * 20 + [5000] * 20))]
        parts = list(self.semantic.batches(rows))
        self.assertEqual([r for p in parts for r in p], rows)
        for part in parts:
            self.assertLessEqual(len(part) * min(1024, max(len(t) for _, t in part)), 8192)
            self.assertLessEqual(len(part), 64)
        self.assertEqual(len(parts[-2]), 8)                 # 1024-token chunks: 8 per batch, not 64

    def test_index_vectors_embeds_only_chunks_without_a_vector(self):
        stub = StubEmbedder()
        self.assertEqual(self.semantic.index_vectors(self.memory, 'curated', stub)['embedded'], 2)
        db = self.memory.sdk_db()
        self.assertEqual(db.execute('SELECT DISTINCT length(blob), dim FROM vectors').fetchall(), [(4, 2)])  # float16
        db.close()
        self.assertEqual(self.semantic.index_vectors(self.memory, 'curated', stub)['embedded'], 0)
        atomic(self.memory.path(self.rel), '# Context\nNever skip validation.\n')
        self.memory.index_collection('curated')
        stub.seen.clear()
        self.assertEqual(self.semantic.index_vectors(self.memory, 'curated', stub)['embedded'], 1)
        self.assertIn('skip', stub.seen[0])

    def test_search_scoped_before_ranking(self):
        stub = StubEmbedder()
        self.semantic.index_vectors(self.memory, 'curated', stub)
        search = lambda scope: [h['source'] for h in self.semantic.search_sqlite(
            self.memory, 'validation', str(self.cwd), scope, None, 'curated', 5, stub)]
        self.assertEqual(search('project'), [self.rel])     # the better-matching private page never shows
        self.assertEqual(set(search('all')), {self.rel, self.other})

    def test_search_drops_hits_whose_file_changed_since_indexing(self):
        stub = StubEmbedder()
        self.semantic.index_vectors(self.memory, 'curated', stub)
        # No lexical match left, so only the vector leg's stale chunk could surface it.
        atomic(self.memory.path(self.rel), '# Context\nNothing here.\n')
        hits = self.semantic.search_sqlite(self.memory, 'validation', str(self.cwd), 'project', None, 'curated', 5, stub)
        self.assertEqual(hits, [])


class SdkFtsTests(unittest.TestCase):
    """RAG plan T1.3: `sdk` collection, SQLite FTS5 over references/_md, lexical only."""
    setUp = Tests.setUp

    def page(self, rel, name, members):
        text = f'---\nname: {name}\nkind: sdk\n---\n# SAPbobsCOM.{name}\n\n## Public Methods\n' + \
               ''.join(f'- **{m}** — {d}\n' for m, d in members)
        path = self.root / 'references/_md' / rel  # generated corpus: memory.path() refuses it, rightly
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def build(self):
        self.page('di/Documents.md', 'Documents', [('Add', 'Adds a record.'), ('GetByKey', 'Retrieves by key.')])
        self.page('di/Items.md', 'Items', [('Add', 'Adds an item.'), ('Update', 'Updates an item.')])
        self.page('ui/Form.md', 'Form', [('Close', 'Closes the form.')])
        return self.memory.index_sdk()

    def sources(self, query, limit=5):
        return [h['source'] for h in self.memory.search(query, self.cwd, collection='sdk', limit=limit, log=False)['results']]

    def test_a_identifier_ranks_its_page_first_one_hit_per_source(self):
        self.assertEqual(self.build(), {'indexed': 3, 'changed': 3, 'removed': 0})
        self.assertEqual(self.sources('Documents GetByKey')[0], 'references/_md/di/Documents.md')
        # Two matching sections in one page must still yield one hit for that page.
        (self.root / 'references/_md/di/Items.md').write_text(
            '# SAPbobsCOM.Items\n## Public Methods\n- **Add** — Adds.\n## Public Properties\n- **AddDate** — Add date.\n')
        self.memory.index_sdk()
        found = self.memory.search('Add', self.cwd, collection='sdk', log=False)
        self.assertEqual(found['engine'], 'fts5')
        self.assertEqual(sorted(h['source'] for h in found['results']),
                         ['references/_md/di/Documents.md', 'references/_md/di/Items.md'])

    def test_b_changed_file_updates_revision_and_content_only(self):
        self.build()
        self.page('di/Items.md', 'Items', [('Remove', 'Deletes an obsolete item.')])
        self.assertEqual(self.memory.index_sdk(), {'indexed': 3, 'changed': 1, 'removed': 0})
        hit = self.memory.search('obsolete', self.cwd, collection='sdk', log=False)['results'][0]
        self.assertIn('Deletes an obsolete item.', hit['content'])
        self.assertEqual(hit['revision'], digest(read(self.root / 'references/_md/di/Items.md')))
        self.assertEqual(self.sources('Updates'), [])

    def test_c_deleted_file_leaves_the_index(self):
        self.build()
        (self.root / 'references/_md/ui/Form.md').unlink()
        self.assertEqual(self.memory.index_sdk()['removed'], 1)
        self.assertEqual(self.sources('Closes form'), [])

    def test_d_never_returns_a_path_outside_references_md(self):
        self.build()
        db = self.memory.sdk_db()
        with db:
            doc = db.execute("INSERT INTO docs(source, revision, collection) VALUES ('context/USER.md', 'x', 'sdk')").lastrowid
            db.execute("INSERT INTO chunks(doc_id, line, end_line, heading, content) VALUES (?, 1, 1, 'x', 'GetByKey GetByKey GetByKey')", (doc,))
        db.close()
        self.assertTrue(self.sources('GetByKey'))
        self.assertTrue(all(s.startswith('references/_md/') for s in self.sources('GetByKey')))

    def test_operators_in_query_are_quoted_and_expand_reads_the_file(self):
        self.build()
        found = self.memory.search('SAPbobsCOM.Documents get-by-key "x" AND', self.cwd, collection='sdk')
        self.assertEqual(found['results'][0]['source'], 'references/_md/di/Documents.md')
        self.assertIn('Retrieves by key.', self.memory.expand(found['request_id'], found['results'][0]['id'])['content'])

    def test_matched_terms_see_through_dotted_code(self):
        path = self.root / 'references/_md/samples/a.cs.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# a.cs\n\n```csharp\noRecordSet.DoQuery(sql);\n```\n')
        self.memory.index_sdk()
        hit = self.memory.search('DoQuery Recordset', self.cwd, collection='sdk', log=False)['results'][0]
        self.assertEqual(hit['matched_terms'], ['doquery'])

    def test_reindexed_chunk_never_inherits_an_old_vector(self):
        # T3.2: a deleted chunk's vector must go with it - SQLite reuses the freed rowid.
        self.build()
        db = self.memory.sdk_db()
        with db:
            cid = db.execute("SELECT c.id FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.source LIKE '%Items.md'").fetchone()[0]
            db.execute("INSERT INTO vectors(chunk_id, model, dim, blob) VALUES (?, 'm', 1, x'00000000')", (cid,))
        db.close()
        self.page('di/Items.md', 'Items', [('Remove', 'Changed page.')])
        self.memory.index_sdk()
        db = self.memory.sdk_db()
        self.assertEqual(db.execute('SELECT count(*) FROM vectors').fetchone()[0], 0)
        db.close()

    def test_missing_index_is_an_error_not_an_empty_result(self):
        with self.assertRaisesRegex(ValueError, 'sdk index not built'):
            self.memory.search('GetByKey', self.cwd, collection='sdk')

    def test_long_section_split_with_overlap(self):
        text = '# T\n## S\n' + ''.join(f'line {i}\n' for i in range(100))
        spans = [(a, b) for a, b, _, _ in memory_core.sdk_chunks(text)]
        self.assertEqual(spans, [(2, 41), (38, 77), (74, 102)])
        self.assertEqual({h for _, _, h, _ in memory_core.sdk_chunks(text)}, {'T > S'})

    def test_cli_rejects_semantic_and_auto_stays_fts_and_logged(self):
        self.build()
        cli = module('cli_sdk', BASE / 'scripts/memory-cli.py')
        base = ['mem', 'search', 'GetByKey', '-c', 'sdk', '--json', '--cwd', str(self.cwd)]
        with patch.object(cli, 'Memory', return_value=self.memory), patch.object(sys, 'argv', base + ['--semantic']):
            with self.assertRaisesRegex(ValueError, 'lexical only'):
                cli.run()
        out = io.StringIO()
        with patch.object(cli, 'Memory', return_value=self.memory), patch.object(sys, 'argv', base + ['--auto']), \
                patch.object(cli, 'semantic') as sem, redirect_stdout(out):
            cli.run()
        found = json.loads(out.getvalue())
        self.assertEqual((found['engine'], sem.call_count), ('fts5', 0))
        self.assertTrue(self.memory.expand(found['request_id'], found['results'][0]['id']))


class IntegrationTests(unittest.TestCase):
    setUp = Tests.setUp
    def test_global_caps_reject_overflow_and_split_preserves_original(self):
        target = 'context/USER.md'
        original = 'Preference accented ação.\n' * 40
        atomic(self.memory.path(target), original)
        with self.assertRaisesRegex(ValueError, 'cap exceeded'):
            self.memory.apply({'version': VERSION, 'changes': [self.memory.change(target, original + 'x' * 1400)]})
        self.memory.split(target, force=True)
        self.assertLess(len(read(self.memory.path(target))), 1375)
        self.assertTrue(any(read(path) == original for path in (self.root / 'context').glob('index-*.md')))

    def test_split_preserves_relative_link_destinations(self):
        target = self.ctx + '/topics/decision.md'
        atomic(self.memory.path(target), 'Original decision')
        original = '[Decision](topics/decision.md)\n'
        atomic(self.memory.path(self.rel), original)
        result = self.memory.split(self.rel, force=True)
        archive = next(self.memory.path(path) for path in result['changed'] if '/index-' in path)
        self.assertEqual(read(archive), original)
        self.assertEqual(read(archive.parent / 'topics/decision.md'), 'Original decision')

    def test_legacy_import_is_lossless_and_idempotent(self):
        import sqlite3
        importer = module('importer', BASE / 'scripts/memory-import-codex.py')
        database = Path(self.tmp.name) / 'legacy.sqlite'
        connection = sqlite3.connect(database)
        connection.execute('CREATE TABLE memories(id TEXT, project TEXT, client TEXT, scope TEXT, title TEXT, content TEXT, status TEXT, updated_at TEXT)')
        connection.execute("INSERT INTO memories VALUES ('original-id', 'project-A', NULL, 'project', 'Decision', 'Never disable validation. ação', 'active', 'yesterday')")
        connection.commit()
        connection.close()
        original = database.read_bytes()
        proposed = importer.proposal(self.memory, database)
        self.memory.apply(proposed)
        self.assertEqual(importer.proposal(self.memory, database)['changes'], [])
        found = self.memory.search('validation', str(self.cwd), 'all')
        legacy = next(hit for hit in found['results'] if 'legacy-codex-project-A' in hit['project'])
        self.assertIn('Never disable validation. ação', self.memory.expand(found['request_id'], legacy['id'])['content'])
        self.assertEqual(database.read_bytes(), original)

    def test_cli_hook_emits_valid_json_for_both_harnesses(self):
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'PreCompact'):
            result = subprocess.run([sys.executable, str(BASE / 'hooks/memory-lifecycle.py')],
                input=json.dumps({'hook_event_name': event, 'cwd': str(self.cwd), 'session_id': 'test', 'prompt': 'validation'}),
                env={**os.environ, 'AI_MEMORY_HOME': str(self.root)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            if event in ('SessionStart', 'UserPromptSubmit'):
                self.assertIn('additionalContext', payload['hookSpecificOutput'])
            else:
                self.assertEqual(payload, {})

    def test_feedback_rejects_unrelated_result(self):
        found = self.memory.search('validation', str(self.cwd))
        reference = found['results'][0]['id']
        self.assertEqual(self.memory.feedback(found['request_id'], reference, 'relevant'), {'recorded': 'relevant'})
        with self.assertRaisesRegex(ValueError, 'belong'):
            self.memory.feedback(found['request_id'], 'not-returned', 'used')

    def test_store_reads_refuse_symlinks_anywhere_on_the_path(self):
        import datetime as dt
        doctor = module('doctor', BASE / 'scripts/memory-doctor.py')
        tmp, ctx = Path(self.tmp.name), self.root / self.ctx
        (tmp / 'outside.md').write_text('# s\nleaked canary\n')
        (tmp / 'outdir').mkdir()
        (tmp / 'outdir/x.md').write_text('# d\nleaked canary\n')
        (tmp / 'outlogs').mkdir()
        (tmp / 'outlogs' / (dt.date.today().isoformat() + '.md')).write_text('# l\nleaked canary\n')
        (tmp / 'outstore/context').mkdir(parents=True)
        (tmp / 'outstore/context/MEMORY.md').write_text('# e\nleaked canary\n')
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat() + '.md'
        before = (dt.date.today() - dt.timedelta(days=2)).isoformat() + '.md'
        (ctx / 'MEMORY.md').unlink()
        links = [(ctx / 'MEMORY.md', 'outside.md'), (ctx / 'topics', 'outdir'),
                 (self.root / 'context/USER.md', 'outside.md'),
                 (ctx / 'memory', 'outlogs'),
                 (self.root / 'projects/clean/context/transcripts' / before, 'outside.md'),
                 (self.root / 'projects/evil', 'outstore')]
        for link, target in links:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(tmp / target)
        # Positive controls: real files with the same word ARE read.
        atomic(self.root / 'context/MEMORY.md', '# Global\nreal canary\n')
        atomic(self.root / 'projects/clean/context/transcripts' / yesterday, '# t\nreal canary\n')
        atomic(ctx / 'transcripts' / yesterday, '# t\nlog dir is a link\n')  # its log is unwritable: skip, never abort
        os.utime(tmp / 'outside.md', (0, dt.datetime.now().timestamp() - 86400))

        snapshot = self.memory.snapshot(self.cwd)['text']
        self.assertIn('real canary', snapshot)
        self.assertNotIn('leaked', snapshot)
        hits = self.memory.search('canary', self.cwd, scope='all', log=False)['results']
        self.assertTrue(hits)
        self.assertFalse([h['source'] for h in hits if not self.memory.stored(self.root / h['source'])])
        with patch.object(maintain, 'generate', side_effect=ValueError('stop')) as generate:
            failures = maintain.maintain(self.memory, 'backfill')
        self.assertIn(self.ctx + '/transcripts/' + yesterday, failures)  # reported, not silent
        sent = [call.args[1] for call in generate.call_args_list]
        self.assertEqual(sent, ['# t\nreal canary\n'])
        self.assertEqual(set(doctor.symlinks(self.root)), {link for link, _ in links})

    def test_doctor_reports_symlinked_projects_dir(self):
        doctor = module('doctor', BASE / 'scripts/memory-doctor.py')
        real = Path(self.tmp.name) / 'real-projects'
        (self.root / 'projects').rename(real)
        (self.root / 'projects').symlink_to(real)
        (self.root / 'context').mkdir(parents=True, exist_ok=True)
        (self.root / 'context/USER.md').symlink_to(real / 'x.md')
        atomic(real / self.rel.split('/', 1)[1].replace('MEMORY.md', 'topics/lost.md'), 'behind the link\n')
        (real / self.rel.split('/', 1)[1].replace('MEMORY.md', 'inner.md')).symlink_to(real / 'x.md')  # seen only by walking through
        self.assertEqual(doctor.orphans(self.root), [])  # hidden by the link: reported once, as a symlink
        self.assertIsNone(self.memory.read_stored(self.root / self.rel))  # project memory hidden...
        self.assertEqual(set(doctor.symlinks(self.root)),                 # ...so it must be reported
                         {self.root / 'projects', self.root / 'context/USER.md'})
        issues = doctor.diagnose(self.memory)['issues']                   # and the full run must not crash
        self.assertIn({'type': 'symlink_in_store', 'path': str(self.root / 'projects')}, issues)

    def test_doctor_never_reads_through_a_link(self):
        doctor = module('doctor', BASE / 'scripts/memory-doctor.py')
        big = Path(self.tmp.name) / 'big.md'
        big.write_text('x' * 3000)
        (self.root / self.rel).unlink()
        (self.root / self.rel).symlink_to(big)
        linked_ctx = Path(self.tmp.name) / 'linked-ctx'
        linked_ctx.mkdir()
        (linked_ctx / 'inner.md').symlink_to(big)  # reported only if the doctor walks through the link
        (self.root / 'projects/other').mkdir()
        (self.root / 'projects/other/context').symlink_to(linked_ctx)
        skills = self.root / 'skills'
        atomic(skills / 'plain/references/r.md', 'real\n')
        (skills / 'plain/references/linked.md').symlink_to(big)          # dropped from search: report
        atomic(Path(self.tmp.name) / 'ext-skill/knowledge/k.md', 'k\n')
        (skills / 'installed').symlink_to(Path(self.tmp.name) / 'ext-skill')  # hides knowledge/: report
        (Path(self.tmp.name) / 'bare-skill').mkdir()
        (skills / 'kl').mkdir()
        (skills / 'kl/knowledge').symlink_to(Path(self.tmp.name) / 'ext-skill/knowledge')  # search root itself linked: report
        (skills / 'bare').symlink_to(Path(self.tmp.name) / 'bare-skill')     # SKILL.md only: normal, quiet
        self.assertEqual(set(doctor.symlinks(self.root)), {self.root / self.rel, self.root / 'projects/other/context',
                                                           skills / 'plain/references/linked.md', skills / 'installed',
                                                           skills / 'kl/knowledge'})
        issues = doctor.diagnose(self.memory)['issues']
        self.assertEqual([i for i in issues if i['type'] == 'over_cap'], [])  # a read through the link would flag 3000 > 2500
        # Installed files are copies: a link to an identical file, or a path escaping the root, is drift.
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/tool.py').symlink_to(big)
        json_write(self.memory.state / 'installation.json',
                   {'files': {'scripts/tool.py': digest('x' * 3000), '../big.md': digest('x' * 3000)}})
        drift = {i['path'] for i in doctor.diagnose(self.memory)['issues'] if i['type'] == 'installation_drift'}
        self.assertEqual(drift, {'scripts/tool.py', '../big.md'})
        self.assertIn({'type': 'symlink_in_store', 'path': str(self.root / self.rel)}, issues)

    def test_doctor_flags_only_unreachable_pages(self):
        doctor = module('doctor', BASE / 'scripts/memory-doctor.py')
        ctx = self.root / self.ctx
        atomic(self.memory.path(self.rel), '# Context\n- [a](topics/a.md)\n')
        atomic(ctx / 'topics/a.md', 'see [[b]] and [[GLOBAL]]\n')
        atomic(ctx / 'topics/b.md', 'reached through a\n')
        atomic(ctx / 'topics/lost.md', 'nothing links here\n')
        atomic(ctx / 'topics/archive/old.md', 'unindexed on purpose\n')
        atomic(ctx / 'checkpoints/x.md', '[[orphan-by-checkpoint]]\n')
        atomic(self.root / 'context/SATELLITE.md', 'no pointer in MEMORY.md\n')
        atomic(self.root / 'context/GLOBAL.md', 'reached from a project page\n')
        atomic(self.root / 'projects/other/context/MEMORY.md', '# Other\n')
        atomic(self.root / 'projects/other/context/topics/b.md', 'same stem, other store\n')
        found = {p.relative_to(self.root.resolve()).as_posix() for p in doctor.orphans(self.root)}
        self.assertEqual(found, {self.ctx + '/topics/lost.md', 'context/SATELLITE.md',
                                 'projects/other/context/topics/b.md'})

if __name__ == '__main__':
    unittest.main()
