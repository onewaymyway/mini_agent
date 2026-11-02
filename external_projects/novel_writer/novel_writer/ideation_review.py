"""novel_writer/ideation_review.py — 构思审核检查点：构思完成后停下，等作者确认。

开关「构思完成后暂停审核」（新建时设置，之后可在规划页修改）开启后：
  * 计划里在「前三章开篇设计」之后插入 `ideation_review` 步骤；
  * 运行该步骤会生成「构思审核清单」（蓝图概要、主题、节奏、世界观、角色外形、开篇），
    并停在 waiting_review，直到作者批准；批准后才进入章节写作；
  * 作者如果不满意，用「构思修订」提出新的思路：修订完成后，本审核会**自动重新打开**
    （刷新清单、状态改为 waiting_review），作者再次审核即可，形成「审核 → 修改 → 重新审核」的循环。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_text
from novel_writer.workspace import Workspace

EXCERPT = 500


def _excerpt(text: str, limit: int = EXCERPT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "……"


def _world_excerpt(ws: Workspace) -> str:
    path = ws.path("world_bible")
    if not path.is_file():
        return "（尚未生成世界观）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _excerpt(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        keys = "、".join(str(k) for k in data.keys())
        return f"包含：{keys}\n\n" + _excerpt(json.dumps(data, ensure_ascii=False, indent=2))
    return _excerpt(json.dumps(data, ensure_ascii=False, indent=2))


def _file_excerpt(ws: Workspace, key: str, missing: str) -> str:
    path = ws.path(key)
    return _excerpt(path.read_text(encoding="utf-8")) if path.is_file() else missing


def build_summary(ws: Workspace) -> str:
    """组装构思审核清单（Markdown）。缺失的产物会如实标注，不编造内容。"""
    from novel_writer import blueprint_store as BS
    from novel_writer import character_store as CS
    from novel_writer.pipeline import main_characters, total_chapters

    bp = BS.load_for_ws(ws, strict=False) or {}
    meta = bp.get("meta") if isinstance(bp.get("meta"), dict) else {}
    lines: List[str] = [
        "# 构思审核清单",
        "",
        "> 构思已完成，请审核下面的内容。满意后在总控台批准「构思审核」，之后进入章节写作。",
        "> 需要修改请使用「🧭 构思修订」提出新的思路；修订完成后，本审核会自动重新打开。",
        "",
        "## 蓝图概要",
        "",
    ]
    if meta:
        for key, label in (("title", "书名"), ("theme", "主题"), ("target_length", "目标篇幅"),
                           ("platform", "平台"), ("ending_type", "结局类型")):
            if meta.get(key):
                lines.append(f"- {label}：{meta[key]}")
    total = total_chapters(ws, bp if bp else None)
    lines.append(f"- 计划章节数：{total or '未定'}")
    roles = main_characters(bp if bp else None)
    lines.append(f"- 主要角色：{'、'.join(roles) or '未定'}")
    lines += ["", "## 主题设计", "", _file_excerpt(ws, "theme_design", "（尚未生成主题设计）"), ""]
    lines += ["## 节奏规划", "", _file_excerpt(ws, "pacing_design", "（尚未生成节奏规划）"), ""]
    lines += ["## 世界观", "", _world_excerpt(ws), ""]

    chars = CS.load_or_none(ws.path("characters_dir"), ws.path("characters"), ws.path("characters_legacy")) or {}
    lines += ["## 角色外形", ""]
    if not chars:
        lines.append("（尚未建立角色档案）")
    for rec in sorted(chars.values(), key=lambda r: str(r.get("id"))):
        look = CS.visual_summary(rec) or "（未记录外形）"
        gaps = CS.visual_gaps(rec)
        tail = f"　⚠ 待补充：{'、'.join(gaps)}" if gaps else ""
        lines.append(f"- **{rec.get('name')}**（{rec.get('role', '')}）：{look}{tail}")
    lines += ["", "## 前三章开篇设计", "", _file_excerpt(ws, "opening_design", "（尚未生成开篇设计）"), ""]
    return "\n".join(lines) + "\n"


def h_ideation_review(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """运行检查点：写出构思审核清单。之后的闸门为 review，由运行器置为 waiting_review。"""
    text = build_summary(ws)
    path = ws.path("ideation_review")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return {"summary": "已生成构思审核清单，等待作者确认"}


def reopen_after_revision(ws: Workspace, note: str = "构思已修订，正在自动续跑到构思审核") -> bool:
    """构思修订或撤销之后调用（开关开启时）：

    * 审核标为「需重跑」（不是直接等待审核：前面失效的构思步骤必须先重新生成，清单才是最新的）；
    * 置位 ideation_autorun，运行器会自动跑完构思范围内的步骤，然后停在构思审核等待作者批准。

    返回是否已安排（开关关闭或计划中没有审核时返回 False）。
    """
    from novel_writer import state as st
    from novel_writer.pipeline import build_plan

    if not ws.settings().get("pause_after_ideation"):
        return False
    if not any(s.id == S.IDEATION_REVIEW_ID for s in build_plan(ws)):
        return False
    st.update_step(ws, S.IDEATION_REVIEW_ID, status=st.ST_STALE, note=note)
    st.write_control(ws, ideation_autorun=True)
    return True


HANDLERS = {"ideation_review": h_ideation_review}
