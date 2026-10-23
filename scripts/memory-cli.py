#!/usr/bin/env python3
"""Compatible human-readable mem entry point with explicit scope and request IDs."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from memory_core import Memory, main


# How long a search waits for the resident process before loading the model itself (~2.3 s). It
# answers curated in ~1.3 s and transcripts in ~2.6 s (measured 2026-10-11); past this it is busy with someone else's
# request or stuck, and waiting longer costs more than the load it was meant to save.
RESIDENT_WAIT = 5
# Where an agent CLI puts its session id: Claude Code, then Codex.
SESSION_VARS = ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID')


def resident(memory, args, collection):
    """Ask the prompt hook's resident embedder: no ~2.3 s model load per call. None = not running,
    busy past RESIDENT_WAIT, failed, or started from older code that ignores `collection`; the caller
    then loads the model itself, so the worst case is RESIDENT_WAIT on top of the old path.
    ponytail: one request at a time over there, so a long search here can make a prompt's vector
    leg time out (2 s) and fall back to lexical; a second worker if that shows in `mem report`."""
    started = time.monotonic()

    def left():
        # One deadline for the whole exchange: a socket timeout alone restarts on every connect, send
        # and recv, so a process that trickles bytes would hold the search far past RESIDENT_WAIT.
        remaining = started + RESIDENT_WAIT - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('resident process too slow')
        return remaining
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(left())
            client.connect(str(memory.state / 'semantic.sock'))
            client.settimeout(left())
            client.sendall((json.dumps({'query': args.query, 'cwd': args.cwd, 'scope': args.scope, 'domain': args.domain,
                                        'collection': collection, 'limit': args.top_k, 'log': False}) + '\n').encode('utf-8'))
            data = b''
            while not data.endswith(b'\n'):
                client.settimeout(left())
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
            reply = json.loads(data)
    except (OSError, ValueError):
        return None
    if 'error' in reply or reply.get('collection') != collection:
        return None
    return {'request_id': reply['request_id'], 'results': reply['results'], 'scope': args.scope,
            'engine': 'semantic-sqlite', 'elapsed_ms': round((time.monotonic() - started) * 1000)}


def semantic(memory, args, collection, log=True):
    # Validate scope/domain before invoking optional dependencies.
    memory.roots(args.cwd, args.scope, args.domain, collection)
    found = resident(memory, args, collection)
    if found:
        if log:
            memory.log_search(found, args.query, args.session, 'cli')
        return found
    payload = {'root': str(memory.root), 'query': args.query, 'cwd': args.cwd, 'scope': args.scope,
               'domain': args.domain, 'collection': collection, 'limit': args.top_k, 'session': args.session, 'log': log, 'origin': 'cli'}
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('memory-search.py'))],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def run():
    if len(sys.argv) < 2 or sys.argv[1] not in ('search', 'expand'):
        return main()
    memory = Memory()
    if sys.argv[1] == 'expand':
        if len(sys.argv) != 3 or ':' not in sys.argv[2]:
            raise ValueError('use mem expand <request_id>:<result_id> from a new search; legacy hashes require re-search')
        request, result = sys.argv[2].split(':', 1)
        print(json.dumps(memory.expand(request, result), ensure_ascii=False, indent=2))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('query')
    parser.add_argument('-k', '--top-k', type=int, default=5)
    parser.add_argument('-c', '--collection', default='curated')
    parser.add_argument('--scope', choices=['all', 'project'], default=memory.config.get('automatic_recall_scope', 'project'))
    parser.add_argument('--cwd', '--project', default=os.getcwd())
    parser.add_argument('--domain')
    engine = parser.add_mutually_exclusive_group()
    engine.add_argument('--semantic', action='store_true')
    engine.add_argument('--auto', action='store_true', help='lexical; semantic only when the top score is under recall_min_score')
    parser.add_argument('--json', action='store_true')
    # The agent's own searches count as real-session use (salience); bench and cron run without it.
    # Codex 0.163 sets CODEX_THREAD_ID and CODEX_SESSION_ID (equal); mem-bench.py strips the same list.
    parser.add_argument('--session', default=next((os.environ[v] for v in SESSION_VARS if os.environ.get(v)), None))
    args = parser.parse_args(sys.argv[2:])
    collection = {'memsearch_chunks': 'curated', 'memsearch_transcripts': 'transcripts'}.get(args.collection, args.collection)
    if collection not in ('curated', 'transcripts', 'checkpoints', 'sdk') or not 1 <= args.top_k <= 50:
        raise ValueError('invalid collection or result limit')
    if collection == 'sdk' and args.semantic:
        raise ValueError('sdk collection is lexical only')
    auto = args.auto and collection != 'sdk'  # sdk has no semantic side; bm25 is not on the floor's scale
    if args.semantic:
        output = semantic(memory, args, collection)
    else:
        output = memory.search(args.query, args.cwd, args.scope, args.domain, args.top_k, args.session, collection,
                               log=not auto, origin='cli')
    # RAG plan T2.3: semantic costs ~18 s p50, so it runs only when lexical is weak.
    # Same floor as the prompt hook; with no floor configured only an empty result is weak.
    if auto:
        if not output['results'] or output['results'][0]['score'] < float(memory.config.get('recall_min_score', 0)):
            try:
                found = semantic(memory, args, collection, log=False)
                if found['results']:  # an empty semantic answer must not erase weak lexical hits
                    output = found
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                output['semantic_error'] = str(exc)  # never worse than lexical
        memory.log_search(output, args.query, args.session, 'cli')  # one query, one event, for the kept result
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    if not output['results']:
        print('(no results in searched scope; not proof of absence — try identifiers, the other language, or --semantic)')
    elif args.auto and output['engine'].startswith('semantic'):
        # Measured 2026-09-26: on absent queries semantic still returns unrelated pages at ~0.8.
        print('(lexical was weak, semantic shown: nearest pages, not proof the topic exists — verify)')
    if output.get('semantic_error'):
        print('(semantic fallback failed, lexical shown: ' + output['semantic_error'] + ')', file=sys.stderr)
    for i, hit in enumerate(output['results'], 1):
        print(f"{i}. [{hit['score']:.4f}] {output['request_id']}:{hit['id']}  {memory.root / hit['source']}")
        print('   Project: ' + hit['project'] + ' | ' + hit['content'].replace('\n', ' ')[:240])


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        print('mem: ' + str(exc), file=sys.stderr)
        sys.exit(1)
