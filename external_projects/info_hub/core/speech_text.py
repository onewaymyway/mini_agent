"""把报告的结构化数据（json）转换为适合朗读的段落列表。

输出是 [{"text": "...", "kind": "overview|section|source|item"}]，前端逐段交给浏览器语音合成。
处理规则：去掉 url 与 markdown 符号；过长段落按标点切分，避免语音合成中途截断。
"""
from __future__ import annotations

import re
from typing import Any

MAX_SEGMENT_CHARS = 160
_URL_RE = re.compile(r"https?://\S+")
_MD_RE = re.compile(r"[*_`#>\[\]()]")
_SPACE_RE = re.compile(r"\s+")
_SPLIT_RE = re.compile(r"(?<=[。！？!?；;，,])")


def clean(text: str) -> str:
    text = _URL_RE.sub("", text or "")
    text = _MD_RE.sub("", text)
    return _SPACE_RE.sub(" ", text).strip()


def split_long(text: str, limit: int = MAX_SEGMENT_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    parts, buf = [], ""
    for piece in _SPLIT_RE.split(text):
        if not piece:
            continue
        if len(buf) + len(piece) > limit and buf:
            parts.append(buf)
            buf = ""
        buf += piece
    if buf:
        parts.append(buf)
    # 仍然超长的硬切
    out: list[str] = []
    for p in parts:
        while len(p) > limit:
            out.append(p[:limit])
            p = p[limit:]
        if p:
            out.append(p)
    return out


def _seg(text: str, kind: str) -> list[dict[str, str]]:
    return [{"text": t, "kind": kind} for t in split_long(clean(text))]


def digest_to_segments(digest: dict[str, Any]) -> list[dict[str, str]]:
    segs: list[dict[str, str]] = []
    s = digest.get("summary", {})
    segs += _seg(
        f"本次报告。窗口内新增 {s.get('new_items', 0)} 条，涉及 {s.get('source_count', 0)} 个信息源，"
        f"异常 {s.get('error_count', 0)} 次。",
        "overview",
    )
    for sub in digest.get("subscriptions", []):
        prev = f"，上次是{sub.get('previous_title')}" if sub.get("previous_title") else ""
        segs += _seg(f"订阅更新：{sub.get('source_name', '')}更新至{sub.get('title', '')}{prev}。", "subscription")
    for pr in digest.get("health", []):
        label = {"paused": "已暂停", "degraded": "连续失败", "config_error": "配置错误"}.get(pr.get("kind"), "异常")
        segs += _seg(f"异常：{pr.get('source_name', '')}{label}，{pr.get('last_error', '')}", "error")
    for err in digest.get("errors", []):
        segs += _seg(f"异常：{err.get('source_id', '')}，{err.get('error', '')}", "error")

    for sec in digest.get("sections", []):
        segs += _seg(f"{sec.get('name', '')}。", "section")
        for src in sec.get("sources", []):
            head = f"{src.get('source_name', '')}。"
            hot = src.get("hot_changes")
            if hot:
                for label, key in (("新进", "new"), ("上升", "rising"), ("下降", "falling")):
                    rows = hot.get(key) or []
                    if rows:
                        titles = "；".join(r.get("title", "") for r in rows)
                        head += f"{label}：{titles}。"
            segs += _seg(head, "source")
            for it in src.get("items", []):
                segs += _seg(it.get("title", ""), "item")
    return segs


def item_to_segments(item: dict[str, Any]) -> list[dict[str, str]]:
    text = item.get("title", "")
    if item.get("summary"):
        text += "。" + item["summary"]
    return _seg(text, "item")
