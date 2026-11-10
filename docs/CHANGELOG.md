# Changelog

Dates are when a change landed on `main`. Commit hashes are from this repo.

## Unreleased (on `hmg`)

**Upgrading** — what changes in behavior:
- Committed journal records move to `state/journal/done/YYYY-MM/`; only records still being
  written stay at the top level, so recovery no longer reads the whole journal on every write.
  The first write after the upgrade moves the existing ones, under the writer lock.
- Daily curation now also does retention: `done/` months older than `journal_retention_days`
  (90) are deleted, and usage events older than `usage_loose_days` (14) are packed into
  `state/usage/archive-YYYY-MM.jsonl`. Packed events still count for salience and `mem report`;
  `mem expand` only resolves requests from the loose window.
- A pin is now an inline `<!-- pin -->` marker on the line, written by `mem pin` and removed by
  `mem unpin`. It travels with the Markdown between machines. A pin made before this (kept in
  `state/salience.json`) still holds on the machine that made it.
- A line edited lightly (similarity 0.8 or more) keeps its salience history; before, any edit
  reset it and dropped its pin.
- Automatic recall no longer injects a page it already injected in the same session, until a
  compaction or a new session start. The recall event logs `repeats`.

**Added**
- `snapshot_global_index` (default `true`): set `false` to keep the global `context/MEMORY.md`
  out of the startup snapshot. It stays searched and recalled.
- A `read` usage event (`{event, source, session, store}`) counts for salience with weight
  `salience_read_weight` (2). `hooks/memory-read.py` logs it: a `PostToolUse` hook on Claude Code's
  `Read` tool, wired by `./install.sh` (re-run it) and shown in `settings.example.json`. Not wired for Codex.
- `memory-maintain.py distill` ends with a report-only check: lines of the global index that look
  owned by a skill with a `knowledge/` or `references/` dir go to `state/skill-flags.json` and
  the alert channel. Nothing is moved. `memory-maintain.py flags` lists them,
  `flags --dismiss "<fragment>"` keeps a line for good. One model call, only when the index changed.
- The recall event logs `hybrid_miss` / `semantic_miss` (`not_running`, `timeout`, `error`) when
  the vector leg did not answer.

- `./install.sh` supports one shared instructions file: when the Claude Code file imports (`@path`
  on its own line) a file that carries the memory block, it gets no block of its own, and one
  written by an earlier install is removed. Before, every session loaded the block twice.

**Fixed**
- A daemon reply that was not a JSON object, or a daemon that failed to start, could raise out of
  the prompt hook.

## 2026-11-09

Update the same way as in the 2026-09-19 entry below (`git pull origin main`, then
`./install.sh` with `--dry-run` first).

**Upgrading from an earlier install** — what changed in behavior:
- The split layout is optional and off by default. Without a `home` in
  `~/.config/ai-memory/config.json` nothing moves: memory stays under one root, as
  before. To move it to `~/.ai-memory`, follow [split layout](split-layout.md)
  (`migration/move-to-ai-memory.py plan`, then `copy`, `switch`, check with
  `scripts/memory-doctor.py` and a new session, then `retire`; `rollback` undoes it).
- SessionStart now records the session's project in `<root>/registry/remotes/`
  (git remote + folder name -> store), in the background, first writer wins. On
  another PC the same repo then resolves to the same store even with another
  username or folder layout. To backfill stores made before this:
  `python3 migration/register-remotes.py --dry-run [--root DIR ...]`, then without
  `--dry-run`.
- When this project's store is empty but a store for a folder with the same name has
  content, the startup snapshot lists it and asks the agent to confirm with you
  before proposing `project-store.js --bind`. It never binds by itself.
- `hooks/memory-inject.js` is gone from the repo. Re-running `./install.sh` drops
  its hook entry from your settings, as before. It does not delete an installed
  copy: `rm ~/.claude/hooks/memory-inject.js` if you have one.
- `settings.example.json` now matches what the installer writes: all four events
  call `hooks/memory-lifecycle.py`. If you wired hooks by hand from the old example
  (`transcript-capture.js`, `capture-maintenance.js` called directly), replace
  them; the installer only rewrites the entries it owns.
- The demands list (from `data/demands.json`, optional `demand_status_command`) and
  the renamed-folder notice moved into the startup snapshot.
- Lexical recall caches page tokens in `state/cache/search-tokens.json` (rebuilt
  when a page changes; safe to delete).

**Added**
- `docs/split-layout.md`, `scripts/memory-paths.py` (the layout for shell scripts:
  `export` lines by default, `--json`), `scripts/memory-layout.test.py`. (`a1b5ebd`)
- `migration/move-to-ai-memory.py` (phased move with rollback) and
  `migration/register-remotes.py` (registry backfill, `--root DIR` repeatable).
  (`a1b5ebd`, `ecdf542`)
- `project-store.js --register [dir]` and `--bind <store> --for <dir>`
  (per-machine binding in `store-bindings.json`: `~/.config/ai-memory/` with the
  split layout, `<root>/data/` without it). (`a1b5ebd`)

**Changed**
- `scripts/memory_core.py` `layout()` is the single authority for paths;
  `hooks/project-store.js` mirrors it. (`a1b5ebd`)
- Prompt recall is faster: the hook went from about 4.5 s to 1.6 s on an 8 MB
  corpus, with identical results on 12 real queries. (`3ec7b01`)
- README banner: the retrieval ladder reads `lexical` and `semantic*` (semantic
  search is optional and not installed by default); test badge back to 20 suites.

**Removed**
- `hooks/memory-inject.js` and `hooks/memory-inject.test.js`. (`c781359`)

**Fixed**
- `cron/jsonl-to-transcript.py` read the store from the human `store:` line, which
  now ends with ` (path)`, and created duplicate `<store> (path)` stores. It reads
  the resolver's JSON now. (`ed4a843`)

## 2026-11-08

Update the same way as in the 2026-09-19 entry below (`git pull origin main`, then
`./install.sh` with `--dry-run` first).

**Upgrading from an earlier install** — what changed in behavior:
- Nothing changes until you use it. The headless save is a new script; no hook or
  schedule calls it. Wire it as a command of your own (see the README).
- `generate()` in `scripts/memory-maintain.py` takes `model`, `timeout` and `env`.
  The defaults (Haiku, 180 s, the current environment) keep the nightly extraction
  as it was.

**Added**
- `scripts/memory-save.py`: the write step of a "save this session" command, run
  by a tool-free `claude -p` (Sonnet by default) instead of your main model. It
  reads only the transcript added since its last run, writes one journaled batch,
  and hands back what it could not write, grouped by kind (`other-repo`,
  `protected`, `correction`, `cap`, `other`), until cleared by id. Optional
  config keys: `save_protected_command`, `save_protected_hint`,
  `save_status_command`. (`016d68b`, `3ba0b9a`)
- `scripts/memory-save.test.py`: 24 cases (allowed paths, caps, possible
  secrets, unlinked pages, the cursor, interrupted batches, dry runs). (`016d68b`)

**Changed**
- README reorganized: release-tag and other badges, the banner back at the top, a
  Features list and a Headless save section. (`6892caa`)

**Fixed**
- A single turn over 600k characters is reported as CLIPPED instead of being cut
  without notice. (`f146f86`)
- `cron/check-caps.test.sh` no longer writes into the real alert log when
  `CLAUDE_ALERT_LOG` is set in your environment; its hub cases read the wrong file
  and failed.

## 2026-11-01

Update the same way as in the 2026-09-19 entry below (`git pull origin main`, then
`./install.sh` with `--dry-run` first).

**Upgrading from an earlier install** — what changed in behavior:
- `mem split` now moves the largest `## ` sections of an index whole to their hub
  page (the `topics/` page linked on the section's first line) and leaves one
  pointer line each. It archives the whole index verbatim only when no section
  can move.
- Salience is opt-in: `salience: true` in `config.json`. Then the daily curate
  keeps each project index's strongest lines within cap minus
  `salience_reserve` (default 300) and moves weak ones to `MEMORY-COLD.md`
  beside it (still searched, not loaded at startup). Other keys:
  `salience_margin` (0.5, needed to bring a cold line back),
  `salience_cross_weight` (2, a recall from another project counts double) and
  `salience_recent_days` (unset = every store; set it to touch only stores with
  a session in that many days). Preview with `mem rebalance PATH --dry-run`.
- `mem prepare` now refuses a `context/...` path when run from inside a project
  store (it named the global file), and `--operation replace` on a missing file
  (use `create`).
- After `/clear`, the startup snapshot leaves out the latest checkpoint; it is
  listed as omitted in `mem snapshot --json`.
- `hooks/memory-inject.js` no longer ships. The installer does not delete it:
  leave it or remove it yourself.

**Added**
- `mem close-section PATH "## X"`: a finished section goes to its hub,
  together with its lines in `MEMORY-COLD.md`. (`59d5ce3`)
- `mem rebalance`, `mem pin` and `mem unpin`. (`59d5ce3`)
- Optional `hooks/recall-observer.py` (not shipped): its `observe(...)` sees
  each prompt recall once decided and cannot change it; an error there only
  logs to stderr. (`cdfe977`)
- `check-caps` alerts on a hub page over 8000 characters; the weekly check
  shows the page a recall opened. (`59d5ce3`)

**Changed**
- Search credits the blocks it showed (vector `excerpt_blocks`) and reads the
  Codex session id. (`59d5ce3`)
- The memory write procedure moved from `docs/memory-instructions.md` into the
  `memory-write` skill. (`e63a0b4`)
- A cap error on a salience project index suggests `mem rebalance` before
  `mem split`. (`e63a0b4`)

**Fixed**
- A CLI search run from another project no longer credits the pages it listed:
  an off-topic page lifted a superseded index line back to startup. Only
  opening the page counts. (`cdfe977`)
- The backfill log names why a summary was rejected, and the limit is 12000.
  (`38fca2f`)

## 2026-10-12

Update the same way as in the 2026-09-19 entry below (`git pull origin main`, then
`./install.sh` with `--dry-run` first).

**Upgrading from an earlier install** — what changed in behavior:
- memsearch (Milvus) is no longer used. Semantic search now lives in
  `data/memory-system/index.sqlite`; the old `milvus.db` is not migrated, so
  build the index by hand once (see the README). If nothing else on the machine
  uses memsearch, `pip uninstall memsearch pymilvus milvus-lite` and delete
  `~/.memsearch/` afterwards.
- `cron/memsearch-index.sh` is now `cron/memory-index.sh`: update your crontab
  line. Its log moved from `~/.memsearch/cron.log` to `<root>/logs/cron.log`.
- `--semantic-updates` (installer) and `mem search --engine` are gone and now
  fail as unknown arguments.
- The installer does not delete files that stopped shipping. Remove them
  yourself: `cron/memsearch-index.sh`, `cron/check-mem-review.sh`,
  `scripts/llm-run`.

**Added**
- Resident embedder process: `mem search --semantic` asks it first (5 s budget)
  and loads the model only if nothing answers. (`e448cf7`)
- `memory-doctor.py` reports `orphan_page`, `symlink_in_store` and symlinks in
  the skills search roots. (`f01c384`, `3fd3277`)
- `mem-bench.py` and `memory-weekly-check.py`; bench cases stay private.
  (`50187f6`)

**Changed**
- [Codex support](codex-support.md) now says what Codex's sandbox blocks: hooks
  work, `mem` commands run by the model do not. (`9c90a3c`)

**Removed**
- memsearch engine and `--semantic-updates`. (`16aee1e`)
- `scripts/llm-run`; Codex is documented as the extractor. (`e8c360a`)
- `cron/check-mem-review.sh`: nothing called it. (`051e9b2`)

**Fixed**
- Store reads refuse symlinks anywhere between the root and the file.
  (`f01c384`)
- The installer read a `MANIFEST` comment as a file name and refused to install.
  (`16aee1e`)
- A sub-agent's final report no longer triggers automatic recall. (`180ebcd`)
- Sessions under Claude's temp directory no longer become memory pages.
  (`37caea3`)
- Injected recall is counted by hits, not bytes. (`a9292a4`)
- `cron/memory-index.sh` exited non-zero on every run of a public install: it
  called two generators that do not ship. (`c74b611`)

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
