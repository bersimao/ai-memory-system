# ai-memory-system — working in this repo

This repo holds the source of a cross-project memory system for Claude Code.
The system is described in `README.md`; this file is about **working on it**.

## The one rule that explains most of the design

An instruction in a prompt is not a guarantee. Every safety property here is
enforced by code that verifies and reverts, never by asking a model nicely:

- `cron/split-memory.py` — the LLM proposes which sections to move; **code**
  moves them, checks no line was lost, and reverts if any was.
- `cron/curate.sh` — a curated file that shrinks ≥30% is flagged, ≥50% is
  rejected and restored from backup, with the rejected proposal kept on disk.
- `sync-release.sh` — refuses to copy anything carrying an absolute home path,
  and scans itself while doing it.

When you add a guarantee, add the check that fails when it breaks. Prose in a
prompt does not count.

## Layout

```
hooks/    the SessionStart/Stop hooks (installed into ~/.claude/hooks/)
cron/     scheduled maintenance: curate, distill, backfill, tripwires
scripts/  mem (retrieval seam), llm-run (LLM calls with quota handling)
docs/     retrieval-interface.md, model-agnostic.md
migration/  one-shot scripts for store layout changes, kept for reference
```

`hooks/`, `cron/` and `scripts/` are **copies** of a live `~/.claude` install.
Do not hand-edit them here — edit the install, then run:

```bash
./sync-release.sh --check      # what differs
./sync-release.sh --from-home  # copy live -> repo, with the leak guard
```

The `MANIFEST` in `sync-release.sh` is the definition of what ships. Anything
not listed does not reach the public repo — which matters, because a live
`~/.claude` also holds per-client project memory that must never be published.

## Tests

Every non-trivial guard has one. Run them before committing:

```bash
./sync-release.test.sh                  # the leak guard (8 cases)
bash cron/curate-audit.test.sh          # curator veto
python3 cron/split-memory.test.py       # split safety
python3 cron/jsonl-to-transcript.test.py
node hooks/project-store.test.js        # store anchoring
```

Prefer mutation-testing a new case: break the guard on purpose and confirm the
test fails. A test that cannot fail is not a test.

## Conventions

- Commit messages in pt-BR, semantic emoji prefix (`✨ feat:`, `🐛 fix:`,
  `🔒 security:`, `📚 docs:`). Say *why*, not just what — the reasoning is the
  part that is expensive to recover later.
- Comments explain the failure that motivated the code. Several of these files
  exist because something was silently lost once; keep that context.
- Character caps are measured in **characters, not bytes** — accented text
  inflates byte counts and produces false alarms.
