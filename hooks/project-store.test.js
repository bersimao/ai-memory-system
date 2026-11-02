// Self-check for the store anchor: node project-store.test.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { resolveAnchor, storeDir, encodeProjectPath, demandServes, PROJECTS, LAYOUT } = require('./project-store.js');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'store-test-'));
const repo = path.join(tmp, 'repo');
const deep = path.join(repo, 'a', 'b');
fs.mkdirSync(path.join(repo, '.git'), { recursive: true });
fs.mkdirSync(deep, { recursive: true });

// a subdirectory of a repo anchors to the repo root
assert.deepStrictEqual(resolveAnchor(deep), { dir: repo, source: 'git' });
assert.strictEqual(storeDir(deep).dir, path.join(PROJECTS, encodeProjectPath(repo)));

// both hooks land in the same store even though Claude Code's own dir is per-cwd
const ccDir = path.join(LAYOUT.claudeProjects, encodeProjectPath(deep));
assert.strictEqual(storeDir(deep, path.join(ccDir, 's.jsonl')).dir, storeDir(deep).dir);

// no repo above it: cwd, and the snapshot asks the user to pin
const loose = path.join(tmp, 'loose');
fs.mkdirSync(loose);
assert.deepStrictEqual(resolveAnchor(loose), { dir: loose, source: 'cwd' });
// ...and the harness dir name is NOT trusted: it is the LOCAL encoded cwd, which on a second
// PC (other username) named a new empty store. The store comes from the anchor.
const looseCc = path.join(LAYOUT.claudeProjects, 'whatever-claude-code-picked');
assert.strictEqual(storeDir(loose, path.join(looseCc, 's.jsonl')).dir, path.join(PROJECTS, encodeProjectPath(loose)));

// a multi-repo demand serves every component in `workdirs`, not only `workdir`
const multi = { workdir: '/r/gmeg', workdirs: [{ workdir: '/r/gmeg' }, { workdir: '/r/lynus' }] };
assert.ok(demandServes(multi, '/r/lynus'));
assert.ok(demandServes(multi, '/r/gmeg'));
assert.ok(!demandServes(multi, '/r/other'));
assert.ok(!demandServes({ workdir: '' }, '/r/gmeg'));

fs.rmSync(tmp, { recursive: true, force: true });

// --- second PC: other username, other layout (2026-11-01) --------------------------------------
// Child process with its own HOME and config: nothing here touches the real memory root.
{
  const { execFileSync } = require('child_process');
  const box = fs.mkdtempSync(path.join(os.tmpdir(), 'store-2pc-'));
  const home = path.join(box, 'home', 'otheruser');
  const mem = path.join(home, '.ai-memory');
  const cfgDir = path.join(home, '.config', 'ai-memory');
  fs.mkdirSync(path.join(mem, 'projects', '-home-alice-ai-demo', 'context'), { recursive: true });
  fs.mkdirSync(path.join(mem, 'projects', '-home-alice-scratch', 'context'), { recursive: true });
  fs.mkdirSync(cfgDir, { recursive: true });
  fs.writeFileSync(path.join(cfgDir, 'config.json'), JSON.stringify({ home: mem, sources: path.join(home, '.claude') }));
  const repo = path.join(home, 'work', 'clients', 'demo');          // different layout AND username
  fs.mkdirSync(path.join(repo, '.git'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.git', 'config'),
    '[core]\n\tbare = false\n[remote "upstream"]\n\turl = git@github.com:someone/fork.git\n[remote "origin"]\n\turl = https://GitHub.com/example/Demo.git\n');
  const loneDir = path.join(home, 'notes');                          // no git, no remote
  fs.mkdirSync(loneDir, { recursive: true });
  const env = { ...process.env, HOME: home, AI_MEMORY_CONFIG: path.join(cfgDir, 'config.json') };
  delete env.AI_MEMORY_HOME;
  const run = (code) => JSON.parse(execFileSync('node', ['-e', `const p=require(${JSON.stringify(__dirname + '/project-store.js')});console.log(JSON.stringify(${code}))`], { env, stdio: ['ignore', 'pipe', 'pipe'] }).toString());

  // the work PC registered the remote (any URL form) to its store
  assert.strictEqual(run(`p.normalizeRemote('ssh://git@github.com:22/example/demo')`), 'github.com/example/demo');
  const reg = path.join(mem, 'registry', 'remotes');
  fs.mkdirSync(reg, { recursive: true });
  const h = (t, n) => require('crypto').createHash('sha256').update(t).digest('hex').slice(0, n);
  const file = `${h('github.com/example/demo', 16)}-${h('demo', 8)}.json`;   // work PC folder: ~/ai/demo
  fs.writeFileSync(path.join(reg, file), JSON.stringify({ remote: 'github.com/example/demo', name: 'demo', store: '-home-alice-ai-demo' }));

  const hit = run(`p.storeDir(${JSON.stringify(path.join(repo, 'src'))})`);
  assert.strictEqual(hit.dir, path.join(mem, 'projects', '-home-alice-ai-demo'), 'remote finds the existing store');
  assert.strictEqual(hit.identity, 'remote');

  // same remote, other folder name: a candidate for the snapshot, never resolved by itself
  const renamed = path.join(home, 'work', 'demo-renamed');
  fs.mkdirSync(path.join(renamed, '.git'), { recursive: true });
  fs.writeFileSync(path.join(renamed, '.git', 'config'), '[remote "origin"]\n\turl = git@github.com:example/demo.git\n');
  const cand = run(`p.storeDir(${JSON.stringify(renamed)})`);
  assert.strictEqual(cand.identity, 'new', 'no guess across folder names');
  assert.deepStrictEqual(cand.ambiguous, ['-home-alice-ai-demo']);
  assert.strictEqual(execFileSync('node', [__dirname + '/project-store.js', '--register', renamed], { env }).toString().includes('"skipped"'), true,
    'a new (not yet existing) store is not registered by --register');

  // no remote: new local store until bound; binding is per machine and wins
  assert.strictEqual(run(`p.storeDir(${JSON.stringify(loneDir)}).identity`), 'new');
  run(`(p.bind('-home-alice-scratch', ${JSON.stringify(loneDir)}), 1)`);
  assert.strictEqual(run(`p.storeDir(${JSON.stringify(path.join(loneDir, 'x'))}).dir`), path.join(mem, 'projects', '-home-alice-scratch'));
  assert.ok(fs.existsSync(path.join(cfgDir, 'store-bindings.json')), 'bindings live in the per-machine config dir');
  assert.throws(() => run(`p.bind('no-such-store', ${JSON.stringify(loneDir)})`));

  // register: first writer wins, a different store is a conflict, never an overwrite
  const other = path.join(home, 'x', 'demo');
  fs.mkdirSync(path.join(other, '.git'), { recursive: true });
  fs.writeFileSync(path.join(other, '.git', 'config'), '[remote "origin"]\n\turl = git@github.com:example/demo.git\n');
  assert.strictEqual(run(`p.register(${JSON.stringify(other)}, '-home-alice-scratch').status`), 'conflict');
  assert.strictEqual(JSON.parse(fs.readFileSync(path.join(reg, file))).store, '-home-alice-ai-demo');

  // one remote, two projects (branches of one repo): another folder name is ambiguous -> new store
  const sib = path.join(home, 'y', 'sibling');
  fs.mkdirSync(path.join(sib, '.git'), { recursive: true });
  fs.writeFileSync(path.join(sib, '.git', 'config'), '[remote "origin"]\n\turl = git@github.com:example/demo.git\n');
  assert.strictEqual(run(`p.register(${JSON.stringify(sib)}, '-home-alice-scratch').status`), 'registered');
  assert.strictEqual(run(`p.storeDir(${JSON.stringify(repo)})`).identity, 'remote', 'exact name keeps resolving');
  const amb = run(`p.storeDir(${JSON.stringify(renamed)})`);
  assert.strictEqual(amb.identity, 'new', 'two stores for one remote: no guess');
  assert.deepStrictEqual(amb.ambiguous.sort(), ['-home-alice-ai-demo', '-home-alice-scratch']);
  assert.strictEqual(run(`p.storeDir(${JSON.stringify(sib)}).dir`), path.join(mem, 'projects', '-home-alice-scratch'), 'exact name still resolves');

  // a worktree (.git FILE + commondir) reads the main clone's origin
  const wt = path.join(home, 'wt');
  fs.mkdirSync(path.join(repo, '.git', 'worktrees', 'wt'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.git', 'worktrees', 'wt', 'commondir'), '../..\n');
  fs.mkdirSync(wt, { recursive: true });
  fs.writeFileSync(path.join(wt, '.git'), `gitdir: ${path.join(repo, '.git', 'worktrees', 'wt')}\n`);
  assert.strictEqual(run(`p.originRemote(${JSON.stringify(wt)})`), 'github.com/example/demo');

  // nothing was created under the other user's encoded names
  assert.deepStrictEqual(fs.readdirSync(path.join(mem, 'projects')).filter((n) => n.includes('otheruser')), []);
  fs.rmSync(box, { recursive: true, force: true });
}
// Windows drive case: only /mnt/<drive>/ paths are touched; a real drvfs mount is used when present.
{
  const { onDiskCase } = require('./project-store.js');
  assert.strictEqual(onDiskCase('/srv/x/Some/Path'), '/srv/x/Some/Path');
  if (fs.existsSync('/mnt/c/Users')) assert.strictEqual(onDiskCase('/mnt/c/users'), '/mnt/c/Users');
  assert.strictEqual(onDiskCase('/mnt/c/no-such-dir-xyz/a'), '/mnt/c/no-such-dir-xyz/a', 'unknown path left as given');
}
console.log('ok');
