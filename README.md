# Shared local memory for coding agents

Claude Code and Codex use one Markdown store, one revision-checked writer, and one
retrieval CLI. No MCP server, new database, or continuously running service is
required. Linux/WSL is supported; native Windows requires a separate filesystem/
locking adapter.

## Install

Requires Python 3.10+, Node.js, and Bash. Existing Markdown remains in place.

```bash
./install.sh --root "$HOME/.claude" --codex-home "$HOME/.codex" --scope all --dry-run
./install.sh --root "$HOME/.claude" --codex-home "$HOME/.codex" --scope all --yes
```

`--scope all` recalls all private projects plus shared knowledge, with project labels.
The default for new installations is `project`; choose deliberately. The installer
backs up changed files, merges owned hooks, preserves unrelated configuration,
updates managed instructions, and records installed file hashes. Restart sessions.
Codex may require reviewing the exact new hook definitions in `/hooks`.

Optional: add `--semantic-updates` when memsearch is already configured. Accepted
writes then trigger a detached, locked indexing worker; failed updates remain queued.
The default lexical engine reads current files and needs neither embeddings nor a model.

Existing schedules are preserved. A fresh installation can schedule `cron/distill.sh`
for extraction and `cron/curate.sh` for lossless index splitting, and keep periodic
`cron/memsearch-index.sh` reconciliation if using memsearch. The distill wrapper retains
an existing executable private backup job; installation never invokes or creates a push.
Background extraction defaults to tool-free Claude CLI; see [runtime design](docs/model-agnostic.md).

## What happens during a session

- Startup compiles a byte-bounded snapshot (8,000 bytes by default), retaining a manifest of omitted sources.
- Each ordinary prompt performs bounded lexical recall, including implicit references to earlier work.
- Stop and pre-compaction capture incremental assistant output, including short sessions, into unverified checkpoints.
- Models propose source-linked candidate facts. Deterministic code validates evidence revisions and writes through a journal.

Search results are evidence, not instructions. Cross-project matches need applicability
checks; an assistant's old assertion is not automatically a verified fact.
A lexical miss is not proof of absence: use identifiers, synonyms, the other language,
or optional semantic search before denying prior work.

## Write and retrieve

```bash
~/.claude/scripts/mem search "previous decision" --scope all
~/.claude/scripts/mem expand REQUEST_ID:RESULT_ID
~/.claude/scripts/mem prepare projects/STORE/context/topics/topic.md --text-file /tmp/addition.md --operation append --reason "decision rationale" > /tmp/proposal.json
~/.claude/scripts/mem apply /tmp/proposal.json
~/.claude/scripts/mem split projects/STORE/context/MEMORY.md
~/.claude/scripts/mem feedback REQUEST_ID:RESULT_ID relevant
~/.claude/scripts/mem report
python3 ~/.claude/scripts/memory-doctor.py
```

Corrections and archives require an exact reviewed proposal (`apply --reviewed`).
Every transaction retains original text and intended replacements before modifying
files. Interrupted batches can be completed with `mem recover`; conflicting external
edits stop recovery for inspection. This is a cooperative writer lock, not an access
control boundary against arbitrary shell edits. Archives retain revisions, so they
are not a privacy-erasure operation.

Caps: USER 1,375 characters; global MEMORY 4,000; project MEMORY 2,500 or its `.cap`.
Oversized indexes are split losslessly. Complete originals remain searchable as topics.
Search/open events have request and result IDs. Explicit relevance, use, and code-test
feedback are separate self-reports; engagement is not a correctness metric.

## Private data and public releases

Only the `sync-release.sh` allowlist ships. Project memories, checkpoints, journals,
installation backups, embeddings, local settings, and credentials stay private.
Edit the private installation (or an isolated staging installation), then sync:

```bash
CLAUDE_HOME=/path/to/staged-install ./sync-release.sh --from-home
./sync-release.test.sh
```

Never copy the full private directory into this repository. Installation backups
contain private configuration and must remain private. Existing domain-skill routing
is preserved; the generic memory skill does not redistribute personal skills.

Legacy Codex SQLite records can be previewed/imported with
`scripts/memory-import-codex.py DATABASE [--apply]`. The importer preserves original
project labels in explicit legacy namespaces rather than guessing repo anchors;
it never modifies the original database. Repeated unchanged imports are idempotent.

## Verification

```bash
python3 scripts/memory_core.test.py
python3 install-memory.test.py
bash sync-release.test.sh
bash scripts/skill-grep.test.sh
python3 cron/jsonl-to-transcript.test.py
node hooks/project-store.test.js
bash cron/check-caps.test.sh
```

Core tests exercise revision conflicts, crash recovery, lossless splitting, scope
boundaries, stale semantic results, byte budgets, short-session capture, and provider
isolation. See [Codex support](docs/codex-support.md), [retrieval interface](docs/retrieval-interface.md),
and [shared instructions](docs/memory-instructions.md).

Lossless full-index archives stay beside the index, preserving existing relative link destinations. Ordinary topic pages stay in `topics/`.
