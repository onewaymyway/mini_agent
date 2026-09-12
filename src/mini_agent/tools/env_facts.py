"""
tools/env_facts.py — 环境事实库工具

背景
----
Agent 在执行任务时经常需要现场探索环境（比如某个命令行工具到底装没装、
装在哪、要怎么调用），而这类探索结果往往是"这台机器"级别的客观事实，
跟具体项目、具体 session 无关；但目前没有任何机制把这类发现沉淀下来，
导致同一台机器上，同一个问题（如 "ffmpeg 在哪"）在不同任务、不同 session
里被反复探索。

设计取舍（对应设计讨论）
----------------------
1. 不做任何"自动检测/自动触发"——不去正则匹配 bash 输出判断"是不是没
   找到"，因为不同 OS/语言/重定向下这类输出五花八门、极不可靠。判断权
   完全交给 LLM 自己：它能看到真实的命令输出，比任何正则都清楚这次探索
   到底发现了什么。机制上只提供两个工具，靠工具的 description 引导模型
   在合适的时机主动调用。
2. 不常驻 system prompt——跟 notepad.py 不同，这里的内容不会自动注入
   每轮的 system prompt（避免占用固定 token 预算，且大部分任务用不上）。
   查询完全是按需的：模型需要确认某个工具位置时，自己调用 get_env_fact。
3. 按 name 去重覆盖，而不是追加——存储是一个以归一化 name 为 key 的
   映射，同名调用 record_env_fact 会覆盖旧值（保留"最新结论"），文件不会
   无限膨胀，也不会同一个工具存在多条互相矛盾的记录。
4. 落点是 AgentPaths.global_env_facts（~/.agent/env_facts.json），跨
   项目、跨 session 复用——与 global_memory/global_self_profile 等已有
   的 "Global 级" 路径同一层级、同一风格。

工具列表
--------
    record_env_fact(name, fact)  — 记录/更新一条环境事实
    get_env_fact(query="")       — 按关键词查询（不传则返回全部）

落盘格式（~/.agent/env_facts.json）：
    {
      "version": 1,
      "facts": {
        "ffmpeg": {"fact": "...", "updated_at": "2026-09-12 10:00:00"},
        "node":   {"fact": "...", "updated_at": "2026-09-12 10:05:00"}
      }
    }
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from . import tool  # noqa

_VERSION = 1

# 单条 fact 内容长度上限——工具说明里要求"一句话说清楚"，这里做个硬限制，
# 避免被写成大段探索过程/日志，导致文件失控膨胀。
_MAX_FACT_LEN = 500
_MAX_NAME_LEN = 100


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_name(name: str) -> str:
    """归一化主键：去首尾空白、转小写、去掉常见可执行文件后缀，
    尽量让 `FFmpeg` / `ffmpeg.exe` / `ffmpeg` 落到同一个 key 上。"""
    n = (name or "").strip().lower()
    n = re.sub(r"\.(exe|bat|cmd|sh)$", "", n)
    return n


class _EnvFactsStore:
    """~/.agent/env_facts.json 的读写封装。

    与 notepad.py::NotepadStore 同款风格：内存态 + 原子落盘（.tmp +
    os.replace），线程锁保护并发写入。这里不做 session 隔离——环境事实
    是机器级的，跨 session/跨项目共享同一份文件。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            facts = data.get("facts", {})
            return facts if isinstance(facts, dict) else {}
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.tools.env_facts._EnvFactsStore._load")
            return {}

    def _save(self, facts: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _VERSION, "facts": facts}
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".env_facts_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where="mini_agent.tools.env_facts._EnvFactsStore._save")

    def upsert(self, name: str, fact: str) -> tuple[str, bool]:
        """写入/覆盖一条事实，返回 (归一化后的 key, 是否覆盖了已有记录)。"""
        key = _normalize_name(name)
        with self._lock:
            facts = self._load()
            existed = key in facts
            facts[key] = {"fact": fact, "updated_at": _now()}
            self._save(facts)
        return key, existed

    def query(self, query: Optional[str]) -> dict:
        """按关键词模糊匹配（大小写不敏感，匹配 key 或 fact 内容）；
        query 为空则返回全部。"""
        with self._lock:
            facts = self._load()
        if not query:
            return facts
        q = query.strip().lower()
        if not q:
            return facts
        return {
            k: v
            for k, v in facts.items()
            if q in k or q in str(v.get("fact", "")).lower()
        }


# 全局单例——env_facts.json 是机器级文件，不需要像 notepad 那样按
# session/project 区分实例。
_store: Optional[_EnvFactsStore] = None
_store_lock = threading.Lock()


def _get_store() -> _EnvFactsStore:
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            from mini_agent.storage.paths import AgentPaths

            _store = _EnvFactsStore(AgentPaths().global_env_facts)
        return _store


# ── 工具定义 ──────────────────────────────────────────────────────────────────


@tool(
    name="record_env_fact",
    description=(
        "把探索环境时发现的、值得记住的结论记录下来，避免下次重复探索。"
        "典型场景：确认了某个命令行工具的真实安装路径（比如 which/where 找不到，"
        "但实际是装在某个非默认目录）、发现了正确的调用方式、确认了某个工具"
        "确实没有安装、发现了这台机器上某些特殊的环境行为等。"
        "只要你花了力气去探索/排查、并得出了一个后续可以直接复用的结论，就应该调用这个函数。"
        "name 用简短的主体名（通常是工具/命令名，如 'ffmpeg'），同名会覆盖旧记录、"
        "只保留最新结论，不会重复堆积。fact 请用一句话写清楚结论本身"
        "（如具体路径、调用方式，或'未安装'），不要把完整的排查过程/命令输出都写进去。"
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "事实的主体名，通常是工具/命令名，如 'ffmpeg'、'node'",
            },
            "fact": {
                "type": "string",
                "description": "一句话结论，如具体路径/调用方式/未安装等",
            },
        },
        "required": ["name", "fact"],
    },
    requires_approval=False,
)
def record_env_fact(name: str, fact: str) -> str:
    name = (name or "").strip()
    fact = (fact or "").strip()
    if not name:
        return "[error: name 不能为空]"
    if not fact:
        return "[error: fact 不能为空]"
    if len(name) > _MAX_NAME_LEN:
        name = name[:_MAX_NAME_LEN]
    if len(fact) > _MAX_FACT_LEN:
        fact = fact[:_MAX_FACT_LEN] + "…（已截断）"

    key, existed = _get_store().upsert(name, fact)
    action = "已更新" if existed else "已记录"
    return f"{action}环境事实 [{key}]：{fact}"


@tool(
    name="get_env_fact",
    description=(
        "查询之前记录过的环境事实，避免重复探索（比如重复 which/where/-version 排查）。"
        "当你需要确认某个工具/命令在当前机器上的位置或用法时，建议先调用这个函数看看"
        "是否已经有结论。传 query（如工具名关键词）做模糊匹配；不传或传空字符串则"
        "返回全部已记录的环境事实。查不到时会明确告知未找到，而不是报错。"
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，如工具名；留空返回全部记录",
            },
        },
        "required": [],
    },
    requires_approval=False,
)
def get_env_fact(query: str = "") -> str:
    facts = _get_store().query(query)
    if not facts:
        if query:
            return f"未找到与 '{query}' 相关的环境事实记录。"
        return "当前没有任何环境事实记录。"

    lines = []
    for key in sorted(facts.keys()):
        entry = facts[key]
        lines.append(f"- {key}: {entry.get('fact', '')}（更新于 {entry.get('updated_at', '?')}）")
    return "\n".join(lines)
