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
seedStore(alpha, '# alpha memory\n');

// 1 — sibling with a DIFFERENT name must be flagged and offered a pin.
const published = mkRepo(path.join(work, 'alpha-published'));
let out = snapshot(published);
if (/neighbouring directory|similar store/.test(out) && /--pin/.test(out)) {
  ok('differently-named sibling repo is flagged, with a pin offered');
} else {
  bad('sibling repo was not flagged (2026-08-25 regression)');
}

// 2 — the warning must never pin by itself.
if (/Never pin without asking/.test(out)) ok('warns never to pin without asking');
else bad('missing the "never pin on your own" guard');

// 3 — a store that already has content gets no warning at all.
seedStore(published, '# already has memory\n');
out = snapshot(published);
if (!/similar store|neighbouring directory/.test(out)) ok('a store with content gets no warning');
else bad('warned even with a populated store');

// 4 — an empty store with NO populated neighbour stays quiet.
const lonely = mkRepo(path.join(root, 'elsewhere', 'lonely'));
out = snapshot(lonely);
if (!/similar store|neighbouring directory/.test(out)) ok('no populated neighbour, no warning');
else bad('false positive with no neighbour');

// 5 — pinning silences it: the pinned dir resolves to the other store.
const second = mkRepo(path.join(work, 'alpha-infra'));
execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
  '--pin', alpha, '--for', second], { env: { ...process.env, HOME: home } });
out = snapshot(second);
if (/pinned/.test(out) && /alpha memory/.test(out)) {
  ok('after the pin, the new dir loads the existing memory');
} else {
  bad('pin did not redirect the store');
}

// 6-8 — the truncation warning must never CAUSE truncation.
//
// Regression (Codex stop-gate, 2026-09-09): the warning first fired at >10,000B,
// the conservative sweep budget. But truncation is only proven at 10,463B, so a
// snapshot in between would have been injected intact — and the warning's own
// ~430B could push it past the real limit, manufacturing the fault it reported.
// The invariant: below the proven point the hook stays silent, and its output is
// byte-identical to what it would emit with the warning removed entirely.
const PROVEN = 10463;
const bytesOf = (s) => Buffer.byteLength(s, 'utf8');
// Build a snapshot whose CONTENT is exactly `target` bytes, and say so.
//
// Content, not emitted: the gate in memory-inject.js is evaluated on the size
// BEFORE the warning is prepended, so a fixture that targets emitted bytes
// overshoots by the warning's own ~433B exactly when it crosses the threshold —
// i.e. precisely in the boundary cases these tests exist to pin down.
//
// The content size is known by construction rather than measured: the baseline
// carries no warning (asserted), and the padding is ASCII, so one char is one
// byte.
const sized = (dir, target) => {
  seedStore(dir, 'x');
  const baseText = snapshot(dir);
  if (/Snapshot truncated/.test(baseText)) {
    bad('fixture baseline already warns — global layer too big to size against');
  }
  const base = bytesOf(baseText) - 1;
  const pad = Math.max(1, target - base);
  seedStore(dir, 'x'.repeat(pad));
  return { text: snapshot(dir), content: base + pad };
};

const sizer = mkRepo(path.join(root, 'sizing', 'repo'));

// The earlier version of these cases targeted PROVEN-800 / PROVEN-50 / PROVEN+600
// and so tolerated the constant sitting anywhere in roughly [9664, 11063] — only
// the middle case discriminated at all. The point of SNAP_TRUNC_PROVEN is that it
// is EXACTLY the proven point, so the boundary itself is what needs asserting.
// sized() must therefore hit its target exactly; assert that before trusting it.
const sizedExact = (dir, target) => {
  const { text, content } = sized(dir, target);
  if (content !== target) bad(`fixture off by ${content - target}B (quis ${target}, veio ${content})`);
  return text;
};

// 6 — one byte BELOW the proven point: silent. Fails if the gate is 10000.
out = sizedExact(sizer, PROVEN - 1);
if (!/Snapshot truncated/.test(out)) ok(`silencioso em ${PROVEN - 1}B (1B abaixo do provado)`);
else bad(`avisou em ${PROVEN - 1}B — o gate desceu abaixo do ponto provado`);

// 7 — exactly AT the proven point: warns. Fails if the gate drifts upward.
out = sizedExact(sizer, PROVEN);
if (/Snapshot truncated/.test(out)) ok(`avisa exatamente em ${PROVEN}B`);
else bad(`silencioso em ${PROVEN}B — o gate subiu acima do ponto provado`);

// 8 — inside the unknown band the hook must add NO bytes: byte-identical to the
// same snapshot with the warning gone. This is the self-inflicted-truncation
// invariant, and it is the reason the gate is not SNAP_BUDGET.
out = sizedExact(sizer, PROVEN - 50);
if (/Snapshot truncated/.test(out)) {
  bad(`avisou dentro da faixa incerta (${bytesOf(out)}B) — pode causar o corte que anuncia`);
} else {
  ok('dentro da faixa incerta, silencioso e sem acrescentar byte');
}

// 9 — past the proven point the warning must land inside the ~2KB the harness
// keeps. Measure BYTES: the harness cuts bytes, and `indexOf` counts UTF-16
// code units, so accented pt-BR ahead of the warning could push it past 2048
// bytes with a code-unit assertion still green.
out = sizedExact(sizer, PROVEN + 600);
if (!/Snapshot truncated/.test(out)) {
  bad(`sem aviso em ${bytesOf(out)}B, passado o ponto provado`);
} else {
  const idx = out.indexOf('Snapshot truncated');
  const byteOffset = bytesOf(out.slice(0, idx));
  if (byteOffset > 2048) {
    bad(`aviso comeca no byte ${byteOffset}, fora do preview de ~2KB — seria cortado`);
  } else {
    ok(`aviso comeca no byte ${byteOffset}, dentro do preview que sobrevive`);
  }
}

// 10 — short-keyword secrets are redacted; prose that merely contains the
// keyword is left intact (same rules as memory_core.redact).
{
  const repo = mkRepo(path.join(root, 'work', 'redact-case'));
  // Redaction guards the raw-transcript fallback (no daily log yet today).
  seedStore(repo, '# M\n');
  const d = new Date();
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const tdir = path.join(projects, encode(repo), 'context', 'transcripts');
  fs.mkdirSync(tdir, { recursive: true });
  fs.writeFileSync(path.join(tdir, `${day}.md`), 'DB_PASS=hunter2\nUid=sa;Pwd=S3cret;Db=y\nCREDENTIALS=abc123\n' +
    'Authorization: Basic dXNlcjpwYXNz\nfirst pass: we split\nbypass: no\n' +
    'DB_PASS="alpha quotedspace"\n{"password": "jsonsecret", "n": 1}\nPASS="a\\"b escapedsecret"\n', 'utf8');
  const out = snapshot(repo);
  if (!out.includes('first pass')) bad('fallback do transcript nao entrou no snapshot — caso 10 nao prova nada');
  const leaked = ['hunter2', 'S3cret', 'abc123', 'dXNlcjpwYXNz', 'quotedspace', 'jsonsecret', 'escapedsecret'].filter((x) => out.includes(x));
  if (leaked.length) bad(`segredo vazou no snapshot: ${leaked.join(', ')}`);
  else ok('DB_PASS/Pwd/CREDENTIALS/Basic redigidos');
  if (out.includes('first pass: we split') && out.includes('bypass: no') && out.includes(';Db=y')) {
    ok('prosa com "pass" e resto da connection string intactos');
  } else bad('redacao comeu prosa ou o resto da connection string');
}

fs.rmSync(root, { recursive: true, force: true });
if (fails) { console.log(`FAIL (${fails})`); process.exit(1); }
console.log('PASS');
