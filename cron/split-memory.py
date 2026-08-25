#!/usr/bin/env python3
"""Executa um plano de split de MEMORY.md — o CÓDIGO move, o LLM só decide.

Por que existe: quando um MEMORY.md passa do cap, a única ferramenta que o cron
tinha era COMPRIMIR, que é lossy — foi assim que um store foi de 6534 para 2568
chars sem registro. Quebrar em `topics/` + linha de índice é lossless, mas exige
julgamento (o que agrupa, que nome dar). A divisão aqui é deliberada:

    LLM  -> devolve um PLANO (que seções mover, slug, linha de índice)
    código -> faz o move, verifica, e reverte se algo se perdeu

Assim perder conteúdo é impossível por CONSTRUÇÃO, não por o prompt ter pedido
para preservar — pedido que já falhou 3 vezes em 16 hoje ([[2026-08-24]]).

Uso: split-memory.py <MEMORY.md> <cap> <plano.json> [sha256-esperado]
Plano: {"moves":[{"heading":"## X","slug":"x","index_line":"- [X](topics/x.md) — ..."}]}
Sai 0 se aplicou, 2 se recusou/reverteu.
"""
import hashlib, json, os, re, sys, tempfile, datetime

def sections(text):
    """-> (preamble, [(heading, corpo_incluindo_heading)])"""
    parts = re.split(r'(?m)^(?=## )', text)
    pre = parts[0] if parts and not parts[0].startswith('## ') else ''
    out = []
    for p in parts:
        if p.startswith('## '):
            out.append((p.splitlines()[0].strip(), p))
    return pre, out

def norm(line):
    return re.sub(r'\s+', ' ', line).strip()

def main():
    if len(sys.argv) not in (4, 5):
        print("uso: split-memory.py <MEMORY.md> <cap> <plano.json> [sha256]", file=sys.stderr); return 2
    path, cap, planpath = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    esperado = sys.argv[4] if len(sys.argv) == 5 else None

    # Guard 1: só store de PROJETO. O MEMORY.md global não tem `topics/` e nem
    # deve — lá o excedente é fato de dominio e vai para skills via `kb`.
    # abspath ANTES do guard: um caminho relativo ("projects/x/context/MEMORY.md")
    # nao casa "/projects/" e era recusado em silencio, embora fosse valido.
    path = os.path.abspath(path)
    norm_path = path.replace('\\', '/')
    if '/projects/' not in norm_path or not norm_path.endswith('/context/MEMORY.md'):
        print(f"recusado: {path} nao e store de projeto", file=sys.stderr); return 2
    if not os.path.exists(planpath):
        print("recusado: plano nao existe (o LLM nao escreveu)", file=sys.stderr); return 2

    original = open(path, encoding='utf-8').read()

    # ISOLAMENTO: o plano foi feito a partir de um snapshot; a chamada ao LLM
    # leva segundos ou minutos e nesse meio-tempo uma sessao interativa pode ter
    # escrito no arquivo. Aplicar um plano velho sobre conteudo novo move seçoes
    # que ninguem inspecionou. Se o conteudo nao e o que o plano viu, recusa.
    if esperado:
        atual_hash = hashlib.sha256(original.encode('utf-8')).hexdigest()
        if atual_hash != esperado:
            print("recusado: arquivo mudou depois que o plano foi feito "
                  "(plano obsoleto — outra sessao escreveu?)", file=sys.stderr)
            return 2
    try:
        plan = json.load(open(planpath, encoding='utf-8'))
    except Exception as e:
        print(f"recusado: plano invalido ({e})", file=sys.stderr); return 2
    moves = plan.get('moves') or []
    if not moves:
        print("plano vazio: nada a mover"); return 2

    pre, secs = sections(original)
    by_head = {h: b for h, b in secs}
    ctx = os.path.dirname(path)
    tdir = os.path.join(ctx, 'topics')
    os.makedirs(tdir, exist_ok=True)          # 21 de 32 stores nao tem topics/

    created, moved_heads, index_lines = [], [], []
    for m in moves:
        head, slug = (m.get('heading') or '').strip(), (m.get('slug') or '').strip()
        idx = (m.get('index_line') or '').strip()
        if head not in by_head:
            print(f"recusado: heading ausente no arquivo: {head!r}", file=sys.stderr); return 2
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,60}', slug):
            print(f"recusado: slug invalido: {slug!r}", file=sys.stderr); return 2
        dest = os.path.join(tdir, f"{slug}.md")
        if os.path.exists(dest):
            print(f"recusado: {slug}.md ja existe (nao sobrescrever)", file=sys.stderr); return 2
        if not idx.startswith('- ') or f'topics/{slug}.md' not in idx:
            print(f"recusado: index_line nao aponta para topics/{slug}.md", file=sys.stderr); return 2
        created.append((dest, by_head[head], slug)); moved_heads.append(head); index_lines.append(idx)

    hoje = datetime.date.today().isoformat()
    proj = os.path.basename(os.path.dirname(ctx))

    # Monta TUDO em memoria e verifica ANTES de tocar em disco. Escrever primeiro
    # e conferir depois deixa uma janela em que um crash interrompe o processo
    # com o MEMORY.md ja partido e nada para reverter. Verificado-antes-de-gravar
    # nao tem essa janela: se o plano perde conteudo, nenhum byte foi escrito.
    conteudos = []
    for dest, body, slug in created:
        fm = f"---\nname: {slug}\ndate: {hoje}\nproject: {proj}\ntags: [split-automatico]\n---\n\n"
        conteudos.append((dest, fm + body.rstrip() + "\n"))

    keep = [b for h, b in secs if h not in moved_heads]
    novo = pre + ''.join(keep)
    if re.search(r'(?m)^## Índice\s*$', novo):
        novo = re.sub(r'(?m)^(## Índice\s*\n)', r'\1' + '\n'.join(index_lines) + '\n', novo, count=1)
    else:
        linhas = novo.splitlines(True)
        pos = next((i + 1 for i, l in enumerate(linhas) if l.startswith('# ')), 0)
        linhas.insert(pos, "\n## Índice\n" + '\n'.join(index_lines) + "\n")
        novo = ''.join(linhas)

    # VERIFICACAO: e um MOVE. Toda linha nao-vazia do original tem que sobreviver.
    destino = novo + ''.join(c for _, c in conteudos)
    vivos = {norm(l) for l in destino.splitlines() if norm(l)}
    perdidas = [l for l in original.splitlines() if norm(l) and norm(l) not in vivos]
    if perdidas:
        print(f"RECUSADO (nada foi escrito): {len(perdidas)} linha(s) sumiriam, "
              f"ex: {perdidas[0][:60]!r}", file=sys.stderr)
        return 2

    # CRASH-SAFETY: `open(path,'w')` trunca na hora — morrer no meio da escrita
    # deixa o MEMORY.md truncado ou vazio, que e perda de verdade. Grava em
    # arquivo temporario no MESMO diretorio e troca com os.replace(), que e
    # atomico no POSIX: o arquivo e o conteudo velho ou o novo, nunca metade.
    # As topic pages vao primeiro; se o processo morrer entre elas e a troca, o
    # MEMORY.md segue intacto (as paginas orfas sao inertes e o proximo run as
    # recusa por "ja existe").
    def grava_atomico(dest, conteudo):
        d = os.path.dirname(dest) or '.'
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.split-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(conteudo); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, dest)
        except BaseException:
            try: os.remove(tmp)
            except OSError: pass
            raise

    escritos = []
    try:
        for dest, conteudo in conteudos:
            grava_atomico(dest, conteudo); escritos.append(dest)
        grava_atomico(path, novo)
    except OSError as e:
        for d in escritos:
            try: os.remove(d)
            except OSError: pass
        grava_atomico(path, original)
        print(f"REVERTIDO: falha de escrita ({e})", file=sys.stderr)
        return 2

    novo_n = len(novo)
    print(f"split OK: {len(original)} -> {novo_n} chars (cap {cap}) | "
          f"{len(created)} topic page(s): {', '.join(s for _, _, s in created)}")
    if novo_n > cap:
        print(f"  ainda acima do cap por {novo_n - cap} chars — nada perdido, so nao coube")
    return 0

if __name__ == '__main__':
    sys.exit(main())
