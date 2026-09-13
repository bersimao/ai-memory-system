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
project facts in that project's `context/topics/` with an index pointer.
Resolve the project using `node ~/.claude/hooks/project-store.js --json --resolve "$PWD"`.
Read the existing target and check duplicates. Do not store credentials or secrets.

Put additions in a temporary file, then:

```bash
~/.claude/scripts/mem prepare RELATIVE_PATH --text-file /tmp/addition.md --operation append --reason "why" > /tmp/memory-proposal.json
~/.claude/scripts/mem apply /tmp/memory-proposal.json
```

Inspect proposals before applying. A correction uses `replace` with the complete new
text and requires `apply --reviewed`; archive also requires review. Honor existing
user authorization and confirm removals when not already authorized. Never force a
stale revision or rewrite memory with direct file tools.

Keep index caps by relocating complete originals into topics, not compressing facts.
Distilled candidates and raw checkpoints are unverified source material. Confirm the
saved location; accepted writes are immediately searchable through lexical retrieval.
