const fs = require('fs');
const path = require('path');
const os = require('os');

// Shared store resolution for memory-inject.js (SessionStart) and
// transcript-capture.js (Stop). Both hooks MUST agree on the answer, so the
// logic lives here once.
//
// Until 2026-08-21 the store was the session's cwd, so every subdirectory of a
// repo got its own memory store — .../sap-api, .../sap-api/1-API/academicOne and
// .../sap-api/3-Queue/AcademicQueue were three separate memories of one project.
// The anchor is now the repo, not the cwd:
//   1. a pinned anchor in ~/.claude/data/store-anchors.json (nearest ancestor wins)
//   2. the nearest ancestor holding .git
//   3. cwd — the old behaviour, and the snapshot asks the user to pin it
//
// CLI:
//   node project-store.js --resolve [dir]        print the anchor for a dir
//   node project-store.js --pin <anchor> [--for <cwd>]   pin an anchor

const home = os.homedir();
const PROJECTS = path.join(home, '.claude', 'projects');
const ANCHORS = path.join(home, '.claude', 'data', 'store-anchors.json');
const DEMANDS = path.join(home, '.claude', 'data', 'demands.json');

// Claude Code's own project-dir encoding: everything outside [A-Za-z0-9-] → '-'.
const encodeProjectPath = (p) => p.replace(/[^a-zA-Z0-9-]/g, '-');

const readJson = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return null; } };

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

// transcriptPath is <projects>/<encoded-cwd>/<session>.jsonl — Claude Code's own
// encoding of the cwd. When the anchor IS the cwd, prefer that dirname: it is the
// encoding straight from the harness, no guesswork.
const storeDir = (cwd, transcriptPath) => {
  const anchor = resolveAnchor(cwd);
  if (transcriptPath && anchor.dir === path.resolve(cwd)) {
    const dir = path.dirname(transcriptPath);
    if (path.dirname(dir) === PROJECTS) return { dir, anchor };
  }
  return { dir: path.join(PROJECTS, encodeProjectPath(anchor.dir)), anchor };
};

// Demands registered against this anchor — the index of what lives in this repo.
const demandsFor = (anchor) => {
  const reg = readJson(DEMANDS);
  return ((reg && reg.demands) || []).filter(
    (d) => d.workdir && d.active !== false && (isUnder(d.workdir, anchor) || isUnder(anchor, d.workdir))
  );
};

const pin = (anchor, forDir) => {
  const map = readJson(ANCHORS) || {};
  map[path.resolve(forDir)] = path.resolve(anchor);
  fs.mkdirSync(path.dirname(ANCHORS), { recursive: true });
  fs.writeFileSync(ANCHORS, JSON.stringify(map, null, 2) + '\n');
  return map;
};

module.exports = { encodeProjectPath, resolveAnchor, storeDir, demandsFor, pin, isUnder, PROJECTS };

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
  } else {
    const dir = arg('--resolve') || process.cwd();
    const { dir: store, anchor } = storeDir(dir);
    console.log(`cwd:    ${path.resolve(dir)}`);
    console.log(`anchor: ${anchor.dir} (${anchor.source})`);
    console.log(`store:  ${store}`);
    for (const d of demandsFor(anchor.dir)) console.log(`demand: ${d.name} [${d.client}] ${d.status || ''}`);
  }
}
