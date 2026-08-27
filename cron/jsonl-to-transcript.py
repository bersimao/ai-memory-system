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
# Codex writes rollouts to ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, and
# the cwd lives in session_meta rather than in the directory name. Same
# project, same memory: both agents write to the SAME day transcript, ordered
# by timestamp. Absent = nothing happens; nobody needs Codex installed.
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
STORE_RESOLVER = os.path.expanduser("~/.claude/hooks/project-store.js")
TODAY = datetime.date.today().isoformat()
# ponytail: flat per-day cap. A day that overflows loses its tail rather than
# bloating the L3 index; raise if truncation markers start showing up often.
# 2026-08-23: raised 40k -> 250k. Measured across the 79 days still held in the
# native .jsonl: p50 24k, p90 112k, max 220k — the old cap truncated 30 of them.
MAX_CHARS = 250_000
# Signature of this script's own output. A file WITHOUT it came from the Stop
# hook (transcript-capture.js), which records only the last user/assistant
# pair per stop — measured at 8-43% of the day. While the skip-guard protected
# that partial, the complete extraction never ran: 79 days skipped, 0 written,
# for as long as the script existed.
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
    """cwd -> store NAME (basename), not an absolute path.

    Anchoring is delegated to project-store.js on purpose: the order is
    pin -> git root -> cwd, and reimplementing that here in Python would
    create two truths that diverge at the first pin.

    Returns only the basename because the caller joins it with ROOT.
    Returning the absolute path punched through the test sandbox (which swaps
    ROOT for a tmpdir) and made the test write to a real transcript."""
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
                # Only user_message/agent_message: the rest is telemetry, tool
                # calls and reasoning. Same criterion as the Claude Code side
                # (user+assistant).
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
        # A session run on Windows (cwd "C:\...") does not resolve here;
        # resolve_store returns None and the day is ignored instead of
        # becoming a junk store.
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

    # Merge the two sources BEFORE writing. A day worked in both agents
    # becomes ONE transcript, ordered by timestamp — not two files competing
    # for the same name, nor the second overwriting the first.
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
            # The MARKER (prefix) must stay identical: the provenance rule in
            # main() depends on it to tell a complete extraction from a Stop
            # hook partial.
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

            # Decide by the file's PROVENANCE, not its existence:
            #  - no marker               -> Stop hook partial: overwrite
            #  - marker and >= current   -> nothing to gain: skip
            #  - marker and SMALLER      -> the previous extraction caught half
            #    the day (session crossing midnight, .jsonl still growing): redo
            if old and not force and old.startswith(MARKER) and len(new_text) <= len(old):
                skipped += 1
                continue
            prev = len(old)
            delta = f"{prev:,} -> {len(new_text):,}" if prev else f"{len(new_text):,}"
            why = "" if not prev else (" [no marker]" if not old.startswith(MARKER) else " [re-extraction]")
            print(f"{'would write' if dry else 'write'} {dest}  ({delta} chars, {len(entries)} msgs){why}")
            if not dry:
                os.makedirs(tdir, exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(new_text)
            written += 1
    print(f"\n{'would write' if dry else 'wrote'}: {written}   already had a transcript: {skipped}")


if __name__ == "__main__":
    main()
