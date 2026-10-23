#!/usr/bin/env python3
"""Weekly retrieval check. Runs from a systemd user timer; needs no LLM.

1. Lexical benchmark at the configured recall floor (mem-bench.py), compared with
   the previous weekly run. The IDF scale moves with corpus size, so a floor that
   held last week can let an absent case through this week (c035, 2026-09-26).
2. Last 7 days of prompt-hook `recall` events: injection rate, hook latency.
3. A labeling sheet: a score-stratified sample of real recalls with prompt text.
   The bench tests search(); the real failures ("Done"/"yes" injected, typos under
   the floor) only showed up in real recalls. Judging relevance needs a session.
4. One item in the vault's SAVE-FOR-LATER note pointing at the report, unless the
   previous week's item is still open.

Report and sheet stay in data/memory-system/bench/weekly/ (private: real prompts).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_core import Memory, json_read

TITLE = 'Checagem semanal da memória'


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else None


def summarize(rows, floor, known=frozenset()):
    """rows from mem-bench --json; absent cases pass when top_score stays under the floor.
    `known`: cases marked `known_over_floor` in cases.jsonl - accepted leaks, listed apart so they
    do not raise the alert every week and hide a new crossing (a09, 2026-09-28)."""
    hits = [r for r in rows if r['kind'] != 'absent' and not r.get('error')]
    absent = [r for r in rows if r['kind'] == 'absent' and not r.get('error')]
    ranked = lambda r: isinstance(r.get('rank'), int)
    over = sorted((r['id'], r['top_score']) for r in absent if (r.get('top_score') or 0) >= floor)
    return {'n': len(rows), 'errors': sum(1 for r in rows if r.get('error')),
            'recall@1': round(sum(ranked(r) and r['rank'] == 1 for r in hits) / max(1, len(hits)), 3),
            'recall@5': round(sum(ranked(r) and r['rank'] <= 5 for r in hits) / max(1, len(hits)), 3),
            'absent_ok': round((len(absent) - len(over)) / max(1, len(absent)), 3),
            'absent_over_floor': [c for c in over if c[0] not in known],
            'absent_over_floor_known': [c for c in over if c[0] in known],
            'p50_ms': pct([r['elapsed_ms'] for r in rows if r.get('elapsed_ms') is not None], 0.5)}


def recall_drop(rows, old_rows):
    """(old, new, n) when recall@5 fell on the cases both runs share, else None. Comparing whole
    runs read added harder cases as a regression (37 -> 60 cases, 2026-09-28: 89% -> 84%)."""
    common = {r['id'] for r in rows} & {r['id'] for r in old_rows}
    new, old = (summarize([r for r in x if r['id'] in common], 0)['recall@5'] for x in (rows, old_rows))
    return (old, new, len(common)) if new < old else None


def recall_stats(events, since):
    recalls = [e for e in events if e.get('event') == 'recall' and e.get('at', '') >= since]
    elapsed = [e['elapsed_ms'] for e in recalls if isinstance(e.get('elapsed_ms'), int)]
    # hits, not injected_bytes: a cue prompt with nothing above the floor still
    # injects the absence notice, which is not a recall.
    injected = sum(1 for e in recalls if e.get('hits'))
    return {'recalls': len(recalls), 'injected': injected,
            'injection_rate': round(injected / len(recalls), 3) if recalls else None,
            'p50_ms': pct(elapsed, 0.5), 'p95_ms': pct(elapsed, 0.95)}


def recall_rows(events, since):
    """One row per recall with a logged search. top_score is the search's top, before the hook's
    gates (it sets the sample bins and the over-floor count); source/line are the page the hook
    opened with when it injected - a gate can drop the top hit and re-open with another one
    (7 of 148 injections, 2026-10-17). opener None: the opener is not among the logged search
    hits (a vector-only fill), and the row shows the top hit instead."""
    searches = {e['request_id']: e for e in events if e.get('event') == 'search' and e.get('hits')}
    rows = []
    for event in events:
        search = searches.get(event.get('request_id'))
        if event.get('event') != 'recall' or event.get('at', '') < since or not search:
            continue
        top = max(search['hits'], key=lambda h: h['score'])
        opener = next((h for h in search['hits'] if h.get('id') == event['hits'][0]), None) if event.get('hits') else top
        shown = opener or top
        rows.append({'hash': search['query_hash'], 'top_score': top['score'], 'source': shown['source'],
                     'line': shown['line'], 'terms': shown.get('matched_terms', []), 'injected': bool(event.get('hits')),
                     'opener_score': opener and opener is not top and opener['score'], 'opener_found': opener is not None})
    return rows


def prompts_by_hash(root, wanted):
    """Recover prompt text from Claude Code session logs; the events keep only a hash."""
    found = {}
    for path in (root / 'projects').glob('*/*.jsonl'):
        try:
            lines = path.open(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        with lines:
            for line in lines:
                if '"user"' not in line:
                    continue
                try:
                    content = json.loads(line).get('message', {}).get('content')
                except (ValueError, AttributeError):
                    continue
                texts = [content] if isinstance(content, str) else [c.get('text', '') for c in content or [] if isinstance(c, dict)]
                for text in texts:
                    key = hashlib.sha256(text.encode('utf-8')).hexdigest()
                    if key in wanted:
                        found[key] = text
    return found


def sample(recalls, floor, per_bin=10, seed=0):
    """Stratify by top score: below the floor, just above it, well above it."""
    bins = [(0, floor), (floor, floor + 2), (floor + 2, float('inf'))]
    rng = random.Random(seed)
    picked = []
    for low, high in bins:
        group = [r for r in recalls if low <= r['top_score'] < high]
        rng.shuffle(group)
        picked += group[:per_bin]
    return picked


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines() if Path(path).is_file() else []:
        match = re.match(r'\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)', line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"\'')
    return values


def park(note, entry):
    """Append unless the previous weekly item is still open."""
    text = note.read_text(encoding='utf-8') if note.is_file() else ''
    if re.search(r'^- \[ \] #parked \*\*\[memoria\] ' + TITLE, text, re.M):
        return False
    with note.open('a', encoding='utf-8') as handle:
        handle.write(('' if not text or text.endswith('\n') else '\n') + entry)
    return True


def main():
    memory = Memory()
    config, root = memory.config, memory.root
    floor = float(config.get('recall_min_score', 0))
    now = datetime.now(timezone.utc).astimezone()
    today = now.strftime('%Y-%m-%d')
    weekly = memory.state / 'bench' / 'weekly'
    weekly.mkdir(parents=True, exist_ok=True)

    run = weekly / (today + '.json')
    previous = sorted(p for p in weekly.glob('*.json') if p.name < run.name)
    bench = subprocess.run([sys.executable, str(root / 'scripts/mem-bench.py'), '--modes', 'lexical', '--include-held-out',
                            '--floor', str(floor), '--json', str(run)], capture_output=True, text=True, timeout=1800)
    cases = memory.state / 'bench' / 'cases.jsonl'
    known = {c['id'] for c in map(json.loads, filter(str.strip, cases.read_text(encoding='utf-8').splitlines()))
             if c.get('known_over_floor')} if cases.is_file() else set()
    drop = None
    if bench.returncode != 0 or not run.is_file():
        summary, before = {'error': (bench.stderr or bench.stdout)[-500:]}, None
    else:
        rows = json_read(run)['rows']
        summary = summarize(rows, floor, known)
        old_rows = json_read(previous[-1])['rows'] if previous else None
        before = summarize(old_rows, floor, known) if old_rows else None
        drop = recall_drop(rows, old_rows) if old_rows else None

    events = [json_read(p) for p in (memory.state / 'usage').glob('*.json')]
    since = (now - timedelta(days=7)).astimezone(timezone.utc).isoformat()
    stats = recall_stats(events, since)
    recalls = recall_rows(events, since)
    # Recalls whose top was over the floor before the gates: separates a traffic-mix change from
    # the gates' effect (2026-10-17: injection 47.2% -> 29.5%, but over-floor itself fell to 35.6%).
    stats['over_floor'] = sum(r['top_score'] >= floor for r in recalls)
    picked = sample(recalls, floor, seed=int(today.replace('-', '')))
    prompts = prompts_by_hash(root, {r['hash'] for r in picked})
    sheet = weekly / (today + '-rotulos.md')
    rows = ['# Rotulos ' + today, '', 'Marque [x] quando a pagina da linha ajudaria a responder o prompt: a que o hook injetou '
            '(injetado=True) ou o topo da busca (injetado=False). abertura=fora-da-busca: a pagina injetada nao esta nos hits '
            'logados, nao rotular.', '']
    for i, r in enumerate(picked):
        try:
            lines = (root / r['source']).read_text(encoding='utf-8').splitlines()
            snippet = ' | '.join(x.strip() for x in lines[r['line'] - 1:r['line'] + 12] if x.strip())[:300]
        except OSError:
            snippet = '(fonte sumiu)'
        if not r['opener_found']:
            snippet = '(abertura fora da busca: pagina injetada desconhecida)'
        opened = (' aberto_com=%.2f' % r['opener_score'] if r['opener_score'] else '') + (
            '' if r['opener_found'] else ' abertura=fora-da-busca')
        rows += ['- [ ] #%d score=%.2f injetado=%s%s `%s`' % (i, r['top_score'], r['injected'], opened, r['source']),
                 '  - prompt: ' + repr(prompts.get(r['hash'], '(prompt nao recuperado)')[:300]),
                 '  - termos: ' + ', '.join(r['terms']), '  - hit: ' + snippet]
    sheet.write_text('\n'.join(rows) + '\n', encoding='utf-8')

    report = weekly / (today + '.md')
    report.write_text('# Checagem semanal ' + today + '\n\n```json\n' + json.dumps(
        {'floor': floor, 'bench': summary, 'bench_previous': before, 'recalls_7d': stats,
         'sheet': sheet.name, 'sample': len(picked)}, indent=2, ensure_ascii=False) + '\n```\n', encoding='utf-8')

    findings = []
    if 'error' in summary:
        findings.append('benchmark falhou: ' + summary['error'].strip().splitlines()[-1] if summary['error'].strip() else 'benchmark falhou')
    else:
        if summary['absent_over_floor']:
            findings.append('casos ausentes acima do piso %.2f: %s' % (floor, ', '.join('%s=%.2f' % c for c in summary['absent_over_floor'])))
        if drop:
            findings.append('recall@5 caiu de %.0f%% para %.0f%% nos %d casos em comum' % (drop[0] * 100, drop[1] * 100, drop[2]))
    env = read_env(root / 'data/memory.env')
    if not env.get('CLAUDE_MEM_INBOX'):
        print(report)
        return
    resume = config.get('weekly_check_workdir', '')
    demands = json_read(root / 'data/demands.json', {})
    demand = next((d['name'] for d in (demands if isinstance(demands, list) else demands.get('demands', [])) if isinstance(d, dict) and d.get('workdir') == resume), None)
    bench_line = summary.get('error') and 'falhou' or 'recall@5 %.0f%%, absent_ok %.0f%%, p50 %s ms' % (
        summary['recall@5'] * 100, summary['absent_ok'] * 100, summary['p50_ms'])
    entry = '\n'.join([
        '- [ ] #parked **[memoria] %s %s** ^sfl-%s' % (TITLE, today, now.strftime('%Y%m%d-%H%M')),
        '\t- **Retomar em:** ' + ' · '.join(x for x in (demand and '[[' + demand + ']]', resume and '`' + resume + '`') if x),
        '\t- **Capturado:** %s — timer semanal (`memory-weekly-check.py`), sem sessão' % now.strftime('%Y-%m-%d %H:%M'),
        '\t- **Sintoma:** bench léxico: %s. Recalls 7d: %s, acima do piso antes dos gates %s, injetados %s, hook p50 %s ms / p95 %s ms.%s' % (
            bench_line, stats['recalls'], stats['over_floor'], stats['injected'], stats['p50_ms'], stats['p95_ms'],
            (' Atenção: ' + '; '.join(findings) + '.') if findings else ''),
        '\t- **Pistas:** relatório `%s`; folha de rótulos `%s` (%d recalls reais).' % (report, sheet, len(picked)),
        '\t- **Próximo passo:** numa sessão%s, rotular a folha (precisão acima/abaixo do piso) e decidir se o piso ou os casos do bench mudam.' % (
            resume and ' em `' + Path(resume).name + '`'),
    ]) + '\n'
    parked = park(Path(env['CLAUDE_MEM_INBOX']).parent / 'SAVE-FOR-LATER.md', entry)
    print(report, 'parked' if parked else 'previous item still open')


if __name__ == '__main__':
    main()
