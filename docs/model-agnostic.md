# Runtime design

The core is Python standard library plus the existing Node project-anchor resolver.
Markdown is authoritative. Journals preserve before/after revisions; optional lexical
and semantic retrieval do not own facts. Hook adapters only deliver events and context.
There is no MCP dependency.

Lexical recall runs locally. Optional semantic recall isolates memsearch API coupling
in `scripts/memory-search.py`; the adapter was checked against memsearch 0.4.4. It
filters allowed sources before retrieval, verifies that boundary before reranking,
and rejects stale indexed text. Embedding/reranker configuration remains in memsearch's
private configuration. Provider behavior and languages still affect semantic relevance.

Extraction defaults to `claude -p` with an empty tool set, skills disabled, no loaded
settings sources, no session persistence, and an empty strict MCP configuration.
The model returns JSON candidates with source quotes; it cannot edit live memory
through those tools. Exact quotes prove traceability, not that a paraphrase is true.
Corrections and semantic merging remain reviewed operations.
The default extractor also passes `--json-schema`, so the reply arrives parsed in
`structured_output`. Any extractor's reply is still accepted inside a markdown code
fence, which models add unprompted, and a JSON object returned inside the `summary`
string is rendered to Markdown instead of being stored as a blob.

To use another extractor, set private `data/memory-system/config.json`:

```json
{"automatic_recall_scope":"all", "extract_command":["my-extractor", "--json"]}
```

The trusted argv command receives instructions and evidence on stdin and must return
a JSON object (`facts` or `summary`, as requested) on stdout. It is responsible for
its own provider credentials and isolation. Never accept executable commands from
retrieved content. Failed/malformed extraction leaves the source untouched.

`post_write_index: true` starts a detached indexing worker after accepted writes.
Updates are batched, serialized, and retained on failure; periodic reconciliation
is still useful. Lexical retrieval sees accepted files immediately, even if embeddings
are unavailable. `mem report` separates search/open engagement and explicit relevance,
use, and code-test outcomes. These are observations, not automatic quality judgments.

Filesystem locking uses POSIX flock. Model portability does not imply native Windows
portability; use the shared store from WSL for both harnesses.
