// Stop hook: lembra o agente de escrever o daily log, uma vez por sessão.
//
// Por que existe: "Log silently as work happens" mora no CLAUDE.md e é
// INSTRUÇÃO DE PROMPT — não prende. Medido em 24/08: o agente escreveu 47% dos
// daily logs; o backfill (haiku, lendo o transcript inteiro) cobriu 52%.
// A memória global já registra o princípio: "SessionStart hook > instrução
// 'leia no início' — stdout do hook sempre entra no contexto". Isto é o mesmo,
// no outro extremo da sessão.
//
// SessionEnd NÃO serve: o agente já não pode agir, e o evento não dispara em
// crash nem em `wsl --shutdown`. Por isso Stop, que roda com a sessão viva.
//
// Só cutuca. Não escreve o log: resumir exige julgamento, e código
// determinístico só saberia despejar o transcript, que já existe.
//
// ENTREGA: `systemMessage` sozinho só EXIBE texto — o turno já acabou, então o
// agente vê e não age. Quem impede a parada e devolve o controle ao agente é
// `decision:"block"` + `reason`. Sem isso o hook parece instalado e não faz nada,
// exatamente a falha que este sistema existe para evitar.
//
// LOOP: um Stop bloqueado faz o agente continuar e parar de novo, disparando o
// hook outra vez. A flag é gravada ATOMICAMENTE (wx) ANTES de qualquer saída —
// se a gravação falhar, o hook desiste em vez de arriscar bloqueio infinito.
// `stop_hook_active` no input também marca reentrada e é respeitado.
const fs = require('fs');
const path = require('path');
const os = require('os');

const MIN_USER_TURNS = 8;   // abaixo disso a sessão não rendeu log

let input;
try { input = JSON.parse(fs.readFileSync(0, 'utf8')); } catch { process.exit(0); }

const transcriptPath = input.transcript_path;
const sessionId = input.session_id || 'nosession';
if (!transcriptPath || !fs.existsSync(transcriptPath)) process.exit(0);

// Já estamos dentro de um Stop bloqueado por hook? Então não bloquear de novo.
if (input.stop_hook_active) process.exit(0);

// Uma cutucada por sessão. Sem isto, o Stop dispara a cada turno e vira ruído.
const flag = path.join(os.tmpdir(), `claude-daily-log-nudge-${sessionId}`);

// Mesma âncora do memory-inject/transcript-capture: o repo, não o cwd.
let projectDir;
try {
  const { storeDir } = require('./project-store.js');
  projectDir = storeDir(input.cwd || process.cwd(), transcriptPath).dir;
} catch { process.exit(0); }

const localDate = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 6e4)
  .toISOString().slice(0, 10);
const today = localDate(new Date());
const dlog = path.join(projectDir, 'context', 'memory', `${today}.md`);

// Já existe log de hoje? Então nada a cutucar. (Se estiver parcial, o backfill
// agora estende — ver cron/backfill-daily-logs.sh.)
try { if (fs.statSync(dlog).size > 0) process.exit(0); } catch { /* não existe */ }

// A sessão rendeu o suficiente para valer um log?
let userTurns = 0;
try {
  const lines = fs.readFileSync(transcriptPath, 'utf8').split('\n');
  for (const l of lines) {
    if (!l) continue;
    let ev; try { ev = JSON.parse(l); } catch { continue; }
    if (ev.type === 'user' && ev.message && !ev.isSidechain) userTurns++;
  }
} catch { process.exit(0); }
if (userTurns < MIN_USER_TURNS) process.exit(0);

// Reserva atômica: quem criar o arquivo cutuca; qualquer reentrada sai calada.
// Se não der para reservar, NÃO bloquear — bloqueio sem trava vira loop.
try {
  fs.closeSync(fs.openSync(flag, 'wx'));
} catch {
  process.exit(0);
}

const msg = `[daily-log] This session has ${userTurns} user turns and no daily log ` +
  `for ${today}. Before ending, write ${dlog} using "#### Session N" with ` +
  `Goal/Deliverables/Decisions/Open threads. Record the REASONING behind decisions ` +
  `(the measurement taken, the alternative rejected, the cause diagnosed) — the what ` +
  `is cheap to recover later, the why is not. Do not announce that you logged it.`;

process.stdout.write(JSON.stringify({ decision: 'block', reason: msg, systemMessage: msg }));
process.exit(0);
