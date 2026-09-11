# Memory instructions

These are the instructions the agent needs in order to *use* the memory system.
The hooks and cron jobs move files around; this file is what makes an agent
write to them, respect the caps, and search before saying "I don't know".

**Append this to your global instructions file** (`~/.claude/CLAUDE.md` for
Claude Code, `AGENTS.md` for other CLIs). `install.sh` offers to do it for you.

Everything below is written to be pasted verbatim.

---

## Memory System

Two layers. The **global layer** loads in every session, every project. The
**project layer** loads automatically per project — stored centrally, keyed by
the project's **repo root**, not the session's working directory. No per-project
setup; the project context dir is created on first session. Project memory
survives deletion of the project folder.

### Layers

**Global (always loaded):**
- `~/.claude/context/USER.md` — your profile and preferences. Cap 1,375 chars.
- `~/.claude/context/MEMORY.md` — cross-project environment/workflow gotchas
  only. Cap 4,000 chars.

**Project (central store, keyed by the repo root):**
Path: `~/.claude/projects/<encoded-anchor>/context/`

The anchor is the nearest ancestor directory containing `.git`, so a session
started in `repo/services/api` loads the same memory as one started at `repo/` —
one repo, one memory, however many subdirectories. Resolution order
(`hooks/project-store.js`): pinned anchor in `~/.claude/data/store-anchors.json`
→ git root → working directory.

**No git repo above the session** → the snapshot says so; ask which directory
anchors this project, then pin it:
`node ~/.claude/hooks/project-store.js --pin <dir> --for <cwd>`

- `context/MEMORY.md` — **index first**, scratchpad second: one bullet per page
  in `context/topics/` (`- [Title](topics/<slug>.md) — one line`), then active
  threads, pending decisions, project-specific quirks. Cap 2,500 chars, unless
  that store has a `context/.cap` file holding a different integer — read it
  before trimming (`cron/store-cap.sh` is the parser the cron jobs use, so the
  alert and the enforcement cannot disagree). When it fills, move the detail into
  a new `context/topics/<slug>.md` and leave the bullet behind; when a topic
  CLOSES, drop its index bullet and move the page to `context/topics/archive/` —
  it stays indexed for search and stops costing startup tokens.
- `context/topics/<slug>.md` — the indexed pages: one per ticket, feature, or
  gotcha. Not injected at startup; found via the index bullet or search.
- `context/memory/{YYYY-MM-DD}.md` — daily session logs.
- `context/transcripts/{YYYY-MM-DD}.md` — transcript summaries (Stop hook).

The project's source directory is **never** touched, so the repos you work in
stay clean.

### Caps are measured in characters, not bytes

Accented text inflates byte counts by a few percent and produces false alarms.
`cron/check-caps.sh` measures characters.

### Session startup (silent — output nothing)

The SessionStart hook injects the snapshot automatically: `USER.md`, global
`MEMORY.md`, the project's `MEMORY.md`, and today's daily log (falling back to
yesterday's when today is empty).

It is a **frozen snapshot** — loaded once. Writes made mid-session persist to
disk but take effect next session. This is deliberate: it preserves the prefix
cache. Do not load more at startup.

### Memory write

When the user says "remember this", "note that", "update memory", "save this",
or "forget about" — route the fact to the right layer:

| What | Where | Cap |
|---|---|---|
| Cross-project environment/workflow gotcha | Global `MEMORY.md` | 4,000 |
| Project-bound state (active work, this project's quirk, pending decision) | Project `MEMORY.md` | 2,500 — **unless** that store has `context/.cap` with another integer; check it before trimming |
| User profile / preference | `USER.md` | 1,375 |
| One ticket or sub-topic inside a big project | `topics/<slug>.md` + **one index line** in that project's `MEMORY.md` | — |

Ambiguous → ask which layer. **Bias toward global** when uncertain: project
memory does not travel between projects, so a misrouted reusable fact is lost to
every future project.

Steps: decide destination → read the target file in full → check for an existing
substring match (update rather than duplicate) → check the cap → write →
confirm "Saved — will be active from next session."

Removing a fact **always requires confirming with the user first.**

### When a file hits its cap: split, do not compress

Compressing is lossy and silently destroys durable facts. Splitting is lossless:

1. Move a stable section into `context/topics/<slug>.md`.
2. Leave one index line behind in `MEMORY.md`.
3. When a ticket closes, delete the index line and **keep the file** — it stays
   searchable and stops costing startup tokens.

Roughly 200 chars per index line, so about twelve open topics fills `MEMORY.md`
on its own. Hitting the cap usually means step 3 was skipped.

### Memory retrieval — search before denying

This ladder triggers when the user asks about past context. It MUST also trigger
**before asserting that something never happened, never worked, does not exist,
or was never decided.** Those claims sound like knowledge and close the topic, so
a wrong one is expensive. Startup injects the global layer plus *this* project
only, so a fact learned in another project is structurally invisible here —
"I don't recall it" is evidence of nothing.

Escalate only if the previous tier fails:

1. **Tier 0** — the injected snapshot (already in context, zero cost).
2. **Grep** — with a known keyword or name, `grep -ril` then targeted
   `grep -i -A3` over `~/.claude/context/` and `~/.claude/projects/*/context/`,
   excluding `transcripts/`. Cheaper than vector search, and agentic grep beats
   embeddings when the keyword is known.
3. **Search** — `~/.claude/scripts/mem search "query" --top-k 5` for fuzzy or
   semantic recall, or when grep misses.
4. **Expand** — `~/.claude/scripts/mem expand <chunk_hash>` for the full section
   around a match. Always expand before relying on a hit.
5. **Transcripts** — `mem search "query" -c transcripts`, last resort only.
   Transcripts restate the question verbatim, so they out-score curated pages on
   similarity while being the least reliable source.
6. **Fallback** — "I don't have a record of that."

### Daily log

Track session activity in `context/memory/{YYYY-MM-DD}.md`, one file per day,
numbered session blocks:

```
#### Session N
**Goal**: [one line, filled when the user states their goal]
**Deliverables**: [files created/modified]
**Decisions**: [key decisions and the REASONING — the measurement taken, the
alternative rejected, the cause diagnosed. The what is cheap to recover later;
the why is not.]
**Open threads**: [anything unfinished]
```

Log silently as work happens. Never announce that you logged something.
