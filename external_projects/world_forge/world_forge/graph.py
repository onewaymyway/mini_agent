"""world_forge/graph.py — 设定关系图：从数据构建节点与连线，并渲染为自包含的交互式图（无外部依赖）。

节点：设定实体 + 角色。连线：实体之间的关系（标注类型），以及角色到所属势力的“隶属”连线。
渲染：内联的力导向布局（SVG），支持拖动节点、滚轮缩放、拖动背景平移、悬停查看说明。
"""

from __future__ import annotations

import html
import json
from collections import deque
from typing import Any, Dict, List, Optional

from . import repo

KIND_COLORS = {
    "地理": "#4f9d69", "势力": "#c0574a", "物种": "#8e6fc2", "技术/魔法": "#3b82c4",
    "历史事件": "#c49a2c", "物品": "#7a8a99", "组织": "#d07a3c", "其它": "#888888", "角色": "#e0c26a",
}


def build_graph(wid: str, focus: Optional[str] = None, depth: int = 1) -> Dict[str, Any]:
    ents = repo.load_entities(wid)
    chars = repo.load_characters(wid)
    rels = repo.load_relations(wid)
    nodes: Dict[str, dict] = {}
    for e in ents:
        nodes[e["id"]] = {"id": e["id"], "label": e["name"], "group": e["kind"],
                          "info": (e.get("summary") or "")[:160]}
    for c in chars:
        nodes[c["id"]] = {"id": c["id"], "label": c["name"], "group": "角色",
                          "info": f"{c.get('role', '')}｜{c.get('status', '')}｜{(c.get('summary') or '')[:120]}"}
    edges: List[dict] = []
    for r in rels:
        edges.append({"source": r["src"], "target": r["dst"], "label": r.get("type") or "关联"})
    for c in chars:
        if c.get("faction_id"):
            edges.append({"source": c["id"], "target": c["faction_id"], "label": "隶属"})
    edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]

    if focus and focus in nodes:
        adj: Dict[str, set] = {}
        for e in edges:
            adj.setdefault(e["source"], set()).add(e["target"])
            adj.setdefault(e["target"], set()).add(e["source"])
        keep = {focus}
        q = deque([(focus, 0)])
        while q:
            cur, d = q.popleft()
            if d >= depth:
                continue
            for nb in adj.get(cur, ()):
                if nb not in keep:
                    keep.add(nb)
                    q.append((nb, d + 1))
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    return {"nodes": list(nodes.values()), "edges": edges}


_JS = r"""
const DATA = __DATA__;
const COLORS = __COLORS__;
const svg = document.getElementById('g');
const vp = document.getElementById('vp');
const gE = document.getElementById('edges');
const gN = document.getElementById('nodes');
const NS = 'http://www.w3.org/2000/svg';
const W = svg.getBoundingClientRect().width || 900;
const H = svg.getBoundingClientRect().height || 600;
const nodes = DATA.nodes.map((n, i) => {
  const a = (i / Math.max(1, DATA.nodes.length)) * Math.PI * 2;
  return Object.assign({}, n, {x: W / 2 + Math.cos(a) * Math.min(W, H) * 0.3,
                              y: H / 2 + Math.sin(a) * Math.min(W, H) * 0.3, vx: 0, vy: 0});
});
const byId = {}; nodes.forEach(n => byId[n.id] = n);
const links = DATA.edges.filter(e => byId[e.source] && byId[e.target])
  .map(e => Object.assign({}, e, {s: byId[e.source], t: byId[e.target]}));
// 力导向模拟（一次性迭代到稳定）
for (let it = 0; it < 350; it++) {
  const alpha = Math.max(0.02, 1 - it / 350);
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    let dx = a.x - b.x, dy = a.y - b.y, d2 = Math.max(dx * dx + dy * dy, 80);
    const f = 2600 * alpha / d2, d = Math.sqrt(d2);
    a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
  }
  for (const l of links) {
    const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
    const k = (d - 130) * 0.03 * alpha;
    l.s.vx += dx / d * k; l.s.vy += dy / d * k; l.t.vx -= dx / d * k; l.t.vy -= dy / d * k;
  }
  for (const n of nodes) {
    n.vx += (W / 2 - n.x) * 0.004 * alpha; n.vy += (H / 2 - n.y) * 0.004 * alpha;
    n.x += n.vx; n.y += n.vy; n.vx *= 0.55; n.vy *= 0.55;
  }
}
function el(name, attrs, parent) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
const edgeEls = links.map(l => {
  const g = el('g', {}, gE);
  const line = el('line', {stroke: '#5b6785', 'stroke-width': 1.6, 'stroke-opacity': 0.8}, g);
  const txt = el('text', {'font-size': 11, fill: '#aab4cc', 'text-anchor': 'middle'}, g);
  txt.textContent = l.label;
  const tip = el('title', {}, g); tip.textContent = `${l.s.label} — ${l.label} — ${l.t.label}`;
  return {l, line, txt};
});
const nodeEls = nodes.map(n => {
  const g = el('g', {cursor: 'grab'}, gN);
  el('circle', {r: 13, fill: COLORS[n.group] || '#888', stroke: '#0f1424', 'stroke-width': 2}, g);
  const t = el('text', {'font-size': 12, fill: '#f2f4fa', 'text-anchor': 'middle', dy: 28}, g);
  t.textContent = n.label;
  const tip = el('title', {}, g); tip.textContent = `【${n.group}】${n.label}\n${n.info || ''}`;
  g.addEventListener('mousedown', ev => { ev.stopPropagation(); drag = {n, sx: ev.clientX, sy: ev.clientY}; });
  return {n, g};
});
function draw() {
  for (const e of edgeEls) {
    e.line.setAttribute('x1', e.l.s.x); e.line.setAttribute('y1', e.l.s.y);
    e.line.setAttribute('x2', e.l.t.x); e.line.setAttribute('y2', e.l.t.y);
    e.txt.setAttribute('x', (e.l.s.x + e.l.t.x) / 2); e.txt.setAttribute('y', (e.l.s.y + e.l.t.y) / 2 - 4);
  }
  for (const e of nodeEls) e.g.setAttribute('transform', `translate(${e.n.x},${e.n.y})`);
}
let scale = 1, tx = 0, ty = 0, drag = null, pan = null;
function applyView() { vp.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`); }
svg.addEventListener('wheel', ev => {
  ev.preventDefault();
  const r = svg.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  const f = ev.deltaY < 0 ? 1.12 : 1 / 1.12, ns = Math.min(4, Math.max(0.3, scale * f));
  tx = mx - (mx - tx) * (ns / scale); ty = my - (my - ty) * (ns / scale); scale = ns; applyView();
}, {passive: false});
svg.addEventListener('mousedown', ev => { if (!drag) pan = {x: ev.clientX, y: ev.clientY, tx, ty}; });
window.addEventListener('mousemove', ev => {
  if (drag) {
    const r = svg.getBoundingClientRect();
    drag.n.x = (ev.clientX - r.left - tx) / scale; drag.n.y = (ev.clientY - r.top - ty) / scale; draw();
  } else if (pan) { tx = pan.tx + ev.clientX - pan.x; ty = pan.ty + ev.clientY - pan.y; applyView(); }
});
window.addEventListener('mouseup', () => { drag = null; pan = null; });
const legend = document.getElementById('legend');
const kinds = [...new Set(DATA.nodes.map(n => n.group))];
legend.innerHTML = '';
for (const k of kinds) {
  const s = document.createElement('span');
  s.style.cssText = 'display:inline-flex;align-items:center;margin:0 10px 4px 0;color:#c8d0e4;font-size:12px';
  s.innerHTML = `<i style="display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;background:${COLORS[k] || '#888'}"></i>`;
  s.appendChild(document.createTextNode(k));
  legend.appendChild(s);
}
const hint = document.getElementById('hint');
hint.textContent = `${nodes.length} 个节点 · ${links.length} 条关系　|　拖动节点调整位置 · 滚轮缩放 · 拖动空白处平移`;
draw(); applyView();
"""


def render_html(data: Dict[str, Any], height: int = 640) -> str:
    if not data["nodes"]:
        return ("<div style='color:#c8d0e4;font:14px sans-serif;padding:24px'>"
                "还没有可显示的实体或关系。</div>")
    js = (_JS.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
          .replace("__COLORS__", json.dumps(KIND_COLORS, ensure_ascii=False)))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:#0f1424;font-family:-apple-system,"Microsoft YaHei",sans-serif}}
#wrap{{position:relative;width:100%;height:{height}px;border-radius:10px;overflow:hidden;background:#0f1424}}
svg{{width:100%;height:100%;display:block;user-select:none}}
#bar{{position:absolute;left:12px;right:12px;bottom:10px;pointer-events:none}}
#legend{{margin-bottom:4px}}
#hint{{color:#7f8aa6;font-size:12px}}
</style></head><body>
<div id="wrap">
<svg id="g"><g id="vp"><g id="edges"></g><g id="nodes"></g></g></svg>
<div id="bar"><div id="legend"></div><div id="hint"></div></div>
</div>
<script>{js}</script></body></html>"""
