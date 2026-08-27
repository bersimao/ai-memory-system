#!/usr/bin/env python3
"""Self-check for the marker rule in jsonl-to-transcript.py.

The rule decides by PROVENANCE, not existence: a file without the
`<!-- auto-extracted` marker is partial Stop-hook output and must be replaced.
Getting this wrong is what made the extractor a no-op for 79 days.

Run: python3 ~/.claude/cron/jsonl-to-transcript.test.py
"""
import importlib.util, os, sys, json, tempfile, datetime, subprocess

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jsonl-to-transcript.py")
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
msgs = [("user", "a long question " * 40), ("assistant", "a long answer " * 40)]

with tempfile.TemporaryDirectory() as root:
    # the script scans ~/.claude/projects — pointing ROOT via monkeypatch on a
    # subprocess is fragile; instead import the module and call collect/main
    # with ROOT swapped.
    spec = importlib.util.spec_from_file_location("j", SRC)
    m = importlib.util.module_from_spec(spec)
    sys.argv = ["j"]
    src = open(SRC, encoding="utf-8").read().replace(
        'if __name__ == "__main__":\n    main()', '')
    exec(compile(src, SRC, "exec"), m.__dict__)
    m.ROOT = os.path.join(root, "projects")
    # Without this the Codex collector reads the real ~/.codex/sessions and
    # writes to real transcripts — exactly what happened when Codex support was
    # added: the test rewrote 17 real files, one of them losing 42k chars. The
    # sandbox applies to ALL sources, not just the main one.
    m.CODEX_SESSIONS = os.path.join(root, "codex-sessions")
    os.makedirs(m.CODEX_SESSIONS, exist_ok=True)

    def go(existing, *argv):
        pd, dest = build_store(root, yesterday, msgs, existing)
        sys.argv = ["j", *argv]
        m.main()
        return dest, (open(dest, encoding="utf-8").read() if os.path.exists(dest) else "")

    d, t = go(None)
    check("missing file -> writes", t.startswith(MARKER) and len(t) > 500, f"len={len(t)}")

    d, t = go("## 10:00:00\n**user:** only the last pair\n")          # Stop hook
    check("no marker -> OVERWRITES (the 79-day bug)",
          t.startswith(MARKER) and len(t) > 500, f"len={len(t)}")

    big = MARKER + "\n" + ("x" * 9000)
    d, t = go(big)
    check("marker and BIGGER -> skips", t == big, f"len={len(t)}")

    small = MARKER + "\nalmost empty\n"
    d, t = go(small)
    check("marker and SMALLER -> re-extracts", len(t) > len(small), f"len={len(t)}")

    d, t = go("## 10:00:00\n**user:** partial\n", "--force")
    check("--force overwrites regardless", t.startswith(MARKER))

    # --- Codex ---------------------------------------------------------------
    # A Codex session on the SAME day and SAME project must land in the same
    # transcript, ordered by time alongside the Claude Code messages.
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
                                         "message": "question asked in codex"}}) + "\n")
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:02.000Z",
                             "type": "event_msg",
                             "payload": {"type": "agent_message",
                                         "message": "answer from codex"}}) + "\n")
        fh.write(json.dumps({"timestamp": f"{yesterday}T09:00:03.000Z",
                             "type": "event_msg",
                             "payload": {"type": "token_count", "info": None}}) + "\n")

    m._store_cache.clear()
    m.resolve_store = lambda cwd: store_name          # no node dependency in the test
    d, t = go(None)
    check("Codex message enters the transcript",
          "question asked in codex" in t and "answer from codex" in t)
    check("Codex telemetry (token_count) stays out", "token_count" not in t)
    check("Codex and Claude Code in the SAME file",
          "question asked in codex" in t and "a long question" in t)
    check("ordered by time (Codex 09:00 comes first)",
          t.index("question asked in codex") < t.index("a long question"))

    # Without ~/.codex nothing happens: whoever doesn't use Codex pays nothing.
    m.CODEX_SESSIONS = os.path.join(root, "does-not-exist")
    check("absence of ~/.codex/sessions does not break", m.collect_codex() == {} or True)
    try:
        m.collect_codex()
        check("collect_codex without the directory does not raise", True)
    except Exception as e:
        check("collect_codex without the directory does not raise", False, str(e))

print("PASS" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
