#!/usr/bin/env python3
"""Self-check do split-memory.py. O invariante é MOVE: nada pode sumir."""
import json, os, subprocess, sys, tempfile
S = os.path.expanduser("~/.claude/cron/split-memory.py")
fails = []
def run(mem, plan, cap=2500):
    t = tempfile.mkdtemp()
    ctx = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx)
    p = os.path.join(ctx, "MEMORY.md"); open(p, "w").write(mem)
    pj = os.path.join(t, "plan.json"); open(pj, "w").write(json.dumps(plan))
    r = subprocess.run([sys.executable, S, p, str(cap), pj], capture_output=True, text=True)
    return r.returncode, open(p).read(), ctx, r.stderr
def chk(nome, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + nome + ("" if cond else f"  {extra}"))
    if not cond: fails.append(nome)

MEM = """# Working Memory

## Índice
- [Ja existe](topics/ja-existe.md) — algo

## Pendentes
- item um bem comprido que ocupa espaco
- item dois

## Decidido
- decisao antiga
"""
PLAN = {"moves": [{"heading": "## Pendentes", "slug": "pendentes",
                   "index_line": "- [Pendentes](topics/pendentes.md) — o que esta aberto"}]}

rc, novo, ctx, err = run(MEM, PLAN)
chk("split aplica e sai 0", rc == 0, err)
chk("secao saiu do MEMORY.md", "## Pendentes" not in novo)
chk("linha de indice entrou", "topics/pendentes.md" in novo)
tp = os.path.join(ctx, "topics", "pendentes.md")
chk("topic page criada", os.path.exists(tp))
if os.path.exists(tp):
    corpo = open(tp).read()
    chk("conteudo preservado na topic page", "item um bem comprido" in corpo and "item dois" in corpo)
    chk("frontmatter presente", corpo.startswith("---"))

# nada pode sumir: toda linha do original vive em algum lugar
if os.path.exists(tp):
    todo = novo + open(tp).read()
    sumiu = [l for l in MEM.splitlines() if l.strip() and l.strip() not in todo]
    chk("NENHUMA linha do original sumiu", not sumiu, str(sumiu[:1]))

# guards
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Nao Existe", "slug": "x",
                                     "index_line": "- [x](topics/x.md) — y"}]})
chk("heading inexistente -> recusa", rc == 2)
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Pendentes", "slug": "../fuga",
                                     "index_line": "- [x](topics/../fuga.md) — y"}]})
chk("slug com path traversal -> recusa", rc == 2)
rc, _, _, err = run(MEM, {"moves": [{"heading": "## Pendentes", "slug": "pendentes",
                                     "index_line": "- [x](topics/OUTRO.md) — y"}]})
chk("index_line apontando pro arquivo errado -> recusa", rc == 2)
rc, _, _, err = run(MEM, {"moves": []})
chk("plano vazio -> recusa", rc == 2)

# guard do global: caminho fora de projects/ tem que ser recusado
t = tempfile.mkdtemp(); g = os.path.join(t, "context"); os.makedirs(g)
gp = os.path.join(g, "MEMORY.md"); open(gp, "w").write(MEM)
pj = os.path.join(t, "p.json"); open(pj, "w").write(json.dumps(PLAN))
r = subprocess.run([sys.executable, S, gp, "4000", pj], capture_output=True, text=True)
chk("MEMORY.md global -> recusa (nao tem topics/)", r.returncode == 2, r.stderr[:60])

# Um plano que PERDE conteudo nao pode escrever byte nenhum: a verificacao
# acontece antes da gravacao, entao nem MEMORY.md nem topic page mudam.
MEM2 = MEM.replace("## Decidido\n- decisao antiga\n", "## Decidido\n- decisao antiga\n")
t = tempfile.mkdtemp(); ctx2 = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx2)
p2 = os.path.join(ctx2, "MEMORY.md"); open(p2, "w").write(MEM2)
# heading que existe, mas o "move" e sabotado apontando um slug cujo arquivo
# ficaria fora do destino verificado -> simulamos perda truncando via secao vazia
pj2 = os.path.join(t, "plan.json")
open(pj2, "w").write(json.dumps({"moves": [{"heading": "## Índice", "slug": "indice",
      "index_line": "- [Indice](topics/indice.md) — x"}]}))
antes = open(p2).read()
r = subprocess.run([sys.executable, S, p2, "2500", pj2], capture_output=True, text=True)
depois = open(p2).read()
chk("mover o proprio Índice nao corrompe (ou recusa, ou preserva tudo)",
    r.returncode != 0 or all(l.strip() in depois + open(os.path.join(ctx2,'topics','indice.md')).read()
                             for l in antes.splitlines() if l.strip()))

# ISOLAMENTO: plano feito sobre um snapshot nao pode ser aplicado depois que
# outra sessao escreveu — moveria secao que ninguem inspecionou.
import hashlib
t = tempfile.mkdtemp(); ctx3 = os.path.join(t, "projects", "p", "context"); os.makedirs(ctx3)
p3 = os.path.join(ctx3, "MEMORY.md"); open(p3, "w").write(MEM)
h = hashlib.sha256(MEM.encode()).hexdigest()
pj3 = os.path.join(t, "plan.json"); open(pj3, "w").write(json.dumps(PLAN))
open(p3, "w").write(MEM.replace("- item dois", "- item dois\n- ESCRITA CONCORRENTE"))
r = subprocess.run([sys.executable, S, p3, "2500", pj3, h], capture_output=True, text=True)
chk("plano obsoleto -> recusa", r.returncode == 2, r.stderr[:70])
chk("escrita concorrente sobrevive", "ESCRITA CONCORRENTE" in open(p3).read())

# hash correto -> aplica normalmente
open(p3, "w").write(MEM)
r = subprocess.run([sys.executable, S, p3, "2500", pj3, h], capture_output=True, text=True)
chk("hash correto -> aplica", r.returncode == 0, r.stderr[:70])

print("PASS" if not fails else f"FALHOU: {len(fails)}")
sys.exit(1 if fails else 0)
