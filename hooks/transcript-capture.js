const fs = require('fs');
const path = require('path');

// Stop hook: append this turn's exchange (user prompt + assistant reply) to a
// daily transcript file in the central project context store at
//   ~/.claude/projects/<encoded-repo-anchor>/context/transcripts/
// Auto-created on demand — every project captures by default; no opt-in required.
//
// 2026-08-20: was assistant-only, first 500 chars. Measured against one real
// session: 5,740 of 70,642 chars kept (8%) and 0% of the user side — so the
// nightly distill rebuilt memory from a skeleton containing none of the user's
// own decisions. Both roles now, with a bigger slice.

// Per-message cap. Transcripts feed backfill-daily-logs.sh and the L3 index, so
// they need to be readable, not complete — the raw .jsonl is the complete record,
// until Claude Code prunes it at ~30 days.
// ponytail: flat per-message cap, no per-day budget. A very long day grows the
// file linearly; add a daily cap if the L3 index starts feeling it.
const MAX_CHARS = 1500;

// Harness chatter, not conversation: local-command echoes and injected context.
// Distilling these yields a daily log of "unclear" — learned the same day from
// cron/jsonl-to-transcript.py, whose first extraction was 100% /model noise.
const NOISE = [
  '<system-reminder>', '<command-name>', '<command-message>', '<command-args>',
  '<local-command-stdout>', '<local-command-caveat>', '<user-prompt-submit-hook>',
];

let raw = '';
try { raw = fs.readFileSync(0, 'utf8'); } catch { process.exit(0); }

let input;
try { input = JSON.parse(raw); } catch { process.exit(0); }

const cwd = input.cwd || process.env.CLAUDE_PROJECT_DIR || '.';

const transcriptPath = input.transcript_path;
if (!transcriptPath || !fs.existsSync(transcriptPath)) process.exit(0);

// content is either a plain string or an array of blocks; only `text` blocks are
// conversation (tool_use / tool_result / thinking are not).
const extractText = (content) => {
  if (typeof content === 'string') return content.trim();
  if (!Array.isArray(content)) return '';
  return content
    .filter(c => c && c.type === 'text' && c.text)
    .map(c => c.text)
    .join('\n')
    .trim();
};

const isNoise = (t) => NOISE.some(p => t.startsWith(p));

// Walk backwards: the assistant reply comes first, then the user prompt that
// triggered it — so requiring the assistant before accepting a user message
// pairs each prompt with its own reply.
let lastAssistant = '';
let lastUser = '';
try {
  const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n').filter(Boolean);
  for (let i = lines.length - 1; i >= 0 && !(lastAssistant && lastUser); i--) {
    let ev;
    try { ev = JSON.parse(lines[i]); } catch { continue; }
    if (ev.isSidechain || !ev.message) continue;
    const role = ev.message.role;
    const text = extractText(ev.message.content);
    if (!text || isNoise(text)) continue;
    if (ev.type === 'assistant' && role === 'assistant' && !lastAssistant) lastAssistant = text;
    else if (ev.type === 'user' && role === 'user' && !lastUser && lastAssistant) lastUser = text;
  }
} catch { process.exit(0); }

if (!lastAssistant) process.exit(0);

// Same anchor as memory-inject.js — the repo, not the cwd (see project-store.js).
// Both hooks must land in the same store or the Stop hook writes transcripts the
// next SessionStart will never read.
const { storeDir } = require('./project-store.js');
const { dir: projectDir } = storeDir(cwd, transcriptPath);

const dir = path.join(projectDir, 'context', 'transcripts');
const today = new Date().toISOString().slice(0, 10);
const file = path.join(dir, `${today}.md`);

try {
  fs.mkdirSync(dir, { recursive: true });
  const clip = (s) => s.slice(0, MAX_CHARS).replace(/\n{3,}/g, '\n\n');
  const timestamp = new Date().toTimeString().slice(0, 8);
  let out = `\n## ${timestamp}\n`;
  if (lastUser) out += `**user:** ${clip(lastUser)}\n\n`;
  out += `**assistant:** ${clip(lastAssistant)}\n`;
  fs.appendFileSync(file, out);
} catch {
  // Fire and forget — don't break the session.
}
