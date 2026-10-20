"""novel_writer/json_repair.py — 损坏 JSON 的抢救与分条修复（通用，供迁移与后续各阶段使用）。

分层处理，代价逐层升高，能不调 LLM 就不调：
1. 严格解析：合法则直接用（origin = "strict"）。
2. 宽松清洗后解析：去代码围栏、去尾逗号（origin = "lenient"）。
3. 按数组条目拆分：整文件无法解析时，逐条解析；能解析的保留，失败的条目记为「待修复」。
4. 待修复条目交给修复函数（通常是 LLM 技能 json-repair）；修复后仍不合法的，由调用方隔离。

任何 origin 不是 strict 的条目，都意味着内容经过了推断或修改，调用方必须让作者复核。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

RepairFn = Callable[[str, str], Optional[str]]   # (损坏片段原文, 提示) -> 修复后的 JSON 文本；无法修复返回 None

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def clean_text(text: str) -> str:
    t = text.lstrip("\ufeff").strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1)
    return _TRAILING_COMMA.sub(r"\1", t)


def lenient_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(clean_text(text))


def _scan_array_items(text: str, start: int) -> Tuple[List[str], bool]:
    """从 start（数组内容的起点，即 '[' 之后）扫描顶层条目原文。返回 (条目原文列表, 数组是否正常闭合)。
    正确处理字符串与转义；未闭合的条目会一直收到文本末尾（这正是截断损坏的情形）。"""
    items: List[str] = []
    depth = 0
    in_str = False
    esc = False
    cur_start: Optional[int] = None
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                if depth == 0 and ch == "{":
                    cur_start = i
                depth += 1
            elif ch in "}]":
                if depth == 0:
                    # 数组在此闭合
                    return items, ch == "]"
                depth -= 1
                if depth == 0 and ch == "}" and cur_start is not None:
                    items.append(text[cur_start:i + 1])
                    cur_start = None
        i += 1
    if cur_start is not None:           # 最后一个条目被截断
        items.append(text[cur_start:])
    return items, False


def split_array(text: str, key: str) -> Tuple[List[str], bool]:
    """取出根对象中 key 对应数组的各条目原文。找不到该数组返回 ([], False)。"""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), text)
    if not m:
        return [], False
    return _scan_array_items(text, m.end())


def extract_object(text: str, key: str) -> Optional[Dict[str, Any]]:
    """取出根对象中 key 对应的对象（平衡花括号），宽松解析；失败返回 None。"""
    m = re.search(r'"%s"\s*:\s*\{' % re.escape(key), text)
    if not m:
        return None
    start = m.end() - 1
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    val = lenient_loads(text[start:i + 1])
                    return val if isinstance(val, dict) else None
                except ValueError:
                    return None
    return None


@dataclass
class Salvage:
    records: List[Tuple[Dict[str, Any], str]] = field(default_factory=list)   # (条目, origin)
    pending: List[str] = field(default_factory=list)                          # 待修复的条目原文
    applied: Optional[Dict[str, Any]] = None
    applied_lost: bool = False
    whole_ok: bool = False
    notes: List[str] = field(default_factory=list)


def salvage_ledger(text: str, list_key: str, applied_key: str) -> Salvage:
    """抢救一份账本式 JSON（根对象 + 一个条目数组 + 可选的 applied 对象）。"""
    out = Salvage()
    for origin, src in (("strict", text), ("lenient", clean_text(text))):
        try:
            obj = json.loads(src)
        except ValueError:
            continue
        if isinstance(obj, dict):
            items = obj.get(list_key) or []
            out.records = [(it, origin) for it in items if isinstance(it, dict)]
            if len(out.records) != len(items):
                out.notes.append("数组中存在非对象条目，已跳过")
            out.applied = obj.get(applied_key) if isinstance(obj.get(applied_key), dict) else None
            out.whole_ok = True
            return out
    # 整体不可解析：按条目拆分
    raw_items, closed = split_array(text, list_key)
    if not raw_items and not closed:
        out.notes.append(f"找不到 {list_key} 数组，无法拆分抢救")
    if not closed:
        out.notes.append("条目数组未正常闭合（文件可能被截断）")
    for raw in raw_items:
        for origin, src in (("strict", raw), ("lenient", clean_text(raw))):
            try:
                obj = json.loads(src)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.records.append((obj, origin))
                break
        else:
            out.pending.append(raw)
    applied = extract_object(text, applied_key)
    if applied is not None:
        out.applied = applied
    else:
        out.applied_lost = applied_key in text
        if out.applied_lost:
            out.notes.append(f"{applied_key} 无法解析，相关的增量撤销记录丢失")
    return out


def repair_pending(pending: List[str], repair: Optional[RepairFn], hint: str,
                   validate: Callable[[Dict[str, Any]], Optional[str]]) -> Tuple[List[Tuple[Dict[str, Any], str]], List[str]]:
    """对待修复条目调用修复函数。返回 (修复成功的 (条目, "llm"), 仍失败的原文)。
    validate 返回 None 表示合格，否则返回不合格的原因；修复结果必须通过校验才算成功。"""
    fixed: List[Tuple[Dict[str, Any], str]] = []
    failed: List[str] = []
    for raw in pending:
        result = None
        if repair is not None:
            try:
                txt = repair(raw, hint)
            except Exception:  # noqa: BLE001  修复器自身异常不应中断迁移
                txt = None
            if txt:
                try:
                    obj = lenient_loads(txt)
                    if isinstance(obj, dict) and validate(obj) is None:
                        result = obj
                except ValueError:
                    result = None
        if result is not None:
            fixed.append((result, "llm"))
        else:
            failed.append(raw)
    return fixed, failed


def make_skill_repair(invoker: Callable[..., Any], workdir: Path, step_id: str = "json_repair") -> RepairFn:
    """基于框架技能 json-repair 的修复函数。每条损坏片段单独调用一次，结果写入 workdir/repaired.json。"""
    workdir = Path(workdir)

    def repair(raw: str, hint: str) -> Optional[str]:
        export = workdir / "item"
        export.mkdir(parents=True, exist_ok=True)
        out = export / "repaired.json"
        if out.exists():
            out.unlink()
        prompt = (
            "【任务】修复下面这段损坏的 JSON 对象，只改正语法错误（如缺失的引号、括号、逗号，截断的尾部），"
            "不得删减或改写任何字段的内容；无法确定的值保留为 null。\n"
            f"【结构要求】{hint}\n"
            f"【输出】把修复后的一个 JSON 对象（只含对象本身，无其它文字）写入：{out}\n"
            "【损坏原文】\n" + raw
        )
        invoker(skill="json-repair", prompt=prompt, export_dir=export, step_id=step_id)
        return out.read_text(encoding="utf-8") if out.is_file() else None

    return repair
