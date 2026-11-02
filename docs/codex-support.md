# Codex support

Both harnesses execute `hooks/memory-lifecycle.py` against the same `AI_MEMORY_HOME`.
The installer registers SessionStart, UserPromptSubmit, Stop, and PreCompact in
Claude settings and optionally Codex `hooks.json`. Every event returns valid JSON;
startup/prompt context uses `hookSpecificOutput.additionalContext`.

Codex requires trust for exact non-managed hook definitions. Restart the CLI, open
`/hooks`, review the memory commands, and trust them if appropriate. Installation
does not bypass or manufacture trust. Actual injection must be checked in a new
host session; adapter unit tests do not prove that a host enabled the hook.

The installed runtime can be inspected with `scripts/memory-doctor.py`. Compatibility
was checked against Codex CLI 0.154.0 and the documented event schema, and injection
was last observed on 0.162.0 (`codex exec` sessions); avoid claims that documentation
alone proves an older version supports hooks.

## What the sandbox blocks

Hooks run outside Codex's sandbox, so the startup snapshot, automatic recall and
checkpoint capture work. Commands the model runs itself do not: with the memory root
outside the sandbox's writable roots, every `mem search` fails with
`[Errno 30] Read-only file system` (it records usage under `data/memory-system/usage/`),
and `mem apply` cannot write. In that setup Codex reads memory through the hooks only,
and what is saved from a session comes from outside the model: the hooks record
checkpoints of assistant output, and the daily maintenance reads the session files
under `~/.codex/sessions`. The model cannot search on its own or write a curated
memory page.

Do not add `data/memory-system/` as a writable root to fix this. `config.json` lives
there, and its `extract_command` is executed by the maintenance cron outside any
sandbox, so a sandboxed session that can write it can run commands unsandboxed.

An execpolicy rule that allows the `mem` script by prefix runs it outside the sandbox
without a writable root. It also lets `mem apply` write without an approval prompt, and
only the refusal of targets outside the curated Markdown was tested, not every
subcommand. Treat it as a deliberate choice, not a default.

Where Codex can run `mem search`, the search counts as use for salience: the CLI reads
the session from `CODEX_THREAD_ID` or `CODEX_SESSION_ID` (Codex 0.163 sets both). Automatic
Codex runs (review gates, delegated tasks) that search count too.

## Without hooks

If the harness does not execute hooks, the shared CLI and skill are the fallback, and
they need the access described above. Run `mem snapshot`, retrieve before answering
prior-context questions, and explicitly record checkpoints. This fallback relies on
agent behavior; only a functioning prompt hook makes the initial lookup automatic.

Reference: [official Codex hook documentation](https://learn.chatgpt.com/docs/hooks).
