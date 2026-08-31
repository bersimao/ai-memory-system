// Self-check for the cheap wikilink defaults: node daily-log-nudge.test.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { buildLinkHints } = require('./daily-log-nudge.js');

// DST regression (Codex stop-gate, 2026-08-28): "yesterday" used to be
// `Date.now() - 86400e3`, which breaks the day after a spring-forward
// transition because that day is only 23h long. Run in a subprocess pinned
// to a DST-observing zone so the assertion doesn't depend on the host TZ.
const dstCheck = execFileSync(
  process.execPath,
  ['-e', `
    const { previousLocalDate } = require(${JSON.stringify(path.join(__dirname, 'daily-log-nudge.js'))});
    // 00:30 EDT on 2026-03-09, the day after the 23h spring-forward day.
    console.log(previousLocalDate(new Date('2026-03-09T00:30:00-04:00')));
  `],
  { env: { ...process.env, TZ: 'America/New_York' }, encoding: 'utf8' }
).trim();
assert.strictEqual(dstCheck, '2026-03-08', `spring-forward regression: got ${dstCheck}`);

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'daily-log-nudge-test-'));
const memDir = path.join(tmp, 'context', 'memory');
fs.mkdirSync(memDir, { recursive: true });

// no log yesterday, no demand -> no hints
assert.deepStrictEqual(
  buildLinkHints({ projectDir: tmp, yesterday: '2026-08-27', demands: [] }),
  []
);

// yesterday's log exists -> hinted
fs.writeFileSync(path.join(memDir, '2026-08-27.md'), '# 2026-08-27\ncontent\n');
assert.deepStrictEqual(
  buildLinkHints({ projectDir: tmp, yesterday: '2026-08-27', demands: [] }),
  ['link the previous day with [[2026-08-27]]']
);

// empty (zero-byte) log doesn't count as existing
fs.writeFileSync(path.join(memDir, '2026-08-26.md'), '');
assert.deepStrictEqual(
  buildLinkHints({ projectDir: tmp, yesterday: '2026-08-26', demands: [] }),
  []
);

// demand note -> hinted by basename, extension stripped
assert.deepStrictEqual(
  buildLinkHints({
    projectDir: tmp,
    yesterday: '2026-08-25', // no log for this date
    demands: [{ note: '/vault/Ajuste Saldo PN.md' }],
  }),
  ['link the demand note with [[Ajuste Saldo PN]]']
);

// a demand with no note is skipped, not crashed on
assert.deepStrictEqual(
  buildLinkHints({ projectDir: tmp, yesterday: '2026-08-25', demands: [{ note: null }] }),
  []
);

// both hints combine
assert.deepStrictEqual(
  buildLinkHints({
    projectDir: tmp,
    yesterday: '2026-08-27',
    demands: [{ note: '/vault/Ajuste Saldo PN.md' }],
  }),
  ['link the previous day with [[2026-08-27]]', 'link the demand note with [[Ajuste Saldo PN]]']
);

// --- the emitted JSON: `reason` is the one channel proven to reach Claude ---
// 2026-08-31: briefly shortened `systemMessage` on the belief it, not `reason`,
// was shown to the user — backwards. Claude Code displays `reason` to the user
// verbatim as "Stop hook error: <reason>"; that's confirmed by observing a live
// session, not just the docs. So `reason` must carry the full instruction
// (format spec + hints), even though the user sees it too. `systemMessage` is
// dropped rather than mirrored — its delivery to Claude is unverified, and
// duplicating the full text into it only doubles what the user reads.
const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'daily-log-nudge-e2e-'));
fs.mkdirSync(path.join(repo, '.git'));   // project-store anchors on the git root
const transcript = path.join(repo, 't.jsonl');
fs.writeFileSync(
  transcript,
  Array.from({ length: 9 }, () => JSON.stringify({ type: 'user', message: { role: 'user' } })).join('\n')
);

const out = execFileSync(process.execPath, [path.join(__dirname, 'daily-log-nudge.js')], {
  input: JSON.stringify({
    session_id: `test-${process.pid}-${Date.now()}`,   // fresh, or the once-per-session flag eats it
    transcript_path: transcript,
    cwd: repo,
  }),
  encoding: 'utf8',
});
const emitted = JSON.parse(out);

assert.strictEqual(emitted.decision, 'block', 'must block, or the nudge only displays and dies');
assert.match(emitted.reason, /Do not announce that you logged it\.$/,
  'reason must keep the full instruction — it is the channel proven to reach Claude');
assert.match(emitted.reason, /#### Session N/, 'reason must still carry the log format spec');
assert.strictEqual(emitted.systemMessage, undefined,
  'no systemMessage — duplicating the full text there only doubles what the user reads');

fs.rmSync(repo, { recursive: true, force: true });
fs.rmSync(tmp, { recursive: true, force: true });
console.log('ok');
