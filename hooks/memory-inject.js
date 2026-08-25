const fs = require('fs');
const path = require('path');
const os = require('os');

// SessionStart hook: inject the memory snapshot into context deterministically.
// Global layer (USER.md, MEMORY.md) — always loaded.
// Project layer — loaded from a central store at
//   ~/.claude/projects/<encoded-repo-anchor>/context/  (see project-store.js)
// The context dir auto-creates on first session per project — no per-project setup.
//
// Portable core: `node memory-inject.js --cwd <dir>` prints the same snapshot
// for ANY harness — no Claude Code stdin payload required. This is the
// CLI-neutral entry point (see docs/model-agnostic.md in the memory-system repo).
// Default mode (no args) reads the SessionStart hook JSON from stdin.

let input = {};
const cwdFlag = process.argv.indexOf('--cwd');
if (cwdFlag !== -1 && process.argv[cwdFlag + 1]) {
  input = { cwd: path.resolve(process.argv[cwdFlag + 1]) };
} else {
  try { input = JSON.parse(fs.readFileSync(0, 'utf8')); } catch {}
}
const cwd = input.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();

const read = (p) => {
  try { return fs.readFileSync(p, 'utf8').trim(); } catch { return ''; }
};

// Local date YYYY-MM-DD (avoid UTC drift near midnight)
const localDate = (d) => {
  const z = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
};

const { storeDir, demandsFor } = require('./project-store.js');

const home = os.homedir();

// The store is anchored to the REPO, not the cwd (see project-store.js) — a
// session started in a subdirectory loads the same memory as one started at the
// repo root. In --cwd mode there is no transcript_path; the anchor is the contract.
const { dir: projectDir, anchor } = storeDir(cwd, input.transcript_path);

const projects = path.join(home, '.claude', 'projects');
const ctx = path.join(projectDir, 'context');

// Auto-create on first use — no per-project init required.
try { fs.mkdirSync(ctx, { recursive: true }); } catch {}

// Rename detection: an empty store whose name closely matches a store with
// content usually means the project directory was renamed — the old memory is
// orphaned, not gone. Warn once (first Stop writes a transcript, making the
// store non-empty, so the warning self-quiets). Never auto-merge.
const storeIsEmpty = (c) => {
  const nonEmptyFile = (p) => { try { return fs.statSync(p).size > 0; } catch { return false; } };
  const nonEmptyDir = (p) => { try { return fs.readdirSync(p).length > 0; } catch { return false; } };
  return !nonEmptyFile(path.join(c, 'MEMORY.md')) &&
         !nonEmptyDir(path.join(c, 'memory')) &&
         !nonEmptyDir(path.join(c, 'transcripts'));
};

const similarity = (a, b) => {
  const m = a.length, n = b.length;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return 1 - prev[n] / Math.max(m, n);
};

const renameWarning = () => {
  if (!storeIsEmpty(ctx)) return '';
  const self = path.basename(path.dirname(ctx));
  let names = [];
  try { names = fs.readdirSync(projects); } catch {}
  let best = null;
  for (const n of names) {
    if (n === self) continue;
    const c = path.join(projects, n, 'context');
    if (storeIsEmpty(c)) continue;
    const sim = similarity(n, self);
    if (sim < 0.85 || (best && sim <= best.sim)) continue;
    let last = 0;
    for (const sub of ['MEMORY.md', 'memory', 'transcripts']) {
      try { last = Math.max(last, fs.statSync(path.join(c, sub)).mtimeMs); } catch {}
    }
    best = { n, sim, last };
  }
  if (!best) return '';
  return 'This project\'s memory store is empty, but a similar store has content:\n' +
    `- \`${path.join(projects, best.n)}\`\n` +
    `- similarity ${(best.sim * 100).toFixed(0)}%, last activity ${new Date(best.last).toISOString().slice(0, 10)}\n` +
    'If the project directory was renamed, that store is this project\'s orphaned memory — ' +
    'tell the user and offer to merge its context/ into this store. ' +
    'If this is a genuinely new project that merely resembles another, ignore this.';
};


// Where this session's memory lives, and — when nothing anchors it — the ask.
const anchorSection = () => {
  if (anchor.source === 'cwd') {
    return `No git repository was found above this session's directory:\n` +
      `- \`${cwd}\`\n` +
      'Memory is being stored per-directory, which splits one project across several stores. ' +
      'Ask the user which directory should anchor this project\'s memory, then run:\n' +
      `\`node ~/.claude/hooks/project-store.js --pin <dir> --for ${cwd}\`\n` +
      'Ask once — if the user declines, drop it for the session.';
  }
  return `\`${anchor.dir}\` (${anchor.source === 'pin' ? 'pinned' : 'git root'})`;
};

// Index of the demands registered against this repo — new demand on the same
// repo appends here instead of starting a private memory. Source of truth is
// ~/.claude/data/demands.json (owned by the start-new-project skill).
const demandsSection = () => demandsFor(anchor.dir).map((d) => {
  const lines = [`- **${d.name}** [${d.client}] — ${d.status || 'sem status'}`];
  if (d.note) lines.push(`  note: ${d.note}`);
  if (d.workdir && d.workdir !== anchor.dir) lines.push(`  workdir: ${d.workdir}`);
  return lines.join('\n');
}).join('\n');

const parts = [];
const add = (label, body) => { if (body) parts.push(`### ${label}\n${body}`); };

add('Possible renamed project — orphaned store?', renameWarning());
add('Project store anchor', anchorSection());

add('Global USER.md', read(path.join(home, '.claude', 'context', 'USER.md')));
add('Global MEMORY.md', read(path.join(home, '.claude', 'context', 'MEMORY.md')));

add('Project MEMORY.md', read(path.join(ctx, 'MEMORY.md')));
add('Demands on this project (auto, from demands.json)', demandsSection());

const today = new Date();
const yesterday = new Date(today.getTime() - 86400000);
const todayLog = read(path.join(ctx, 'memory', `${localDate(today)}.md`));
add(`Daily log ${localDate(today)}`, todayLog);
if (!todayLog) {
  add(`Daily log ${localDate(yesterday)}`, read(path.join(ctx, 'memory', `${localDate(yesterday)}.md`)));
}

if (parts.length) {
  process.stdout.write(
    '## Memory snapshot (session startup)\n' +
    'Frozen snapshot loaded once. Mid-session writes take effect next session.\n\n' +
    parts.join('\n\n') + '\n'
  );
}
