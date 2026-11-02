#!/usr/bin/env python3
"""Retrieval benchmark for the shared memory CLI (RAG plan, T0.2).

Runs every case in cases.jsonl through `scripts/mem search --json` and scores where
the expected page landed. Stdlib only; the real `mem` is a subprocess, so the runner
never imports memory_core and the tests can point MEM_BIN at a stub.

Modes:
  lexical             plain `mem search`
  semantic            `mem search --semantic` (`semantic-sqlite` is the same mode: the name the
                      runs carried while memsearch was still a second engine)

Calibration rule: derive thresholds from the DEFAULT run (held-out cases excluded).
--include-held-out is for reporting only; tuning on it contaminates the holdout.

Scoring:
  rank        1-based position of `expected` in results[].source, or None
  absent_ok   a case with expected=null is CORRECT when top_score < --floor
  scope_ok    a scope case is correct when expected is inside top-k
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

HOME = pathlib.Path(os.environ.get('AI_MEMORY_HOME', pathlib.Path.home() / '.claude'))
BENCH = HOME / 'data/memory-system/bench'
MODES = ('lexical', 'semantic', 'semantic-sqlite')
# Same list as `mem search --session` reads (memory-cli.py); kept apart: the runner never imports the core.
SESSION_VARS = ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID')


def mem_bin():
    # Read at call time: the tests swap the stub between cases.
    return os.environ.get('MEM_BIN', str(HOME / 'scripts/mem'))


def run_case(case, mode, k, timeout):
    cmd = [mem_bin(), 'search', case['query'], '-k', str(k), '--json', '--scope', 'all']
    if mode != 'lexical':
        cmd.append('--semantic')
    if case.get('cwd'):
        cmd += ['--cwd', case['cwd']]

    started = time.monotonic()
    try:
        # No session: a bench query is not use (salience credits real sessions only).
        env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        error = None if proc.returncode == 0 else f'exit {proc.returncode}: {proc.stderr.strip()[:200]}'
    except subprocess.TimeoutExpired:
        payload, error = {}, f'timeout after {timeout}s'
    except json.JSONDecodeError as exc:
        payload, error = {}, f'bad json: {exc}'
    wall_ms = (time.monotonic() - started) * 1000

    results = payload.get('results') or []
    # `source` is already relative to the memory root; compare on the raw string but
    # tolerate the truncation the CLI applies to long paths.
    # A line salience moved to MEMORY-COLD.md is still the index's content, found.
    sources = [str(r.get('source', '')).replace('/context/MEMORY-COLD.md', '/context/MEMORY.md') for r in results]
    rank = None
    if case.get('expected'):
        # expected_any: real long prompts have more than one right page (2026-09-28).
        wanted = case.get('expected_any') or [case['expected']]
        for i, src in enumerate(sources, 1):
            if any(src == e or e.startswith(src) or src.startswith(e) for e in wanted):
                rank = i
                break
    scores = [float(r.get('score', 0) or 0) for r in results]
    return {
        'id': case['id'], 'mode': mode, 'lang': case['lang'], 'kind': case['kind'],
        'held_out': case.get('held_out', False), 'expected': case.get('expected'),
        'rank': rank, 'top_score': max(scores) if scores else 0.0,
        'n_results': len(results), 'elapsed_ms': payload.get('elapsed_ms'),
        'wall_ms': round(wall_ms, 1), 'engine': payload.get('engine'), 'error': error,
    }


def summarise(rows, floor):
    hits = [r for r in rows if r['kind'] not in ('absent',) and r['expected']]
    absent = [r for r in rows if r['kind'] == 'absent']
    scope = [r for r in rows if r['kind'] == 'scope']
    lat = [r['wall_ms'] for r in rows if r['wall_ms'] is not None]

    def pct(n, d):
        return f'{100.0 * n / d:.0f}%' if d else 'n/a'

    return {
        'n': len(rows),
        'recall@1': pct(sum(1 for r in hits if r['rank'] == 1), len(hits)),
        'recall@5': pct(sum(1 for r in hits if r['rank'] is not None), len(hits)),
        'absent_ok': pct(sum(1 for r in absent if not r['error'] and r['top_score'] < floor), len(absent)),
        'scope_ok': pct(sum(1 for r in scope if r['rank'] is not None), len(scope)),
        'p50_ms': f'{statistics.median(lat):.0f}' if lat else 'n/a',
        'p95_ms': f'{sorted(lat)[min(len(lat) - 1, int(len(lat) * 0.95))]:.0f}' if lat else 'n/a',
        'errors': sum(1 for r in rows if r['error']),
    }


def render(by_mode, floor):
    out = []
    cols = ['mode', 'n', 'recall@1', 'recall@5', 'absent_ok', 'scope_ok', 'p50_ms', 'p95_ms', 'errors']
    out.append('| ' + ' | '.join(cols) + ' |')
    out.append('|' + '---|' * len(cols))
    for mode, rows in by_mode.items():
        s = summarise(rows, floor)
        out.append('| ' + ' | '.join([mode] + [str(s[c]) for c in cols[1:]]) + ' |')
    out.append('')
    out.append('| id | mode | kind | lang | rank | top_score | ms |')
    out.append('|---|---|---|---|---|---|---|')
    for rows in by_mode.values():
        for r in rows:
            rank = r['rank'] if r['rank'] is not None else ('—' if r['kind'] == 'absent' else 'MISS')
            out.append(f"| {r['id']} | {r['mode']} | {r['kind']} | {r['lang']} | {rank} | "
                       f"{r['top_score']:.2f} | {r['wall_ms']:.0f} |")
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', default=str(BENCH / 'cases.jsonl'))
    ap.add_argument('--modes', default='lexical')
    ap.add_argument('--k', type=int, default=5)
    ap.add_argument('--floor', type=float, default=1.0, help='absent-case threshold (T0.3 calibrates it)')
    ap.add_argument('--include-held-out', action='store_true')
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--json', dest='json_out', default=None)
    args = ap.parse_args(argv)

    modes = [m.strip() for m in args.modes.split(',') if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad:
        ap.error(f'unknown mode(s): {bad}; choose from {MODES}')

    cases = [json.loads(l) for l in open(args.cases) if l.strip()]
    if not args.include_held_out:
        cases = [c for c in cases if not c.get('held_out')]

    # Untimed warm-up: the first search after a boot pays for a cold page cache, and the
    # weekly check once ran 2 min after boot and reported p50 3x too high (2026-10-10).
    if cases:
        run_case(cases[0], modes[0], args.k, args.timeout)

    by_mode = {}
    for mode in modes:
        by_mode[mode] = [run_case(c, mode, args.k, args.timeout) for c in cases]

    print(render(by_mode, args.floor))

    out = args.json_out or str(BENCH / 'runs' / (time.strftime('%Y-%m-%dT%H-%M-%S') + '.json'))
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out).write_text(json.dumps({
        'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'k': args.k, 'floor': args.floor,
        'include_held_out': args.include_held_out, 'cases': args.cases,
        # Fingerprint of the case file this run was produced from. Editing any case -- even
        # keeping its id -- changes this, so a stale run can be detected instead of passing
        # a row-count check (found by review 2026-09-20).
        'cases_sha256': hashlib.sha256(pathlib.Path(args.cases).read_bytes()).hexdigest(),
        'rows': [r for rows in by_mode.values() for r in rows],
    }, indent=1))
    print(f'\nraw rows: {out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
