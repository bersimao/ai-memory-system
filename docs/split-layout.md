# Split layout (optional): memory outside `~/.claude`

By default everything lives under one root (`~/.claude`, or your `--root`). The split
layout moves the memory content out of the harness directory:

| what | where |
|---|---|
| content: `context/`, `projects/<store>/context/`, `registry/` | `~/.ai-memory/` (can be its own private git repo) |
| local state: journal, usage, index, locks | `~/.ai-memory/state/` (never versioned) |
| config: `config.json`, `installation.json`, `memory.env`, `store-anchors.json`, bindings | `~/.config/ai-memory/` |
| code, skills, Claude JSONL | stay in `~/.claude` |

`scripts/memory_core.py` `layout()` is the single authority (`hooks/project-store.js`
mirrors it; shell scripts read it through `scripts/memory-paths.py --sh`). The split
layout is active only when `~/.config/ai-memory/config.json` has a `home`; without it the
legacy one-root layout is used, unchanged. Logical paths (`context/..`, `projects/..`)
are the same in both, so journal, usage and index entries stay valid across the move.

## Why

- One memory for several harnesses (Claude Code, Codex) without living inside either
  one's config directory.
- Config outside every agent-writable directory.
- The content can sync between machines as a plain git repo.

## Moving an existing install

`migration/move-to-ai-memory.py` runs in phases; each one can be re-run:

```bash
python3 migration/move-to-ai-memory.py plan      # what would move; writes nothing
python3 migration/move-to-ai-memory.py copy      # copy under the writer lock, hash-verified
python3 migration/move-to-ai-memory.py switch    # write the config (the cut-over), then a delta pass
python3 migration/move-to-ai-memory.py delta     # carry files written to the old place since copy; never overwrites
python3 migration/move-to-ai-memory.py retire    # move the old copies to ~/.local/share/ai-memory-rollback/
python3 migration/move-to-ai-memory.py rollback  # undo: old copies back, config removed
```

Run `scripts/memory-doctor.py` and a new session after `switch`, before `retire`.

**Codex:** its sandbox must be allowed to write the new root. Add it to
`writable_roots` under `[sandbox_workspace_write]` in `~/.codex/config.toml`. A folder
Codex does not trust runs read-only, memory included.

## Same project on another machine

A store name is the encoded path of the project, so another username or folder layout
would create an empty store. The resolver checks, in order:

1. a per-machine binding (`node ~/.claude/hooks/project-store.js --bind <store> --for <dir>`,
   kept in `~/.config/ai-memory/store-bindings.json`);
2. the store named after the local path;
3. `registry/remotes/` — git remote + folder name -> store. The remote alone is not an
   identity: one remote can hold several projects.

The SessionStart hook records the session's project in the registry (in the background,
first writer wins; a store reached through the registry is never re-recorded).
`migration/register-remotes.py` backfills stores that existed before the registry
(`--dry-run` reports only; `--root DIR`, repeatable, also scans the git repos under DIR);
`node ~/.claude/hooks/project-store.js --register [dir]` does one
directory by hand.

When this project's store is empty but a store for a folder with the same name has content
(no git remote, or one remote shared by several stores), the startup snapshot lists the
candidates and asks the agent to confirm with you before proposing `--bind`. It never binds
on its own.

Syncing `~/.ai-memory` between machines is up to you (a private git remote). Nothing
in the release pushes.
