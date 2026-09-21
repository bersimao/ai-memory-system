#!/usr/bin/env python3
"""Scoped semantic search adapter. Optional dependency isolated from the core."""
import asyncio
import json
from pathlib import Path
import sys
import time
import uuid

from memory_core import Memory, digest, read, redact


async def search(memory, query, cwd, scope, domain, collection, limit):
    # Milvus Lite has an exclusive process-level database lock. Coordinate
    # retrieval with the post-write worker and periodic reconciliation.
    import fcntl
    memory.state.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    with (memory.state / 'index.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('semantic index is busy; retry or use lexical retrieval')
                await asyncio.sleep(0.1)
        from memory_core import semantic_db_owned_by
        if not semantic_db_owned_by(memory.root):
            raise ValueError('semantic index belongs to another memory root; use lexical retrieval')
        return await search_scoped(memory, query, cwd, scope, domain, collection, limit)


async def search_scoped(memory, query, cwd, scope, domain, collection, limit):
    from memsearch.core import MemSearch
    from memsearch.config import resolve_config
    from memsearch.cli import _cfg_to_memsearch_kwargs
    from memsearch.store import _escape_filter_value
    from memsearch.reranker import rerank

    allowed = {str(p.resolve()): (project, kind) for p, project, kind in memory.documents(cwd, scope, domain, collection)}
    if not allowed:
        return []
    cfg = resolve_config()
    kwargs = _cfg_to_memsearch_kwargs(cfg)
    kwargs['collection'] = 'memsearch_transcripts' if collection == 'transcripts' else 'memsearch_chunks'
    ms = MemSearch(**kwargs)
    try:
        # Scope applies before candidate retrieval and reranking. Using the exact
        # allowlist also excludes stale/deleted paths and sibling-prefix matches.
        expression = 'source in [' + ','.join('"' + _escape_filter_value(p) + '"' for p in allowed) + ']'
        vectors = await ms._embedder.embed([query])
        hits = ms._store.search(vectors[0], query_text=query, top_k=limit * 3, filter_expr=expression)
        if any(str(Path(hit['source']).resolve()) not in allowed for hit in hits):
            raise ValueError('backend returned a source outside the allowed scope')
        if ms._reranker_model and hits:
            hits = rerank(query, hits, model_name=ms._reranker_model, top_k=limit * 3)
        result = []
        seen = set()
        for hit in hits:
            if len(result) >= limit:
                break
            path = str(Path(hit['source']).resolve())
            if path not in allowed:
                raise ValueError('backend returned a source outside the allowed scope')
            text = memory.read_stored(path)
            content = hit.get('content', '')
            # Never present old indexed text as current evidence.
            if text is None or not content or content not in text.replace('\r\n', '\n').replace('\r', '\n') or path in seen:
                continue
            seen.add(path)
            relative = str(Path(path).relative_to(memory.root))
            project, kind = allowed[path]
            line = int(hit.get('start_line', 1))
            result.append({'id': digest(relative + ':' + str(line) + ':' + digest(text))[:24],
                           'source': relative, 'project': project, 'kind': kind, 'line': line,
                           'revision': digest(text), 'score': hit['score'], 'content': redact(content)})
        return result
    finally:
        ms.close()


if __name__ == '__main__':
    try:
        args = json.load(sys.stdin)
        memory = Memory(args.get('root'))
        started = time.monotonic()
        hits = asyncio.run(search(memory, args['query'], args['cwd'], args['scope'], args.get('domain'), args['collection'], args['limit']))
        result = {'request_id': uuid.uuid4().hex, 'results': hits, 'scope': args['scope'],
                  'engine': 'semantic', 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        memory.event({'event': 'search', 'request_id': result['request_id'], 'session': args.get('session'),
                      'query_hash': digest(args['query']), 'scope': args['scope'], 'engine': 'semantic',
                      'hits': [{k: v for k, v in h.items() if k != 'content'} for h in hits]})
        print(json.dumps(result))
    except Exception as exc:
        print('Semantic retrieval unavailable: ' + str(exc) + '. Use lexical search; this is not an absence of memory.', file=sys.stderr)
        sys.exit(1)
