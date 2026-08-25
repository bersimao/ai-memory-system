---
name: memory-write
description: Route a durable fact to the correct memory layer, enforce the character cap, and confirm. Use when the user says "remember this", "note that", "update memory", "save this", or "forget about".
---

# Memory Write

## Outcome
- Fact added to, updated in, or removed from the correct memory layer
- Per-layer character cap respected
- Confirmation: "Saved — will be active from next session."

## Destinations

| What | Where | Cap |
|---|---|---|
| Cross-project environment/workflow gotcha (shell, git, CI, containers…) | Global `~/.claude/context/MEMORY.md` | 4,000 |
| Project-bound state (active work, this project's quirk, pending decision) | Project `~/.claude/projects/<encoded-anchor>/context/MEMORY.md` | 2,500 |
| User profile / preference | `~/.claude/context/USER.md` | 1,375 |
| One ticket or sub-topic inside a big project | Own file `…/context/topics/<slug>.md` + **one index line** in that project's `MEMORY.md` | — |
| Reusable domain knowledge tied to a tool or stack | The matching skill's own `knowledge/` or `references/`, if you keep skills that way | — |

Ambiguous → ask the user which layer. **Bias toward global** when uncertain:
project memory does not travel between projects, so a misrouted reusable fact is
lost to every future project.

## Steps

1. Decide the destination.
2. Read the target file **in full**.
3. **Dedup**: scan for a substring match. If the fact is already there, update it
   rather than appending a near-duplicate.
4. **Cap check**: count *characters*, not bytes — accents inflate byte counts.
   Over cap → split (below), do not compress.
5. Write.
6. Confirm: "Saved — will be active from next session."

Actions: **add** (append under the right section), **replace** (substring match,
then swap), **remove** (*always confirm with the user first*).

## Topic files — one repo, many tickets

A long-lived repo has one store but many unrelated problem contexts. They do not
all belong in a 2,500-char `MEMORY.md`: each ticket would starve the others, and
every session would pay for tickets that closed months ago.

Split instead — `MEMORY.md` becomes an **index**, details live beside it:

1. **One file per ticket**: `…/context/topics/<slug>.md`, with the ticket id in
   the frontmatter `tags`. Topic pages live in `topics/`, never loose in
   `context/` — that directory holds `MEMORY.md` and the three subdirectories
   (`topics/`, `memory/`, `transcripts/`), nothing else.
2. **While the ticket is open**: one line in `MEMORY.md` —
   `- [Title](topics/slug.md) — one-line summary`. That line is the menu the next
   session sees; without it the file is reachable only by search.
3. **When it closes**: delete the index line, **keep the file**. It stays
   searchable forever and stops costing startup tokens.

Cap math: ~200 chars per index line, so ~12 open tickets fills `MEMORY.md` by
itself. Hitting the cap usually means step 3 was skipped — prune closed tickets
before consolidating anything else.

## Why splitting, never compressing

Compression is lossy and its losses are silent: a durable fact disappears and
nothing reports it. Splitting moves text without losing a line, which is why
`cron/split-memory.py` verifies that every line survived and reverts when one
did not. Apply the same rule by hand: deleting a durable fact to hit a number is
a bug, not curation.

## Note format

The memory store doubles as a plain-markdown knowledge base:

- Link related facts and files with `[[wikilinks]]` in bullets (e.g.
  `[[2026-08-25]]`, `[[useful-queries]]`) — Obsidian-compatible, harmless
  without it.
- New standalone files start with YAML frontmatter: `name`, `date`, `project`,
  `tags`.
- Backfill that format into old entries opportunistically when you touch them —
  never in bulk.
