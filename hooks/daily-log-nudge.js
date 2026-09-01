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

// What counts as a user turn. `type:"user"` is the transcript's envelope for far
// more than the human's prompts: every tool_result comes back as one, slash-command
// expansions and this hook's own feedback arrive as `isMeta`, background-agent
// notices as `promptSource:"system"`, a compaction preamble as `isCompactSummary`,
// an Esc as an `interruptedMessageId` marker, and a local/bash command replays its
// own output as a plain user string. Measured 2026-09-01 on scratch-claude
// d9bcf599: 19 raw events for 9 real prompts -- the nudge fired at "10 user turns"
// on the user's THIRD prompt, which is exactly what MIN_USER_TURNS exists to
// prevent. Median inflation over 333 sessions: 4x. The threshold means nothing
// unless the unit it counts is an actual human turn.
//
// Prefer the structural flag over the text, but do not trust it alone: the shapes
// below either carry no distinguishing field at all (`<bash-stdout>` and
// `<local-command-stderr>` share even the promptId of the `!` command the human
// typed) or only started carrying one recently -- 2.1.251 marks an interrupt with
// `interruptedMessageId`, 2.1.238/240 emit the same sentence as a bare text block.
// A transcript spans versions, so the text match stays as the floor.
const SYNTHETIC_TEXT =
  /^\s*(<(local-command-(stdout|stderr|caveat)|bash-(stdout|stderr))>|\[Request interrupted)/;

const isUserTurn = (ev) => {
  if (ev.type !== 'user' || !ev.message) return false;
  if (ev.isSidechain || ev.isMeta || ev.isCompactSummary) return false;
  if (ev.promptSource === 'system') return false;   // task notifications, and whatever
                                                    // else the harness injects next
  if (ev.interruptedMessageId) return false;        // the Esc marker is not a prompt
  const c = ev.message.content;
  let text;
  if (Array.isArray(c)) {
    // Array content is the human's prompt (text, plus images when pasted) or a tool
    // result coming back; across 9,479 of them here a tool_result block is always
    // alone, but excluding on its presence also covers a future format that rides a
    // text block along.
    if (c.some((b) => b && b.type === 'tool_result')) return false;
    text = c.filter((b) => b && b.type === 'text' && b.text).map((b) => b.text).join('\n');
  } else if (typeof c === 'string') {
    text = c;
  } else {
    return false;
  }
  // `<bash-input>` and `<command-name>` survive on purpose -- the human typed those.
  // What this rejects is the OUTPUT echoed back afterwards as another user event.
  return !SYNTHETIC_TEXT.test(text);
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
      if (isUserTurn(ev)) userTurns++;
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

  // 2026-08-31: tried splitting this into a short `systemMessage` for the user
  // and the full `msg` in `reason`, on the belief that `reason` is agent-only
  // and `systemMessage` is what reaches the terminal. Backwards: Claude Code
  // shows `reason` to the user verbatim as "Stop hook error: <reason>" (that's
  // the whole point — it explains why the turn didn't end). Whether
  // `systemMessage` reaches Claude at all for a Stop block is unverified, and
  // duplicating the full text into it just doubles what the user reads for no
  // proven benefit. `reason` alone is the one channel proven to reach Claude
  // (that's what this hook has been measured against) and is unavoidably
  // shown to the user too — the trailing "do not announce" line being visible
  // is an artifact of how blocking works, not a leak to fix.
  process.stdout.write(JSON.stringify({ decision: 'block', reason: msg }));
  process.exit(0);
}

if (require.main === module) main();

module.exports = { buildLinkHints, previousLocalDate, isUserTurn };
