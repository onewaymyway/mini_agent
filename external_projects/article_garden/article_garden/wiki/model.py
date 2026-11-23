"""wiki/model.py — 概念层的落盘结构（方案 §4.1 wiki/ 目录）。

  wiki/concept_ids.json   概念 id 映射：c-0001 → {canonical, surfaces}；保证重跑后 id 稳定
  wiki/concepts/<cid>.md  概念页：front matter（名称、别名、出处、human_edited…）+ 定义 + 依据
  wiki/claims.jsonl       断言账本：每条断言 → 文章、原文摘录、所属概念
  wiki/edges.jsonl        关系边：类型、依据摘录、所在文章（无依据的边不写入）
  wiki/conflicts.jsonl    冲突：同一概念的两条互相矛盾的断言（保留各自出处）

事实源是这些文件本身；所有读写都经 store 原子写入。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import store
from ..library import front_matter


def _id_map_path(ctx):
    return ctx.path("wiki", "concept_ids.json")


def load_id_map(ctx) -> Dict[str, Any]:
    data = store.read_json(_id_map_path(ctx), default=None)
    if not isinstance(data, dict) or not isinstance(data.get("concepts"), dict):
        return {"next_seq": 1, "concepts": {}}
    data.setdefault("next_seq", 1)
    return data


def save_id_map(ctx, data: Dict[str, Any]) -> None:
    store.write_json_atomic(_id_map_path(ctx), data)


def allocate_cid(data: Dict[str, Any]) -> str:
    seq = int(data.get("next_seq", 1))
    data["next_seq"] = seq + 1
    return f"c-{seq:04d}"


def page_path(ctx, cid: str):
    return ctx.path("wiki", "concepts", f"{cid}.md")


def read_page(ctx, cid: str) -> Optional[Tuple[Dict[str, Any], str]]:
    p = page_path(ctx, cid)
    if not p.is_file():
        return None
    return front_matter.split(p.read_text(encoding="utf-8"))


def write_page(ctx, cid: str, meta: Dict[str, Any], body: str) -> None:
    store.write_text_atomic(page_path(ctx, cid), front_matter.dump(meta, body))


def delete_page(ctx, cid: str) -> None:
    p = page_path(ctx, cid)
    if p.is_file():
        p.unlink()


def list_concepts(ctx) -> List[Dict[str, Any]]:
    """所有概念页的 front matter（按 id 排序）。"""
    out = []
    d = ctx.path("wiki", "concepts")
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        meta, _ = front_matter.split(p.read_text(encoding="utf-8"))
        if meta.get("id"):
            out.append(meta)
    return out


def write_jsonl(ctx, name: str, rows: List[Dict[str, Any]]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    store.write_text_atomic(ctx.path("wiki", name), text)


def read_jsonl(ctx, name: str) -> List[Dict[str, Any]]:
    p = ctx.path("wiki", name)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # 半行（被中断写入）忽略；下次构建会整体重写
    return rows
