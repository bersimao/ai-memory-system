import math, random, sys

W, H = 1200, 320
random.seed(20260827)

BG, CYAN, PURPLE, BLUE, DIM = "#0a0d13", "#22d3ee", "#a78bfa", "#60a5fa", "#3b4d66"
TEXT, MUTED, FAINT = "#e6edf5", "#8b9bb0", "#5b6b80"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, 'DejaVu Sans', sans-serif"

# neural cluster: nodes = notes, edges = links, brightness = recall depth
CX, CY, RX, RY = 895.0, 160.0, 208.0, 120.0
FOCUS = (CX - 15, CY - 5)

nodes, tries = [], 0
while len(nodes) < 30 and tries < 8000:
    tries += 1
    a, r = random.uniform(0, 2*math.pi), math.sqrt(random.random())
    x, y = CX + math.cos(a)*RX*r, CY + math.sin(a)*RY*r
    if all((x-px)**2 + (y-py)**2 > 36**2 for px, py, *_ in nodes):
        d = math.hypot(x-FOCUS[0], y-FOCUS[1]) / math.hypot(RX, RY)
        nodes.append([x, y, min(1.0, max(0.0, 1.0 - d*1.25))])

nodes.sort(key=lambda n: -n[2])
for i, n in enumerate(nodes):
    n.append(i < 5)                      # core = what a session gets injected

edges = []
for i, (x, y, _a, _c) in enumerate(nodes):
    for dist, j in sorted((math.hypot(x-nodes[j][0], y-nodes[j][1]), j)
                          for j in range(len(nodes)) if j != i)[:3]:
        if dist < 120 and (j, i) not in edges:
            edges.append((i, j))

o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
     f'role="img" aria-label="ai-memory-system — persistent, cross-project memory for coding agents" '
     f'font-family="{SANS}">']
o.append(f'''<defs>
<filter id="g" x="-260%" y="-260%" width="620%" height="620%">
  <feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="gs" x="-260%" y="-260%" width="620%" height="620%">
  <feGaussianBlur stdDeviation="11" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<radialGradient id="halo" cx="50%" cy="50%" r="50%">
  <stop offset="0%" stop-color="{CYAN}" stop-opacity="0.20"/>
  <stop offset="55%" stop-color="{PURPLE}" stop-opacity="0.07"/>
  <stop offset="100%" stop-color="{PURPLE}" stop-opacity="0"/></radialGradient>
<linearGradient id="mk" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0%" stop-color="{CYAN}"/><stop offset="100%" stop-color="{PURPLE}"/></linearGradient>
</defs>''')

o.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
o.append(f'<ellipse cx="{CX}" cy="{CY}" rx="236" ry="152" fill="url(#halo)"/>')

for i, j in edges:
    x1, y1, a1, _ = nodes[i]; x2, y2, a2, _ = nodes[j]
    act = (a1 + a2) / 2
    mx, my = (x1+x2)/2 - (y2-y1)*0.13, (y1+y2)/2 + (x2-x1)*0.13
    col = CYAN if act > 0.62 else (BLUE if act > 0.34 else DIM)
    o.append(f'<path d="M{x1:.1f} {y1:.1f} Q{mx:.1f} {my:.1f} {x2:.1f} {y2:.1f}" fill="none" stroke="{col}" '
             f'stroke-width="{0.7+act*1.5:.2f}" stroke-opacity="{0.13+act*0.62:.2f}" stroke-linecap="round"/>')

for x, y, act, core in nodes:
    r = 3.0 + act*4.2 + (2.2 if core else 0)
    col = CYAN if core else (BLUE if act > 0.45 else DIM)
    if core:
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r+7:.1f}" fill="{col}" opacity="0.11" filter="url(#gs)"/>')
    o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{col}" fill-opacity="{0.4+act*0.6:.2f}"'
             + (' filter="url(#g)"' if act > 0.5 else '') + '/>')
    if core:
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="none" stroke="#dffaff" stroke-width="1.1" stroke-opacity="0.75"/>')

# mark
o.append('<g transform="translate(62,52)">')
o.append('<circle cx="26" cy="26" r="24" fill="none" stroke="url(#mk)" stroke-width="2.4"/>')
o.append('<circle cx="26" cy="26" r="8.5" fill="url(#mk)" filter="url(#g)"/>')
for ang in (-58, 12, 82, 152, 222):
    a = math.radians(ang)
    o.append(f'<line x1="{26+math.cos(a)*9.5:.1f}" y1="{26+math.sin(a)*9.5:.1f}" '
             f'x2="{26+math.cos(a)*23.5:.1f}" y2="{26+math.sin(a)*23.5:.1f}" '
             f'stroke="url(#mk)" stroke-width="2" stroke-linecap="round" opacity="0.85"/>')
    o.append(f'<circle cx="{26+math.cos(a)*23.5:.1f}" cy="{26+math.sin(a)*23.5:.1f}" r="3.1" fill="{CYAN}" opacity="0.95"/>')
o.append('</g>')

o.append(f'<text x="130" y="92" font-family="{MONO}" font-size="40" font-weight="700" fill="{TEXT}" letter-spacing="-0.5">ai-memory-system</text>')
o.append(f'<text x="132" y="127" font-size="17" fill="{MUTED}">Persistent, cross-project memory for coding agents.</text>')
o.append(f'<text x="132" y="154" font-size="14" fill="{FAINT}">Markdown files plus a few hooks — no service, no vendor, no lock-in.</text>')

o.append(f'<text x="132" y="226" font-family="{MONO}" font-size="12.5" fill="{FAINT}" letter-spacing="0.6">retrieval</text>')
x = 210
for i, (lab, col) in enumerate([("context", CYAN), ("grep", BLUE), ("vector", PURPLE), ("transcripts", "#f0a4c8")]):
    if i:
        o.append(f'<text x="{x}" y="226" font-family="{MONO}" font-size="12.5" fill="{DIM}">→</text>')
        x += 22
    lbl = lab
    o.append(f'<text x="{x}" y="226" font-family="{MONO}" font-size="12.5" fill="{col}">{lbl}</text>')
    x += int(len(lbl) * 7.55) + 8
o.append('</svg>')

out = sys.argv[1]
open(out, "w").write("\n".join(o))
print("wrote", out, "|", len(nodes), "nodes,", len(edges), "edges")
