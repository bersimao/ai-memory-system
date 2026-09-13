#!/usr/bin/env python3
"""Incremental memsearch adapter: never treat changed files as the whole corpus.

The caller holds data/memory-system/index.lock. MemSearch.index() prunes every
source absent from its input list in 0.4.4; only _index_file is safe for deltas.
"""
import argparse
import asyncio
from pathlib import Path
from memory_core import Memory


async def update(paths, collection='memsearch_chunks', batch_across_files=False):
    from memsearch.core import MemSearch
    from memsearch.config import resolve_config
    from memsearch.cli import _cfg_to_memsearch_kwargs
    from memsearch.scanner import scan_paths
    kwargs = _cfg_to_memsearch_kwargs(resolve_config())
    kwargs['collection'] = collection
    index = MemSearch(**kwargs)
    try:
        count = 0
        pending = []
        for item in scan_paths(paths):
            if not batch_across_files:
                count += await index._index_file(item)
                continue
            # Same source-local reconciliation as _index_file, with embeddings
            # batched across files. Small notes otherwise run batch size one.
            from memsearch.chunker import chunk_markdown
            from memsearch.core import compute_chunk_id
            source = str(item.path)
            chunks = chunk_markdown(item.path.read_text(encoding='utf-8'), source=source,
                                    max_chunk_size=index._max_chunk_size, overlap_lines=index._overlap_lines)
            model = index._embedder.model_name
            def identity(chunk):
                return compute_chunk_id(chunk.source, chunk.start_line, chunk.end_line, chunk.content_hash, model)
            old_ids = index._store.hashes_by_source(source)
            stale = old_ids - {identity(chunk) for chunk in chunks}
            if stale:
                index._store.delete_by_hashes(list(stale))
            pending.extend(chunk for chunk in chunks if identity(chunk) not in old_ids)
            while len(pending) >= 32:
                count += await index._embed_and_store(pending[:32])
                pending = pending[32:]
                print('Chunks rebuilt: ' + str(count), flush=True)
        if pending:
            count += await index._embed_and_store(pending)
        # Do not call index.index(): it deletes unrelated existing sources.
        return count
    finally:
        index.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args()
    memory = Memory(args.root)
    if args.reconcile:
        import fcntl
        memory.state.mkdir(parents=True, exist_ok=True)
        with (memory.state / 'index.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            paths = [str(p) for p, _, _ in memory.documents(str(Path.cwd()), 'all')]
            print('Reconciling ' + str(len(paths)) + ' curated files.', flush=True)
            print('Chunks indexed: ' + str(asyncio.run(update(paths, batch_across_files=True))), flush=True)
    else:
        for path in args.paths:
            memory.path(str(Path(path).relative_to(memory.root)))
        print('Chunks indexed: ' + str(asyncio.run(update(args.paths))))
