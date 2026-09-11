// Self-check for the cheap wikilink defaults: node daily-log-nudge.test.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { buildLinkHints, isUserTurn } = require('./daily-log-nudge.js');

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

// --- what counts as a user turn ---
// 2026-09-01: the nudge fired on the user's THIRD prompt claiming "10 user
// turns". `type:"user"` in the transcript covers far more than human prompts.
// Every case below is a shape COUNTED by census over 21 days of real transcripts
// (846 genuine prompts vs 52 `<command-name>`, 17 `<task-notification>`, 9
// interrupt markers, 4 `<bash-stdout>`, 2 `<local-command-stderr>`, 1 compaction
// preamble) -- not shapes imagined at the desk. Assert the SHAPE, not just that
// some number came out.
//
// The first pass of this fix filtered only `<local-command-stdout|caveat>` and
// shipped green: the suite had no case for the other five, so the assertion could
// not see them. That is the repo's recurring failure -- the code was wrong in a
// way the test was structurally unable to observe.
const turn = (extra) => ({ type: 'user', message: { role: 'user', content: 'oi' }, ...extra });
assert.strictEqual(isUserTurn(turn()), true, 'a plain typed prompt is a turn');
assert.strictEqual(isUserTurn(turn({ isMeta: true })), false, 'command expansion / hook feedback is not');
assert.strictEqual(isUserTurn(turn({ isSidechain: true })), false, 'subagent traffic is not');
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: [{ type: 'tool_result', content: 'x' }] } }),
  false, 'a tool result is not a user turn');
assert.strictEqual(   // the shape the text/image allowlist alone would not reject
  isUserTurn({ type: 'user', message: { content: [{ type: 'tool_result', content: 'x' }, { type: 'text', text: '<system-reminder>' }] } }),
  false, 'a tool result stays excluded even if a text block rides along');
// --- synthetic events that carry a structural flag: match the flag, not the text
assert.strictEqual(isUserTurn(turn({ isCompactSummary: true })), false,
  'the compaction preamble is written by the harness, not typed');
assert.strictEqual(isUserTurn(turn({ promptSource: 'system' })), false,
  'a background-task notification is injected, not typed');
assert.strictEqual(isUserTurn(turn({ interruptedMessageId: 'msg_01' })), false,
  'the Esc interrupt marker is not a prompt');
// ...and the SAME event from an older client (2.1.238/240) carries no such field:
// just the sentence, in an array text block. Measured: 5 of 9 interrupts had the
// flag, 4 did not. A transcript spans versions, so both paths must reject.
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: [{ type: 'text', text: '[Request interrupted by user]' }] } }),
  false, 'a flagless interrupt record from an older client is still not a prompt');
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: '[Request interrupted by user for tool use]' } }),
  false, 'the tool-use interrupt variant too');
// An image pasted with no caption yields no text at all -- still a turn.
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: [{ type: 'image' }] } }),
  true, 'a bare pasted image is a turn');
assert.strictEqual(isUserTurn(turn({ promptSource: 'typed' })), true, 'typed is human');
assert.strictEqual(isUserTurn(turn({ promptSource: 'queued' })), true,
  'queued is the human typing while Claude works -- still human');
assert.strictEqual(isUserTurn(turn({ promptSource: 'sdk' })), true,
  'headless prompts drive the session; measured, none reach the threshold anyway');

// --- output echoed back as a user event: no structural flag exists, so match the tag
for (const tag of ['local-command-stdout', 'local-command-stderr', 'local-command-caveat',
                   'bash-stdout', 'bash-stderr']) {
  assert.strictEqual(
    isUserTurn({ type: 'user', message: { content: `<${tag}>output</${tag}>` } }),
    false, `<${tag}> is output, not a user turn`);
}
// ...but what the human TYPED to produce it still counts
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: '<bash-input>ls -la</bash-input>' } }),
  true, 'the `!` command the human typed is a turn; only its echo is not');
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: '<command-name>/commit</command-name>' } }),
  true, 'a typed slash command is a turn');
assert.strictEqual(
  isUserTurn({ type: 'user', message: { content: [{ type: 'image' }, { type: 'text', text: 'look' }] } }),
  true, 'a prompt with a pasted image still counts');
assert.strictEqual(isUserTurn({ type: 'assistant', message: { content: 'x' } }), false);

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
// 8 real prompts (the threshold) buried in the noise that used to be counted:
// if any of the noise leaks back in, the reported number stops being 8.
fs.writeFileSync(
  transcript,
  [
    ...Array.from({ length: 8 }, () => JSON.stringify(turn())),
    JSON.stringify(turn({ isMeta: true })),
    JSON.stringify(turn({ isCompactSummary: true })),
    JSON.stringify(turn({ promptSource: 'system' })),
    JSON.stringify(turn({ interruptedMessageId: 'msg_01' })),
    JSON.stringify({ type: 'user', message: { content: [{ type: 'text', text: '[Request interrupted by user]' }] } }),
    JSON.stringify(turn({ isSidechain: true })),
    JSON.stringify({ type: 'user', message: { content: [{ type: 'tool_result', content: 'x' }] } }),
    JSON.stringify({ type: 'user', message: { content: '<local-command-stdout>Copied</local-command-stdout>' } }),
    JSON.stringify({ type: 'user', message: { content: '<local-command-stderr>boom</local-command-stderr>' } }),
    JSON.stringify({ type: 'user', message: { content: '<bash-stdout>total 0</bash-stdout>' } }),
  ].join('\n')
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
assert.match(emitted.reason, /has 8 user turns/,
  'the count must be human turns only -- tool results and meta events inflated it 3x');
assert.match(emitted.reason, /Do not announce that you logged it\.$/,
  'reason must keep the full instruction — it is the channel proven to reach Claude');
assert.match(emitted.reason, /#### Session N/, 'reason must still carry the log format spec');
assert.strictEqual(emitted.systemMessage, undefined,
  'no systemMessage — duplicating the full text there only doubles what the user reads');

// A threshold is only observable at its boundary: with 8 turns alone, MIN_USER_TURNS
// could drift to 6 or 1 and every assertion above still passes. One turn short must
// produce NO nudge -- that is the half that pins the number down.
const quiet = fs.mkdtempSync(path.join(os.tmpdir(), 'daily-log-nudge-quiet-'));
fs.mkdirSync(path.join(quiet, '.git'));
const quietTranscript = path.join(quiet, 't.jsonl');
fs.writeFileSync(
  quietTranscript,
  Array.from({ length: 7 }, () => JSON.stringify(turn())).join('\n')
);
const quietOut = execFileSync(process.execPath, [path.join(__dirname, 'daily-log-nudge.js')], {
  input: JSON.stringify({
    session_id: `test-quiet-${process.pid}-${Date.now()}`,
    transcript_path: quietTranscript,
    cwd: quiet,
  }),
  encoding: 'utf8',
});
assert.strictEqual(quietOut, '',
  'one turn below MIN_USER_TURNS must stay silent -- otherwise the threshold is decorative');
fs.rmSync(quiet, { recursive: true, force: true });

fs.rmSync(repo, { recursive: true, force: true });
fs.rmSync(tmp, { recursive: true, force: true });
console.log('ok');
