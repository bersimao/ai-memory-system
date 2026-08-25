# ai-memory-system

Persistent, cross-project memory for [Claude Code](https://claude.com/claude-code).

Claude Code forgets everything when a session ends. This adds a memory that
survives — one store per repository, loaded automatically at session start,
written during work, curated on a schedule, and searchable when the automatic
load isn't enough.

It is markdown files plus a few hooks. No database, no service, no vendor.

## What it actually does

**Loads context automatically.** A `SessionStart` hook injects ~3,500 tokens:
your profile, cross-project gotchas, this project's memory, and today's log.
The store is keyed to the repo's git root, so a session started in
`repo/services/api` gets the same memory as one started in `repo/`.

**Keeps the injected set small on purpose.** Every layer has a character cap
(1,375 / 4,000 / 2,500). The cap is the whole design: startup context is the
most expensive tokens you spend, because you pay them in every session whether
or not you needed them.

**Splits instead of deleting when a file outgrows its cap.** An LLM proposes a
plan — which sections to move, what to name them; code executes it, verifies no
line was lost, and reverts if any was. The LLM never writes to your memory. See
`cron/split-memory.py`.

**Records what the curator changed.** A weekly pass tightens the writing. If a
file shrinks by ≥30% it is flagged; ≥50% and the change is rejected outright and
restored from backup, with the rejected proposal kept on disk for you to inspect
and accept by hand. An automated editor that can silently delete your memory is
not a memory system.

**Escalates retrieval only as far as needed** — injected context, then grep,
then vector search, then raw transcripts. See `docs/retrieval-interface.md`.

## Install

Requires Node (for the hooks), Python 3 (for the cron scripts), and bash.

```bash
git clone https://github.com/bersimao/ai-memory-system
cd ai-memory-system
./install.sh --dry-run   # see what would change
./install.sh
```

The installer copies the files into `~/.claude/`, registers the hooks in
`settings.json`, appends the agent instructions to your `~/.claude/CLAUDE.md`
(asking first, with a backup), and runs the self-checks. Use `--yes` to accept
the instructions non-interactively; without a terminal it skips them rather than
blocking. It is safe to re-run: it adds only
hooks that are missing, never duplicates them, never drops hooks you already
had, and backs `settings.json` up before touching it. If that file exists but
isn't valid JSON it refuses outright rather than overwriting your config.

Optional local config lands at `~/.claude/data/memory.env`.

To verify by hand at any time:

```bash
node ~/.claude/hooks/project-store.test.js   # store anchoring
node ~/.claude/hooks/memory-inject.test.js   # empty-store / sibling-repo warning
bash ~/.claude/cron/curate-audit.test.sh     # curator veto
python3 ~/.claude/cron/split-memory.test.py  # split safety
```

Start a session in any git repo. The store is created on first use at
`~/.claude/projects/<encoded-repo-root>/context/`.

### The instructions are not optional

The hooks and cron jobs move files around. What makes an agent actually *write*
memory, respect the caps, split instead of compress, and search before saying
"I don't know" is `docs/memory-instructions.md`, appended to your global
instructions file. Skip it and you get transcript capture and daily logs, but the
curated layer stays empty — the system logs, it does not remember.

`skills/memory-write/` handles "remember this" routing and installs alongside.

There is no installer yet — the copy above is the install.

## Layout

```
hooks/
  memory-inject.js        SessionStart: builds and injects the snapshot
  project-store.js        resolves repo -> store (pin > git root > cwd)
  transcript-capture.js   Stop: captures the session transcript
  capture-maintenance.js  SessionStart (async): self-healing transcript capture
  daily-log-nudge.js      Stop: blocks if a long session left no daily log
cron/
  curate.sh               weekly curation, with the audit trail and the veto
  split-memory.py         executes an over-cap split plan; verifies nothing lost
  distill.sh              daily: transcripts -> daily logs
  backfill-daily-logs.sh  redistills days the agent never logged
  jsonl-to-transcript.py  complete-capture path from Claude Code's own .jsonl
  check-*.sh              tripwires: caps respected, hooks registered, recall used
scripts/
  mem                     the retrieval seam (search / expand / report)
  llm-run                 LLM invocation with quota and rate-limit handling
```

`sync-release.sh` compares this repo against a live `~/.claude` install and
copies the release surface across. Its `MANIFEST` is the definition of what
ships, and its leak guard scans the whole release surface — itself included —
refusing to copy anything carrying an absolute home path. `sync-release.test.sh`
covers that guard.

## What is not here

- **Domain knowledge.** The skills that use this memory are separate.
- **A Windows-native path.** Developed and run on WSL2. The hooks are portable
  Node; the cron scripts assume a POSIX shell.
- **The vector tier.** `scripts/mem` shells out to an external index and is a
  documented seam — swap the backend without touching anything else.

## Status

Working daily since 2026-05. Published because the design decisions here —
capped layers, split-not-compress, a curator that cannot silently delete —
were each paid for by losing memory the hard way first.

## License

MIT — see `LICENSE`.
