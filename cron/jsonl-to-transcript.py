#!/usr/bin/env python3
"""Fill missing transcript files from Claude Code's own session .jsonl.

Why: transcript-capture.js (Stop hook) only records the LAST assistant message
per session stop, and only while it is installed and firing. Days where it never
ran leave no transcript, so backfill-daily-logs.sh has nothing to distill and the
day vanishes from memory. Claude Code's native .jsonl holds those days — but only
for its own retention window (~30 days), so this is worth running daily, not once.

Deliberately does NOT call an LLM: it writes transcripts in the existing format
and lets backfill-daily-logs.sh (already scheduled) turn them into daily logs.
One pipeline, not two.

Never overwrites a real transcript. Skips today (still accumulating).

--force overwrites existing transcripts instead of skipping them. Needed once:
the Stop hook (transcript-capture.js) wrote a partial file for every day, and
that file is exactly what the skip-guard below protects — so this script has
never written a single day. Transcripts are versioned in git, so a forced run
is revertible; run it on a clean tree.
"""
import json, os, glob, sys, datetime, collections, subprocess

ROOT = os.path.expanduser("~/.claude/projects")
# Codex grava rollouts em ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, e o cwd
# vive no session_meta em vez de no nome do diretorio. Mesmo projeto, mesma
# memoria: os dois agentes escrevem no MESMO transcript do dia, ordenados por
# timestamp. Ausente = nada acontece; ninguem precisa ter Codex instalado.
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
STORE_RESOLVER = os.path.expanduser("~/.claude/hooks/project-store.js")
TODAY = datetime.date.today().isoformat()
# ponytail: flat per-day cap. A day that overflows loses its tail rather than
# bloating the L3 index; raise if truncation markers start showing up often.
# 2026-08-23: raised 40k -> 250k. Measured across the 79 days still held in the
# native .jsonl: p50 24k, p90 112k, max 220k — the old cap truncated 30 of them.
MAX_CHARS = 250_000
# Assinatura da própria saída. Arquivo SEM ela veio do Stop hook
# (transcript-capture.js), que grava só o último par user/assistant por parada —
# medido em 8-43% do dia. Enquanto o skip-guard protegia esse parcial, a extração
# completa nunca rodava: 79 dias pulados, 0 escritos, desde que o script existe.
MARKER = "<!-- auto-extracted"
SKIP_TYPES = {"attachment", "last-prompt", "queue-operation", "system"}
NOISE_PREFIXES = (
    "<system-reminder>", "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-caveat>", "<user-prompt-submit-hook>",
)


def blocks_to_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(str(b.get("text", "")))
    return "\n".join(out)


def collect(project_dir):
    """-> {day: [(timestamp, role, text)]}"""
    days = collections.defaultdict(list)
    for j in glob.glob(os.path.join(project_dir, "*.jsonl")):
        try:
            fh = open(j, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") in SKIP_TYPES or e.get("isSidechain"):
                    continue
                msg = e.get("message") or {}
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                ts = e.get("timestamp")
                if not ts:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                except Exception:
                    continue
                text = blocks_to_text(msg.get("content")).strip()
                # Harness chatter, not conversation. Local-command echoes (/model,
                # /clear, their stdout and caveat banners) made up 100% of some days'
                # extraction — distilling those produces a daily log of "unclear".
                if not text or text.startswith(NOISE_PREFIXES):
                    continue
                days[dt.date().isoformat()].append((dt, role, text))
    return days


_store_cache = {}


def resolve_store(cwd):
    """cwd -> NOME do store (basename), nao caminho absoluto.

    Delega a ancoragem ao project-store.js de proposito: a ordem e
    pin -> git root -> cwd, e reimplementar isso aqui em Python criaria duas
    verdades que divergem no primeiro pin.

    Devolve so o basename porque quem chama junta com ROOT. Devolver o caminho
    absoluto furava o sandbox dos testes (que trocam ROOT por um tmpdir) e
    fazia o teste escrever em transcript de verdade."""
    if cwd in _store_cache:
        return _store_cache[cwd]
    out = None
    if os.path.isdir(cwd) and os.path.exists(STORE_RESOLVER):
        try:
            r = subprocess.run(["node", STORE_RESOLVER, "--resolve", cwd],
                               capture_output=True, text=True, timeout=20)
            for line in r.stdout.splitlines():
                if line.startswith("store:"):
                    out = os.path.basename(line.split(":", 1)[1].strip())
                    break
        except Exception:
            out = None
    _store_cache[cwd] = out
    return out


def collect_codex():
    """-> {store_dir: {day: [(timestamp, role, text)]}}"""
    days = collections.defaultdict(lambda: collections.defaultdict(list))
    if not os.path.isdir(CODEX_SESSIONS):
        return days
    for j in glob.glob(os.path.join(CODEX_SESSIONS, "**", "*.jsonl"), recursive=True):
        cwd, entries = None, []
        try:
            fh = open(j, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                payload = e.get("payload") or {}
                if e.get("type") == "session_meta":
                    cwd = payload.get("cwd")
                    continue
                # So user_message/agent_message: o resto e telemetria, tool call
                # e reasoning. Mesmo criterio do lado Claude Code (user+assistant).
                kind = payload.get("type")
                if kind == "user_message":
                    role = "user"
                elif kind == "agent_message":
                    role = "assistant"
                else:
                    continue
                text = str(payload.get("message") or "").strip()
                if not text or text.startswith(NOISE_PREFIXES):
                    continue
                ts = e.get("timestamp")
                if not ts:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(
                        ts.replace("Z", "+00:00")).astimezone()
                except Exception:
                    continue
                entries.append((dt, role, text))
        if not cwd or not entries:
            continue
        # Sessao rodada no Windows (cwd "C:\...") nao resolve aqui; resolve_store
        # devolve None e o dia e ignorado em vez de virar um store de lixo.
        name = resolve_store(cwd)
        if not name:
            continue
        store = os.path.join(ROOT, name)
        for dt, role, text in entries:
            days[store][dt.date().isoformat()].append((dt, role, text))
    return days


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    written = skipped = 0

    # Une as duas origens ANTES de escrever. Um dia em que se trabalhou nos dois
    # agentes vira UM transcript, ordenado por timestamp — nao dois arquivos
    # concorrendo pelo mesmo nome, nem o segundo sobrescrevendo o primeiro.
    merged = collections.defaultdict(lambda: collections.defaultdict(list))
    for pd in sorted(glob.glob(os.path.join(ROOT, "*"))):
        if not os.path.isdir(pd):
            continue
        for day, entries in collect(pd).items():
            merged[pd][day].extend(entries)
    for store, by_day in collect_codex().items():
        for day, entries in by_day.items():
            merged[store][day].extend(entries)

    for pd in sorted(merged):
        tdir = os.path.join(pd, "context", "transcripts")
        for day, entries in sorted(merged[pd].items()):
            if day == TODAY:
                continue
            dest = os.path.join(tdir, f"{day}.md")
            old = ""
            if os.path.exists(dest):
                try:
                    with open(dest, encoding="utf-8", errors="replace") as fh:
                        old = fh.read()
                except OSError:
                    old = ""
            if not entries:
                continue
            entries.sort(key=lambda r: r[0])
            parts, total, cut = ["<!-- auto-extracted from native session jsonl -->"], 0, False
            # O MARKER (prefixo) tem que continuar identico: a regra de
            # procedencia em main() depende dele para distinguir extracao
            # completa de parcial do Stop hook.
            for dt, role, text in entries:
                chunk = f"\n## {dt.strftime('%H:%M:%S')}\n**{role}:** {text}"
                if total + len(chunk) > MAX_CHARS:
                    cut = True
                    break
                parts.append(chunk)
                total += len(chunk)
            if cut:
                parts.append(f"\n<!-- truncated at {MAX_CHARS} chars -->")
            new_text = "\n".join(parts) + "\n"

            # Decide pela PROCEDÊNCIA do arquivo, não pela sua existência:
            #  - sem marker            -> parcial do Stop hook: sobrescrever
            #  - com marker e >= atual -> nada a ganhar: pular
            #  - com marker e MENOR    -> extração anterior pegou o dia pela metade
            #    (sessão cruzando a meia-noite, .jsonl ainda crescendo): refazer
            if old and not force and old.startswith(MARKER) and len(new_text) <= len(old):
                skipped += 1
                continue
            prev = len(old)
            delta = f"{prev:,} -> {len(new_text):,}" if prev else f"{len(new_text):,}"
            why = "" if not prev else (" [sem marker]" if not old.startswith(MARKER) else " [re-extracao]")
            print(f"{'would write' if dry else 'write'} {dest}  ({delta} chars, {len(entries)} msgs){why}")
            if not dry:
                os.makedirs(tdir, exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(new_text)
            written += 1
    print(f"\n{'would write' if dry else 'wrote'}: {written}   already had a transcript: {skipped}")


if __name__ == "__main__":
    main()
