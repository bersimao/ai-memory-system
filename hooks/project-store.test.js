// Self-check for the store anchor: node project-store.test.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { resolveAnchor, storeDir, encodeProjectPath, PROJECTS } = require('./project-store.js');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'store-test-'));
const repo = path.join(tmp, 'repo');
const deep = path.join(repo, 'a', 'b');
fs.mkdirSync(path.join(repo, '.git'), { recursive: true });
fs.mkdirSync(deep, { recursive: true });

// a subdirectory of a repo anchors to the repo root
assert.deepStrictEqual(resolveAnchor(deep), { dir: repo, source: 'git' });
assert.strictEqual(storeDir(deep).dir, path.join(PROJECTS, encodeProjectPath(repo)));

// both hooks land in the same store even though Claude Code's own dir is per-cwd
const ccDir = path.join(PROJECTS, encodeProjectPath(deep));
assert.strictEqual(storeDir(deep, path.join(ccDir, 's.jsonl')).dir, storeDir(deep).dir);

// no repo above it: cwd, and the snapshot asks the user to pin
const loose = path.join(tmp, 'loose');
fs.mkdirSync(loose);
assert.deepStrictEqual(resolveAnchor(loose), { dir: loose, source: 'cwd' });
// ...and then the harness dir is trusted verbatim, since anchor === cwd
const looseCc = path.join(PROJECTS, 'whatever-claude-code-picked');
assert.strictEqual(storeDir(loose, path.join(looseCc, 's.jsonl')).dir, looseCc);

fs.rmSync(tmp, { recursive: true, force: true });
console.log('ok');
