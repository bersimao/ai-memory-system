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

const { storeDir, demandsFor, encodeProjectPath } = require('./project-store.js');

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
  // Two shapes reach here, and name similarity only catches the first:
  //   1. RENAME  — same project, directory renamed, names stay close.
  //   2. SIBLING — same project, second repo alongside the first, names
  //      deliberately DIFFERENT (claude-mem / ai-memory-system scored 66.7%,
  //      well under the 0.85 bar, so this went undetected on 2026-08-25).
  // A sibling is detected structurally instead: its store name starts with the
  // encoded parent directory. No decoding needed — the encoding is lossy, but
  // it is a prefix, so `~/ai/x` and `~/ai/y` share `-home-<user>-ai-`.
  const parentPrefix = encodeProjectPath(path.dirname(anchor.dir)) + '-';
  let best = null;
  for (const n of names) {
    if (n === self) continue;
    const c = path.join(projects, n, 'context');
    if (storeIsEmpty(c)) continue;
    const sim = similarity(n, self);
    const sibling = parentPrefix.length > 1 && n.startsWith(parentPrefix);
    if ((sim < 0.85 && !sibling) || (best && sim <= best.sim)) continue;
    let last = 0;
    for (const sub of ['MEMORY.md', 'memory', 'transcripts']) {
      try { last = Math.max(last, fs.statSync(path.join(c, sub)).mtimeMs); } catch {}
    }
    best = { n, sim, last, sibling };
  }
  if (!best) return '';
  const how = best.sibling && best.sim < 0.85
    ? 'a store for a neighbouring directory has content'
    : 'a similar store has content';
  return `This project's memory store is empty, but ${how}:\n` +
    `- \`${path.join(projects, best.n)}\`\n` +
    `- similarity ${(best.sim * 100).toFixed(0)}%, last activity ${new Date(best.last).toISOString().slice(0, 10)}\n` +
    'If the project directory was RENAMED, that store is this project\'s orphaned memory — ' +
    'tell the user and offer to merge its context/ into this store.\n' +
    'If this is a SECOND REPO of the same project (source + published, app + infra), ' +
    'the two should share one store — tell the user and offer to pin it:\n' +
    `\`node ~/.claude/hooks/project-store.js --pin <anchor> --for ${anchor.dir}\`\n` +
    'Never pin without asking: guessing that two directories are one project and ' +
    'merging their memory is a worse error than leaving them apart. ' +
    'If this is a genuinely new project that merely sits next door, ignore this.';
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

// ORDER IS THE TRUNCATION POLICY. The harness persists a hook's stdout to a
// file and injects only the first ~2KB when the output exceeds ~10KB (smallest
// persisted output observed: 10,463 bytes). Whatever is emitted LAST is what
// the session silently loses. Global layers are reconstructible — they are the
// same in every session, and USER.md/MEMORY.md are two grep-able paths. The
// project layer is not: it is the only part that is unique to this session, and
// it was third in line until 2026-09-09, which meant every store whose snapshot
// crossed the threshold started sessions blind to its own project memory.
// So: project-specific first, global last. Keeping the total under ~10KB is
// still the actual fix (see cron/check-caps.sh) — this only decides what
// survives when it is not.
add('Possible renamed project — orphaned store?', renameWarning());
add('Project store anchor', anchorSection());

add('Project MEMORY.md', read(path.join(ctx, 'MEMORY.md')));
add('Demands on this project (auto, from demands.json)', demandsSection());

// SNAP_TRUNC_PROVEN / SNAP_BUDGET: see the truncation-policy comment below,
// where the byte math against them is done. Declared here because the
// transcript fallback (next block) also needs to size itself against the
// budget before the truncation check runs.
const SNAP_TRUNC_PROVEN = 10463;
const SNAP_BUDGET = 10000;

// Crude, line-oriented secret redaction — not a real scanner, just enough to
// stop the obvious cases (a pasted token, an `export TOKEN=...`) from being
// echoed back into every session that hits this fallback. Ceiling: anything
// not matching one of these shapes still gets through. Upgrade path: run the
// existing `security-review` secret patterns here if this keeps missing.
//
// Two bugs fixed 2026-09-10: the generic KEY/TOKEN/... rule used `\w*` right
// after the keyword, which matches into an ordinary word that merely starts
// with one — "secretaria"/"secretária" (PT-BR "secretary") both start with
// "secret", so "área de secretaria: administrativa" was getting its back half
// redacted as if "secretaria" were a variable name. Real env-var suffixes are
// separator-joined (`AUTH_TOKEN_V2`, `API_KEY_2`) — requiring the separator
// keeps those while refusing to swallow a plain continuation of the same
// word. Second, coverage: the prefix list only caught GitLab/GitHub/OpenAI/
// Slack-shaped tokens and missed the other common shapes entirely — a JWT, a
// PEM private key block, and a password embedded in a connection URL
// (`postgres://user:pass@host`) — now covered too.
const redactSecrets = (text) => text
  .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED-key]')
  .replace(/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, '[REDACTED-jwt]')
  .replace(/\b(gl|gh[pousr]|sk|pk|rk|xox[baprs]|npm)[a-zA-Z]*[_-][A-Za-z0-9_.-]{10,}/g, '[REDACTED]')
  .replace(/\bAKIA[0-9A-Z]{16}\b/g, '[REDACTED]')
  .replace(/\bBearer\s+[A-Za-z0-9._-]{10,}/g, 'Bearer [REDACTED]')
  // Password class deliberately allows '@' (greedy + backtrack lands on the
  // LAST '@' before the next '/' or space, i.e. the real host separator) — a
  // password containing '@' otherwise truncated the match at that first '@'
  // and leaked the rest of the password after it.
  .replace(/(:\/\/[^/\s:@]+):[^/\s]+@/g, '$1:[REDACTED]@')
  .replace(/((?:TOKEN|SECRET|PASSWORD|API_KEY|APIKEY)(?:[_-][A-Za-z0-9]+)*\s*[=:]\s*)\S+/gi, '$1[REDACTED]');

const today = new Date();
const yesterday = new Date(today.getTime() - 86400000);
const todayLog = read(path.join(ctx, 'memory', `${localDate(today)}.md`));
add(`Daily log ${localDate(today)}`, todayLog);
if (!todayLog) {
  // Today's curated log only exists once the nudge fires (8+ real turns, at a
  // real Stop) or backfill runs (overnight, skips "today" on purpose) — so a
  // same-day /clear before either has a chance can wipe visible context with
  // nothing curated to reload, even though transcript-capture.js already wrote
  // every turn to disk, unconditionally, all along. Tail of that raw transcript
  // is the cheap, deterministic fallback: no LLM distillation needed, and it is
  // exactly the part a same-day /clear needs back.
  //
  // Two risks a curated log doesn't have, because a human/LLM summary never
  // reproduces either verbatim: (1) raw transcript can carry secrets typed or
  // echoed into the session (redacted above, best-effort); (2) it is much
  // longer per byte of signal than a curated summary, so a fixed cap here was
  // pushing otherwise-small snapshots over SNAP_TRUNC_PROVEN. Budget it
  // dynamically against what the rest of the snapshot (including the two
  // global sections still to come) is already using instead.
  const userMd = read(path.join(home, '.claude', 'context', 'USER.md'));
  const globalMd = read(path.join(home, '.claude', 'context', 'MEMORY.md'));
  const usedSoFar = Buffer.byteLength(parts.join('\n\n'), 'utf8') +
    Buffer.byteLength(userMd, 'utf8') + Buffer.byteLength(globalMd, 'utf8');
  const SAFETY_MARGIN = 1500; // headers, labels, the redaction/truncation markers themselves
  const transcriptBudget = Math.max(0, SNAP_BUDGET - usedSoFar - SAFETY_MARGIN);

  const rawTranscript = read(path.join(ctx, 'transcripts', `${localDate(today)}.md`));
  if (rawTranscript && transcriptBudget > 200) {
    const clean = redactSecrets(rawTranscript);
    // transcriptBudget is a BYTE count; .slice() counts UTF-16 code units.
    // Portuguese text (á/ã/ç) and the marker below are multi-byte in UTF-8, so
    // slicing by character count let the real byte size run past the budget
    // this was computed to respect. Slice the UTF-8 buffer itself instead —
    // toString('utf8') replaces a chopped leading byte with U+FFFD, which is
    // fine for a diagnostic fallback.
    const buf = Buffer.from(clean, 'utf8');
    const tail = buf.length > transcriptBudget
      ? '…(truncated)…\n' + buf.slice(-transcriptBudget).toString('utf8')
      : clean;
    add(`Today's transcript ${localDate(today)} (raw, no curated log yet)`, tail);
  } else {
    add(`Daily log ${localDate(yesterday)}`, read(path.join(ctx, 'memory', `${localDate(yesterday)}.md`)));
  }

  add('Global USER.md', userMd);
  add('Global MEMORY.md', globalMd);
} else {
  add('Global USER.md', read(path.join(home, '.claude', 'context', 'USER.md')));
  add('Global MEMORY.md', read(path.join(home, '.claude', 'context', 'MEMORY.md')));
}

// Self-measured truncation warning.
//
// The harness persists a hook's stdout to a file and injects only the first
// ~2KB of it once the output crosses ~10KB. It says so — "Output too large …
// Full output saved to <path>" — but the session has to NOTICE, and on
// 2026-09-09 one did not: it asserted a fact was "in context" because the hook
// injects project MEMORY.md, when that section had been cut away.
//
// This is the only place the size can be known exactly. Anything downstream
// (see cron/check-caps.sh) has to re-derive it by summing source files, which
// misses this framing and cannot resolve a store's anchor back from its encoded
// directory name — `-home-user-ai-claude-mem` is equally `.../ai/claude-mem`
// and `.../ai/claude/mem`. So the sweep screens; this measures.
//
// Two different numbers, and conflating them makes the warning self-defeating.
//
// SNAP_TRUNC_PROVEN — the smallest hook stdout observed to have been persisted
//   and truncated, across 379 samples: 10,463B. At or above this, truncation is
//   a FACT. The real threshold is somewhere <= this; the largest surviving
//   output was never observed, so the band below is genuinely unknown.
// SNAP_BUDGET — the "stay under this" line the sweep screens against (10,000).
//   Conservative on purpose, which is exactly why it must NOT gate this warning.
//
// The warning costs ~430B. Firing it in the unknown band (BUDGET..PROVEN) could
// push a snapshot that the harness would have injected INTACT past the real
// limit — manufacturing the truncation it announces, and announcing it falsely.
// A diagnostic that causes the fault it reports is worse than no diagnostic, so
// it fires only where truncation is already certain and its bytes change
// nothing. The unknown band is left to cron/check-caps.sh, which runs out of
// band and cannot perturb what it measures.
if (parts.length) {
  const header = '## Memory snapshot (session startup)\n' +
    'Frozen snapshot loaded once. Mid-session writes take effect next session.\n\n';
  let out = header + parts.join('\n\n') + '\n';
  const size = Buffer.byteLength(out, 'utf8');

  if (size >= SNAP_TRUNC_PROVEN) {
    // Goes FIRST, so it survives the truncation it is reporting. Naming the
    // biggest section makes the fix actionable instead of "trim something".
    const biggest = parts
      .map((p) => ({ label: p.slice(4, p.indexOf('\n')), bytes: Buffer.byteLength(p, 'utf8') }))
      .sort((a, b) => b.bytes - a.bytes)[0];
    out = header +
      `### ⚠ Snapshot truncated — sections below are missing\n` +
      `${size}B of content, past the ${SNAP_TRUNC_PROVEN}B point where the harness injects ` +
      `only the first ~2KB and saves the rest to the file named in the ` +
      `"Output too large" notice. Do NOT treat a missing section as absent memory: ` +
      `read that file before concluding anything is not recorded.\n` +
      `Biggest section: ${biggest.label} (${biggest.bytes}B), budget ${SNAP_BUDGET}B. ` +
      `Trim it, or run \`~/.claude/cron/check-caps.sh\`.\n\n` +
      parts.join('\n\n') + '\n';
  }

  process.stdout.write(out);
}
