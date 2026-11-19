"""batch/prep.py — 队列界面用的纯函数（不依赖 Streamlit，便于测试）：标题作者建议、估算、参数解析、进度文字。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..importer import summarize
from ..pipeline.gates import STAGES
from ..project_store import read_json
from ..source.author_meta import resolve_author
from ..source.md_parse import parse_markdown

STATE_LABELS = {"queued": "排队中", "running": "运行中", "done": "完成", "failed": "失败（重试用尽）",
                "stopped": "已停止", "waiting": "停在人工检查点", "budget_exceeded": "超出预算"}
STAGE_LABELS = {"outline": "大纲", "script": "口播稿", "voice": "配音", "storyboard": "分镜",
                "render": "画面渲染", "compose": "章合成", "export": "导出"}


def suggest_meta(text: str, source_name: str) -> Dict[str, Any]:
    """为上传的文章给出标题与作者的初始建议（用户可在表格里改）并估算体量；不调用任何模型。"""
    text = text.lstrip("﻿")
    parsed = parse_markdown(text)
    h1 = next((s["title"] for s in parsed["sections"] if s.get("title")), "")
    title = h1 or Path(source_name).stem or "未命名文章"
    author, _src = resolve_author("", parsed["front_matter"], parsed["body_head_lines"])
    summ = summarize(parsed, text)
    return {"title": title, "author": author, "chars": summ["chinese_chars"],
            "minutes": summ["est_full_read_minutes"]}


def parse_delays(text: str) -> List[float]:
    """「30, 60, 120」→ [30.0, 60.0, 120.0]；全角逗号、空格、顿号都可作分隔；不合法抛 ValueError。"""
    parts = [p for p in re.split(r"[,，、\s]+", (text or "").strip()) if p]
    if not parts:
        raise ValueError("请至少填写一个等待秒数，例如 30,60,120")
    out = []
    for p in parts:
        v = float(p)                          # 不是数字会抛 ValueError
        if v < 0 or v > 3600:
            raise ValueError("等待秒数需在 0～3600 之间")
        out.append(v)
    return out


def format_delays(delays: List[float]) -> str:
    return ",".join(f"{d:g}" for d in delays)


def item_progress(pdir: Path, item: Dict[str, Any]) -> str:
    """条目当前进度的一句话：读项目自己的 pipeline.json（已完成的阶段数与正在做的阶段）。"""
    if not item.get("project_id"):
        return "尚未开始" if item["state"] == "queued" else ""
    p = Path(pdir) / "pipeline.json"
    if not p.is_file():
        return "已创建项目，流水线尚未开始"
    try:
        st = read_json(p)
    except (OSError, ValueError):
        return ""
    done = [s["stage"] for s in st.get("steps", []) if s.get("result") == "done"]
    if st.get("state") == "done":
        return f"{len(STAGES)}/{len(STAGES)} 阶段完成"
    nxt = next((s for s in STAGES if s not in done), None)
    where = f"，当前：{STAGE_LABELS.get(nxt, nxt)}" if nxt and item["state"] == "running" else ""
    return f"{len(done)}/{len(STAGES)} 阶段完成{where}"


def queue_rows(root: Path, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """队列表格的行（界面直接展示）。"""
    rows = []
    for it in data["items"]:
        pdir = Path(root) / it["project_id"] if it.get("project_id") else Path(root)
        rows.append({
            "编号": it["id"], "标题": it["title"], "作者": it["author"] or "（不署名）",
            "状态": STATE_LABELS.get(it["state"], it["state"]),
            "进度": item_progress(pdir, it),
            "失败次数": it.get("failures", 0),
            "说明": it.get("error", ""),
            "项目": it.get("project_id", ""),
        })
    return rows


def pending_estimate(data: Dict[str, Any]) -> Tuple[int, int, float]:
    """尚未完成的条目：(篇数, 汉字数, 全文朗读约多少分钟)。只是体量估算，不是费用。"""
    from ..importer import CHARS_PER_SECOND
    n = chars = 0
    for it in data["items"]:
        if it["state"] in ("queued", "running"):
            n += 1
            chars += int(it.get("chinese_chars") or 0)
    return n, chars, round(chars / CHARS_PER_SECOND / 60, 1)
