#!/usr/bin/env python3
"""Compatible human-readable mem entry point with explicit scope and request IDs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from memory_core import Memory, main


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
    parser.add_argument('--semantic', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--session')
    args = parser.parse_args(sys.argv[2:])
    collection = {'memsearch_chunks': 'curated', 'memsearch_transcripts': 'transcripts'}.get(args.collection, args.collection)
    if collection not in ('curated', 'transcripts') or not 1 <= args.top_k <= 50:
        raise ValueError('invalid collection or result limit')
    if args.semantic:
        # Validate scope/domain before invoking optional dependencies.
        memory.roots(args.cwd, args.scope, args.domain, collection)
        payload = {'root': str(memory.root), 'query': args.query, 'cwd': args.cwd, 'scope': args.scope,
                   'domain': args.domain, 'collection': collection, 'limit': args.top_k, 'session': args.session}
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('memory-search.py'))],
                                input=json.dumps(payload), text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        output = json.loads(result.stdout)
    else:
        output = memory.search(args.query, args.cwd, args.scope, args.domain, args.top_k, args.session, collection)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    if not output['results']:
        print('(no results in searched scope; not proof of absence — try identifiers, the other language, or --semantic)')
    for i, hit in enumerate(output['results'], 1):
        print(f"{i}. [{hit['score']:.4f}] {output['request_id']}:{hit['id']}  {memory.root / hit['source']}")
        print('   Project: ' + hit['project'] + ' | ' + hit['content'].replace('\n', ' ')[:240])


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        print('mem: ' + str(exc), file=sys.stderr)
        sys.exit(1)
