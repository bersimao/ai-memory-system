# Changelog

Dates are when a change landed on `main`. Commit hashes are from this repo.

## 2026-09-10

**Added**
- Per-store cap override (`context/.cap`). One character cap for every project
  store was a poor fit once a store grew many topic pages — the index alone
  grows with their count. A store can now carry its own integer in
  `context/.cap`; missing or malformed falls back to the default, which is the
  stricter value, so raising a cap stays a deliberate edit, not an accident.
  (`0be26d3`)

**Fixed**
- Daily-log nudge was counting every `type:"user"` transcript event as a
  prompt — tool results, slash-command expansions, background-task notices,
  the compaction preamble, and echoed command output all inflated the count.
  Measured 4x median inflation across real sessions; the nudge could fire on a
  user's third actual prompt. Now counts only real human turns.
  (`6546b3d`, `d9cfbf9`)
- `/clear` could leave the truncation warning and the transcript fallback out
  of sync with what was actually injected. (`58282a1`)
- The cap check measured the file's *current* size instead of its size after
  the pending write, letting a store land over cap with nothing noticing until
  the next `check-caps.sh` run. (`58b171f`)
- Config-surface guard now blocks personal config from landing in a shipped
  file, detected by co-occurrence rather than an exact path match. (`2efd7bb`)
- Secret redaction in connection strings and tokens: fixed a pass that was
  corrupting context and missing common real-world cases, tightened the token
  prefix match to exact formats (covering PT-BR variants), then had to revert
  an added placeholder-password allowlist after it opened a real credential
  leak. (`85505ca`, `acff146`, `79e85b7`, `e185179`)

See `git log` for full commit messages — each documents the specific failure
it fixes and how it was verified.
