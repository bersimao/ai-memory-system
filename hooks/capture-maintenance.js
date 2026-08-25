// SessionStart (async): tira a captura de transcript do anacron.
//
// Por que existe: `jsonl-to-transcript.py` só era alcançável via distill.sh ->
// /etc/anacrontab (root, Linux-only) — o bloqueio do repo público. O trigger
// vira um evento; o script continua o mesmo.
//
// Não chama LLM, logo não há recursão via `claude -p`. O throttle existe por
// outro motivo: um lote headless (backfill/distill abrem dezenas de sessões)
// dispararia uma extração cada.
//
// CONCORRÊNCIA: `statSync` seguido de `writeFileSync` é check-then-act — duas
// sessões abrindo juntas passam as duas pelo teste antes de qualquer uma
// carimbar. A exclusão tem que vir de uma operação atômica: `open(..., 'wx')`
// falha se o arquivo existir, e só um processo ganha.
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync } = require('child_process');

const home = os.homedir();
const dataDir = path.join(home, '.claude', 'data');
const stamp = path.join(dataDir, 'last-capture.stamp');
const lock = path.join(dataDir, 'capture.lock');
const THROTTLE_MS = 6 * 60 * 60 * 1000;
const STALE_LOCK_MS = 10 * 60 * 1000;   // extractor tem timeout de 5 min

try { fs.mkdirSync(dataDir, { recursive: true }); } catch { process.exit(0); }

// Lock órfão (processo morto no meio) travaria a captura para sempre.
try {
  if (Date.now() - fs.statSync(lock).mtimeMs > STALE_LOCK_MS) fs.unlinkSync(lock);
} catch { /* sem lock: normal */ }

let fd;
try {
  fd = fs.openSync(lock, 'wx');          // atômico: só um processo passa
} catch {
  process.exit(0);                        // outra sessão já está capturando
}

try {
  // Throttle DENTRO do lock — aqui a leitura é confiável.
  let recent = false;
  try { recent = Date.now() - fs.statSync(stamp).mtimeMs < THROTTLE_MS; } catch {}
  if (!recent) {
    fs.writeFileSync(stamp, new Date().toISOString());
    execFileSync('python3', [path.join(home, '.claude', 'cron', 'jsonl-to-transcript.py')],
      { stdio: 'ignore', timeout: 5 * 60 * 1000 });
  }
} catch {
  // fire and forget: nunca quebrar o início de sessão
} finally {
  try { fs.closeSync(fd); } catch {}
  try { fs.unlinkSync(lock); } catch {}
}

process.exit(0);
