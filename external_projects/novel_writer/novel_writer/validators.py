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

from typing import Any, List, Sequence

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
