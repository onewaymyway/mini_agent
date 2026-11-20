"""R6 评估报告导出：把某个版本的问题池整理成一份可阅读、可归档的 Markdown 报告。

报告内容：
  * 概况：纳入 / 未纳入的章节、因引文无法核对而丢弃的事实条目、各来源与各处理状态的数量；
  * 按类别与严重度分组的全部问题（含证据与核实标记、模型仲裁结论、大纲偏离、采纳的变更、
    已应用 / 已解决 / 仍存在 等历史状态、作者备注）；
  * 「证据未核实」的问题单独列出，便于核对后再处理。

只读：只读取问题池，写入 `_revision/review/rev_<版本>/export.md`，不改版本、不改问题池。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow.common import FlowError

SOURCE_LABEL = {"deterministic": "规则候选", "llm": "模型复核", "outline": "大纲偏离检查"}
STATUS_LABEL = {"open": "待处理", "adopted": "已采纳", "rejected": "已驳回", "applied": "已应用",
                "resolved": "已解决", "still_open": "仍存在", "stale": "基于旧版本"}
VERDICT_LABEL = {"confirmed": "确认为问题", "false_positive": "误报", "uncertain": "无法判定"}


def export_path(ws, version: str) -> Path:
    return RK.review_dir(ws, version) / "export.md"


def _summary_lines(data: Dict[str, Any], vid: str) -> List[str]:
    fs = data["findings"]
    st = data.get("stats", {})
    by_src: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for f in fs:
        by_src[f["source"]] = by_src.get(f["source"], 0) + 1
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    unverified = sum(1 for f in fs if f["source"] in ("llm", "outline") and not f.get("evidence_ok", True))
    lines = [f"# 全书评估报告（版本 {vid}）", "",
             f"- 生成时间：{_dt.datetime.now().isoformat(timespec='seconds')}",
             f"- 纳入评估章节：{st.get('chapters_with_cards', 0)}　未纳入：{len(st.get('chapters_missing_cards', []))}"
             f"　因引文无法核对而丢弃的事实条目：{st.get('dropped_items', 0)}",
             f"- 问题共 {len(fs)} 条（严重 {sum(f['severity'] == '严重' for f in fs)} / 中 {sum(f['severity'] == '中' for f in fs)}"
             f" / 轻 {sum(f['severity'] == '轻' for f in fs)}）；证据未核实 {unverified} 条",
             "- 来源：" + "；".join(f"{SOURCE_LABEL.get(k, k)} {v}" for k, v in sorted(by_src.items())) if by_src else "- 来源：无",
             "- 处理状态：" + "；".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in sorted(by_status.items())) if by_status else "- 处理状态：无",
             "", "> 所有问题都需要作者裁决；规则候选与模型判断都可能有误。报告只做整理，不改动任何章节。", ""]
    return lines


def _finding_lines(f: Dict[str, Any]) -> List[str]:
    chapters = "、".join(str(c) for c in f["chapters"])
    lines = [f"### [{_tag(f)}] {f['title']}",
             f"- 编号：{f['id']}　来源：{SOURCE_LABEL.get(f['source'], f['source'])}　状态：{STATUS_LABEL.get(f['status'], f['status'])}"
             f"　涉及章节：{chapters}"]
    if f["source"] in ("llm", "outline") and not f.get("evidence_ok", True):
        lines.append("- **证据未核实**：有引文不在章节原文中，请核对原文后再决定。")
    lines.append(f"- {f['description']}")
    if f.get("adjudication"):
        adj = f["adjudication"]
        lines.append(f"- 模型仲裁：{VERDICT_LABEL.get(adj.get('verdict'), adj.get('verdict'))}——{adj.get('reason', '')}")
    if f.get("change_id"):
        applied = f"，已应用于 {f['applied_version']}" if f.get("applied_version") else ""
        lines.append(f"- 关联变更：{f['change_id']}{applied}")
    if f.get("new_since"):
        lines.append(f"- 增量复评中新出现（相对 {f['new_since']}）")
    for e in f["evidence"]:
        if e.get("quote"):
            mark = "原文已核实" if e.get("verified") else "原文未核实"
            lines.append(f"  - 第{e['chapter']}章（{mark}）：「{e['quote']}」")
        elif e.get("note"):
            lines.append(f"  - 第{e['chapter']}章：{e['note']}")
    if f.get("notes"):
        lines.append(f"- 作者备注：{f['notes']}")
    lines.append("")
    return lines


def _tag(f: Dict[str, Any]) -> str:
    return f"{f['severity']}·{RK.CATEGORY_LABEL.get(f['category'], f['category'])}"


def render(data: Dict[str, Any]) -> str:
    vid = data["version"]
    lines = _summary_lines(data, vid)
    fs = data["findings"]
    if not fs:
        lines.append("未发现问题。")
        return "\n".join(lines) + "\n"
    # 历史：已应用 / 已解决 / 仍存在 单独成节，便于追溯改动效果
    history = [f for f in fs if f["status"] in ("applied", "resolved", "still_open", "adopted")]
    if history:
        lines += [f"## 改动追踪（{len(history)}）", ""]
        for f in history:
            lines.append(f"- {STATUS_LABEL.get(f['status'], f['status'])}：{f['title']}（第{'、'.join(str(c) for c in f['chapters'])}章）")
        lines.append("")
    for sev in RK.SEVERITIES:
        group = [f for f in fs if f["severity"] == sev]
        if not group:
            continue
        lines += [f"## {sev}（{len(group)}）", ""]
        for f in group:
            lines += _finding_lines(f)
    return "\n".join(lines) + "\n"


def export(ws, version: Optional[str] = None) -> Dict[str, Any]:
    vid = RC.resolve_version(ws, version)
    data = RK.load_findings(ws, vid)
    if not data.get("updated_at") and not data["findings"]:
        raise FlowError("该版本还没有评估记录，请先运行评估")
    data.setdefault("version", vid)
    text = render(data)
    path = export_path(ws, vid)
    path.write_text(text, encoding="utf-8")
    return {"version": vid, "path": str(path.relative_to(ws.root)), "findings": len(data["findings"]), "text": text}
