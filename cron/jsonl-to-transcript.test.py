#!/usr/bin/env python3
"""Self-check for the marker rule in jsonl-to-transcript.py.

The rule decides by PROVENANCE, not existence: a file without the
`<!-- auto-extracted` marker is partial Stop-hook output and must be replaced.
Getting this wrong is what made the extractor a no-op for 79 days.

Run: python3 ~/.claude/cron/jsonl-to-transcript.test.py
"""
import importlib.util, os, sys, json, tempfile, datetime, subprocess

SRC = os.path.expanduser("~/.claude/cron/jsonl-to-transcript.py")
fails = []

def build_store(root, day, msgs, existing=None):
    pd = os.path.join(root, "projects", "store")
    os.makedirs(pd, exist_ok=True)
    with open(os.path.join(pd, "s.jsonl"), "w", encoding="utf-8") as f:
        for i, (role, text) in enumerate(msgs):
            f.write(json.dumps({"type": role, "message": {"role": role, "content": text},
                                "timestamp": f"{day}T1{i}:00:00+00:00"}) + "\n")
    tdir = os.path.join(pd, "context", "transcripts")
    os.makedirs(tdir, exist_ok=True)
    dest = os.path.join(tdir, f"{day}.md")
    if existing is not None:
        open(dest, "w", encoding="utf-8").write(existing)
    elif os.path.exists(dest):
        os.remove(dest)
    return pd, dest

def run(root, *args):
    env = dict(os.environ)
    out = subprocess.run([sys.executable, SRC, *args], capture_output=True, text=True,
                         env=env, cwd=root)
    return out.stdout + out.stderr

def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: fails.append(name)

MARKER = "<!-- auto-extracted from native session jsonl -->"
yesterday = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
msgs = [("user", "pergunta longa " * 40), ("assistant", "resposta longa " * 40)]

with tempfile.TemporaryDirectory() as root:
    # o script varre ~/.claude/projects — apontar ROOT via monkeypatch é frágil;
    # em vez disso importa o módulo e chama collect/main com ROOT trocado.
    spec = importlib.util.spec_from_file_location("j", SRC)
    m = importlib.util.module_from_spec(spec)
    sys.argv = ["j"]
    src = open(SRC, encoding="utf-8").read().replace(
        'if __name__ == "__main__":\n    main()', '')
    exec(compile(src, SRC, "exec"), m.__dict__)
    m.ROOT = os.path.join(root, "projects")
    # Sem isto o coletor do Codex le ~/.codex/sessions de verdade e escreve em
    # transcript de verdade — foi exatamente o que aconteceu ao adicionar o
    # suporte a Codex: o teste reescreveu 17 arquivos reais, um deles perdendo
    # 42k chars. Sandbox vale para TODAS as origens, nao so a principal.
    m.CODEX_SESSIONS = os.path.join(root, "codex-sessions")
    os.makedirs(m.CODEX_SESSIONS, exist_ok=True)

    def go(existing, *argv):
        pd, dest = build_store(root, yesterday, msgs, existing)
        sys.argv = ["j", *argv]
        m.main()
        return dest, (open(dest, encoding="utf-8").read() if os.path.exists(dest) else "")

    d, t = go(None)
    check("arquivo ausente -> escreve", t.startswith(MARKER) and len(t) > 500, f"len={len(t)}")

    d, t = go("## 10:00:00\n**user:** so o ultimo par\n")          # Stop hook
    check("sem marker -> SOBRESCREVE (era o bug dos 79 dias)",
          t.startswith(MARKER) and len(t) > 500, f"len={len(t)}")

    big = MARKER + "\n" + ("x" * 9000)
    d, t = go(big)
    check("com marker e MAIOR -> pula", t == big, f"len={len(t)}")

    small = MARKER + "\nquase vazio\n"
    d, t = go(small)
    check("com marker e MENOR -> re-extrai", len(t) > len(small), f"len={len(t)}")

    d, t = go("## 10:00:00\n**user:** parcial\n", "--force")
    check("--force sobrescreve de qualquer forma", t.startswith(MARKER))

    # --- Codex ---------------------------------------------------------------
    # Uma sessao do Codex no MESMO dia e MESMO projeto tem que entrar no mesmo
    # transcript, ordenada por horario junto das mensagens do Claude Code.
    store_name = os.path.basename(os.path.dirname(os.path.dirname(
        os.path.dirname(build_store(root, yesterday, msgs, None)[1]))))
    day_dir = os.path.join(m.CODEX_SESSIONS, "2026", "01", "01")
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "rollout-x.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:00.000Z",
                             "type": "session_meta",
                             "payload": {"id": "s1", "cwd": "/fake/repo"}}) + "\n")
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:01.000Z",
                             "type": "event_msg",
                             "payload": {"type": "user_message",
                                         "message": "pergunta feita no codex"}}) + "\n")
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:02.000Z",
                             "type": "event_msg",
                             "payload": {"type": "agent_message",
                                         "message": "resposta do codex"}}) + "\n")
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:03.000Z",
                             "type": "event_msg",
                             "payload": {"type": "token_count", "info": None}}) + "\n")

    m._store_cache.clear()
    m.resolve_store = lambda cwd: store_name          # nao depende do node no teste
    d, t = go(None)
    check("mensagem do Codex entra no transcript",
          "pergunta feita no codex" in t and "resposta do codex" in t)
    check("telemetria do Codex (token_count) fica de fora", "token_count" not in t)
    check("Codex e Claude Code no MESMO arquivo",
          "pergunta feita no codex" in t and "pergunta longa" in t)
    check("ordenado por horario (Codex 09:00 vem antes)",
          t.index("pergunta feita no codex") < t.index("pergunta longa"))

    # Sem ~/.codex nada acontece: quem nao usa Codex nao paga nada.
    m.CODEX_SESSIONS = os.path.join(root, "nao-existe")
    check("ausencia de ~/.codex/sessions nao quebra", m.collect_codex() == {} or True)
    try:
        m.collect_codex()
        check("collect_codex sem diretorio nao levanta", True)
    except Exception as e:
        check("collect_codex sem diretorio nao levanta", False, str(e))

print("PASS" if not fails else f"FALHOU: {len(fails)}")
sys.exit(1 if fails else 0)
