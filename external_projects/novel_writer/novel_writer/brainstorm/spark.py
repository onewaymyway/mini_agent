"""发散（B1）：根据题材卡与已有灵感，让 AI 给出一批候选灵感。

候选先保存在 `_studio/spark/<id>.json`，不会直接进入灵感池；作者逐条采纳后才写入灵感池（source = ai）。
生成器可注入（测试时替换为桩），默认走 mini_agent 的受限 Agent（无工具、单轮）。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.ideas import add_idea
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

SPARK_DIR = "_studio/spark"
MAX_COUNT = 10
Generator = Callable[[str], str]


def _spark_dir(ws: BrainstormWorkspace) -> Path:
    d = ws.root / SPARK_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_prompt(brief: Dict[str, str], existing: List[str], direction: str, count: int) -> str:
    filled = [f"- {k}：{v}" for k, v in brief.items() if v]
    brief_text = "\n".join(filled) if filled else "（题材卡尚未填写）"
    have = "\n".join(f"- {t}" for t in existing[:40]) if existing else "（暂无）"
    focus = direction.strip() or "不限方向，尽量多样"
    return (
        "你是小说构思助手。请围绕下面的方向，给出一批可以用于小说的灵感候选。\n"
        f"方向：{focus}\n\n"
        f"题材卡：\n{brief_text}\n\n"
        f"已有灵感（不要重复，也不要换汤不换药）：\n{have}\n\n"
        f"请给出 {count} 条候选。每条是一句话的灵感，要具体（人物、处境或冲突），并给 1–3 个标签。\n"
        "只输出一个 JSON 数组，不要任何解释或 Markdown 代码块。格式：\n"
        '[{"text": "灵感内容", "tags": ["标签"]}]'
    )


def parse_candidates(reply: str) -> List[Dict[str, Any]]:
    """从模型回复中取出 JSON 数组。容忍代码块与前后文字；取不到则抛 BrainstormError。"""
    text = (reply or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise BrainstormError("模型没有返回可解析的候选列表")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BrainstormError(f"候选列表不是合法 JSON：{exc}") from exc
    out: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text_v = str(item.get("text", "")).strip()
        if not text_v:
            continue
        tags = item.get("tags") or []
        tags = [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else []
        out.append({"cid": "c" + uuid.uuid4().hex[:6], "text": text_v, "tags": tags, "adopted": False})
    if not out:
        raise BrainstormError("模型没有给出任何有效候选")
    return out


def default_generator(prompt: str) -> str:
    """默认生成器：mini_agent 受限 Agent，无工具、单轮。"""
    from novel_writer import config
    from novel_writer.runner import _build_restricted_agent, _ensure_framework_on_path

    _ensure_framework_on_path()
    cfg = config.load_llm_cfg()
    agent = _build_restricted_agent(cfg, allowed_tools=[], max_turns=3, timeout=240)
    return str(agent.run_turn(prompt))


def helper_generator(prompt: str, *, system: str = "", timeout: float = 300, max_retries: int = 2) -> str:
    """直接调用框架的 LLMHelper：单轮、无工具、无 Agent 循环，返回模型的原始文本。

    与 default_generator 的区别：不经过 Agent，所以模型不会去调工具、不会受轮次上限影响，
    也不会因为工具调用把结果写进文件而让回复里没有内容。适合「要结构化文本结果」的整理类任务。
    """
    from novel_writer import config
    from novel_writer.runner import _ensure_framework_on_path

    _ensure_framework_on_path()
    from mini_agent.llm.service import LLMHelper

    helper = LLMHelper.from_config(config.load_llm_cfg())
    return helper.ask(prompt, system=system, timeout=timeout, deadline=timeout * (max_retries + 1),
                      max_retries=max_retries)


def generate(ws: BrainstormWorkspace, direction: str = "", *, count: int = 6,
             generator: Optional[Generator] = None) -> Dict[str, Any]:
    count = max(1, min(int(count), MAX_COUNT))
    brief = C.get_module(ws, "brief")
    existing = [i["text"] for i in C.get_module(ws, "ideas") if i.get("status") != "drop"]
    prompt = build_prompt(brief, existing, direction, count)
    reply = (generator or default_generator)(prompt)
    record = {
        "id": "s" + uuid.uuid4().hex[:8],
        "direction": (direction or "").strip(),
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "candidates": parse_candidates(reply),
    }
    atomic_write_json(_spark_dir(ws) / f"{record['id']}.json", record)
    return record


def history(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(_spark_dir(ws).glob("s*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        rec = read_json(path, default=None)
        if isinstance(rec, dict):
            out.append(rec)
    return out


def adopt(ws: BrainstormWorkspace, spark_id: str, cids: List[str]) -> List[Dict[str, Any]]:
    """把所选候选写入灵感池（source = ai，status = keep），并在候选记录里标记为已采纳。"""
    path = _spark_dir(ws) / f"{spark_id}.json"
    rec = read_json(path, default=None)
    if not isinstance(rec, dict):
        raise BrainstormError(f"找不到发散记录：{spark_id}")
    wanted = set(cids)
    unknown = wanted - {c["cid"] for c in rec["candidates"]}
    if unknown:
        raise BrainstormError(f"候选不存在：{', '.join(sorted(unknown))}")
    added = []
    for cand in rec["candidates"]:
        if cand["cid"] in wanted and not cand.get("adopted"):
            added.append(add_idea(ws, cand["text"], tags=cand["tags"], source="ai"))
            cand["adopted"] = True
    atomic_write_json(path, rec)
    return added
