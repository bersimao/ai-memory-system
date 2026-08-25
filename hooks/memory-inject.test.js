// Self-check for memory-inject's empty-store warning.
//
// Case 2 is the regression: on 2026-08-25 a second repo of this same project
// (claude-mem -> ai-memory-system) started with an EMPTY store and no warning,
// because the names scored 66.7% against a 0.85 similarity bar. Siblings get
// deliberately different names, so similarity was never going to catch them.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mi-test-'));
let fails = 0;
const ok = (m) => console.log('ok   ' + m);
const bad = (m) => { console.log('FAIL ' + m); fails++; };

// A fake HOME whose projects/ we control, plus real git repos to anchor on.
const home = path.join(root, 'home');
const projects = path.join(home, '.claude', 'projects');
fs.mkdirSync(projects, { recursive: true });
fs.mkdirSync(path.join(home, '.claude', 'context'), { recursive: true });

const encode = (p) => p.replace(/[^a-zA-Z0-9-]/g, '-');
const mkRepo = (dir) => {
  fs.mkdirSync(dir, { recursive: true });
  execFileSync('git', ['init', '-q'], { cwd: dir });
  return dir;
};
const seedStore = (dir, body) => {
  const c = path.join(projects, encode(dir), 'context');
  fs.mkdirSync(c, { recursive: true });
  fs.writeFileSync(path.join(c, 'MEMORY.md'), body, 'utf8');
};
const snapshot = (cwd) => execFileSync(
  process.execPath,
  [path.join(__dirname, 'memory-inject.js'), '--cwd', cwd],
  { env: { ...process.env, HOME: home }, encoding: 'utf8' }
);

// Neighbourhood: <root>/work/{alpha,alpha-published,unrelated-thing}
const work = path.join(root, 'work');
const alpha = mkRepo(path.join(work, 'alpha'));
seedStore(alpha, '# memoria do alpha\n');

// 1 — sibling with a DIFFERENT name must be flagged and offered a pin.
const published = mkRepo(path.join(work, 'alpha-published'));
let out = snapshot(published);
if (/neighbouring directory|similar store/.test(out) && /--pin/.test(out)) {
  ok('repo irmao de nome diferente e sinalizado, com pin oferecido');
} else {
  bad('repo irmao nao foi sinalizado (regressao de 2026-08-25)');
}

// 2 — the warning must never pin by itself.
if (/Never pin without asking/.test(out)) ok('avisa para nao pinar sem perguntar');
else bad('faltou a trava de "nao pine sozinho"');

// 3 — a store that already has content gets no warning at all.
seedStore(published, '# ja tem memoria\n');
out = snapshot(published);
if (!/similar store|neighbouring directory/.test(out)) ok('store com conteudo nao recebe aviso');
else bad('avisou mesmo com store populado');

// 4 — an empty store with NO populated neighbour stays quiet.
const lonely = mkRepo(path.join(root, 'elsewhere', 'lonely'));
out = snapshot(lonely);
if (!/similar store|neighbouring directory/.test(out)) ok('sem vizinho populado, nenhum aviso');
else bad('falso-positivo sem vizinho');

// 5 — pinning silences it: the pinned dir resolves to the other store.
const second = mkRepo(path.join(work, 'alpha-infra'));
execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
  '--pin', alpha, '--for', second], { env: { ...process.env, HOME: home } });
out = snapshot(second);
if (/pinned/.test(out) && /memoria do alpha/.test(out)) {
  ok('depois do pin, o dir novo carrega a memoria existente');
} else {
  bad('pin nao redirecionou o store');
}

fs.rmSync(root, { recursive: true, force: true });
if (fails) { console.log(`FAIL (${fails})`); process.exit(1); }
console.log('PASS');
