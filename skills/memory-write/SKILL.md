---
name: memory-write
description: Save, correct, or retrieve durable shared memory across Claude Code and Codex. Use for remembered preferences, prior decisions, project history, explicit save requests, and implicit references to earlier work.
---

# Shared memory

Use `~/.claude/scripts/mem`. Markdown is authoritative; both harnesses use the same
journaled writer. Do not create a parallel harness-specific memory database.

For recall, run `mem search "query" --scope all`, inspect project labels, and expand
relevant `REQUEST_ID:RESULT_ID` results before relying on them. Try domain identifiers,
English/Portuguese alternatives, and `--semantic` if needed. A lexical miss does not
establish absence. Verify that a result from another project applies to this task.

For saves, preserve existing KB/domain-skill routing when a skill owns the fact.
Preferences belong in `context/USER.md`; shared workflow facts in `context/MEMORY.md`;
project facts in that project's `context/topics/` with an index pointer. In an index with
`## <topic>` sections, a new page's pointer goes at the end of that section's hub (the `topics/`
page linked on the section's first line), not into `MEMORY.md`. An always/never rule belongs in
the repo's instruction file, not in memory.
Resolve the project using `node ~/.claude/hooks/project-store.js --json --resolve "$PWD"`.
Read the existing target and check duplicates, with `mem search` too: it covers
`MEMORY-COLD.md`, which the index no longer shows. Do not store credentials or secrets.

Put additions in a temporary file, then:

```bash
~/.claude/scripts/mem prepare RELATIVE_PATH --text-file /tmp/addition.md --operation append --reason "why" > /tmp/memory-proposal.json
~/.claude/scripts/mem apply /tmp/memory-proposal.json
```

Inspect proposals before applying. A correction uses `replace` with the complete new
text and requires `apply --reviewed`; archive also requires review. Honor existing
user authorization and confirm removals when not already authorized. Never force a
stale revision (regenerate it from the current file) or rewrite memory with direct file tools.
Every accepted transaction records originals and replacements before writing. The lock
coordinates these writers; arbitrary external file edits do not participate in the lock.

## Caps, split, salience

Caps are characters: USER 1,375; global MEMORY 4,000; project index 2,500 or its `.cap`.
When an index fills: with salience on, run `mem rebalance RELATIVE_PATH` first on a project index
(weak lines to `MEMORY-COLD.md`); still over, or any other index → `mem split RELATIVE_PATH`: it
moves the largest `## ` sections whole to their hub and leaves one pointer line each; with nothing
movable it archives the index verbatim in `index-*.md`, linked from the new index. Finished topic:
`mem close-section RELATIVE_PATH "## X"` (section to its hub, pointer under the closed heading,
config `closed_heading`, default `## Closed`). Never compress facts to fit. Scheduled curation runs
the same split.

Salience (opt-in, config `salience: true`; optional `salience_recent_days` limits it to stores with
a recent session): curation keeps a project index's strongest lines (fresh, recalled, opened,
recalled from another project counts double, or pinned with `mem pin RELATIVE_PATH "fragment"`)
within cap - 300 and moves weak ones to `MEMORY-COLD.md` beside it: searched and recalled, not
loaded at startup; a cold line used again comes back. `mem rebalance RELATIVE_PATH --dry-run` lists
the moves.

Lossless full-index archives stay beside the index, preserving existing relative link
destinations. Ordinary topic pages stay in `topics/`.

## Checkpoints and diagnostics

Hooks capture incremental assistant output, including short sessions. Capture is a fallback
record, not a substitute for explicitly saving decisions with their rationale. Without hooks:
`mem checkpoint --session ID --cursor UNIQUE_EVENT --text "decision and why"`. Distilled
candidates and raw checkpoints are unverified until checked against their evidence.
Startup snapshots are bounded (a Claude Code session started by `/clear` omits the latest
checkpoint); retrieve omitted sources as needed and inspect the full manifest with
`mem snapshot --json`.

`python3 ~/.claude/scripts/memory-doctor.py` reports installation and corpus health.
`mem report` measures retrieval engagement; opening a result does not prove correctness.

Confirm the saved location. Accepted writes are immediately visible to lexical search; semantic
search sees them after the next index run.
