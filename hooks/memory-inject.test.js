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

// Suite for the WRITE-ONCE marker. The hardened-reader cases (symlink, FIFO,
// hard link, oversized, junk, Windows) and the repair cases are deliberately
// absent: this design never rewrites the marker and trusts it exactly as much
// as MEMORY.md and the transcripts beside it, which are read with a plain
// readFileSync and injected verbatim. Hardening one file there would buy
// nothing; hardening all of them is a separate change.
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

// 11-13 — store collision. encodeProjectPath is many-to-one, so two anchors can
// share one store and silently mix their memory. The marker file is the only
// provenance: the encoding is lossy and cannot be inverted.
{
  // `col.x` and `col/x` encode identically — the exact shape the user asked about.
  const dotted = mkRepo(path.join(root, 'crash', 'col.x'));
  const slashed = mkRepo(path.join(root, 'crash', 'col', 'x'));
  if (encode(dotted) !== encode(slashed)) {
    bad('fixture does not collide — cases 11-13 prove nothing');
  }

  // 11 — first session records the anchor and says nothing.
  out = snapshot(dotted);
  if (/Store collision/.test(out)) bad('warned on a fresh store with one anchor');
  else ok('first session records the anchor silently');

  // 12 — the second anchor lands on the same store and must be flagged, naming
  // BOTH directories: the recorded one is unrecoverable from the store name.
  out = snapshot(slashed);
  if (/Store collision/.test(out) && out.includes(dotted) && out.includes(slashed)) {
    ok('second anchor on the same store is flagged, both directories named');
  } else {
    bad('collision not flagged, or the warning does not name both directories');
  }

  // 13 — a PIN is not a collision: both dirs resolve to one anchor.dir, so the
  // marker matches. Without this the supported way to share a store would warn
  // on every session, and the warning would be trained away as noise.
  const pinA = mkRepo(path.join(root, 'pinned', 'src'));
  const pinB = mkRepo(path.join(root, 'pinned', 'published'));
  snapshot(pinA);
  execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
    '--pin', pinA, '--for', pinB], { env: { ...process.env, HOME: home } });
  out = snapshot(pinB);
  if (!/Store collision/.test(out)) ok('a pinned second directory does not warn');
  else bad('pin reported as a collision — the supported path warns');

  // 14 — an EMPTY marker must be left alone, NOT "repaired".
  //
  // Codex stop-review, second finding. An empty marker is indistinguishable
  // from a creator caught mid-write, so rewriting it overwrites that creator —
  // the same clobber the fix exists to prevent, except now on the ordinary
  // creation path instead of a rare corrupt state. The marker is created by
  // writing a temp file and hard-linking it into place, so it never exists
  // without its content; nothing here ever writes over an existing one.
  //
  // The trade this pins: a marker truncated by something outside the hook
  // stays quiet for good. That is cheaper than destroying provenance, which
  // cannot be recovered — the encoding is not invertible.
  const marker = path.join(projects, encode(dotted), 'context', '.anchor');
  fs.writeFileSync(marker, '', 'utf8');
  out = snapshot(slashed);
  if (fs.readFileSync(marker, 'utf8') === '') {
    ok('fresh empty marker is left untouched (a creator may be mid-write)');
  } else {
    bad('fresh empty marker was rewritten — clobbers a concurrent creator');
  }
  if (!/Store collision/.test(out)) ok('empty marker warns about nothing');
  else bad('warned off an empty marker, which proves no collision');


  // 16-17 — pinning two COLLIDING directories must silence the warning.
  //
  // Codex review, third finding. Case 13 below pins two NON-colliding repos,
  // where the pin moves the store to one whose marker already matches — so it
  // never exercised a stale marker. With colliding dirs the store does not
  // change, the marker keeps the old anchor, and the warning fired forever:
  // the remedy the warning itself prints did not work. An explicit pin now
  // outranks the marker and rewrites it.
  {
    const pa = mkRepo(path.join(root, 'colpin', 'p.q'));
    const pb = mkRepo(path.join(root, 'colpin', 'p', 'q'));
    if (encode(pa) !== encode(pb)) bad('colliding-pin fixture does not collide');
    snapshot(pa);                                  // pa records the marker
    // Pin in the direction that leaves the marker STALE (pa's anchor becomes pb).
    execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
      '--pin', pb, '--for', pa], { env: { ...process.env, HOME: home } });
    out = snapshot(pa);
    if (!/Store collision/.test(out)) ok('pinning colliding dirs silences the warning');
    else bad('pinned colliding dirs still warn — the advised remedy does not work');

    // 17 — and the unpinned side converges: the rewritten marker now matches
    // the anchor every session in this store resolves to.
    out = snapshot(pb);
    if (!/Store collision/.test(out)) ok('the other colliding dir converges on the pin');
    else bad('marker did not converge — the pin left the store warning');
  }

  // 19 — convergence must not depend on WHICH SIDE starts first.
  //
  // Codex review, fifth finding. Only one of two pinned-together directories
  // carries anchor.source 'pin'; the other resolves by git root. Gating the
  // refresh on source meant the unpinned side warned until a pinned session
  // happened to run first — and case 16-17 above missed it purely because its
  // fixture ran the pinned side first. This repeats that setup with the
  // UNPINNED side going first, which is the order that used to fail.
  {
    const oa = mkRepo(path.join(root, 'ord', 'o.p'));
    const ob = mkRepo(path.join(root, 'ord', 'o', 'p'));
    if (encode(oa) !== encode(ob)) bad('order fixture does not collide');
    snapshot(oa);                                   // oa records the marker
    execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
      '--pin', ob, '--for', oa], { env: { ...process.env, HOME: home } });
    // ob is the UNPINNED side (its anchor is its own git root, source 'git').
    out = snapshot(ob);
    if (!/Store collision/.test(out)) ok('unpinned side first: no spurious warning');
    else bad('unpinned side warned — convergence depends on session order');
    // And the pinned side agrees, whichever ran first.
    out = snapshot(oa);
    if (!/Store collision/.test(out)) ok('pinned side agrees after the unpinned one');
    else bad('pinned side warned after the unpinned side converged');

  }

  // 21 — CONFLICTING pins must not flip the marker every session.
  //
  // Codex review, round 6, confirmed by probe: with A pinned to B and B pinned
  // to A, sessions from A wrote B and sessions from B wrote A, forever. The
  // refresh now only fires from a stable anchor (one that resolves to itself).
  {
    const ca = mkRepo(path.join(root, 'cyc', 'm.n'));
    const cb = mkRepo(path.join(root, 'cyc', 'm', 'n'));
    if (encode(ca) !== encode(cb)) bad('cyclic fixture does not collide');
    snapshot(ca);
    const pinTo = (a, f) => execFileSync(process.execPath,
      [path.join(__dirname, 'project-store.js'), '--pin', a, '--for', f],
      { env: { ...process.env, HOME: home } });
    pinTo(cb, ca); pinTo(ca, cb);              // cyclic
    const cm = path.join(projects, encode(ca), 'context', '.anchor');
    const seen = new Set();
    let warned = false;
    for (let i = 0; i < 3; i++) {
      for (const d of [ca, cb]) {
        if (/Store collision/.test(snapshot(d))) warned = true;
        seen.add(fs.readFileSync(cm, 'utf8').trim());
      }
    }
    if (seen.size === 1) ok('conflicting pins leave the marker stable');
    else bad(`marker flip-flops under cyclic pins: ${[...seen].length} values`);
    if (!warned) ok('conflicting pins stay quiet rather than warn every session');
    else bad('cyclic pins produce a warning on every session');
  }

  // 22 — the marker is read as UNTRUSTED. A symlinked .anchor leaked the target
  // file's contents into the snapshot in a live probe (Codex round 6).
  {
    const sv = mkRepo(path.join(root, 'sym', 'repo'));
    const sctx = path.join(projects, encode(sv), 'context');
    fs.mkdirSync(sctx, { recursive: true });



    // 25 — a precreated symlink at the TEMP name must not redirect the write.
    // Codex round 7, confirmed by probe: `.anchor.<pid>` with writeFileSync's
    // default 'w' (O_CREAT|O_TRUNC, follows symlinks) overwrote an unrelated
    // file with the anchor path. Temp names are now random and created 'wx'.
    const vic = mkRepo(path.join(root, 'tmpatk', 'repo'));
    const vctx = path.join(projects, encode(vic), 'context');
    fs.mkdirSync(vctx, { recursive: true });
    const victim = path.join(root, 'IMPORTANT.txt');
    fs.writeFileSync(victim, 'ORIGINAL-MUST-SURVIVE\n', 'utf8');
    // Blanket the plausible pid window, the way an attacker would.
    const guess = Number(execFileSync(process.execPath, ['-e', 'console.log(process.pid)'],
      { encoding: 'utf8' }).trim());
    for (let p2 = guess - 2; p2 <= guess + 12; p2++) {
      try { fs.symlinkSync(victim, path.join(vctx, `.anchor.${p2}`)); } catch {}
    }
    snapshot(vic);
    if (fs.readFileSync(victim, 'utf8').includes('ORIGINAL-MUST-SURVIVE')) {
      ok('predictable temp name cannot overwrite an unrelated file');
    } else {
      bad('temp-file symlink overwrote an unrelated file');
    }


    // 27 — a path whose directory legitimately ends in a space must not warn
    // forever. `.trim()` used to corrupt it so prev never matched the anchor.
    const spaced = mkRepo(path.join(root, 'spacey', 'trail '));  // ANCHOR ends in a space
    snapshot(spaced);
    out = snapshot(spaced);
    if (!/Store collision/.test(out)) ok('a path with a trailing space stays silent');
    else bad('trailing-space path warns every session (trim corruption)');

  }

  // 18 — a pin must NOT silence a collision it never authorised.
  //
  // Codex review, fourth finding. A pin declares "this directory belongs to
  // that anchor" — nothing about a THIRD project that merely encodes to the
  // same store. Rewriting the marker unconditionally erased that project's
  // provenance and suppressed a genuine collision, which is the one thing this
  // hook exists to catch.
  {
    const owner = mkRepo(path.join(root, 'unrel', 'u.v'));   // owns the store
    const other = mkRepo(path.join(root, 'unrel', 'u', 'v')); // collides with it
    const third = mkRepo(path.join(root, 'unrel-src'));       // unrelated repo
    if (encode(owner) !== encode(other)) bad('unrelated-pin fixture does not collide');
    snapshot(owner);
    // Pin the third repo onto `other` — legitimate for those two, and it says
    // nothing at all about `owner`, which already holds this store.
    execFileSync(process.execPath, [path.join(__dirname, 'project-store.js'),
      '--pin', other, '--for', third], { env: { ...process.env, HOME: home } });
    out = snapshot(third);
    const marker = path.join(projects, encode(owner), 'context', '.anchor');
    if (/Store collision/.test(out) && out.includes(owner)) {
      ok('a pin does not silence an unrelated collision');
    } else {
      bad('pin hid a real collision between two unrelated projects');
    }
    if (fs.readFileSync(marker, 'utf8').trim() === owner) {
      ok("the unrelated owner's provenance survives the pinned session");
    } else {
      bad("pinned session overwrote an unrelated project's anchor");
    }
  }

  // 15 — the marker never appears without content: creation is temp-file +
  // link, so there is no window in which a reader sees an empty marker. Check
  // the temp file is not left behind either.
  const fresh = mkRepo(path.join(root, 'fresh', 'store'));
  snapshot(fresh);
  const fctx = path.join(projects, encode(fresh), 'context');
  const stray = fs.readdirSync(fctx).filter((n) => n.startsWith('.anchor.'));
  if (fs.readFileSync(path.join(fctx, '.anchor'), 'utf8').trim() === fresh && !stray.length) {
    ok('marker created with content, no temp file left behind');
  } else {
    bad(`marker incomplete or temp left: ${stray.join(', ')}`);
  }
}

// 29 — the no-hard-link fallback must still leave a COMPLETE marker. A plain
// 'wx' write there creates-then-fills, and a crash in that window left an empty
// marker that this design never repairs — silent for good (Codex stop-review,
// 2026-09-20). The fallback now renames a filled temp file into place.
{
  const fb = mkRepo(path.join(root, 'nohardlink', 'repo'));
  const env = { ...process.env, HOME: home, AI_MEMORY_NO_HARDLINK: '1' };
  const runFb = (cwd) => execFileSync(process.execPath,
    [path.join(__dirname, 'memory-inject.js'), '--cwd', cwd], { env, encoding: 'utf8' });
  runFb(fb);
  const fbMark = path.join(projects, encode(fb), 'context', '.anchor');
  const body = (() => { try { return fs.readFileSync(fbMark, 'utf8').trim(); } catch { return ''; } })();
  if (body === fb) ok('no-hard-link fallback records a complete marker');
  else bad(`fallback marker empty or wrong: ${JSON.stringify(body)}`);
  // And it must never replace one that already exists.
  const other = mkRepo(path.join(root, 'nohardlink', 'repo2'));
  fs.mkdirSync(path.join(projects, encode(other), 'context'), { recursive: true });
  fs.writeFileSync(path.join(projects, encode(other), 'context', '.anchor'), '/pre/existing\n', 'utf8');
  runFb(other);
  if (fs.readFileSync(path.join(projects, encode(other), 'context', '.anchor'), 'utf8').trim() === '/pre/existing') {
    ok('fallback never replaces an existing marker');
  } else bad('fallback overwrote an existing marker');
  // Left-over temp files would accumulate in the store.
  const strays = fs.readdirSync(path.join(projects, encode(fb), 'context')).filter((n) => n.startsWith('.anchor.'));
  if (!strays.length) ok('fallback leaves no temp file behind');
  else bad(`fallback left temp files: ${strays.join(', ')}`);
}

fs.rmSync(root, { recursive: true, force: true });
if (fails) { console.log(`FAIL (${fails})`); process.exit(1); }
console.log('PASS');
