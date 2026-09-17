#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_core import Memory, VERSION, atomic, digest, json_read, json_write, read


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BASE = Path(__file__).resolve().parent.parent
hooks = module('lifecycle', BASE / 'hooks/memory-lifecycle.py')
maintain = module('maintain', BASE / 'scripts/memory-maintain.py')


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
        self.assertEqual(len(list((self.memory.state / 'dirty').glob('*'))), 1)

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

    def test_index_failure_retains_dirty_receipt(self):
        self.apply(self.memory.change(self.rel, read(self.memory.path(self.rel)) + 'new'))
        with patch.object(maintain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'offline')):
            self.assertEqual(maintain.reindex(self.memory), 1)
        self.assertEqual(len(list((self.memory.state / 'dirty').glob('*.json'))), 1)

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

    def test_incremental_index_does_not_prune_other_sources(self):
        import asyncio
        import types
        from unittest.mock import Mock, AsyncMock
        indexer = module('indexer', BASE / 'scripts/memory-index.py')
        backend = Mock()
        backend._index_file = AsyncMock(return_value=1)
        scanned = types.SimpleNamespace(path=self.memory.path(self.rel))
        modules = {
            'memsearch.core': types.SimpleNamespace(MemSearch=Mock(return_value=backend)),
            'memsearch.config': types.SimpleNamespace(resolve_config=lambda: {}),
            'memsearch.cli': types.SimpleNamespace(_cfg_to_memsearch_kwargs=lambda _: {}),
            'memsearch.scanner': types.SimpleNamespace(scan_paths=lambda _: [scanned])}
        with patch.dict(sys.modules, modules):
            self.assertEqual(asyncio.run(indexer.update([str(scanned.path)])), 1)
        backend._index_file.assert_awaited_once_with(scanned)
        backend.index.assert_not_called()
        backend._store.delete_by_source.assert_not_called()
        backend.close.assert_called_once()

    def test_rebuild_batches_files_without_collection_pruning(self):
        import asyncio
        import types
        from unittest.mock import Mock, AsyncMock
        compute_chunk_id = lambda *parts: digest(repr(parts))
        indexer = module('indexer_batch', BASE / 'scripts/memory-index.py')
        first = self.memory.path(self.rel)
        second = self.memory.path(self.ctx + '/topics/second.md')
        atomic(second, 'Another independent decision.')
        backend = Mock()
        backend._max_chunk_size = 1500
        backend._overlap_lines = 2
        backend._embedder.model_name = 'test-model'
        backend._store.hashes_by_source.return_value = set()
        backend._embed_and_store = AsyncMock(side_effect=lambda chunks: len(chunks))
        modules = {
            'memsearch.core': types.SimpleNamespace(MemSearch=Mock(return_value=backend), compute_chunk_id=compute_chunk_id),
            'memsearch.chunker': types.SimpleNamespace(chunk_markdown=lambda text, source, **_: [types.SimpleNamespace(source=source, start_line=1, end_line=1, content_hash=digest(text), content=text)]),
            'memsearch.config': types.SimpleNamespace(resolve_config=lambda: {}),
            'memsearch.cli': types.SimpleNamespace(_cfg_to_memsearch_kwargs=lambda _: {}),
            'memsearch.scanner': types.SimpleNamespace(scan_paths=lambda _: [types.SimpleNamespace(path=p) for p in (first, second)])}
        with patch.dict(sys.modules, modules):
            count = asyncio.run(indexer.update([str(first), str(second)], batch_across_files=True))
        self.assertEqual(count, 2)
        sent = backend._embed_and_store.call_args.args[0]
        self.assertEqual({chunk.source for chunk in sent}, {str(first), str(second)})
        backend._store.delete_by_source.assert_not_called()
        backend.index.assert_not_called()

    def test_semantic_search_waits_for_index_worker(self):
        import asyncio
        import fcntl
        from unittest.mock import AsyncMock
        semantic = module('semantic_lock', BASE / 'scripts/memory-search.py')
        self.memory.state.mkdir(parents=True, exist_ok=True)
        async def check():
            with (self.memory.state / 'index.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with patch.object(semantic, 'search_scoped', new_callable=AsyncMock, return_value=[]) as query:
                    task = asyncio.create_task(semantic.search(self.memory, 'test', str(self.cwd), 'all', None, 'curated', 5))
                    await asyncio.sleep(0.03)
                    query.assert_not_called()
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    self.assertEqual(await task, [])
                    query.assert_awaited_once()
        asyncio.run(check())

    def test_semantic_keeps_crlf_evidence_and_drops_stale_duplicates(self):
        import asyncio
        import types
        from unittest.mock import Mock, AsyncMock
        semantic = module('semantic_crlf', BASE / 'scripts/memory-search.py')
        path = self.memory.path(self.rel)
        atomic(path, 'Heading\r\nNever disable validation.\r\n')
        backend = Mock()
        backend._embedder.embed = AsyncMock(return_value=[[1.0]])
        good = {'source': str(path), 'content': 'Heading\nNever disable validation.', 'score': 1}
        backend._store.search.return_value = [{**good, 'content': 'Always disable validation.'}, good, good]
        backend._reranker_model = ''
        modules = {
            'memsearch.core': types.SimpleNamespace(MemSearch=Mock(return_value=backend)),
            'memsearch.config': types.SimpleNamespace(resolve_config=lambda: {}),
            'memsearch.cli': types.SimpleNamespace(_cfg_to_memsearch_kwargs=lambda _: {}),
            'memsearch.store': types.SimpleNamespace(_escape_filter_value=lambda value: value),
            'memsearch.reranker': types.SimpleNamespace(rerank=Mock())}
        with patch.dict(sys.modules, modules):
            hits = asyncio.run(semantic.search(self.memory, 'validation', str(self.cwd), 'project', None, 'curated', 5))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['content'], good['content'])
        self.assertEqual(hits[0]['revision'], digest(read(path)))

    def test_semantic_scope_checked_before_reranking(self):
        import asyncio
        import types
        from unittest.mock import Mock, AsyncMock
        semantic = module('semantic', BASE / 'scripts/memory-search.py')
        backend = Mock()
        backend._embedder.embed = AsyncMock(return_value=[[1.0]])
        backend._store.search.return_value = [{'source': str(self.root / 'projects/other/context/MEMORY.md'), 'content': 'private', 'score': 1}]
        backend._reranker_model = 'test'
        rerank = Mock()
        modules = {
            'memsearch.core': types.SimpleNamespace(MemSearch=Mock(return_value=backend)),
            'memsearch.config': types.SimpleNamespace(resolve_config=lambda: {}),
            'memsearch.cli': types.SimpleNamespace(_cfg_to_memsearch_kwargs=lambda _: {}),
            'memsearch.store': types.SimpleNamespace(_escape_filter_value=lambda value: value),
            'memsearch.reranker': types.SimpleNamespace(rerank=rerank)}
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'outside'):
                asyncio.run(semantic.search(self.memory, 'validation', str(self.cwd), 'project', None, 'curated', 5))
        expression = backend._store.search.call_args.kwargs['filter_expr']
        self.assertIn(str(self.memory.path(self.rel)), expression)
        self.assertNotIn('/projects/other/', expression)
        rerank.assert_not_called()
        backend.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
