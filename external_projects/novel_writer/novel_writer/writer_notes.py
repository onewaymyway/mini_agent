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

识别方式（2026-11 起）：按「块」识别——从备注起始行开始，一直吸收到下一个真正的正文段落为止，
备注块可以出现在文件中间（如备注之后又被追加了内容），也不会因为块内某一行长得像正文而整块漏切。
旧的 `tail_cut`（"最早备注行之后不得有任何正文"）保留，仅作兼容，导出与检查不再使用它。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 备注关键字（用于识别；比 CANON_LABELS 宽，覆盖历史上出现过的写法）
NOTE_KEYS = (
    "写作备注", "实际字数", "chapter_type", "完成情节任务", "完成的情节任务",
    "本章场景覆盖", "本章埋设的伏笔", "埋设伏笔", "埋设的伏笔", "本章兑现的伏笔", "兑现伏笔",
    "本章出场角色变化", "出场角色变化", "本章出场角色", "角色状态变化", "角色状态",
    "章末钩子", "实际钩子文本", "钩子文本",
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


# ── 按块识别（导出清洗与格式检查共用）────────────────────────────────────────

# 框架在截断工具输出时追加的提示行；它属于运行时的杂质，不是正文，也不是备注
_FRAMEWORK_MARKER = re.compile(r"^\s*\[full output saved\b.*\]\s*$")
# 加粗标签行：**钩子文本：** …（内容可以很长，甚至带对白，但整行仍属于备注）
_BOLD_LABEL = re.compile(r"^\s*\*\*[^*]{1,20}\*\*")
# 备注关键字后必须紧跟的字符：结束 / 冒号 / 括号 / 空白 / 加粗符号，防止「实际字数的问题……」这类正文被当成备注
_LABEL_END = re.compile(r"^(?:$|[：:（(\s*】])")
# 备注起始行最长字符数：超过这个长度的行不当作备注标签（更像是以关键字开头的正文）
NOTE_START_MAX_LEN = 100


# 模型误写进文件的工具调用/标记残留：独占一行的 </parameter>、</intext>、<content> 等
_STRAY_TAG = re.compile(
    r"^\s*</?(?:parameter|intext|content|invoke|function_calls|function_results|file_text|new_string|old_string|"
    r"tool_use|tool_result|result|output|antml:[\w\-]+)(?:\s[^<>]*)?>\s*$", re.I)
# 括号包住的整行：「（本章无待兑现伏笔）」「（无）」——备注字段值常这样单独成行
_BRACKETED = re.compile(r"^\s*[（(【\[].{0,60}[）)】\]]\s*$")


def is_stray_tag(line: str) -> bool:
    """是否为独占一行的工具调用残留标签（如 `</parameter>`、`</intext>`）。"""
    return bool(_STRAY_TAG.match(line))


def strip_stray_tags(lines: Sequence[str]) -> Tuple[List[str], int]:
    """删除所有工具调用残留标签行，返回 (剩余行, 删除数)。"""
    kept = [l for l in lines if not is_stray_tag(l)]
    return kept, len(lines) - len(kept)


def is_framework_marker(line: str) -> bool:
    """是否为框架追加的「[full output saved — …]」截断提示行。"""
    return bool(_FRAMEWORK_MARKER.match(line))


def strip_framework_markers(lines: Sequence[str]) -> Tuple[List[str], int]:
    """删除所有截断提示行，返回 (剩余行, 删除数)。"""
    kept = [l for l in lines if not is_framework_marker(l)]
    return kept, len(lines) - len(kept)


def is_note_label(line: str) -> bool:
    """备注块的起始行：以备注关键字开头，且关键字后紧跟标签结束符，长度不过长。

    比 `is_note_start` 严格：「实际字数的问题他并没有想过……」这种以关键字开头的正文不算。
    """
    n = norm(line)
    m = _NOTE_START.match(n)
    if not m or len(n) > NOTE_START_MAX_LEN:
        return False
    return bool(_LABEL_END.match(n[m.end():]))


# 对白引导语的结尾：「影子说：“…”」「他问道：“…”」——标签以言说/思考动词结尾，说明冒号后的引文是对白
_SPEECH_END = re.compile(r"(说|道|问|答|喊|叫|笑|想|称|低语|回答|回应|补充|开口|接着|嘀咕|喃喃)$")
_OPEN_QUOTES = ("「", "『", "“", '"')


def _is_field_line(line: str) -> bool:
    """「短标签：内容」形式的字段行（含内容以引号开头的，如「钩子文本："……"」）。

    冒号后直接是开引号时要区分两种情况：标签以言说动词结尾（「他说：“…”」）= 对白，不是字段；
    其它标签（「钩子文本：“…”」）= 字段。只在标签位置判断，不在整行里搜「冒号+引号」。
    """
    n = norm(line)
    m = _FIELD_LINE.match(n)
    if not m:
        return False
    if n[m.end():].lstrip()[:1] not in _OPEN_QUOTES:
        return True
    return not _SPEECH_END.search(n[:m.end() - 1])


def _in_note_block(line: str) -> bool:
    """备注块内部的行：空行、分隔线、缩进行、列表行、加粗标签行、短标签字段行、备注关键字行、截断提示行。"""
    s = line.strip()
    if not s or s in _SEPARATORS or line[:1].isspace():
        return True
    if s.startswith(("-", "•", "✓", "✗", "×", "✔", "*", "[")):
        return True
    return (is_framework_marker(line) or is_stray_tag(line) or bool(_BOLD_LABEL.match(line))
            or bool(_BRACKETED.match(line)) or is_note_start(line) or _is_field_line(line))


def _is_value_line(lines: Sequence[str], block_start: int, idx: int) -> bool:
    """备注字段的值单独成行：上一行有内容的行是以冒号结尾的标签（「兑现伏笔（id）：」），本行较短，则本行是它的值。"""
    if len(lines[idx].strip()) > 80:
        return False
    for k in range(idx - 1, block_start - 1, -1):
        if lines[k].strip() and lines[k].strip() not in _SEPARATORS:
            return norm(lines[k]).endswith(("：", ":"))
    return False


def find_note_blocks(lines: Sequence[str]) -> List[Tuple[int, int]]:
    """返回所有备注块的 [起, 止) 行下标（左闭右开）。

    起点是备注起始行（`is_note_label`）；块一直延伸到第一个「不属于备注」的行，也就是恢复成正文的位置。
    块的起点会向前吸收紧邻的空行与分隔线（`---`），这样删除后不留空洞。
    """
    blocks: List[Tuple[int, int]] = []
    i, n = 0, len(lines)
    while i < n:
        if is_note_label(lines[i]):
            start = i
            while start > 0 and (not lines[start - 1].strip() or lines[start - 1].strip() in _SEPARATORS):
                start -= 1
            end = i + 1
            while end < n:
                if not _in_note_block(lines[end]) and not _is_value_line(lines, i, end):
                    break
                end += 1
            blocks.append((start, end))
            i = end
        else:
            i += 1
    return blocks


def strip_note_blocks(lines: Sequence[str]) -> Tuple[List[str], List[Tuple[int, int]]]:
    """删除所有备注块，返回 (剩余行, 被删除的区间)。区间下标针对传入的 lines。"""
    blocks = find_note_blocks(lines)
    drop = {k for a, b in blocks for k in range(a, b)}
    return [l for k, l in enumerate(lines) if k not in drop], blocks


def content_after_first_block(lines: Sequence[str], blocks: Sequence[Tuple[int, int]]) -> int:
    """第一个备注块之后仍留在文件里的正文行数（不含空行、分隔线、其它备注块）。"""
    if not blocks:
        return 0
    drop = {k for a, b in blocks for k in range(a, b)}
    return sum(1 for k in range(blocks[0][1], len(lines))
               if k not in drop and lines[k].strip() and lines[k].strip() not in _SEPARATORS)


def layout_problems(text: str) -> List[str]:
    """备注的「位置/结构」问题（比字段格式更严重，会让备注混进正文）。

    - 文件里含框架截断提示：说明章节可能是基于被截断的工具输出写入的，正文可能不完整；
    - 出现多个备注块；
    - 备注块之后仍有正文（备注必须在文件最末尾）。
    返回的文案同时是写给修改轮次的指令，要能让 agent 照着改。
    """
    lines = text.splitlines()
    issues: List[str] = []
    markers = sum(1 for l in lines if is_framework_marker(l))
    if markers:
        issues.append(f"章节文件中混入了 {markers} 处「[full output saved …]」工具截断提示：删除这些行，"
                      "并按提示里的路径读取完整原文，核对正文是否被截断后补全")
    tags = sum(1 for l in lines if is_stray_tag(l))
    if tags:
        issues.append(f"章节文件中混入了 {tags} 行工具调用残留标签（如 </parameter>、</intext>）：直接删除这些行")
    blocks = find_note_blocks(lines)
    # 紧挨着的两个备注块会被合并成一个区间，所以同时按「写作备注」标题的个数判断
    heads = sum(1 for l in lines if norm(l).startswith("写作备注"))
    count = max(len(blocks), heads)
    if count > 1:
        issues.append(f"出现 {count} 个写作备注块：只保留最末尾一个，其余删除")
    after = content_after_first_block(lines, blocks)
    if blocks and after:
        issues.append(f"写作备注块之后仍有 {after} 行正文：备注必须位于文件最末尾，"
                      "把备注块下方的正文移到备注块之前（或删除重复内容），正文本身不要改动")
    return issues


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
    """检查章节文件的备注是否符合统一格式。返回问题列表（空列表 = 合格）。

    先报位置/结构问题（`layout_problems`），再按第一个备注块检查字段格式。
    """
    lines = text.splitlines()
    blocks = find_note_blocks(lines)
    if not blocks:
        if any(is_note_start(l) for l in lines):
            return layout_problems(text) + ["备注不在统一块中，或字段名不在统一格式内"]
        return layout_problems(text) + ["缺少写作备注块"]

    issues: List[str] = layout_problems(text)
    cut, end = blocks[0]
    # 起点已向前吸收了分隔线；格式检查要看「标题」，所以从第一行非空、非分隔线的内容开始
    block = lines[cut:end]
    before = [l for l in lines[:cut] if l.strip()]
    sep_before = any(l.strip() in _SEPARATORS for l in block[:3] if l.strip()) or (
        bool(before) and before[-1].strip() == "---")
    if not sep_before:
        issues.append("备注块前缺少分隔线 ---")
    nonblank = [l for l in block if l.strip() and l.strip() not in _SEPARATORS]
    if not nonblank or nonblank[0].strip() != CANON_HEADING:
        issues.append(f"备注块标题应为「{CANON_HEADING}」")

    seen: List[str] = []
    for line in block:
        if not line.strip() or line.strip() == CANON_HEADING or line.strip() in _SEPARATORS:
            continue
        if is_framework_marker(line):
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
