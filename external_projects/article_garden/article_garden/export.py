"""export.py — 导出（方案 Q13、§14 P7）：静态 wiki 站（HTML）与已发布文章的 zip。

* 只导出 status=published 的文章；草稿、已审阅、已归档均不导出。
* 概念页只导出定义状态为 ok 的概念（定义只依据导入文章，见 P6 守卫）。
* HTML 为纯静态文件，不依赖 JavaScript 与外部资源；Markdown 用内置的小型转换器（段落、标题、列表、
  引用、表格、链接、粗体、行内代码、脚注）。复杂语法会退化为普通段落，不会丢失文字。
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .library import catalog, front_matter
from .wiki import model

CSS = """body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:760px;margin:2em auto;
padding:0 1em;line-height:1.75;color:#222}a{color:#1a6b3c}blockquote{color:#555;border-left:3px solid #ccc;margin:0;padding-left:1em}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3em .6em}code{background:#f3f3f3;padding:0 .2em}
sup{color:#1a6b3c}nav{font-size:.9em;margin-bottom:2em}"""


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\[\^(src-\d{4})\]", r"<sup>\1</sup>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(md: str) -> str:
    out: List[str] = []
    para: List[str] = []
    items: List[str] = []
    table: List[str] = []
    quote: List[str] = []
    footnotes: List[Tuple[str, str]] = []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            items.clear()
        if quote:
            out.append("<blockquote><p>" + _inline(" ".join(quote)) + "</p></blockquote>")
            quote.clear()
        if table:
            rows = [r for r in table if not re.match(r"^\|\s*:?-", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            if cells:
                head = "".join(f"<th>{_inline(c)}</th>" for c in cells[0])
                body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
                out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            table.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        fn = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", line)
        if fn:
            flush()
            footnotes.append((fn.group(1), fn.group(2)))
            continue
        if not line.strip():
            flush()
            continue
        if line.startswith("|"):
            if para or items or quote:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            flush()
            n = len(h.group(1))
            out.append(f"<h{n}>{_inline(h.group(2))}</h{n}>")
            continue
        if line.startswith("> "):
            if para or items:
                flush()
            quote.append(line[2:])
            continue
        if line.startswith("- "):
            if para:
                flush()
            items.append(line[2:])
            continue
        if quote:
            flush()
        para.append(line)
    flush()
    if footnotes:
        out.append('<section><h2>参考来源</h2><ol>' + "".join(
            f'<li id="{html.escape(i)}">{_inline(t)}</li>' for i, t in footnotes) + "</ol></section>")
    return "\n".join(out)


def _page(title: str, body_html: str, nav: str = "") -> str:
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
            f'<nav><a href="{nav}index.html">目录</a></nav>{body_html}</body></html>')


def published_articles(ctx) -> List[dict]:
    out = []
    for row in catalog.list_articles(ctx):
        if row.get("status") != "published":
            continue
        meta, body = front_matter.split(ctx.path(row["path"]).read_text(encoding="utf-8"))
        out.append({"id": row["id"], "title": meta.get("title") or row["title"], "meta": meta, "body": body,
                    "path": row["path"]})
    return out


def ok_concepts(ctx) -> List[Tuple[dict, str]]:
    out = []
    for meta in model.list_concepts(ctx):
        if meta.get("status") != "ok":
            continue
        page = model.read_page(ctx, meta["id"])
        if page:
            out.append((meta, page[1]))
    return out


def export_site(ctx, out_dir: Optional[Path] = None) -> Path:
    out = Path(out_dir) if out_dir else ctx.path("exports", "site")
    (out / "articles").mkdir(parents=True, exist_ok=True)
    (out / "concepts").mkdir(parents=True, exist_ok=True)
    arts = published_articles(ctx)
    concepts = ok_concepts(ctx)
    items = []
    for a in arts:
        (out / "articles" / f"{a['id']}.html").write_text(
            _page(a["title"], f"<h1>{html.escape(a['title'])}</h1>" + md_to_html(a["body"]), "../"), encoding="utf-8")
        items.append(f'<li><a href="articles/{a["id"]}.html">{html.escape(a["title"])}</a></li>')
    citems = []
    for meta, body in concepts:
        (out / "concepts" / f"{meta['id']}.html").write_text(
            _page(meta.get("name", meta["id"]), md_to_html(body), "../"), encoding="utf-8")
        citems.append(f'<li><a href="concepts/{meta["id"]}.html">{html.escape(meta.get("name", meta["id"]))}</a></li>')
    index_body = (f"<h1>{html.escape(ctx.info().get('name') or ctx.id)}</h1>"
                  f"<h2>文章（{len(arts)}）</h2><ul>{''.join(items) or '<li>暂无已发布的文章</li>'}</ul>"
                  f"<h2>概念（{len(concepts)}）</h2><ul>{''.join(citems) or '<li>暂无概念</li>'}</ul>")
    (out / "index.html").write_text(_page("目录", index_body), encoding="utf-8")
    return out


def export_published_zip(ctx, out_dir: Optional[Path] = None) -> Path:
    out_dir = Path(out_dir) if out_dir else ctx.path("exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"{ctx.id}_published_{stamp}.zip"
    arts = published_articles(ctx)
    manifest = {"domain": ctx.id, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "articles": [{"id": a["id"], "title": a["title"], "file": f"articles/{a['id']}.md"} for a in arts]}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for a in arts:
            zf.writestr(f"articles/{a['id']}.md", ctx.path(a["path"]).read_text(encoding="utf-8"))
    return target
