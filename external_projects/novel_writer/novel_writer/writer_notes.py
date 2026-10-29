"""novel_writer/writer_notes.py — 写作备注：统一格式、识别与检查（过渡阶段）。

章节文件末尾的写作备注必须是唯一的一种格式（见 CANON_LABELS / render_notes）：

    ---

    ## 写作备注

    - 实际字数：5487
    - chapter_type：opening
    - 完成情节任务：
      - [x] 任务描述
      - [ ] 任务描述（未完成原因）
    - 本章场景覆盖：
      - [x] 场景描述
    - 埋设伏笔：fs_001（埋设方式）；无则写 无
    - 兑现伏笔：无
    - 角色状态变化：林远：…；无则写 无
    - 章末钩子类型：悬念钩子：实际钩子文本

过渡阶段：机器校验只把不规范的备注记为「提示」（warn），不阻断流程，用于观察违规率；
导出时仍由 final_export 切除备注，因此旧格式章节不会泄漏进最终稿。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 备注关键字（用于识别；比 CANON_LABELS 宽，覆盖历史上出现过的写法）
NOTE_KEYS = (
    "写作备注", "实际字数", "chapter_type", "完成情节任务", "完成的情节任务",
    "本章场景覆盖", "本章埋设的伏笔", "埋设伏笔", "埋设的伏笔", "本章兑现的伏笔", "兑现伏笔",
    "本章出场角色变化", "出场角色变化", "本章出场角色", "角色状态变化", "角色状态",
    "章末钩子", "实际钩子文本",
)
_NOTE_START = re.compile("^(" + "|".join(re.escape(k) for k in NOTE_KEYS) + ")")
_SEPARATORS = {"---", "***", "___"}
# 「短标签：内容」形式（标签不超过 8 字，且冒号后不是开引号）视为备注字段，即使内容很长也不算正文段落
_FIELD_LINE = re.compile(r"^[^\s「『“\"：:]{1,8}[：:]")
_DIALOGUE = re.compile(r"[：:][「『“\"]")   # 冒号后直接跟开引号 = 对白

# 统一格式：标题 + 固定字段（顺序固定，每项都必须出现）
CANON_HEADING = "## 写作备注"
CANON_LABELS: Tuple[str, ...] = (
    "实际字数", "chapter_type", "完成情节任务", "本章场景覆盖",
    "埋设伏笔", "兑现伏笔", "角色状态变化", "章末钩子类型",
)
_INT_LINE = re.compile(r"^- 实际字数：\d+$")


def norm(line: str) -> str:
    """去掉 markdown 与括号装饰后的纯文本，用于识别备注关键字。"""
    s = re.sub(r"[*【】]", "", line.strip())
    s = re.sub(r"^[#>\s]+", "", s)
    return s.strip()


def is_note_start(line: str) -> bool:
    return bool(_NOTE_START.match(norm(line)))


def is_prose(line: str) -> bool:
    """正文样式的长段落：非空、非分隔线、非备注关键字、非缩进/列表项，且较长。"""
    s = line.strip()
    if not s or s in _SEPARATORS or is_note_start(line):
        return False
    if line[:1].isspace() or s.startswith(("-", "•", "✓", "✗", "×", "✔")):
        return False
    if _FIELD_LINE.match(norm(line)) and not _DIALOGUE.search(s):
        return False
    return len(norm(line)) > 60


def tail_cut(lines: Sequence[str]) -> int:
    """返回结尾备注块的起始行下标（没有则为 len(lines)）。

    取最早的备注关键字行，且其后不存在正文样式的长段落，才认为是备注块。
    """
    for i, line in enumerate(lines):
        if not is_note_start(line):
            continue
        if any(is_prose(rest) for rest in lines[i:]):
            continue
        return i
    return len(lines)


def render_notes(fields: Dict[str, Any]) -> str:
    """按统一格式渲染备注块。fields 键：word_count, chapter_type, plot_tasks, scenes,
    planted, resolved, character_changes, hook。列表值为空时写「无」。"""

    def items(value: Any) -> List[str]:
        if not value:
            return []
        return [str(v).strip() for v in value if str(v).strip()]

    def line_or_none(value: Any) -> str:
        parts = items(value)
        return "；".join(parts) if parts else "无"

    out = ["---", "", CANON_HEADING, ""]
    out.append(f"- 实际字数：{int(fields.get('word_count') or 0)}")
    out.append(f"- chapter_type：{str(fields.get('chapter_type') or '').strip() or '未标注'}")
    out.append("- 完成情节任务：")
    for task in fields.get("plot_tasks") or []:
        done = bool(task.get("done")) if isinstance(task, dict) else True
        text = task.get("text", "") if isinstance(task, dict) else str(task)
        reason = task.get("reason", "") if isinstance(task, dict) else ""
        suffix = f"（未完成原因：{reason}）" if (not done and reason) else ""
        out.append(f"  - [{'x' if done else ' '}] {text}{suffix}")
    if not fields.get("plot_tasks"):
        out.append("  - 无")
    out.append("- 本章场景覆盖：")
    for scene in fields.get("scenes") or []:
        covered = bool(scene.get("covered", True)) if isinstance(scene, dict) else True
        text = scene.get("text", "") if isinstance(scene, dict) else str(scene)
        out.append(f"  - [{'x' if covered else ' '}] {text}")
    if not fields.get("scenes"):
        out.append("  - 无")
    out.append(f"- 埋设伏笔：{line_or_none(fields.get('planted'))}")
    out.append(f"- 兑现伏笔：{line_or_none(fields.get('resolved'))}")
    out.append(f"- 角色状态变化：{line_or_none(fields.get('character_changes'))}")
    out.append(f"- 章末钩子类型：{str(fields.get('hook') or '').strip() or '无'}")
    return "\n".join(out) + "\n"


def audit_notes(text: str) -> List[str]:
    """检查章节文件末尾的备注是否符合统一格式。返回问题列表（空列表 = 合格）。"""
    lines = text.splitlines()
    cut = tail_cut(lines)
    if cut == len(lines):
        if any(is_note_start(l) for l in lines):
            return ["备注不在正文末尾的统一块中，或字段名不在统一格式内"]
        return ["缺少写作备注块"]

    issues: List[str] = []
    block = lines[cut:]
    before = [l for l in lines[:cut] if l.strip()]
    if not before or before[-1].strip() != "---":
        issues.append("备注块前缺少分隔线 ---")
    nonblank = [l for l in block if l.strip()]
    if not nonblank or nonblank[0].strip() != CANON_HEADING:
        issues.append(f"备注块标题应为「{CANON_HEADING}」")

    seen: List[str] = []
    for line in block:
        if not line.strip() or line.strip() == CANON_HEADING or line.strip() in _SEPARATORS:
            continue
        if line.startswith("  "):
            continue                         # 子项（任务、场景）
        if not line.startswith("- "):
            issues.append(f"字段行格式不统一：{line.strip()[:24]}")
            continue
        label = line[2:].split("：", 1)[0].strip()
        if label not in CANON_LABELS:
            issues.append(f"未知字段：{label[:20]}")
        else:
            seen.append(label)
    missing = [l for l in CANON_LABELS if l not in seen]
    if missing:
        issues.append("缺少字段：" + "、".join(missing))
    elif seen != list(CANON_LABELS):
        issues.append("字段顺序与统一格式不一致")
    if "实际字数" in seen and not any(_INT_LINE.match(l) for l in block):
        issues.append("实际字数应为整数（如「实际字数：5487」）")
    return issues
