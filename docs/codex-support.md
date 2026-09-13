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
was checked against Codex CLI 0.154.0 and the documented event schema; avoid claims
that documentation alone proves an older version supports hooks.

If the harness does not execute hooks, the shared CLI and skill still work. Run
`mem snapshot`, retrieve before answering prior-context questions, and explicitly
record checkpoints. This fallback relies on agent behavior; only a functioning
prompt hook makes the initial lookup automatic.

Reference: [official Codex hook documentation](https://learn.chatgpt.com/docs/hooks).
