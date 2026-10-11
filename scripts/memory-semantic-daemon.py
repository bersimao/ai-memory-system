#!/usr/bin/env python3
"""Resident embedder for the prompt hook: the vector leg of hybrid recall (`mode: "vector"`, every
prompt whose lexical #1 clears the floor, 2026-09-28) and cue-gated semantic recall (`recall_semantic`).

Loading the embedder is the cost of a cold semantic call (2.3 s of ~3.7 s, measured 2026-09-26),
so this process keeps ONLY the embedder resident; the vectors are read from index.sqlite per request.

Protocol: one JSON line in ({query, cwd, scope, limit, session}, plus `mode` for the vector leg),
one JSON line out.
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


def answer(memory, request, search, vectors=None):
    query, scope, limit = request['query'], request.get('scope', 'all'), int(request.get('limit', 2))
    # mode "vector": the vector leg alone, which the hook fuses with the lexical ranking it already
    # has (hybrid recall, 2026-09-28) - deeper, since RRF needs a ranking, not a top 2.
    vector = request.get('mode') == 'vector'
    if not isinstance(query, str) or not query.strip() or scope not in ('project', 'all') or not 0 < limit <= (30 if vector else 10):
        raise ValueError('invalid request')
    if vector and vectors is None:
        raise ValueError('vector mode needs semantic_engine sqlite')
    if vector:
        hits = vectors(memory, query, request['cwd'], scope, None, 'curated', limit)
    else:
        hits = asyncio.run(search(memory, query, request['cwd'], scope, None, 'curated', limit))
    engine = 'vector' if vector else 'semantic'
    result = {'request_id': uuid.uuid4().hex, 'results': hits, 'engine': engine}
    # Same event memory-search.py logs, so `mem expand` works on these hits.
    memory.event({'event': 'search', 'request_id': result['request_id'], 'session': request.get('session'),
                  'query_hash': digest(query), 'scope': scope, 'engine': engine,
                  'hits': [{k: v for k, v in h.items() if k != 'content'} for h in hits]})
    return result


def serve(memory, server, search, idle=IDLE_SECONDS, vectors=None):
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
                reply = answer(memory, request, search, vectors)
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
        spec = importlib.util.spec_from_file_location('memory_search', Path(__file__).with_name('memory-search.py'))
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        # Resident embedder; vectors are still read from index.sqlite per request.
        embedder = adapter.Embedder()
        async def search(memory, query, cwd, scope, domain, collection, limit):
            return adapter.search_sqlite(memory, query, cwd, scope, domain, collection, limit, embedder)
        def vectors(memory, query, cwd, scope, domain, collection, limit):
            return adapter.vector_hits(memory, query, cwd, scope, domain, collection, limit, embedder)
        listen(memory, search, vectors)

def listen(memory, search, vectors=None):
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
        serve(memory, server, search, vectors=vectors)
    finally:
        server.close()
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
