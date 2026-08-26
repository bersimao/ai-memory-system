# Codex support — what works, and what is waiting on a release

Last verified: **2026-08-25**, against `codex-cli 0.149.1` (the current stable).

This file exists because the answer depends on a version number, and version
numbers go stale. If you are reading this months later, re-run the checks at the
bottom before believing any of it.

## What works today

| Piece | Status on 0.149.1 |
|---|---|
| Instructions (`~/.codex/AGENTS.md`) | **Works.** `install.sh` writes them there when `~/.codex` exists. |
| Transcript capture | **Works.** `cron/jsonl-to-transcript.py` reads `~/.codex/sessions/**/rollout-*.jsonl`. |
| Store, anchoring, caps, split, curation, retrieval | **Works.** These only touch files; they never knew which CLI you used. |
| Session-start injection | **Waiting on hooks.** |
| Daily-log nudge | **Waiting on hooks.** |

A day worked in both CLIs produces **one** transcript for that day, ordered by
timestamp — not two files competing for the same name.

## How Codex sessions are anchored

Claude Code encodes the project path into the directory name under
`~/.claude/projects/`. Codex does not: it writes to a date-based path and puts
the working directory inside the file, in the first record:

```json
{"type":"session_meta","payload":{"id":"…","cwd":"/path/to/your/repo", …}}
```

So the extractor reads `session_meta.cwd` and hands it to `project-store.js`,
which applies the normal rule — pinned anchor, then git root, then the directory
itself. The anchoring logic is **not** reimplemented in Python; two copies of
that rule would disagree the first time you pinned something.

A Codex session whose `cwd` does not exist locally (for example, a session run
on Windows with a `C:\…` path, read from a WSL install) is skipped rather than
given a junk store.

## The hooks situation

Codex has a documented hook system that closely mirrors Claude Code's —
`SessionStart`, `Stop`, `UserPromptSubmit`, `PreCompact`, payloads carrying
`session_id` / `cwd` / `hook_event_name`, `decision: "block"`, `async: true`, and
an `additionalContext` field for injecting text into the session.

See <https://learn.chatgpt.com/docs/hooks>.

**It is not in 0.149.1.** Three independent checks agree:

1. A minimal `~/.codex/hooks.json` with a `SessionStart` command hook never
   fired.
2. `strings` on the native binary contains none of `SessionStart`,
   `PreToolUse`, `hook_event_name`, or `additionalContext`.
3. `npm view @openai/codex version` returns `0.149.1` — the only newer thing
   published is `0.150.0-alpha.9`.

So the feature is documented ahead of the release that carries it.

### What to do when hooks ship

Two pieces become possible, and neither needs new architecture:

- **Session-start injection.** `node ~/.claude/hooks/memory-inject.js --cwd <dir>`
  already prints the whole snapshot for any harness. A Codex `SessionStart` hook
  would call it and return the output as `additionalContext`. The only new code
  is choosing the output shape: raw stdout for Claude Code, a JSON field for
  Codex.
- **Daily-log nudge.** `hooks/daily-log-nudge.js` already emits
  `decision: "block"` with a reason, which is the same contract Codex documents.

Do not build a shell wrapper around `codex` to work around the missing hook. It
only fires when you launch through the wrapper, and a stable release makes it
obsolete.

## Re-checking this page

```bash
# Is a newer Codex out?
npm view @openai/codex version
codex --version

# Does this build know the hook events?
strings "$(npm root -g)/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/*/bin/codex" \
  | grep -xE 'SessionStart|PreToolUse|hook_event_name|additionalContext'

# Do hooks actually fire? (writes a probe file; costs one Codex run)
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command",
  "command":"sh -c \x27echo fired >> /tmp/codex-hook-probe\x27"}]}]}}' > ~/.codex/hooks.json
rm -f /tmp/codex-hook-probe
codex exec "reply with: ok"
cat /tmp/codex-hook-probe 2>/dev/null || echo "hooks not active"
rm -f ~/.codex/hooks.json
```

The `strings` check is the cheap one and is usually enough. The probe costs an
API call, so run it only if `strings` says the events are present and you want
to confirm the wiring.
