// SessionStart (async): takes transcript capture off anacron.
//
// Why it exists: `jsonl-to-transcript.py` was only reachable via distill.sh ->
// /etc/anacrontab (root, Linux-only) — the public-repo blocker. The trigger
// becomes an event; the script stays the same.
//
// It calls no LLM, so there is no recursion via `claude -p`. The throttle
// exists for another reason: a headless batch (backfill/distill open dozens of
// sessions) would fire one extraction each.
//
// CONCURRENCY: `statSync` followed by `writeFileSync` is check-then-act — two
// sessions opening together both pass the test before either stamps. Exclusion
// must come from an atomic operation: `open(..., 'wx')` fails if the file
// exists, and only one process wins.
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync } = require('child_process');

const home = os.homedir();
const dataDir = path.join(home, '.claude', 'data');
const stamp = path.join(dataDir, 'last-capture.stamp');
const lock = path.join(dataDir, 'capture.lock');
const THROTTLE_MS = 6 * 60 * 60 * 1000;
const STALE_LOCK_MS = 10 * 60 * 1000;   // the extractor has a 5-min timeout

try { fs.mkdirSync(dataDir, { recursive: true }); } catch { process.exit(0); }

// An orphaned lock (process died midway) would block capture forever.
try {
  if (Date.now() - fs.statSync(lock).mtimeMs > STALE_LOCK_MS) fs.unlinkSync(lock);
} catch { /* no lock: normal */ }

let fd;
try {
  fd = fs.openSync(lock, 'wx');          // atomic: only one process gets through
} catch {
  process.exit(0);                        // another session is already capturing
}

try {
  // Throttle INSIDE the lock — here the read is reliable.
  let recent = false;
  try { recent = Date.now() - fs.statSync(stamp).mtimeMs < THROTTLE_MS; } catch {}
  if (!recent) {
    fs.writeFileSync(stamp, new Date().toISOString());
    execFileSync('python3', [path.join(home, '.claude', 'cron', 'jsonl-to-transcript.py')],
      { stdio: 'ignore', timeout: 5 * 60 * 1000 });
  }
} catch {
  // fire and forget: never break session startup
} finally {
  try { fs.closeSync(fd); } catch {}
  try { fs.unlinkSync(lock); } catch {}
}

process.exit(0);
