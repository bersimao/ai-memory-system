# Model-agnostic memory infrastructure — status & migration guide

Written 2026-07-11. Purpose: if the memory system ever needs to run on a
different model **or** a different agent CLI (Codex, Gemini CLI, opencode, …),
this doc says what is already portable, what was abstracted, and — most
importantly — what was **deliberately not implemented** and how to do it then.

## Current state

| Layer | Status |
|---|---|
| Memory store (markdown under `~/.claude/context/` + `~/.claude/projects/*/context/`) | **Agnostic.** Plain files; any tool reads/writes them. |
| memsearch (index + retrieval) | **Agnostic.** Local ONNX embeddings + Milvus lite (`~/.memsearch/config.toml`: `provider = "onnx"`). Zero API dependency. |
| Deterministic scripts (`sync-skill-list.sh`, `merge-project-stores.py`, `memsearch-index.sh`) | **Agnostic.** Pure bash/python. |
| Cron LLM calls (distill, backfill, curate) | **Abstracted** behind `~/.claude/scripts/llm-run` (2026-07-11). |
| Session-start injection | **Portable core**: `node ~/.claude/hooks/memory-inject.js --cwd <dir>` prints the snapshot for any harness (2026-07-11). |
| Claude Code wiring (hook registration, transcript capture, skills, CLAUDE.md) | **Still Claude-Code-specific** — see "Not implemented" below. |

## What was implemented (2026-07-11)

### `~/.claude/scripts/llm-run` — tiered LLM invocation
The three cron scripts no longer call the `claude` binary directly; they call
`llm-run <tier> "<prompt>"`:

- `cheap` — high-volume, low-risk extraction (`distill.sh`, `backfill-daily-logs.sh`). Currently `claude -p --model haiku`.
- `smart` — judgment work: deletion/merge (`curate.sh`). Currently `claude -p --model opus`.

**To switch backend: edit the two `case` branches in `llm-run` and nothing
else.** Contract any replacement command must honor:
1. Headless one-shot — takes the prompt as an argument, runs to completion, exits.
2. **Agentic file access** — must be able to read AND edit the files named in
   the prompt (paths under `~/.claude/`) without interactive approval. This is
   the hard requirement: a plain chat-completion endpoint is NOT enough; the
   backend must be an agent with file tools (Claude Code `-p`, `codex exec`,
   `gemini -p --yolo`, etc.). `--dangerously-skip-permissions` (or the new
   CLI's equivalent) lives inside `llm-run`, not in the callers.
3. Non-zero exit on failure (callers log-and-continue).

Verify after any change: run `~/.claude/cron/distill.sh` manually, then
`tail ~/.memsearch/cron.log` and check `MEMORY.md` files were edited sanely
(each has a `.bak` sibling from before the run).

### `memory-inject.js --cwd <dir>` — CLI-neutral snapshot
`node ~/.claude/hooks/memory-inject.js --cwd "$PWD"` prints the full startup
snapshot (USER.md + global MEMORY.md + project MEMORY.md + daily log + rename
warning) to stdout, without the Claude Code stdin payload. Stdin mode is
unchanged (verified byte-identical at implementation time).

## NOT implemented — do these only when actually switching

### 1. Session-start adapter for the new CLI
The contract is simple: **at session start, run
`node ~/.claude/hooks/memory-inject.js --cwd "$PWD"` and place its stdout in
the model's context.** What varies is each CLI's mechanism (hook, extension,
plugin, wrapper function). These mechanisms change fast — check the target
CLI's current docs; don't trust this doc's snapshot of them. If the CLI has no
startup-hook concept at all, a shell wrapper that captures the snapshot and
prepends it to the first prompt is the fallback.

### 2. Transcript capture (Stop-hook equivalent)
`transcript-capture.js` parses Claude Code's session `.jsonl` (via
`transcript_path`) and appends the last assistant message to
`<store>/context/transcripts/YYYY-MM-DD.md` under a `## HH:MM:SS` heading.
That `.jsonl` parsing is Claude-Code-specific and does NOT port. Options for a
new CLI, in order of preference:
1. Write a capture adapter against the new CLI's session log format,
   producing the same markdown transcript files (then `backfill-daily-logs.sh`
   keeps working unchanged).
2. Skip capture and instead instruct the agent (via AGENTS.md) to always write
   the daily log itself before ending — loses the safety net for sessions
   that end abruptly.

### 3. Store-path encoding becomes a contract you own
Today the encoded project dir comes from Claude Code itself
(`dirname(transcript_path)` — ground truth). In `--cwd` mode / any other CLI,
the fallback encoder `[^A-Za-z0-9-] → '-'` (in `memory-inject.js`) is the
contract. Two risks to watch:
- If Claude Code ever changes its encoding scheme, the fallback must follow.
- If two CLIs are used side by side on the same project, both must resolve to
  the same store. The rename-detection warning (empty store + ≥85% similar
  name) is the tripwire if they diverge.

### 4. `CLAUDE.md` → `AGENTS.md`
Most non-Claude CLIs standardize on `AGENTS.md`. When switching: make
`AGENTS.md` the real file per repo and symlink `CLAUDE.md` → `AGENTS.md`
(Claude Code follows symlinks). The **global** `~/.claude/CLAUDE.md` (memory
system rules, retrieval tiers, write routing) has no cross-CLI standard —
its content must be ported into the new CLI's global config file by hand.

### 5. Skills (`kb`, `memory-write`, domain skills)
Skills are a Claude Code concept. The memory system depends on two of them:
`memory-write` (destination routing, caps, dedup, note format) and `kb`
(topic → skill routing). For another CLI, port each SKILL.md's instructions
into AGENTS.md or the CLI's equivalent prompt-extension mechanism. The SAP
domain skills (db/sl/di/…) are content — their `knowledge/` and `references/`
folders are plain markdown and stay readable regardless.

### 6. Cheapest path: keep Claude Code, swap the model behind it
If the goal is only a different *model* (not a different CLI), no adapters are
needed at all:
- Interactive: `claude --model <x>` or `/model`.
- Other providers: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` pointing at an
  Anthropic-API-compatible endpoint (e.g. a LiteLLM proxy), or
  `CLAUDE_CODE_USE_BEDROCK=1` / `CLAUDE_CODE_USE_VERTEX=1` for cloud-hosted
  Claude. **Verify these env vars against the current Claude Code docs before
  relying on them** — noted here from the 2026-07 docs, unexercised.
- Cron: edit the model flags in `llm-run`.

### 7. Nothing to do, ever
memsearch (local ONNX), the markdown store itself, `sync-skill-list.sh`,
`merge-project-stores.py`.

## Operational note
The Claude Code harness classifier blocks the agent from writing to
`~/.claude/hooks/` and from writing any file containing
`--dangerously-skip-permissions`. All changes to `llm-run`, the cron scripts,
and the hooks are therefore staged by the agent in its scratchpad and
installed by the user with `! cp` — plan for that loop when asking the agent
to modify them.
