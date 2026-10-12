#!/usr/bin/env python3
"""Scoped semantic search adapter. Optional dependency isolated from the core."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

from memory_core import Memory, digest, read, redact


# --- RAG plan T3.2: the `sqlite` engine (index.sqlite chunks + bge-m3 vectors + FTS, fused by RRF) ---
# numpy/onnxruntime live ONLY here (executor contract). Same model and pooling as memsearch's ONNX
# provider (pre-pooled `dense_vecs`, L2-normalised), loaded from the local HF cache, never downloaded.
EMBED_MODEL = 'gpahal/bge-m3-onnx-int8'


class Embedder:
    def __init__(self, model=EMBED_MODEL, max_tokens=1024):
        import glob
        import onnxruntime
        import tokenizers
        snaps = sorted(glob.glob(os.path.expanduser('~/.cache/huggingface/hub/models--' + model.replace('/', '--') + '/snapshots/*/')))
        if not snaps:
            # Never downloaded here (executor contract): name the one command that fetches it.
            raise ValueError('embedding model not on disk: ' + model + '. Fetch it once: python3 -c "from huggingface_hub '
                             "import snapshot_download; snapshot_download('" + model + "', allow_patterns=['model_quantized.onnx', "
                             "'tokenizer.json'])\"")
        base = snaps[-1]
        self.model = model
        self.tokenizer = tokenizers.Tokenizer.from_file(base + 'tokenizer.json')
        # ponytail: 1024-token cap (memsearch uses 8192); chunks are <= 40 lines, so it rarely cuts.
        self.tokenizer.enable_truncation(max_length=max_tokens)
        self.tokenizer.enable_padding()
        onnx = base + ('model_quantized.onnx' if os.path.exists(base + 'model_quantized.onnx') else 'model.onnx')
        self.session = onnxruntime.InferenceSession(onnx, providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.outputs = [o.name for o in self.session.get_outputs()]

    def embed(self, texts):
        import numpy as np
        encoded = self.tokenizer.encode_batch(list(texts))
        feed = {'input_ids': np.array([e.ids for e in encoded], dtype=np.int64),
                'attention_mask': np.array([e.attention_mask for e in encoded], dtype=np.int64)}
        if 'token_type_ids' in self.inputs:
            feed['token_type_ids'] = np.zeros_like(feed['input_ids'])
        out = self.session.run(None, feed)
        vecs = out[self.outputs.index('dense_vecs')] if 'dense_vecs' in self.outputs else out[self.outputs.index('last_hidden_state')][:, 0, :]
        vecs = np.asarray(vecs, dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def batches(todo, batch=64, budget=8192, cap=1024):
    """Split length-sorted (id, text) rows so padded tokens per batch stay <= budget. A token is
    >= 1 char, so n * min(cap, longest chars) bounds it without tokenizing. Measured 2026-09-27:
    64 x 1024 tokens asked onnxruntime for one 4 GiB buffer (32 x 1024 = 7.6 GB RSS); 8 x 1024 = 3.5 GB."""
    part = []
    for row in todo:
        if part and (len(part) >= batch or (len(part) + 1) * min(cap, len(row[1])) > budget):
            yield part
            part = []
        part.append(row)
    if part:
        yield part


def index_vectors(memory, collection, embedder=None, batch=64, progress=None):
    """Embed chunks of `collection` that have no vector for this model (T3.2 indexing: changed
    chunks only - a re-chunked doc gets new rows, and the cascade dropped the old vectors)."""
    import numpy as np
    embedder = embedder or Embedder()
    db = memory.sdk_db(create=True)
    todo = db.execute("SELECT c.id, c.heading || char(10) || c.content FROM chunks c JOIN docs d ON d.id = c.doc_id "
                      "LEFT JOIN vectors v ON v.chunk_id = c.id AND v.model = ? WHERE d.collection = ? AND v.chunk_id IS NULL",
                      (embedder.model, collection)).fetchall()
    todo.sort(key=lambda r: len(r[1]))                  # similar lengths per batch = less padding
    done = 0
    for part in batches(todo, batch):
        vecs = embedder.embed([t for _, t in part])
        with db:                                        # commit per batch: an interrupted run keeps its progress
            db.executemany('INSERT OR REPLACE INTO vectors(chunk_id, model, dim, blob) VALUES (?, ?, ?, ?)',
                           [(cid, embedder.model, vecs.shape[1], vec.astype(np.float16).tobytes()) for (cid, _), vec in zip(part, vecs)])
        done += len(part)
        if progress:
            progress(done, len(todo))
    db.close()
    return {'embedded': done, 'model': embedder.model}


def _allowed(memory, cwd, scope, domain, collection):
    return {str(p.relative_to(memory.root)): (project, kind) for p, project, kind in memory.documents(cwd, scope, domain, collection)}


def _vector_rank(memory, query, allowed, collection, depth, embedder):
    """{source: line of its best chunk}, in cosine order, at most `depth` sources."""
    import numpy as np
    db = memory.sdk_db()
    rows = [r for r in db.execute('SELECT d.source, c.line, v.blob FROM vectors v JOIN chunks c ON c.id = v.chunk_id '
                                  'JOIN docs d ON d.id = c.doc_id WHERE d.collection = ? AND v.model = ?', (collection, embedder.model))
            if r[0] in allowed]                         # scope BEFORE ranking, same allow-list as lexical
    db.close()
    ranked = {}
    if rows:
        # ponytail: all vectors of the collection per query (curated 2.3k x 4 KB); an ANN index if it grows 10x.
        # float16 on disk (T3.4: 0/37 bench cases changed vs float32, half the size); a leftover float32
        # blob makes the reshape fail loudly instead of mixing garbage in.
        matrix = np.frombuffer(b''.join(r[2] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
        sims = matrix @ embedder.embed([query])[0]
        for i in np.argsort(-sims):
            if len(ranked) >= depth:
                break
            ranked.setdefault(rows[i][0], rows[i][1])     # best chunk per source, in rank order
    return ranked


def _chunk_hit(memory, db, allowed, source, line, score):
    """The indexed chunk as a hit, or None when the file changed since it was indexed."""
    row = db.execute('SELECT c.end_line, c.content FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.source = ? AND c.line = ?',
                     (source, line)).fetchone()
    text = memory.read_stored(memory.root / source)
    # Never present old indexed text as current evidence.
    if not row or text is None or row[1] not in text.replace('\r\n', '\n').replace('\r', '\n'):
        return None
    project, kind = allowed[source]
    return {'id': digest(source + ':' + str(line) + ':' + digest(text))[:24], 'source': source,
            'project': project, 'kind': kind, 'line': line, 'end_line': row[0], 'revision': digest(text),
            'score': score, 'content': redact(row[1])}


def vector_hits(memory, query, cwd, scope, domain, collection, limit, embedder=None):
    """Vector leg alone, best chunk per source in cosine order: the prompt hook fuses it with the lexical
    ranking it already has, so the resident process skips a second lexical scan (2026-09-28)."""
    allowed = _allowed(memory, cwd, scope, domain, collection)
    if not allowed:
        return []
    ranked = _vector_rank(memory, query, allowed, collection, limit, embedder or Embedder())
    db = memory.sdk_db()
    hits = [_chunk_hit(memory, db, allowed, source, line, round(1 / (60 + rank), 5)) for rank, (source, line) in enumerate(ranked.items())]
    db.close()
    return [h for h in hits if h]


def search_sqlite(memory, query, cwd, scope, domain, collection, limit, embedder=None):
    allowed = _allowed(memory, cwd, scope, domain, collection)
    if not allowed:
        return []
    embedder = embedder or Embedder()
    # RRF by SOURCE: the lexical leg is the calibrated scan (bench T3.2: bm25-per-chunk FTS lost c022,
    # the scan ranks it 1), and its windows do not line up with chunks. Each leg ranks sources.
    vec_line = _vector_rank(memory, query, allowed, collection, limit * 10, embedder)
    fused = {source: 1 / (60 + rank) for rank, source in enumerate(vec_line)}
    lexical = {}
    for rank, hit in enumerate(memory.search(query, cwd, scope, domain, limit * 10, None, collection, log=False)['results']):
        lexical[hit['source']] = hit
        fused[hit['source']] = fused.get(hit['source'], 0) + 1 / (60 + rank)
    db = memory.sdk_db()
    result = []
    for source, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        if len(result) >= limit:
            break
        if source in lexical:                               # already read, verified and redacted by the scan
            result.append({**lexical[source], 'score': round(score, 5)})
            continue
        hit = _chunk_hit(memory, db, allowed, source, vec_line[source], round(score, 5))
        if hit:
            result.append(hit)
    db.close()
    return result


if __name__ == '__main__':
    try:
        args = json.load(sys.stdin)
        memory = Memory(args.get('root'))
        started = time.monotonic()
        hits = search_sqlite(memory, args['query'], args['cwd'], args['scope'], args.get('domain'), args['collection'], args['limit'])
        result = {'request_id': uuid.uuid4().hex, 'results': hits, 'scope': args['scope'],
                  'engine': 'semantic-sqlite',  # label kept: usage logs and bench runs are keyed on it
                  'elapsed_ms': round((time.monotonic() - started) * 1000)}
        if args.get('log', True):  # `mem search --auto` logs only the result it keeps
            memory.event({'event': 'search', 'request_id': result['request_id'], 'session': args.get('session'),
                          'query_hash': digest(args['query']), 'scope': args['scope'], 'engine': result['engine'],
                          'hits': [{k: v for k, v in h.items() if k != 'content'} for h in hits]})
        print(json.dumps(result))
    except Exception as exc:
        print('Semantic retrieval unavailable: ' + str(exc) + '. Use lexical search; this is not an absence of memory.', file=sys.stderr)
        sys.exit(1)
