"""
agent 包内部共享的模块级辅助函数与工具类。

从原 agent.py 拆分而来（纯粹搬迁，不改变任何逻辑），供 core.py 及各 Mixin
文件共同使用：终端写锁上下文、加锁打印、工具错误识别别名、confidence 裁剪、
lesson/timeline 反思结果的 JSON 解析。
"""

from __future__ import annotations

import copy
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy, parse_backoff
from mini_agent.llm.client_pool import LLMClientPool
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_base import MemoryBackend
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry
from mini_agent.perception.memory_factory import create_memory_backend
from mini_agent.context_builder import ContextBuilder
from mini_agent.tool_executor import ToolExecutor
from mini_agent.history_manager import HistoryManager
from mini_agent.reminders import ReminderManager

import re as _re


def _term_write_lock_ctx():
    """
    [FIX] daemon 多 session 场景下，`trigger_summary_and_profile()` 触发的
    `_generate_and_save_summary()` 在独立后台线程（"mini-agent-summary"）里
    直接调用 R.print_info()/R.print_warning() —— 这两个函数一路往下最终是
    `term.print(...)`，会不经任何互斥直接把消息投进 Terminal 的渲染队列。

    根因：daemon 模式下，`server.py` 用 `_local_term_write_lock` 把"某个
    session 的一整轮 run_turn()"当成本地终端写入的临界区，序列化了不同
    session 之间的输出——但这把锁只在 server.py 里、包住 run_turn() 调用
    本身。`_generate_and_save_summary()` 是 run_turn() **返回之后**才起的
    后台线程（save_session() 触发），跑在这把锁的保护范围之外：它的
    print_info("正在后台生成会话摘要...")/print_info("会话摘要记忆已生成")
    等调用完全可能在另一个 session 正在流式输出的**中途**插进终端渲染
    队列——多个消息在同一个物理终端上按到达顺序打印，于是这条"背景摘要"
    的整行文本会被插在另一个 session 尚未收尾的流式内容中间，表现为：
      - 内容从中间某处断开、开头几个字"丢失"（其实是被这条插入的打印行
        实际拆断了视觉连续性，配合 stream 的行内 filter/续写逻辑，看起来
        像是内容被吃掉）；
      - print_assistant_prefix() 打印的 "agent_name ❯ " 前缀所在的那一行
        被无关打印行打断，后续 token 另起一行，看起来"没有 agent 名字"。

    修复：让这些背景线程的打印也去抢同一把 `_local_term_write_lock`——
    锁在被某个 session 的 run_turn() 持有期间，这里的 print 调用会阻塞
    等待，直到那一整轮输出收尾之后才真正打印，不会再插入到别的 session
    正在进行中的流式内容内部。

    非 daemon 场景（比如单进程本地 CLI）下 `mini_agent.api.server` 模块
    根本不会被 import，这里保持"锁不存在就不加锁"的静默降级，行为和之前
    完全一致，不引入任何新依赖。
    """
    try:
        import sys as _sys
        _server_mod = _sys.modules.get("mini_agent.api.server")
        if _server_mod is not None:
            return _server_mod._local_term_write_lock
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.agent._helpers._term_write_lock_ctx')
        pass
    return None


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _locked_print_info(msg: str) -> None:
    lock = _term_write_lock_ctx()
    with (lock if lock is not None else _NullCtx()):
        R.print_info(msg)


def _locked_print_warning(msg: str) -> None:
    lock = _term_write_lock_ctx()
    with (lock if lock is not None else _NullCtx()):
        R.print_warning(msg)


# ── 工具错误识别（Stage 1.2 起迁移至 perception/lesson_rules.py，供 ──────────
#    tool_executor.py 共享复用，避免循环依赖；这里保留 _is_tool_error 别名
#    以兼容本文件内现有调用点）─────────────────────────────────────────────────
from mini_agent.perception.lesson_rules import is_tool_error as _is_tool_error


def _clamp_confidence(value) -> float:
    """把 LLM 返回的 confidence 字段安全转换并裁剪到 [0, 1]。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def _parse_lesson_candidates(text: str) -> list[dict]:
    """
    解析 SessionEnd 反思 LLM 调用返回的 lesson 候选 JSON 数组。

    容错处理：
    - 模型偶尔会用 ```json ... ``` 包裹，先尝试剥离代码块围栏
    - 解析失败或返回的不是数组时，返回空列表（不抛异常，反思失败应静默降级）
    - 数组中非 dict 的元素会被过滤掉
    """
    if not text or not text.strip():
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 剥离 ```json\n...\n``` 或 ```\n...\n``` 围栏
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    import json as _json
    try:
        data = _json.loads(cleaned)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.agent._helpers._parse_lesson_candidates')
        return []

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _parse_timeline_summary(text: str) -> dict:
    """
    解析 timeline 反思 LLM 调用返回的 {theme, key_outcomes} JSON 对象（W2，4.2）。

    与 _parse_lesson_candidates 的容错策略一致（剥离 ```json 围栏、解析失败时
    静默降级），但目标结构是单个 dict 而不是数组。解析失败或字段缺失时返回
    空 dict，调用方据此决定是否跳过本次 timeline 追加。
    """
    if not text or not text.strip():
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    import json as _json
    try:
        data = _json.loads(cleaned)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.agent._helpers._parse_timeline_summary')
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def _parse_task_summary(text: str) -> dict:
    """
    解析 session_to_workflow 第①阶段（TaskSummary）反思 LLM 调用返回的 JSON
    对象（session_to_workflow_design.md 第 3 节）。

    与 _parse_timeline_summary 的容错策略一致（剥离 ```json 围栏、解析失败时
    静默降级返回空 dict）。目标结构是单个 dict，字段校验/默认值填充交给
    调用方（workflow/session_summarizer.py 的 TaskSummary.from_dict()），
    这里只负责"尽力解析出一个 dict，解析不出来就返回空 dict"。
    """
    if not text or not text.strip():
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    import json as _json
    try:
        data = _json.loads(cleaned)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.agent._helpers._parse_task_summary')
        return {}

    if not isinstance(data, dict):
        return {}
    return data


