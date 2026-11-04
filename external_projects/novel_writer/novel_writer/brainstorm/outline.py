"""构思成果总览（B6-2）：把全部产物整理成只读的分组，供对话区上方展示，
以及针对某一项发起修改请求时作为「修改对象」使用。只读，不改任何卡片。
"""

from __future__ import annotations

from typing import Any, Dict, List

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

SECTIONS = ("brief", "premise", "ideas", "world", "cast", "plot", "options", "open_questions")
LABELS = {
    "brief": "题材卡", "premise": "核心命题", "ideas": "灵感池", "world": "世界观",
    "cast": "角色", "plot": "剧情骨架", "options": "备选方案", "open_questions": "开放问题",
}
_WORLD_LABEL = {"rules": "规则", "locations": "地点", "factions": "势力", "timeline": "时间线", "taboos": "禁忌"}
_STATUS = {"keep": "保留", "park": "搁置", "used": "已采用"}   # drop 不显示


def _text_of(item: Any) -> str:
    return item if isinstance(item, str) else str(item.get("text", ""))


def outputs(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    """返回每个模块的 {key, label, count, lines}。lines 为面向阅读的文字行。"""
    m = C.all_modules(ws)
    out: List[Dict[str, Any]] = []

    brief = [f"{k}：{v}" for k, v in m["brief"].items() if v]
    out.append(_sec("brief", brief))
    out.append(_sec("premise", [m["premise"]] if m["premise"] else []))
    ideas = [f"[{_STATUS[i['status']]}] {i['text']}" for i in m["ideas"] if i["status"] in _STATUS]
    out.append(_sec("ideas", ideas))
    world = []
    for key in C.WORLD_LISTS:
        for item in m["world"][key]:
            world.append(f"{_WORLD_LABEL[key]}：{_text_of(item)}")
    out.append(_sec("world", world))
    cast = [f"{c.get('name', cid)}" + (f"（{c['role']}）" if c.get("role") else "") for cid, c in m["cast"].items()]
    out.append(_sec("cast", cast))
    plot: List[str] = []
    for i, act in enumerate(m["plot"]["acts"], 1):
        plot.append(f"第{i}幕 {act.get('title', '')}")
        plot.extend(f"    · {b}" for b in act.get("beats", []))
    plot.extend(f"反转：{t}" for t in m["plot"]["twists"])
    plot.extend(f"结局候选：{e}" for e in m["plot"]["endings"])
    out.append(_sec("plot", plot))
    opts = [f"{o.get('name', '')}（{o.get('module', '')}）" for o in O.list_options(ws)]
    out.append(_sec("options", opts))
    oq = [q["text"] for q in m["open_questions"] if not q["closed"]]
    out.append(_sec("open_questions", oq))
    return out


def _sec(key: str, lines: List[str]) -> Dict[str, Any]:
    return {"key": key, "label": LABELS[key], "count": len(lines), "lines": lines}


def focus_block(ws: BrainstormWorkspace, key: str) -> str:
    """把某个模块的当前内容整理成提示词片段，告诉模型这次的修改请求针对它。"""
    if key not in SECTIONS:
        raise BrainstormError(f"未知的修改对象：{key}")
    sec = next(s for s in outputs(ws) if s["key"] == key)
    body = "\n".join(sec["lines"]) if sec["lines"] else "（目前为空）"
    return (f"【本次修改请求针对：{LABELS[key]}（模块名 {key}）】\n{body}\n"
            f"请只针对这一项提出修改提议（proposals），并沿用上面的模块名。")
