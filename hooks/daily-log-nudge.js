// Stop hook: reminds the agent to write the daily log, once per session.
//
// Why it exists: "Log silently as work happens" lives in CLAUDE.md and is a
// PROMPT instruction — it binds nothing. Measured on 08-24: the agent wrote
// 47% of the daily logs; the backfill (haiku, reading the whole transcript)
// covered 52%. Global memory already records the principle: "SessionStart
// hook > a 'read this at startup' instruction — hook stdout always enters the
// context". This is the same thing, at the other end of the session.
//
// SessionEnd does NOT work: the agent can no longer act, and the event fires
// neither on a crash nor on `wsl --shutdown`. Hence Stop, which runs with the
// session still alive.
//
// It only nudges. It does not write the log: summarizing takes judgment, and
// deterministic code could only dump the transcript, which already exists.
//
// DELIVERY: `systemMessage` alone only DISPLAYS text — the turn is already
// over, so the agent sees it and cannot act. What blocks the stop and hands
// control back to the agent is `decision:"block"` + `reason`. Without that the
// hook looks installed and does nothing — exactly the failure this system
// exists to prevent.
//
// LOOP: a blocked Stop makes the agent continue and stop again, firing the
// hook once more. The flag is written ATOMICALLY (wx) BEFORE any output — if
// the write fails, the hook gives up rather than risk an infinite block.
// `stop_hook_active` in the input also marks reentry and is honored.
const fs = require('fs');
const path = require('path');
const os = require('os');

const MIN_USER_TURNS = 8;   // below this the session didn't yield a log

const localDate = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 6e4)
  .toISOString().slice(0, 10);

// Not `Date.now() - 86400e3`: a day isn't always 24h across a DST transition
// (23h spring-forward / 25h fall-back), so a fixed ms subtraction can land two
// calendar days back instead of one. setDate/getDate operate on local wall-clock
// fields, which is what "yesterday" actually means, so they're DST-safe.
const previousLocalDate = (d) => {
  const y = new Date(d);
  y.setDate(y.getDate() - 1);
  return localDate(y);
};

// Cheap wikilink defaults — measured 2026-08-28: embeddings already surface
// most cross-references on their own (8/9 real [[links]] in this vault were
// found by semantic search alone from a query that never named the target).
// The exception is what semantic search structurally cannot reach: yesterday's
// log has no shared vocabulary to embed a similarity against, and a demand's
// vault note lives outside the memsearch index entirely. Both are one lookup,
// no new detection machinery. Deep/opportunistic cross-linking stays manual.
const buildLinkHints = ({ projectDir, yesterday, demands }) => {
  const hints = [];
  try {
    if (fs.statSync(path.join(projectDir, 'context', 'memory', `${yesterday}.md`)).size > 0) {
      hints.push(`link the previous day with [[${yesterday}]]`);
    }
  } catch { /* no log yesterday, nothing to link */ }
  for (const d of demands) {
    if (d.note) hints.push(`link the demand note with [[${path.basename(d.note, '.md')}]]`);
  }
  return hints;
};

function main() {
  let input;
  try { input = JSON.parse(fs.readFileSync(0, 'utf8')); } catch { process.exit(0); }

  const transcriptPath = input.transcript_path;
  const sessionId = input.session_id || 'nosession';
  if (!transcriptPath || !fs.existsSync(transcriptPath)) process.exit(0);

  // Already inside a hook-blocked Stop? Then do not block again.
  if (input.stop_hook_active) process.exit(0);

  // One nudge per session. Without this, Stop fires every turn and becomes noise.
  const flag = path.join(os.tmpdir(), `claude-daily-log-nudge-${sessionId}`);

  // Same anchor as memory-inject/transcript-capture: the repo, not the cwd.
  let projectDir, anchorDir;
  const { storeDir, demandsFor } = require('./project-store.js');
  try {
    const store = storeDir(input.cwd || process.cwd(), transcriptPath);
    projectDir = store.dir;
    anchorDir = store.anchor.dir;
  } catch { process.exit(0); }

  const today = localDate(new Date());
  const dlog = path.join(projectDir, 'context', 'memory', `${today}.md`);
  const yesterday = previousLocalDate(new Date());
  let demands = [];
  try { demands = demandsFor(anchorDir); } catch { /* registry unavailable, skip */ }
  const linkHints = buildLinkHints({ projectDir, yesterday, demands });

  // Today's log already exists? Then there is nothing to nudge. (If it is
  // partial, the backfill now extends it — see cron/backfill-daily-logs.sh.)
  try { if (fs.statSync(dlog).size > 0) process.exit(0); } catch { /* doesn't exist */ }

  // Did the session produce enough to be worth a log?
  let userTurns = 0;
  try {
    const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n');
    for (const l of lines) {
      if (!l) continue;
      let ev; try { ev = JSON.parse(l); } catch { continue; }
      if (ev.type === 'user' && ev.message && !ev.isSidechain) userTurns++;
    }
  } catch { process.exit(0); }
  if (userTurns < MIN_USER_TURNS) process.exit(0);

  // Atomic reservation: whoever creates the file nudges; any reentry exits
  // silently. If the reservation fails, do NOT block — a block without a lock
  // becomes a loop.
  try {
    fs.closeSync(fs.openSync(flag, 'wx'));
  } catch {
    process.exit(0);
  }

  const hintText = linkHints.length ? ` While there, ${linkHints.join(' and ')}.` : '';
  const msg = `[daily-log] This session has ${userTurns} user turns and no daily log ` +
    `for ${today}. Before ending, write ${dlog} using "#### Session N" with ` +
    `Goal/Deliverables/Decisions/Open threads. Record the REASONING behind decisions ` +
    `(the measurement taken, the alternative rejected, the cause diagnosed) — the what ` +
    `is cheap to recover later, the why is not.${hintText} Do not announce that you logged it.`;

  process.stdout.write(JSON.stringify({ decision: 'block', reason: msg, systemMessage: msg }));
  process.exit(0);
}

if (require.main === module) main();

module.exports = { buildLinkHints, previousLocalDate };
