"""灵感构思 → 小说设定文档（Markdown）。

导出的是当前草稿（working）的全部成果：题材卡、核心命题、灵感池（不含已丢弃）、世界观、角色、
剧情骨架、备选方案、开放问题（未关闭）。结构化渲染，不做摘要、不截断，结构与数据一一对应。
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm import outline as OL
from novel_writer.brainstorm.workspace import BrainstormWorkspace
from novel_writer.intake import INTAKE_FIELDS

EXPORT_DIR = "exports"
EMPTY = "（暂无内容）"


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return "、".join(_fmt(x) for x in v if _fmt(x))
    if isinstance(v, dict):
        return "；".join(f"{k}：{_fmt(x)}" for k, x in v.items() if _fmt(x))
    return str(v)


def _item(v: Any) -> str:
    """世界观等列表的一项：字符串原样；字典取 text，其余字段附在后面。"""
    if isinstance(v, dict):
        text = _fmt(v.get("text"))
        rest = "；".join(f"{k}：{_fmt(x)}" for k, x in v.items() if k != "text" and _fmt(x))
        return text + (f"（{rest}）" if rest and text else rest)
    return _fmt(v)


def _bullets(lines: List[str]) -> List[str]:
    return [f"- {x}" for x in lines if x] or [EMPTY]


def _title(ws: BrainstormWorkspace, brief: Dict[str, Any]) -> str:
    return _fmt(brief.get("title")) or ws.root.name


def build_markdown(ws: BrainstormWorkspace) -> str:
    m = C.all_modules(ws)
    title = _title(ws, m["brief"])
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out: List[str] = [
        f"# 小说设定文档：{title}",
        "",
        f"> 导出时间：{now} ｜ 来源：灵感构思（当前草稿）",
        "> 本文档是作者的设定素材，供写作参考。",
        "",
    ]

    out += ["## 题材卡", ""]
    brief = [f"**{label}**：{_fmt(m['brief'].get(key))}" for key, label, _ in INTAKE_FIELDS if _fmt(m["brief"].get(key))]
    out += _bullets(brief)
    out.append("")

    out += ["## 核心命题", ""]
    out.append(_fmt(m["premise"]) or EMPTY)
    out.append("")

    out += ["## 灵感池", ""]
    ideas = [f"[{OL._STATUS[i['status']]}] {_fmt(i.get('text'))}"
             for i in m["ideas"] if i.get("status") in OL._STATUS]
    out += _bullets(ideas)
    out.append("")

    out += ["## 世界观", ""]
    for key in C.WORLD_LISTS:
        out += [f"### {OL._WORLD_LABEL[key]}", ""]
        out += _bullets([_item(x) for x in m["world"].get(key, [])])
        out.append("")

    out += ["## 角色", ""]
    if not m["cast"]:
        out += [EMPTY, ""]
    for cid, c in m["cast"].items():
        head = _fmt(c.get("name")) or cid
        role = _fmt(c.get("role"))
        out += [f"### {head}" + (f"（{role}）" if role else ""), ""]
        fields = [f"**{k}**：{_fmt(v)}" for k, v in c.items() if k not in ("name", "role") and _fmt(v)]
        out += _bullets(fields)
        out.append("")

    out += ["## 剧情骨架", ""]
    plot = m["plot"]
    if not plot.get("acts") and not plot.get("twists") and not plot.get("endings"):
        out += [EMPTY, ""]
    for i, act in enumerate(plot.get("acts", []), 1):
        out += [f"### 第{i}幕 {_fmt(act.get('title'))}".rstrip(), ""]
        out += _bullets([_fmt(b) for b in act.get("beats", [])])
        out.append("")
    if plot.get("twists"):
        out += ["### 反转", ""] + _bullets([_fmt(t) for t in plot["twists"]]) + [""]
    if plot.get("endings"):
        out += ["### 结局候选", ""] + _bullets([_fmt(e) for e in plot["endings"]]) + [""]

    out += ["## 备选方案", ""]
    opts = O.list_options(ws)
    if not opts:
        out += [EMPTY, ""]
    for o in opts:
        head = _fmt(o.get("name")) or "未命名"
        mod = _fmt(o.get("module"))
        out += [f"### {head}" + (f"（{mod}）" if mod else ""), ""]
        fields = [f"**{k}**：{_fmt(v)}" for k, v in o.items() if k not in ("name", "module") and _fmt(v)]
        out += _bullets(fields)
        out.append("")

    out += ["## 开放问题", ""]
    open_qs = [_fmt(q.get("text")) for q in m["open_questions"] if not q.get("closed")]
    out += _bullets(open_qs)
    out.append("")
    return "\n".join(out)


def export_filename(ws: BrainstormWorkspace) -> str:
    title = re.sub(r'[\\/:*?"<>|\s]+', "_", _title(ws, C.get_module(ws, "brief"))).strip("_") or "未命名"
    return f"设定文档_{title}_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"


def save_export(ws: BrainstormWorkspace) -> Path:
    """把导出结果保存一份到 <构思项目>/exports/，返回保存路径。"""
    d = ws.root / EXPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / export_filename(ws)
    path.write_text(build_markdown(ws), encoding="utf-8")
    return path
