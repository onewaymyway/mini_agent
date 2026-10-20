"""novel_writer/validators.py — 回执契约与产出文件校验（零 LLM 成本，方案 7.2 的基础部分）。

回执（receipt.json）契约，由每个 skill 步骤在结束时写出：
  {
    "status": "done" | "needs_user",   # needs_user 表示需要作者决定，引擎会挂起该步
    "summary": "一句话说明做了什么",
    "outputs": ["相对工作目录的路径", ...],   # 可选，仅供展示
    "needs_user": [{"id": "...", "question": "...", "options": ["..."]}]   # needs_user 时必填
  }
M1 只校验结构；内容质量（字数、伏笔、人物名白名单等）由 M3 的机器校验负责。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from novel_writer.fsutil import atomic_write_json
from novel_writer.textio import read_text_utf8
from novel_writer.workspace import Workspace

RECEIPT_STATUSES = ("done", "needs_user")


def receipt_problems(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return [f"回执顶层不是 JSON 对象，而是 {type(data).__name__}"]
    problems: List[str] = []
    status = data.get("status")
    if status not in RECEIPT_STATUSES:
        problems.append(f"回执 status 必须是 {list(RECEIPT_STATUSES)} 之一，当前 {status!r}")
    if status == "needs_user":
        items = data.get("needs_user")
        if not isinstance(items, list) or not items:
            problems.append("status 为 needs_user 时必须给出非空的 needs_user 列表")
        else:
            for i, item in enumerate(items):
                if not isinstance(item, dict) or not str(item.get("question", "")).strip():
                    problems.append(f"needs_user[{i}] 缺少 question")
    return problems


def _is_glob(spec: str) -> bool:
    return spec.startswith("glob:") or "*" in spec


def missing_outputs(ws: Workspace, outputs: Sequence[str]) -> List[str]:
    """返回缺失的产出（相对路径）。`glob:` 前缀或含 `*` 的按模式匹配，至少要有一个文件。"""
    missing: List[str] = []
    for spec in outputs:
        if _is_glob(spec):
            pattern = spec[len("glob:"):] if spec.startswith("glob:") else spec
            if not any(p.is_file() for p in ws.root.glob(pattern)):
                missing.append(spec)
        else:
            if not (ws.root / spec).is_file():
                missing.append(spec)
    return missing


# ── 产出内容校验（M8）──────────────────────────────────────────────
# 框架只检查产出文件「存在」；对于下游要读取的结构化产出（如择优报告），还要检查内容。
# 内容不合格时，与回执问题一样作为提示交给 agent 重写（见 runner.MAX_RECEIPT_ATTEMPTS）。
# 校验可以顺便做规范化（如 "a"→"A"），规范化结果写回文件，避免下游因写法不同而失败。

PICK_WINNER_ALIASES: Dict[str, str] = {
    "A": "A", "A稿": "A", "正文": "A", "正文稿": "A", "原稿": "A",
    "B": "B", "B稿": "B", "候选": "B", "候选稿": "B", "候选稿B": "B",
}


def normalize_pick_winner(value: Any) -> Optional[str]:
    """把择优结论规范成 "A" / "B"；无法识别返回 None（调用方不得猜测）。"""
    if not isinstance(value, str):
        return None
    return PICK_WINNER_ALIASES.get(value.strip().replace(" ", "").upper())


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)


def best_pick_report_problems(path: Path) -> List[str]:
    """检查择优报告：必须是 JSON 对象，且 winner 能规范为 A/B。合格时写回规范化结果。"""
    if not path.is_file():
        return [f"择优报告不存在：{path.name}"]
    raw = read_text_utf8(path).strip()
    fenced = False
    m = _FENCE.match(raw)
    if m:
        raw, fenced = m.group(1).strip(), True
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return [f"择优报告不是合法 JSON（{exc}）。请只输出一个 JSON 对象，不要夹杂说明文字。"]
    if not isinstance(data, dict):
        return [f"择优报告顶层必须是 JSON 对象，当前是 {type(data).__name__}"]
    winner = normalize_pick_winner(data.get("winner"))
    if winner is None:
        return [f"择优报告的 winner 必须是 \"A\"（正文）或 \"B\"（候选稿），当前为 {data.get('winner')!r}"]
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        return ["择优报告的 reason 必须是字符串"]
    if data.get("winner") != winner or fenced:
        data["winner"] = winner
        atomic_write_json(path, data)
    return []


def _check_best_pick(ws: Workspace, step: Any) -> List[str]:
    return best_pick_report_problems(ws.path("best_pick_report", nn=step.chapter))


OUTPUT_CHECKS: Dict[str, Callable[[Workspace, Any], List[str]]] = {
    "best_pick_report": _check_best_pick,
}


def content_problems(ws: Workspace, step: Any) -> List[str]:
    """步骤声明的产出内容校验（step.extra["output_check"]）。未声明则不校验。"""
    name = (getattr(step, "extra", None) or {}).get("output_check")
    if not name:
        return []
    checker = OUTPUT_CHECKS.get(name)
    if checker is None:
        raise KeyError(f"未知的产出校验：{name}")
    return checker(ws, step)
