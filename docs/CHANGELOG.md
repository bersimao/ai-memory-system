# Changelog

Dates are when a change landed on `main`. Commit hashes are from this repo.

## 2026-09-19

**How to update** (from the clone you installed from):

```bash
cd /path/to/ai-memory-system
git pull origin main
./install.sh --root "$HOME/.claude" --codex-home "$HOME/.codex" --scope all --dry-run   # preview
./install.sh --root "$HOME/.claude" --codex-home "$HOME/.codex" --scope all --yes       # apply
rm -i "$HOME/.claude/cron/backup-push.sh"   # only if you did NOT add it on purpose (see below)
```

`--root` is your memory root (`~/.claude` unless you chose another). Drop
`--codex-home` if you don't use Codex. `--scope` is new: `project` (the default) or `all`
(every project, with labels); see the README before choosing. Installs made before this release had none of these
flags, so pick them now rather than copying an old command. The installer backs up every file it
replaces under `<root>/data/memory-system/install-backups/<timestamp>/`, so local
edits to shipped files can be recovered from there. Restart open Claude Code /
Codex sessions afterwards; in Codex, re-approve the hooks in `/hooks` if asked.

**Upgrading from an earlier install** — what changed in behavior:
- `./install.sh` now only **previews** unless you pass `--yes`. Re-run with
  `--yes`, plus `--codex-home` and `--scope` as needed (see README).
- `--cron` is gone and the installer never creates schedules. Existing ones are
  kept; for a new setup, add the crontab lines from the README yourself.
- The installer stops before writing anything if `node`, `git` or `bash` is
  missing.
- `cron/backup-push.sh` no longer ships. An earlier install may have copied it
  into `~/.claude/cron/`, and `distill.sh` still runs it if it's there and
  executable: it commits and pushes `~/.claude` to its `origin`. **Delete it**
  unless you put it there on purpose.

**Added**
- One memory for Claude Code and Codex: a shared journaled writer, lexical
  recall and CLI, and checkpoints of assistant output. (`d1d29c9`)
- The semantic (memsearch) database has a single owner root, recorded in
  `milvus.db.owner-root`. Another root skips semantic work instead of pruning
  the owner's sources. (`77ead87`)

**Fixed**
- Lexical-only installs no longer fail every maintenance run on the missing
  memsearch import. (`77ead87`)
- A custom `--root` is honored by the cron wrappers and the `mem` CLI instead of
  falling back to `~/.claude`; see the README for the remaining limits.
  (`77ead87`)
- `distill.sh` and `memsearch-index.sh` exit non-zero when they fail.
  (`77ead87`)
- Secret redaction covers `DB_PASS`, `Pwd`, `CREDENTIALS`,
  `Authorization: Basic`, quoted keys, and quoted values with spaces or escaped
  quotes, without redacting prose such as "first pass:". (`77ead87`)
- Daily-maintenance extractor, fact distillation, backfill of days that already
  have a log, and semantic-index pruning past the first 16,000 rows.
  (`d95d3fd`, `df68e88`, `e2290b3`, `f628fcc`, `340180b`)

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
