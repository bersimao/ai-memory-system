#!/usr/bin/env python3
"""Rebuild one collection in index.sqlite by revision (changed docs only, vanished docs removed)."""
import argparse
import json
from pathlib import Path
from memory_core import Memory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--collection', choices=['sdk', 'curated', 'transcripts'], required=True,
                        help='rebuild that collection in index.sqlite; curated/transcripts also embed new chunks')
    args = parser.parse_args()
    memory = Memory(args.root)
    if args.collection == 'sdk':
        print('sdk: ' + json.dumps(memory.index_sdk()))
        raise SystemExit(0)
    # RAG plan T3.2: chunks first (the cascade drops vectors of re-chunked docs), then embed what
    # has no vector. numpy/onnxruntime stay inside memory-search.py (executor contract).
    import importlib.util
    spec = importlib.util.spec_from_file_location('memory_search', Path(__file__).with_name('memory-search.py'))
    search = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(search)
    print(args.collection + ': ' + json.dumps(memory.index_collection(args.collection)), flush=True)
    print(args.collection + ' vectors: ' + json.dumps(search.index_vectors(memory, args.collection)), flush=True)
