"""novel_writer/repair.py — 数据文件校验失败时，由 LLM 修复（通用机制）。

问题背景：有些步骤是纯代码执行的（如 context_pack），它读取的数据文件（人物档案、伏笔账本…）
由 LLM 生成。文件格式坏了以后，代码步骤每次都会报同一个错，原来的自动重跑只是把错误文字塞进提示词，
而代码步骤根本没有 LLM，错误信息没有任何机会被修复，于是无限重复失败。

机制：
1. 校验函数抛出 RepairableDataError，携带：校验器名（VALIDATORS 中注册的名字）、出问题的文件（相对工作区）、
   逐条问题（含行号）。runner 把它记入步骤状态的 repair 字段。
2. worker 在自动重跑之前，若步骤带有 repair 请求，先调用 repair_attempt：
   把问题与文件当前内容交给 LLM（skill: data-repair），让它只修改列出的文件；
3. 修复后重新运行校验器：
   - 问题数量变多 → 回滚所有改动（包括范围外的文件）；
   - 超出范围的文件被改动 → 一律还原；
   - 问题全部消失 → 步骤重新执行；
4. 修复次数有上限（settings.repair_max_attempts，默认 3，按步骤累计，通过后清零）。用尽后不再修复、
   也不再「不停机」式地重置，直接停下来等人处理，并在运行日志里写明原因。

settings 开关：auto_repair（默认 True）；repair_engine（默认 "restricted"）。

修复执行引擎（settings.repair_engine）：
- "restricted"（默认）：受限 Agent。**不挂载任何 skill**（模型没有 skill_activate，不可能去激活别的 skill 做别的步骤），
  只开放 REPAIR_ALLOWED_TOOLS 这组内置文件/目录工具；达到轮次上限直接停止（不继承全局 compact_continue）；
  data-repair 的规则直接写进提示词，文件路径一律给绝对路径。
- "agent"：旧方式，经 skill_step 工作流激活 data-repair skill，使用完整主 Agent。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer import state as st
from novel_writer.fsutil import atomic_write_text
from novel_writer.textio import read_text_utf8
from novel_writer.workspace import Workspace

MAX_REPAIR_ATTEMPTS = 3
REPAIR_SKILL = "data-repair"
REPAIR_MAX_TURNS = 20
REPAIR_TIMEOUT_SEC = 900

ENGINE_RESTRICTED = "restricted"
ENGINE_AGENT = "agent"
ENGINES = (ENGINE_RESTRICTED, ENGINE_AGENT)
# 受限修复 Agent 可用的内置工具（白名单）：读/写/改文件 + 目录浏览与搜索。
# 刻意不含 bash、delete_file、web_search、笔记本/记忆/skill 管理类工具。
REPAIR_ALLOWED_TOOLS = (
    "read_file", "write_file", "create_file", "patch_file", "patch_file_simple",
    "list_dir", "tree_summary", "glob", "grep",
)

# 校验器：名字 → 函数(ws) -> 问题列表（空列表 = 通过）
VALIDATORS: Dict[str, Callable[[Workspace], List[str]]] = {}


class RepairableDataError(Exception):
    """数据文件校验失败，可以交给 LLM 修复。"""

    def __init__(self, message: str, *, validator: str, files: List[str], problems: List[str],
                 scope: Optional[str] = None) -> None:
        super().__init__(message)
        self.validator = validator
        self.files = list(files)          # 允许修改的文件（相对工作区）
        self.problems = list(problems)
        self.scope = scope                # 范围目录（相对工作区）：修复时不得改动其中的其他文件


def register_validator(name: str):
    def deco(fn: Callable[[Workspace], List[str]]):
        VALIDATORS[name] = fn
        return fn
    return deco


@register_validator("characters")
def _characters(ws: Workspace) -> List[str]:
    from novel_writer import character_store as CS
    return CS.problems_for_dir(ws.path("characters_dir"))


# ── 状态与开关 ──────────────────────────────────────────────────────────────

def request_of(ws: Workspace, step_id: str) -> Optional[Dict[str, Any]]:
    req = st.get_step_state(ws, step_id).get("repair")
    return req if isinstance(req, dict) and req.get("validator") in VALIDATORS else None


def enabled(settings: Dict[str, Any]) -> bool:
    return bool((settings or {}).get("auto_repair", True))


def engine(settings: Dict[str, Any]) -> str:
    """修复执行引擎；取值非法时退回默认的受限引擎。"""
    value = (settings or {}).get("repair_engine", ENGINE_RESTRICTED)
    return value if value in ENGINES else ENGINE_RESTRICTED


def max_attempts(settings: Dict[str, Any]) -> int:
    try:
        return max(0, int((settings or {}).get("repair_max_attempts", MAX_REPAIR_ATTEMPTS)))
    except (TypeError, ValueError):
        return MAX_REPAIR_ATTEMPTS


def exhausted(ws: Workspace, step_id: str, settings: Dict[str, Any]) -> bool:
    used = int(st.get_step_state(ws, step_id).get("repair_attempts") or 0)
    return used >= max_attempts(settings)


# ── 修复 ────────────────────────────────────────────────────────────────────

def _build_prompt(step_id: str, request: Dict[str, Any], texts: Dict[str, str], problems: List[str],
                  *, root: Optional[Path] = None) -> str:
    """root 给出时（受限引擎）：文件一律用绝对路径，并内联 data-repair skill 的全部规则。"""
    def show(rel: str) -> str:
        return str((Path(root) / rel)) if root is not None else rel

    lines = [
        "下面的数据文件无法被程序读取。请只修复这些文件中的格式错误，使它们能通过读取校验。",
        "",
        "## 必须修复的问题（逐条，行号以文件为准）",
        *[f"- {p}" for p in problems],
        "",
        "## 修复规则",
        f"- 只能修改以下文件{'（绝对路径）' if root is not None else '（路径相对工作区）'}："
        f"{'、'.join(show(f) for f in request['files'])}。不得修改、创建或删除任何其他文件。",
        "- 保持原意与内容，不得删减、合并或编造信息；只修正格式（引号、转义、类型、缺失的字段名等）。",
        "- 字符串值必须写在同一行；字符串内需要的引号用中文「」或『』，不要用英文双引号。",
        "- 文件头的 id 必须与文件名一致；同一章的记录只能出现一次。",
        "- 信息确实缺失时，用合法的空值（如 []、null、\"\"）补齐，不得编造。",
    ]
    if root is not None:
        lines += [
            "- 你只有读写文件和浏览目录的工具，没有其他能力。这是一次独立的数据修复任务：不要做写作、审核、规划等任何别的事，"
            "也不要检查或处理其他步骤。",
            "- 上面的文件已给出绝对路径，直接用该路径读写，不需要再搜索。",
            "- 修改完成并确认文件已保存后，直接以一句话结束（例如「已修复」），不要继续做任何其他工作。",
        ]
    else:
        lines.append("- 直接保存修改后的文件，不需要输出额外说明。")
    lines += ["", "## 当前文件内容"]
    for rel, text in texts.items():
        lines += [f"### {show(rel)}", "```", text.rstrip(), "```", ""]
    return "\n".join(lines)


_TEXT_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml", ".csv"}


def _snapshot(ws: Workspace) -> Dict[Path, bytes]:
    skip = [ws.studio.resolve(), ws.path("export_dir").resolve()]
    out: Dict[Path, bytes] = {}
    for p in ws.root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _TEXT_SUFFIXES or "__pycache__" in p.parts:
            continue
        rp = p.resolve()
        if any(rp == d or d in rp.parents for d in skip):
            continue
        out[p] = p.read_bytes()
    return out


def repair_attempt(ws: Workspace, step, *, invoker, settings: Dict[str, Any],
                   request: Dict[str, Any]) -> Dict[str, Any]:
    """调用一次 LLM 修复。返回 {fixed, problems, changed, rolled_back}。

    注意：调用方需先确认 exhausted() 为假。每次调用都会把 repair_attempts 加一。
    """
    from novel_writer import runner as R  # 避免循环导入

    validator = VALIDATORS[request["validator"]]
    # 先计数再判断：即使问题已经自行消失也算一次尝试，否则「校验通过→步骤仍失败」会无限循环
    used = int(st.get_step_state(ws, step.id).get("repair_attempts") or 0) + 1
    st.update_step(ws, step.id, repair_attempts=used)
    before = validator(ws)
    if not before:
        st.log_line(ws, f"🔧 {step.id}：校验已通过，无需 LLM 修复")
        return {"fixed": True, "problems": [], "changed": [], "rolled_back": False}

    files = [ws.root / rel for rel in request["files"]]
    # 快照整个工作区的文本文件（排除运行日志与导出目录）：LLM 可能改动任何地方，越界改动必须能被还原
    snapshot = _snapshot(ws)
    texts = {rel: read_text_utf8(ws.root / rel) for rel in request["files"] if (ws.root / rel).is_file()}

    st.log_line(ws, f"🔧 {step.id}：数据校验失败，交给 LLM 修复（第 {used}/{max_attempts(settings)} 次）："
                    f"{'；'.join(before[:3])[:160]}")
    export_dir = ws.step_dir(f"{step.id}.repair", used)
    export_dir.mkdir(parents=True, exist_ok=True)
    if engine(settings) == ENGINE_RESTRICTED:
        # 受限 Agent：不挂 skill（skill=None），只开白名单工具；规则内联进提示词
        result = invoker(
            skill=None, prompt=_build_prompt(step.id, request, texts, before, root=ws.root),
            export_dir=export_dir, step_id=f"{step.id}.repair",
            max_turns=REPAIR_MAX_TURNS, timeout=REPAIR_TIMEOUT_SEC,
            allowed_tools=list(REPAIR_ALLOWED_TOOLS),
        )
    else:
        result = invoker(
            skill=REPAIR_SKILL, prompt=_build_prompt(step.id, request, texts, before),
            export_dir=export_dir, step_id=f"{step.id}.repair",
            max_turns=REPAIR_MAX_TURNS, timeout=REPAIR_TIMEOUT_SEC,
        )
    R._log_agent_output(ws, f"{step.id}.repair", result)
    if not getattr(result, "ok", False):
        st.log_line(ws, f"⚠ {step.id}：修复调用未成功：{getattr(result, 'error', '') or '未知原因'}")

    after = validator(ws)
    changed = [str(p.relative_to(ws.root)) for p, data in snapshot.items()
               if p.is_file() and p.read_bytes() != data]
    changed += [str(p.relative_to(ws.root)) for p in _snapshot(ws).keys() if p not in snapshot]   # 新建的文件也算越界
    out_of_scope = [rel for rel in changed if rel not in request["files"]]
    rolled_back = False
    if len(after) > len(before) or out_of_scope:
        # 改坏了，或者动了范围外的文件：全部还原
        after_count = len(after)
        for p, data in snapshot.items():
            if p.is_file() and p.read_bytes() != data:
                p.write_bytes(data)
        for p in list(_snapshot(ws).keys()):   # 修复期间新建的文件（受限引擎开放了写文件工具）一并删除
            if p not in snapshot:
                p.unlink(missing_ok=True)
        rolled_back = True
        after = validator(ws)
        why = "范围外文件被改动" if out_of_scope else f"问题由 {len(before)} 条增至 {after_count} 条"
        st.log_line(ws, f"↩ {step.id}：修复结果不可接受（{why}），已还原")
        changed = []
    else:
        st.log_line(ws, f"🔧 {step.id}：修复后剩余问题 {len(after)} 条（修复前 {len(before)} 条）")
    return {"fixed": not after, "problems": after, "changed": changed, "rolled_back": rolled_back}
