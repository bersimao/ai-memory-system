# Retrieval interface

The contract between the memory system and whatever engine answers L1/L2
searches. Written 2026-08-23, after discovering that the current engine
(`memsearch`) refuses to run on Windows for a reason that is no longer true.

## Why this exists

`memsearch` is a **convenience, not a constraint**. It was treated as
load-bearing until it turned out that:

- the corpus is **1.373 chunks / 1,2 MB of markdown** — Milvus is built for
  millions of vectors;
- a brute-force numpy search over that corpus takes **0,152 ms**, measured;
- `memsearch` blocks Windows on a stale `sys.platform` check
  (`store.py:75`), while the thing it claims is unavailable — `milvus-lite`
  3.x — actually works there (verified: create/insert/flush/search on win32).

So the engine may need to change, and the system must not have to change with
it. This file defines the seam. Same pattern as `scripts/llm-run`, which is the
backend config for LLM calls: one file, two functions, nothing else.

## The seam is `scripts/mem`

`mem` is the only thing that talks to a retrieval engine. CLAUDE.md's L1/L2
tiers call `mem`, never `python3 -m memsearch` directly. Backend coupling is two
functions at the top of the file:

```bash
backend_search() {          # <query> <k> <collection>
  local q="$1" k="$2" coll="$3" c
  case "$coll" in
    curated)     c=memsearch_chunks ;;
    transcripts) c=memsearch_transcripts ;;
  esac
  python3 -m memsearch search "$q" -j -k "$k" -c "$c"
}
backend_expand() { exec python3 -m memsearch expand "$1"; }
```

Swapping engines means editing those two functions. Everything else in `mem` —
argument parsing, result formatting, usage logging, `mem report` — is
engine-agnostic and must keep working untouched.

**`mem` parses the query controls itself and never forwards raw flags.** This is
the whole point: a backend is not required to speak memsearch's CLI. An
unrecognised option is rejected with exit 2 rather than passed through, so a
backend-specific flag fails loudly instead of silently binding the system to one
engine.

## Contract

### `backend_search <query> <k> <collection>`

Three required arguments — these are the query controls the retrieval ladder
depends on, so they are contractual, not optional extras:

| arg | meaning |
|---|---|
| `query` | the search text |
| `k` | max results. `mem` defaults to 5; L1 in CLAUDE.md passes `--top-k 5` |
| `collection` | **logical** name: `curated` (L1) or `transcripts` (L3) |

`collection` is never a storage name. `memsearch_chunks` /
`memsearch_transcripts` are memsearch's names; a numpy backend would map the
same two logical names onto `curated.npy` / `transcripts.npy`. The mapping lives
inside `backend_search` and nowhere else. `mem` also accepts the legacy
memsearch names on `-c` so CLAUDE.md's existing L3 line keeps working.

The L1/L3 split is architectural, not cosmetic — measured 2026-07-29, mixing
transcripts into the main collection buried the authoritative answer in 13 of 15
top results. A backend that cannot separate the two collections cannot implement
this ladder.

Writes JSON to stdout: either a list, or an object with a `results` list. Each
result is an object with:

| field | type | meaning |
|---|---|---|
| `chunk_hash` | string | stable id; the only thing `expand` receives |
| `score` | number | higher = better. See the scale note below |
| `source` | string | absolute path of the file the chunk came from |
| `content` | string | the chunk text (first ~220 chars are displayed) |

Extra fields are ignored. Exit non-zero on failure — `mem` reports and stops.

### `backend_expand <chunk_id>`

Writes the full section around that chunk to stdout as plain text. Takes the
`chunk_hash` from a prior search — one argument, no options. May `exec`; nothing
runs after it.

### Score scale — the one thing that is not free

`mem report` flags a search as a probable miss when the top score is `<= 0.50`,
because in the current engine that value is the reciprocal-rank-fusion floor:
it means only one retrieval arm matched. **A backend with a different score
scale invalidates that threshold**, and `mem report` will silently mislabel
results. Any swap must either normalize scores to the same meaning, or land
together with a recalibrated threshold.

This is exactly the calibration question already open as pendência 2.

## Frozen: the usage log

`~/.claude/data/memsearch-usage.jsonl` is the measurement baseline for the
pendência-2 review. Existing fields (`ts`, `ev`, `key`, `hits`) do **not** change
until that review runs — not for a backend swap, not for a nicer field name. A
backend that cannot produce `chunk_hash` / `score` / `source` must fake them
rather than change the log.

*Additive* fields are allowed, because they keep old lines readable and
comparable. One was added on 2026-08-23: **`coll`**, the logical collection a
search ran against. Without it the review cannot answer its own L3 question
("Tier0+grep+L1 erram → L3 salva?") — every search looked identical in the log.
Lines written before that date have no `coll` and are read as `curated`.

## Known backends

| backend | Python deps | model | Windows | semantics | status |
|---|---|---|---|---|---|
| **memsearch** (default) | ~196 MB | 560 MB | **blocked** | dense+sparse | in use |
| onnx + numpy brute force | ~114 MB | 560 MB | works | dense only | **proven**, not built |
| sqlite FTS5 (BM25) | **0** (stdlib) | none | works | keyword only | option |
| grep (L0.5) | 0 | none | works | none | already in the ladder |

The 560 MB model (`gpahal/bge-m3-onnx-int8`, dim 1024) is required by any
embedding-based backend, so it is not a differentiator between the first two —
only dropping embeddings escapes it.

### Building the numpy backend, if it comes to that

Proven on Windows 11 / CPython 3.14.4 / onnxruntime 1.29.0:

```python
enc  = tok.encode_batch(texts)                       # tokenizers
out  = sess.run(["dense_vecs"], feed)[0]             # onnxruntime
V    = out / np.linalg.norm(out, axis=1, keepdims=True)
hits = np.argsort(-(V @ query_vec))[:k]              # 0,152 ms over 1.373 vecs
```

Vectors fit in a 5,6 MB `.npy`. What is *not* free, and is the real cost of
owning this: markdown-aware chunking, incremental re-embedding of changed files
only, and `expand`'s section reconstruction.

## What this does not cover

Indexing. `cron/memsearch-index.sh` still calls memsearch directly, because
building the index is a backend-private concern — a numpy backend would write a
`.npy`, FTS5 would write a `.db`. Only *query* is contractual.
