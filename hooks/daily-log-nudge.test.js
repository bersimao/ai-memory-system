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

fs.rmSync(tmp, { recursive: true, force: true });
console.log('ok');
