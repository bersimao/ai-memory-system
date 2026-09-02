# ai-memory-system — working in this repo

This repo holds the source of a cross-project memory system for Claude Code.
The system is described in `README.md`; this file is about **working on it**.

## The one rule that explains most of the design

An instruction in a prompt is not a guarantee. Every safety property here is
enforced by code that verifies and reverts, never by asking a model nicely:

- `cron/split-memory.py` — the LLM proposes which sections to move; **code**
  moves them, checks no line was lost, and reverts if any was.
- `cron/curate.sh` — a curated file that shrinks ≥30% is flagged, ≥50% is
  rejected and restored from backup, with the rejected proposal kept on disk.
- `sync-release.sh` — refuses to copy anything carrying an absolute home path,
  and scans itself while doing it.

When you add a guarantee, add the check that fails when it breaks. Prose in a
prompt does not count.

## What ships and what doesn't: mechanism vs policy

A live `~/.claude` holds two things. Only one of them is this product.

- **Mechanism (ships)** — code that works on anyone's machine: `hooks/`, `cron/`,
  `scripts/`, `docs/`, `install.sh`. It *operates on* `context/`, `projects/` and
  `data/`: the paths are mechanism, the contents are the user's.
- **Policy (never ships)** — what one machine decides: `CLAUDE.md`, `commands/`,
  `skills/`, `agents/`, `settings.json`.

The boundary is the `MANIFEST` — an allowlist, so anything unlisted is private by
default. Note that shape is not membership: a hook with tests can still be pure
policy (a push-approval gate is a hook, and it is nobody else's business).

**The rule inside a shipped file:** shipped code (`hooks/`, `cron/`, `scripts/`)
may not name the config surface **at all, in any syntax**. The few files that
genuinely operate on it are listed by name in `CONFIG_OK` in `sync-release.sh`,
where a reviewer sees them — currently just `cron/check-hooks.sh`, which reads the
settings file to verify the hooks are installed. `docs/` and `README.md` are out
of scope: telling the reader to edit their own instructions file is what they are
for.

Deny-by-default, and it took three tries to get there because each earlier version
modelled a **form** instead of suspecting the reference:

| attempt | modelled | what walked through |
|---|---|---|
| syntax | "config may appear in code, never in a comment" | trailing comments (anchored to line start), the docstrings in four shipped `.py` files |
| path shape | `(~\|$HOME)/.claude/<config>` | `${HOME}/…`, `"$HOME"/…`, and `path.join(home, '.claude', …)` — which is how all eight shipped `.js` files build paths, so half the release |
| co-occurrence | a line naming the store AND something on the config surface | *(current)* spelling, quoting and separators stop mattering |

Same lesson as the shell-parsing one: an enumerator has to recognise every form,
and there is always one more. Match on what the leak *is about*, not how it is
written, and let the few legitimate files prove themselves by name.

The current rule has two deliberate limits, both pinned by test cases so they
cannot be lost by accident: a **bare word** (`the store holds no commands`) is not
a reference, and a **bare filename with no store path** (`reads settings.json at
startup`) is not either. The cost is that `# per the user's settings.json` would
be missed — accepted, because the alternative fires on ordinary prose and a guard
that cries wolf gets switched off.

Why it needed its own check at all: the path tripwire models a leak as "text that
identifies a machine or a person". On 2026-09-01 a comment citing a section of the
author's personal instructions file shipped in `cron/backup-push.sh` and scored
**zero** matches — no absolute path, no username, nothing to match. Two classes of
leak, two checks. Neither catches a client name in prose, or policy that names
nothing ("push to main is fine here"); that is still a review question when you
add to the MANIFEST.

`CONFIG_OK` in `sync-release.sh` lists the files that legitimately operate on the
config surface — `cron/check-hooks.sh` (reads the settings file) and
`cron/memsearch-index.sh` (indexes the skills' knowledge dirs). Both are covered by
their own test cases: dropping either from the list must fail the suite, or the
list decays into a place where things get quietly added.

## Layout

```
hooks/    the SessionStart/Stop hooks (installed into ~/.claude/hooks/)
cron/     scheduled maintenance: curate, distill, backfill, tripwires
scripts/  mem (retrieval seam), llm-run (LLM calls with quota handling)
docs/     retrieval-interface.md, model-agnostic.md
migration/  one-shot scripts for store layout changes, kept for reference
```

`hooks/`, `cron/` and `scripts/` are **copies** of a live `~/.claude` install.
Do not hand-edit them here — edit the install, then run:

```bash
./sync-release.sh --check      # what differs
./sync-release.sh --from-home  # copy live -> repo, with the leak guard
```

The `MANIFEST` in `sync-release.sh` is the definition of what ships. Anything
not listed does not reach the public repo — which matters, because a live
`~/.claude` also holds per-client project memory that must never be published.

## Tests

Every non-trivial guard has one. Run them before committing:

```bash
./sync-release.test.sh                  # the leak guard (8 cases)
bash cron/curate-audit.test.sh          # curator veto
python3 cron/split-memory.test.py       # split safety
python3 cron/jsonl-to-transcript.test.py
node hooks/project-store.test.js        # store anchoring
bash scripts/skill-grep.test.sh         # grep -> mem -> section listing
bash scripts/mem.test.sh                # weak-result retry warning
bash cron/check-caps.test.sh            # per-store cap override + stderr silence
node hooks/daily-log-nudge.test.js      # nudge: link hints, DST, emitted JSON
```

Prefer mutation-testing a new case: break the guard on purpose and confirm the
test fails. A test that cannot fail is not a test.

### Green is not proof — ask what the assertion cannot see

The `.cap` feature shipped five defects past five green suites in one session
(2026-08-31). In every one the code was fine and the **assertion** was too weak
to observe the failure. The pattern is worth more than the individual bugs:

| The assertion looked at | What it could not see |
|---|---|
| exit code only | a cap of `0` reported as `2600/0`, indistinguishable from the default |
| stdout, stderr discarded | a failed redirection spamming the cron log on every store |
| the string after `$(cat)` | NUL bytes, already spliced away before the regex ran |
| `.sh` files only | versioned prose telling the agent the old number |
| the file as a whole | one *mention* reverted while another still satisfied the check |

So when a guard passes, ask **"what is this assertion structurally unable to
observe?"** — a channel it discards, a transformation upstream of it, a scope it
never enters. Then mutate *that*, not the case you already thought of. Twice
here a mutation "passed" because the test case was vacuous: `.cap=0` flags
everything so the exit code was identical either way, and `25 00` spliced into
exactly the default value it was supposed to prove had not been used.

Assert the *value*, not just pass/fail; capture stderr rather than discarding
it; validate bytes when text handling might transform them; and cover the prose
that instructs the agent, because that is enforcement too.

## Conventions

- Commit messages in pt-BR, semantic emoji prefix (`✨ feat:`, `🐛 fix:`,
  `🔒 security:`, `📚 docs:`). Say *why*, not just what — the reasoning is the
  part that is expensive to recover later.
- Comments explain the failure that motivated the code. Several of these files
  exist because something was silently lost once; keep that context.
- Character caps are measured in **characters, not bytes** — accented text
  inflates byte counts and produces false alarms.
