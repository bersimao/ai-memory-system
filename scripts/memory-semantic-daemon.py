#!/usr/bin/env python3
"""Resident semantic retrieval for the prompt hook (recall-cue prompts only).

Measured 2026-09-26: a semantic call costs ~3.7 s, of which 2.3 s is loading the
embedder and ~0.5 s opening Milvus; the query itself is ~0.3 s warm. This process
keeps ONLY the embedder resident. Milvus is still opened and closed per request
under index.lock by memory-search.search(), because Milvus Lite holds an exclusive
per-process database lock and the indexer must keep working between requests.

The reranker is off here: its warm inference is 4-8 s for 6 pairs on CPU.
Protocol: one JSON line in ({query, cwd, scope, limit, session}), one JSON line out.
"""
import asyncio
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_core import Memory, digest

IDLE_SECONDS = 1800


def socket_path(memory):
    return memory.state / 'semantic.sock'


def answer(memory, request, search):
    query, scope, limit = request['query'], request.get('scope', 'all'), int(request.get('limit', 2))
    if not isinstance(query, str) or not query.strip() or scope not in ('project', 'all') or not 0 < limit <= 10:
        raise ValueError('invalid request')
    hits = asyncio.run(search(memory, query, request['cwd'], scope, None, 'curated', limit))
    result = {'request_id': uuid.uuid4().hex, 'results': hits, 'engine': 'semantic'}
    # Same event memory-search.py logs, so `mem expand` works on these hits.
    memory.event({'event': 'search', 'request_id': result['request_id'], 'session': request.get('session'),
                  'query_hash': digest(query), 'scope': scope, 'engine': 'semantic',
                  'hits': [{k: v for k, v in h.items() if k != 'content'} for h in hits]})
    return result


def serve(memory, server, search, idle=IDLE_SECONDS):
    server.settimeout(idle)
    while True:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            return
        with conn:
            try:
                conn.settimeout(5)
                request = json.loads(conn.makefile('r', encoding='utf-8').readline())
                reply = answer(memory, request, search)
            except Exception as exc:
                reply = {'error': str(exc)}
            try:
                conn.sendall((json.dumps(reply) + '\n').encode('utf-8'))
            except OSError:
                pass  # the hook gave up waiting; nothing to deliver


def main():
    memory = Memory()
    memory.state.mkdir(parents=True, exist_ok=True)
    with (memory.state / 'semantic-daemon.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another instance owns the socket
        os.environ['MEMSEARCH_RERANKER'] = 'off'
        import memsearch.core
        from memsearch.config import resolve_config
        from memsearch.cli import _cfg_to_memsearch_kwargs
        kwargs = _cfg_to_memsearch_kwargs(resolve_config())
        embedder = memsearch.core.get_provider(kwargs['embedding_provider'], model=kwargs['embedding_model'],
                                               batch_size=kwargs['embedding_batch_size'],
                                               base_url=kwargs['embedding_base_url'], api_key=kwargs['embedding_api_key'])
        memsearch.core.get_provider = lambda *args, **kw: embedder
        spec = importlib.util.spec_from_file_location('memory_search', Path(__file__).with_name('memory-search.py'))
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        path = socket_path(memory)
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            server.bind(str(path))
        finally:
            os.umask(old)
        server.listen(4)
        try:
            serve(memory, server, adapter.search)
        finally:
            server.close()
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
