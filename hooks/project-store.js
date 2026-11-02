const fs = require('fs');
const path = require('path');
const os = require('os');

// Shared store resolution for memory_core.py (SessionStart snapshot, via --resolve) and
// transcript-capture.js (Stop). Both hooks MUST agree on the answer, so the
// logic lives here once.
//
// Until 2026-08-21 the store was the session's cwd, so every subdirectory of a
// repo got its own memory store — .../sap-api, .../sap-api/1-API/academicOne and
// .../sap-api/3-Queue/AcademicQueue were three separate memories of one project.
// The anchor is now the repo, not the cwd:
//   1. a pinned anchor in store-anchors.json (LAYOUT.anchors; nearest ancestor wins)
//   2. the nearest ancestor holding .git
//   3. cwd — the old behaviour, and the snapshot asks the user to pin it
//
// CLI:
//   node project-store.js --resolve [dir]        print the anchor for a dir
//   node project-store.js --pin <anchor> [--for <cwd>]   pin an anchor
//   node project-store.js --bind <store> [--for <dir>]   tie a dir to a store (per machine)
//   node project-store.js --register [dir]       record dir's git remote -> its store

const home = os.homedir();

const readJson = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; } };

// Mirror of memory_core.layout() (Python): the two MUST agree. Split layout when
// ~/.config/ai-memory/config.json names a `home` and AI_MEMORY_HOME is unset or equal
// to it: content+state in home, anchors beside the config, skills/JSONL in `sources`.
// Otherwise the legacy single root (AI_MEMORY_HOME or the code root, ~/.claude).
const CODE = path.resolve(__dirname, '..');
const expand = (p) => (p && p.startsWith('~/') ? path.join(home, p.slice(2)) : p);
const realish = (p) => { try { return fs.realpathSync(p); } catch { return path.resolve(p); } };
const layout = () => {
  const cfgPath = process.env.AI_MEMORY_CONFIG ? expand(process.env.AI_MEMORY_CONFIG)
    : path.join(process.env.XDG_CONFIG_HOME || path.join(home, '.config'), 'ai-memory', 'config.json');
  const cfg = readJson(cfgPath) || {};
  const explicit = process.env.AI_MEMORY_HOME;
  const cfgHome = cfg.home ? realish(expand(cfg.home)) : '';
  const sources = realish(expand(cfg.sources) || CODE);
  // AI_MEMORY_HOME=<sources> (~/.claude) is the pre-split value pinned in hook commands:
  // it means the live memory, so it maps to the configured home (memory_core.layout()).
  if (cfgHome && (!explicit || [cfgHome, sources].includes(realish(expand(explicit))))) {
    return { root: cfgHome, state: expand(cfg.state) || path.join(cfgHome, 'state'), configDir: path.dirname(cfgPath),
      config: cfgPath, sources, claudeProjects: expand(cfg.claude_projects) || path.join(sources, 'projects'),
      anchors: path.join(path.dirname(cfgPath), 'store-anchors.json'), bindings: path.join(path.dirname(cfgPath), 'store-bindings.json'),
      demands: path.join(sources, 'data', 'demands.json'),
      code: CODE, split: true };
  }
  const r = realish(expand(explicit) || path.join(home, '.claude'));
  const state = path.join(r, 'data', 'memory-system');
  return { root: r, state, configDir: state, config: path.join(state, 'config.json'), sources: r,
    claudeProjects: path.join(r, 'projects'), anchors: path.join(r, 'data', 'store-anchors.json'),
    bindings: path.join(r, 'data', 'store-bindings.json'),
    demands: path.join(r, 'data', 'demands.json'), code: CODE, split: false };
};
const LAYOUT = layout();
const root = LAYOUT.root;
const PROJECTS = path.join(root, 'projects');
const ANCHORS = LAYOUT.anchors;
const DEMANDS = LAYOUT.demands;

// Claude Code's own project-dir encoding: everything outside [A-Za-z0-9-] → '-'.
const encodeProjectPath = (p) => p.replace(/[^a-zA-Z0-9-]/g, '-');

const isUnder = (child, parent) =>
  child === parent || child.startsWith(parent.endsWith(path.sep) ? parent : parent + path.sep);

// { "<cwd or ancestor>": "<anchor dir>" } — deepest matching key wins.
const pinnedAnchor = (cwd) => {
  const map = readJson(ANCHORS) || {};
  let best = '';
  for (const k of Object.keys(map)) if (isUnder(cwd, k) && k.length > best.length) best = k;
  return best ? map[best] : '';
};

// .git is a directory in a normal clone and a file in a worktree/submodule.
// ponytail: nearest .git wins, so a nested clone anchors to itself. Pin it if you
// want it folded into the outer repo.
const gitRoot = (cwd) => {
  let d = path.resolve(cwd);
  for (;;) {
    if (fs.existsSync(path.join(d, '.git'))) return d;
    const up = path.dirname(d);
    if (up === d) return '';
    d = up;
  }
};

const resolveAnchor = (cwd) => {
  const abs = path.resolve(cwd);
  const pin = pinnedAnchor(abs);
  if (pin) return { dir: pin, source: 'pin' };
  const git = gitRoot(abs);
  if (git) return { dir: git, source: 'git' };
  return { dir: abs, source: 'cwd' };
};

// --- Portable identity (2026-11-01) -------------------------------------------------------
// A store NAME is the encoded anchor path of the machine that created it. Another PC (other
// username, other folder layout) computes a different name for the same repo, and used to get
// a new empty store in silence. The repo's git remote is what both machines share, so the
// memory root keeps registry/remotes/<remote hash>-<name hash>.json = {remote, name, store}: one
// file per project, so two PCs registering different repos never edit the same file.
// A repo with no remote is tied by hand, per machine: `--bind <store> --for <dir>`.
const REGISTRY = path.join(root, 'registry', 'remotes');
const BINDINGS = LAYOUT.bindings;

// https://host:8247/a/b.git, ssh://git@host:2222/a/b, git@host:a/b.git -> host/a/b (lowercase:
// GitHub and GitLab paths are case-insensitive; the port differs between https and ssh).
const normalizeRemote = (url) => {
  const u = String(url || '').trim().replace(/\/+$/, '').replace(/\.git$/, '');
  const m = u.match(/^[a-z][a-z0-9+.-]*:\/\/(?:[^@/]+@)?([^/:]+)(?::\d+)?\/(.+)$/i)
         || u.match(/^(?:[^@/]+@)?([^:/]+):(?!\/\/)(.+)$/);
  return m ? `${m[1]}/${m[2].replace(/^\/+/, '')}`.toLowerCase() : '';
};

// .git/config of a clone, worktree or submodule, read directly: no `git` process per hook call.
const gitConfigPath = (dir) => {
  const g = path.join(dir, '.git');
  try {
    if (fs.statSync(g).isDirectory()) return path.join(g, 'config');
    const m = fs.readFileSync(g, 'utf8').match(/^gitdir:\s*(.+)$/m);
    if (!m) return '';
    const gitdir = path.resolve(dir, m[1].trim());
    let common = gitdir;
    try { common = path.resolve(gitdir, fs.readFileSync(path.join(gitdir, 'commondir'), 'utf8').trim()); } catch {}
    return path.join(common, 'config');
  } catch { return ''; }
};

const originRemote = (dir) => {
  let text = '';
  try { text = fs.readFileSync(gitConfigPath(dir), 'utf8'); } catch { return ''; }
  let inOrigin = false;
  for (const line of text.split(/\r?\n/)) {
    const sec = line.match(/^\s*\[(.+)\]\s*$/);
    if (sec) { inOrigin = /^remote\s+"origin"$/.test(sec[1].trim()); continue; }
    const kv = inOrigin && line.match(/^\s*url\s*=\s*(.+?)\s*$/);
    if (kv) return normalizeRemote(kv[1]);
  }
  return '';
};

// One file per (remote, folder name): several local projects can share ONE remote on different
// branches (one monorepo remote can hold several unrelated projects), so the
// remote alone is not an identity. Files of one remote share a prefix, for the fallback below.
const hash = (t, n) => require('crypto').createHash('sha256').update(t).digest('hex').slice(0, n);
const registryFile = (remote, name) => path.join(REGISTRY, `${hash(remote, 16)}-${hash(name.toLowerCase(), 8)}.json`);
const validStore = (n) => typeof n === 'string' && /^[A-Za-z0-9._-]+$/.test(n) && fs.existsSync(path.join(PROJECTS, n));

// Only an exact (remote, folder name) resolves by itself. Same remote under another folder name is
// a CANDIDATE, never an answer: a new project on a shared remote (one remote, several projects on
// branches) would otherwise land in another project's memory, and the registration below would
// make that guess permanent. Candidates go to the snapshot, which asks before a `--bind`.
const registeredStore = (remote, name) => {
  if (!remote) return { store: '' };
  const exact = readJson(registryFile(remote, name));
  if (exact && exact.remote === remote && validStore(exact.store)) return { store: exact.store };
  let files = [];
  try { files = fs.readdirSync(REGISTRY).filter((f) => f.startsWith(hash(remote, 16) + '-')); } catch {}
  const stores = [...new Set(files.map((f) => readJson(path.join(REGISTRY, f)))
    .filter((r) => r && r.remote === remote && validStore(r.store)).map((r) => r.store))];
  return { store: '', ambiguous: stores };
};

// First writer wins; a different store already registered is reported, never overwritten.
// Callers pass only a store that BELONGS to the dir on this machine (identity path/binding/new):
// one reached through the registry is already recorded.
const register = (anchorDir, store) => {
  const remote = originRemote(anchorDir);
  if (!remote || !store) return { remote, status: 'no-remote' };
  const name = path.basename(anchorDir);
  const file = registryFile(remote, name);
  const rec = readJson(file);
  if (rec && rec.store === store) return { remote, status: 'known' };
  if (rec) return { remote, status: 'conflict', registered: rec.store };
  fs.mkdirSync(REGISTRY, { recursive: true });
  try {
    fs.writeFileSync(file, JSON.stringify({ remote, name: name.toLowerCase(), store }, null, 2) + '\n', { flag: 'wx' });
  } catch (e) {
    if (e.code !== 'EEXIST') throw e;
    return register(anchorDir, store);  // lost a race: re-read what the winner wrote
  }
  return { remote, status: 'registered' };
};

// { "<dir>": "<store name>" } per machine — deepest matching key wins.
const boundStore = (cwd) => {
  const map = readJson(BINDINGS) || {};
  let best = '';
  for (const k of Object.keys(map)) if (isUnder(cwd, k) && k.length > best.length) best = k;
  return best && /^[A-Za-z0-9._-]+$/.test(map[best]) ? map[best] : '';
};

const bind = (store, forDir) => {
  if (!/^[A-Za-z0-9._-]+$/.test(store) || !fs.existsSync(path.join(PROJECTS, store)))
    throw new Error(`no store named ${store} under ${PROJECTS}`);
  const map = readJson(BINDINGS) || {};
  map[path.resolve(forDir)] = store;
  fs.mkdirSync(path.dirname(BINDINGS), { recursive: true });
  fs.writeFileSync(BINDINGS, JSON.stringify(map, null, 2) + '\n');
};

// Windows drives under WSL ignore case, store names do not: Codex (VS Code) passes
// /mnt/c/users/.../client/repo and grew a second store beside .../CLIENT/repo
// (found 2026-11-01). Rebuild the on-disk case, one readdir per component (~70 ms on drvfs),
// only on the slow path: when no store exists for the path as given.
const onDiskCase = (p) => {
  const m = p.match(/^\/mnt\/[a-z]\//);
  if (!m) return p;
  let cur = m[0].slice(0, -1);
  for (const part of p.slice(m[0].length).split('/').filter(Boolean)) {
    let entries = [];
    try { entries = fs.readdirSync(cur); } catch { return p; }
    const hit = entries.includes(part) ? part : entries.find((e) => e.toLowerCase() === part.toLowerCase());
    if (!hit) return p;
    cur = path.join(cur, hit);
  }
  return cur;
};

// Order: per-machine binding > this machine's own store for the path > the store the repo's
// remote is registered to (another PC made it) > a new store named after the path.
// transcriptPath is accepted for compatibility and ignored: Claude Code's dir name is the
// LOCAL encoded cwd, exactly what made a second PC grow shadow stores.
// eslint-disable-next-line no-unused-vars
const storeDir = (cwd, transcriptPath) => {
  const anchor = resolveAnchor(cwd);
  const bound = boundStore(path.resolve(cwd));
  if (bound) return { dir: path.join(PROJECTS, bound), anchor, identity: 'binding' };
  let local = path.join(PROJECTS, encodeProjectPath(anchor.dir));
  if (fs.existsSync(local)) return { dir: local, anchor, identity: 'path' };
  const cased = onDiskCase(anchor.dir);
  if (cased !== anchor.dir) {
    local = path.join(PROJECTS, encodeProjectPath(cased));  // a NEW store gets the on-disk case too
    if (fs.existsSync(local)) return { dir: local, anchor, identity: 'path' };
  }
  if (anchor.source !== 'cwd') {
    const remote = originRemote(anchor.dir);
    const reg = registeredStore(remote, path.basename(anchor.dir));
    if (reg.store) return { dir: path.join(PROJECTS, reg.store), anchor, identity: 'remote', remote };
    if (reg.ambiguous && reg.ambiguous.length) return { dir: local, anchor, identity: 'new', remote, ambiguous: reg.ambiguous };
  }
  return { dir: local, anchor, identity: 'new' };
};

// A demand lives in this anchor when its workdir, or any component in `workdirs`
// (combined / multi-repo demands), is the anchor or nests with it.
const demandServes = (d, anchor) =>
  [d.workdir, ...(d.workdirs || []).map((w) => w.workdir)]
    .some((w) => w && (isUnder(w, anchor) || isUnder(anchor, w)));

// Demands registered against this anchor — the index of what lives in this repo.
const demandsFor = (anchor) => {
  const reg = readJson(DEMANDS);
  return ((reg && reg.demands) || []).filter((d) => demandServes(d, anchor));
};

const pin = (anchor, forDir) => {
  const map = readJson(ANCHORS) || {};
  map[path.resolve(forDir)] = path.resolve(anchor);
  fs.mkdirSync(path.dirname(ANCHORS), { recursive: true });
  fs.writeFileSync(ANCHORS, JSON.stringify(map, null, 2) + '\n');
  return map;
};

module.exports = { encodeProjectPath, resolveAnchor, storeDir, demandsFor, demandServes, pin, isUnder, PROJECTS, LAYOUT, layout,
  normalizeRemote, originRemote, register, bind, onDiskCase, REGISTRY };

if (require.main === module) {
  const argv = process.argv;
  const arg = (name) => { const i = argv.indexOf(name); return i !== -1 ? argv[i + 1] : undefined; };
  if (argv.includes('--pin')) {
    const anchor = arg('--pin');
    const forDir = arg('--for') || process.cwd();
    if (!anchor) { console.error('usage: --pin <anchor> [--for <cwd>]'); process.exit(1); }
    pin(anchor, forDir);
    console.log(`pinned ${path.resolve(forDir)} -> ${path.resolve(anchor)}`);
    console.log(`store: ${storeDir(forDir).dir}`);
  } else if (argv.includes('--bind')) {
    const store = arg('--bind');
    const forDir = arg('--for') || process.cwd();
    if (!store) { console.error('usage: --bind <store name> [--for <dir>]'); process.exit(1); }
    try { bind(store, forDir); } catch (e) { console.error(e.message); process.exit(1); }
    console.log(`bound ${path.resolve(forDir)} -> ${store} (this machine only: ${BINDINGS})`);
  } else if (argv.includes('--register')) {
    // Record this dir's git remote -> its current store, so another PC finds the same memory.
    const dir = arg('--register') || process.cwd();
    const { dir: store, anchor, identity } = storeDir(dir);
    if (identity !== 'path' && identity !== 'binding') {
      console.log(JSON.stringify({ anchor: anchor.dir, store: path.basename(store), status: 'skipped', identity }));
    } else {
      console.log(JSON.stringify({ anchor: anchor.dir, store: path.basename(store), ...register(anchor.dir, path.basename(store)) }));
    }
  } else {
    const dir = arg('--resolve') || process.cwd();
    const { dir: store, anchor, identity, remote, ambiguous } = storeDir(dir);
    if (argv.includes('--json')) {
      console.log(JSON.stringify({ dir: store, anchor, identity, remote, ambiguous, root: LAYOUT.root, state: LAYOUT.state,
        demands: demandsFor(anchor.dir) }));
      process.exit(0);
    }
    console.log(`cwd:    ${path.resolve(dir)}`);
    console.log(`anchor: ${anchor.dir} (${anchor.source})`);
    console.log(`store:  ${store} (${identity}${remote ? ' ' + remote : ''})`);
    for (const d of demandsFor(anchor.dir)) console.log(`demand: ${d.name} [${d.client}] ${d.status || ''}`);
  }
}
