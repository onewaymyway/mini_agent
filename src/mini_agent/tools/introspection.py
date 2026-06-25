"""
tools/introspection.py — Agent 自感知与运行时调整工具

提供三个层次的自省能力：
  agent_status()              — 全局简报（轻量，一次了解全貌）
  agent_inspect(target)       — 按需深查具体子系统详情
  agent_patch(target, field, value) — 运行时热修改（白名单写）

可见性与可改性通过 IntrospectionPolicy 统一控制：
  - VISIBLE_TARGETS   : 哪些 inspect target 对 agent 可见
  - HIDDEN_TARGETS    : 哪些 target 对 agent 隐藏（优先级高于 VISIBLE）
  - PATCHABLE_TARGETS : 哪些 (target, field) 允许 patch

默认策略：全部可见、白名单字段可改。

注册方式（在 Agent.__init__ 末尾调用）：
  from mini_agent.tools.introspection import register_introspection_tools
  register_introspection_tools(registry, self)
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import threading
from typing import TYPE_CHECKING, Any, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.tools import ToolRegistry


# ── 可见性 / 可改性策略 ────────────────────────────────────────────────────────

# agent_inspect 的所有合法 target
ALL_INSPECT_TARGETS = {
    "config",
    "history",
    "stats",
    "skills",
    "tools",
    "memory",
    "providers",
    "registry",
    "session",
    "perception",
    "retry_policy",
    "mcp",
    "env",
    "process",
}

# 默认隐藏的 target（agent_inspect 调用时会被拒绝）
# 可在运行时通过 IntrospectionPolicy 调整
_DEFAULT_HIDDEN_TARGETS: set[str] = set()   # 默认全可见

# patch 白名单：{ target -> { field -> (type_coerce, validator_or_None) } }
# type_coerce: 将字符串 value 转换为目标类型的函数
# validator: 额外校验函数 (converted_value) -> Optional[str]，返回 None 表示通过
_PATCH_WHITELIST: dict[str, dict[str, tuple]] = {
    "config": {
        "auto_approve":  (lambda v: v.lower() in ("true", "1", "yes"), None),
        "sandbox":       (lambda v: v.lower() in ("true", "1", "yes"), None),
        "model":         (str, None),
        "max_tokens":    (int, lambda v: "must be > 0" if v <= 0 else None),
        "temperature":   (float, lambda v: "must be in [0, 1]" if not 0.0 <= v <= 1.0 else None),
        "verbose":       (lambda v: v.lower() in ("true", "1", "yes"), None),
        "stream":        (lambda v: v.lower() in ("true", "1", "yes"), None),
        "max_turns":     (int, lambda v: "must be > 0" if v <= 0 else None),
        "max_llm_calls": (int, lambda v: "must be > 0" if v <= 0 else None),
    },
    "retry_policy": {
        "max_retries": (int, lambda v: "must be >= 0" if v < 0 else None),
    },
    "stats": {
        "reset": (str, None),   # 特殊 field，触发 stats 重置
    },
    "tool_cache": {
        "clear": (str, None),   # 特殊 field，触发 tool_cache 清空
    },
    "skill": {
        # field 格式: "<skill_name>:active"，value: "true"/"false"
        # 动态匹配，不在静态白名单里列举具体名字
        "__dynamic__": True,
    },
}

# 默认不可 patch 的 target（优先级高于白名单）
_DEFAULT_LOCKED_TARGETS: set[str] = set()   # 默认全部白名单内可改


class IntrospectionPolicy:
    """
    自省可见性与可改性策略。

    可在运行时修改以收紧或放开权限：
        policy = agent._introspection_policy
        policy.hidden_targets.add("memory")   # 隐藏 memory inspect
        policy.locked_targets.add("config")   # 禁止修改 config
    """

    def __init__(self) -> None:
        # 哪些 inspect target 对 agent 隐藏
        self.hidden_targets: set[str] = set(_DEFAULT_HIDDEN_TARGETS)
        # 哪些 inspect target 只读（agent_inspect 可见但 agent_patch 拒绝）
        self.locked_targets: set[str] = set(_DEFAULT_LOCKED_TARGETS)
        # 自定义 patch 黑名单字段 { target: {field, ...} }
        self.locked_fields: dict[str, set[str]] = {}

    def is_visible(self, target: str) -> bool:
        return target not in self.hidden_targets

    def is_patchable(self, target: str, field: str) -> tuple[bool, str]:
        """返回 (allowed, reason)"""
        if target in self.locked_targets:
            return False, f"target '{target}' 已被锁定（只读）"
        if field in self.locked_fields.get(target, set()):
            return False, f"字段 '{target}.{field}' 已被锁定"
        if target not in _PATCH_WHITELIST:
            return False, f"target '{target}' 不在可修改白名单中"
        tbl = _PATCH_WHITELIST[target]
        if "__dynamic__" in tbl:
            return True, ""
        if field not in tbl:
            return False, f"字段 '{field}' 不在 '{target}' 的可修改白名单中"
        return True, ""


# ── 辅助：安全序列化 ───────────────────────────────────────────────────────────

def _safe_json(obj: Any, indent: int = 2) -> str:
    """将对象序列化为 JSON，对无法序列化的值用字符串描述替代。"""
    def _default(o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        return repr(o)
    try:
        return json.dumps(obj, indent=indent, ensure_ascii=False, default=_default)
    except Exception as e:
        return json.dumps({"_error": f"序列化失败: {e}"}, indent=indent, ensure_ascii=False)


def _mask_secrets(d: dict) -> dict:
    """对 dict 中的敏感字段做脱敏处理（in-place 修改副本）。"""
    SECRET_KEYS = {"api_key", "api_keys", "key", "token", "secret", "password"}
    result = {}
    for k, v in d.items():
        if k in SECRET_KEYS:
            if isinstance(v, str) and v:
                result[k] = f"***({len(v)} chars)"
            elif isinstance(v, list):
                result[k] = [f"***({len(s)} chars)" if isinstance(s, str) else "***" for s in v]
            else:
                result[k] = "***"
        elif isinstance(v, dict):
            result[k] = _mask_secrets(v)
        else:
            result[k] = v
    return result


def _safe_get(fn, default="N/A"):
    try:
        return fn()
    except Exception as e:
        return f"<error: {e}>"


# ── 采集函数（每个 target 一个）───────────────────────────────────────────────

def _collect_config(agent) -> dict:
    cfg = agent.cfg
    raw = {}
    if dataclasses.is_dataclass(cfg):
        raw = dataclasses.asdict(cfg)
    else:
        raw = vars(cfg) if hasattr(cfg, "__dict__") else {}
    return _mask_secrets(raw)


def _collect_history(agent) -> dict:
    hist = agent._history
    msgs = []
    for i, msg in enumerate(hist):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            preview = content[:120] + ("..." if len(content) > 120 else "")
            length = len(content)
        elif isinstance(content, list):
            preview = f"[{len(content)} blocks]"
            length = sum(len(str(b)) for b in content)
        else:
            preview = repr(content)[:120]
            length = len(str(content))
        msgs.append({"index": i, "role": role, "length": length, "preview": preview})
    return {"total_messages": len(hist), "messages": msgs}


def _collect_stats(agent) -> dict:
    s = agent.stats
    result = {
        "turns": s.turns,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "tool_calls": s.tool_calls,
        "elapsed": _safe_get(lambda: s.elapsed()),
    }
    if hasattr(s, "tool_stats"):
        result["tool_stats"] = dict(s.tool_stats)
    if hasattr(s, "skill_activations"):
        result["skill_activations"] = dict(s.skill_activations)
    return result


def _collect_skills(agent) -> dict:
    sl = agent.skill_loader
    if sl is None:
        return {"enabled": False, "reason": "skill_loader 未初始化"}
    skills_detail = []
    for sk in _safe_get(lambda: list(sl.available), []):
        skills_detail.append({
            "name": _safe_get(lambda s=sk: s.name),
            "active": _safe_get(lambda s=sk: s.name in sl.active),
            "path": _safe_get(lambda s=sk: str(s.path) if hasattr(s, "path") else "N/A"),
            "keywords": _safe_get(lambda s=sk: list(s.keywords) if hasattr(s, "keywords") else []),
            "description": _safe_get(lambda s=sk: (s.description or "")[:200] if hasattr(s, "description") else ""),
        })
    return {
        "enabled": True,
        "total_available": len(skills_detail),
        "total_active": _safe_get(lambda: len(sl.active), 0),
        "active_names": _safe_get(lambda: list(sl.active), []),
        "skills": skills_detail,
    }


def _collect_tools(agent) -> dict:
    reg = agent.registry
    tools_list = []
    for name in _safe_get(lambda: reg.names, []):
        td = _safe_get(lambda n=name: reg.get(n))
        if td is None:
            continue
        tools_list.append({
            "name": td.name,
            "description": (td.description or "")[:150],
            "group": _safe_get(lambda t=td: t.group, "builtin"),
            "requires_approval": _safe_get(lambda t=td: t.requires_approval, True),
        })
    groups = _safe_get(lambda: {g: reg.names_in_group(g) for g in reg.groups}, {})
    return {
        "total_tools": len(tools_list),
        "groups": groups,
        "tools": tools_list,
    }


def _collect_memory(agent) -> dict:
    result = {"project_memory": {}, "global_memory": {}}
    for key, mem in [("project_memory", agent._memory), ("global_memory", agent._global_memory)]:
        if mem is None:
            result[key] = {"enabled": False}
            continue
        entries = _safe_get(lambda m=mem: m._entries, [])
        recent = []
        for e in list(reversed(entries))[:10]:
            entry_d = {}
            if dataclasses.is_dataclass(e):
                entry_d = dataclasses.asdict(e)
            elif hasattr(e, "__dict__"):
                entry_d = dict(vars(e))
            # 截断内容字段避免输出过长
            if "content" in entry_d and isinstance(entry_d["content"], str):
                entry_d["content"] = entry_d["content"][:200] + ("..." if len(entry_d["content"]) > 200 else "")
            recent.append(entry_d)
        result[key] = {
            "enabled": True,
            "total_entries": len(entries),
            "max_entries": _safe_get(lambda m=mem: m._max_entries, "N/A"),
            "store_path": _safe_get(lambda m=mem: str(m._path) if hasattr(m, "_path") else "N/A"),
            "recent_10": recent,
        }
    return result


def _collect_providers(agent) -> dict:
    pool = _safe_get(lambda: agent._client_pool)
    if pool is None or pool == "N/A":
        return {"enabled": False}
    info = _safe_get(lambda: pool.status())
    if isinstance(info, dict):
        # 脱敏 entries 中的 api_key
        if "entries" in info:
            info["entries"] = [_mask_secrets(e) if isinstance(e, dict) else e
                               for e in info["entries"]]
    return info if isinstance(info, dict) else {"raw": str(info)}


def _collect_registry(agent) -> dict:
    reg = agent.registry
    return {
        "groups": _safe_get(lambda: {g: reg.names_in_group(g) for g in reg.groups}, {}),
        "all_names": _safe_get(lambda: reg.names, []),
        "total": _safe_get(lambda: len(reg.names), 0),
    }


def _collect_session(agent) -> dict:
    sess = agent._session
    if sess is None:
        return {"enabled": False}
    d = {}
    if dataclasses.is_dataclass(sess):
        d = dataclasses.asdict(sess)
    elif hasattr(sess, "__dict__"):
        d = dict(vars(sess))
    # history 字段太大，用摘要替换
    if "history" in d:
        d["history"] = f"[{len(d['history'])} messages — use target='history' for detail]"
    return d


def _collect_perception(agent) -> dict:
    fw = agent._file_watcher
    tc = agent._tool_cache
    return {
        "project_scan": {
            "enabled": agent.cfg.perception.project_scan_enabled,
            "snapshot_ready": agent._project_snapshot is not None,
            "snapshot_length": len(agent._project_snapshot) if agent._project_snapshot else 0,
        },
        "file_watcher": {
            "enabled": agent.cfg.perception.file_watch_enabled,
            "initialized": fw is not None,
            "pending_changes": _safe_get(lambda: list(agent._pending_file_changes), []),
        },
        "tool_cache": {
            "enabled": agent.cfg.perception.tool_cache_enabled,
            "initialized": tc is not None,
            "status": _safe_get(lambda: tc.status() if tc and hasattr(tc, "status") else "N/A"),
        },
        "token_estimate": {
            "enabled": agent.cfg.perception.token_estimate_enabled,
        },
    }


def _collect_retry_policy(agent) -> dict:
    rp = agent._retry_policy
    if rp is None:
        return {"enabled": False}
    d = {}
    if dataclasses.is_dataclass(rp):
        d = dataclasses.asdict(rp)
    elif hasattr(rp, "__dict__"):
        d = dict(vars(rp))
    return d


def _collect_mcp(agent) -> dict:
    mgr = agent._mcp_manager
    if mgr is None:
        return {"enabled": False}
    servers = _safe_get(lambda: list(mgr._active_servers.keys()), [])
    return {
        "enabled": True,
        "active_servers": servers,
        "total_servers": len(servers),
    }


def _collect_env(agent) -> dict:
    relevant_prefixes = ("MINI_AGENT", "ANTHROPIC", "OPENAI", "CLAUDE",
                         "LLM", "AGENT", "HOME", "PATH", "SHELL", "LANG",
                         "TERM", "PYTHONPATH", "VIRTUAL_ENV")
    env_vars = {}
    for k, v in os.environ.items():
        if any(k.startswith(pfx) for pfx in relevant_prefixes):
            # mask secrets
            if any(secret in k.upper() for secret in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                env_vars[k] = f"***({len(v)} chars)"
            else:
                env_vars[k] = v
    return {"relevant_env": env_vars, "cwd": os.getcwd()}


def _collect_process(_agent) -> dict:
    import os as _os
    result = {
        "pid": _os.getpid(),
        "python": _safe_get(lambda: __import__("sys").executable),
        "threads": _safe_get(lambda: threading.active_count()),
        "thread_names": _safe_get(lambda: [t.name for t in threading.enumerate()]),
    }
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        result["memory_rss_mb"] = _safe_get(lambda: round(ru.ru_maxrss / 1024, 2))
        result["cpu_user_s"] = _safe_get(lambda: round(ru.ru_utime, 2))
        result["cpu_sys_s"] = _safe_get(lambda: round(ru.ru_stime, 2))
    except ImportError:
        try:
            import psutil
            proc = psutil.Process()
            mi = proc.memory_info()
            result["memory_rss_mb"] = round(mi.rss / 1024 / 1024, 2)
        except ImportError:
            result["memory_rss_mb"] = "N/A (install psutil)"
    return result


_COLLECTORS = {
    "config":       _collect_config,
    "history":      _collect_history,
    "stats":        _collect_stats,
    "skills":       _collect_skills,
    "tools":        _collect_tools,
    "memory":       _collect_memory,
    "providers":    _collect_providers,
    "registry":     _collect_registry,
    "session":      _collect_session,
    "perception":   _collect_perception,
    "retry_policy": _collect_retry_policy,
    "mcp":          _collect_mcp,
    "env":          _collect_env,
    "process":      _collect_process,
}


# ── agent_status 简报 ──────────────────────────────────────────────────────────

def _build_status(agent, policy: IntrospectionPolicy) -> dict:
    """快速聚合所有关键对象的一行摘要。"""
    cfg = agent.cfg
    stats = agent.stats

    def _s(fn, default="N/A"):
        try:
            return fn()
        except Exception:
            return default

    sections: dict[str, Any] = {}

    # ── LLM / Provider
    sections["llm"] = {
        "provider": _s(lambda: cfg.llm_provider),
        "model":    _s(lambda: cfg.model),
        "max_tokens": _s(lambda: cfg.max_tokens),
        "stream":   _s(lambda: cfg.stream),
        "fallback_chain_len": _s(lambda: len(cfg.llm_fallback_chain)),
    }

    # ── Run flags
    sections["runtime"] = {
        "sandbox":      _s(lambda: cfg.sandbox),
        "auto_approve": _s(lambda: cfg.auto_approve),
        "verbose":      _s(lambda: cfg.verbose),
        "max_turns":    _s(lambda: cfg.max_turns),
        "max_llm_calls":_s(lambda: cfg.max_llm_calls),
        "is_subagent":  _s(lambda: agent._is_subagent),
    }

    # ── Session
    sess = agent._session
    sections["session"] = {
        "id":          _s(lambda: sess.id if sess else "N/A"),
        "title":       _s(lambda: sess.title if sess else "N/A"),
        "created_at":  _s(lambda: sess.created_at if sess else "N/A"),
        "project_root": _s(lambda: str(cfg.project_root)),
    }

    # ── Conversation stats
    sections["stats"] = {
        "turns":         stats.turns,
        "input_tokens":  stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "tool_calls":    stats.tool_calls,
        "elapsed":       _s(lambda: stats.elapsed()),
    }

    # ── History
    sections["history"] = {
        "message_count": _s(lambda: len(agent._history)),
        "estimated_tokens": _s(lambda: __import__(
            "mini_agent.perception.token_counter", fromlist=["estimate_messages_tokens"]
        ).estimate_messages_tokens(agent._history)),
    }

    # ── Skills
    sl = agent.skill_loader
    sections["skills"] = {
        "enabled": sl is not None,
        "active":  _s(lambda: list(sl.active) if sl else []),
        "available": _s(lambda: len(list(sl.available)) if sl else 0),
    }

    # ── Tools
    sections["tools"] = {
        "total_registered": _s(lambda: len(agent.registry.names)),
        "groups":           _s(lambda: list(agent.registry.groups)),
    }

    # ── Subsystems enabled flags
    sections["subsystems"] = {
        "memory":        _s(lambda: cfg.memory.enabled),
        "compress":      _s(lambda: cfg.compress.enabled),
        "project_scan":  _s(lambda: cfg.perception.project_scan_enabled),
        "file_watch":    _s(lambda: cfg.perception.file_watch_enabled),
        "tool_cache":    _s(lambda: cfg.perception.tool_cache_enabled),
        "mcp":           _s(lambda: cfg.mcp.enabled),
        "reminder":      _s(lambda: getattr(cfg, "reminder", None) and getattr(cfg.reminder, "enabled", False)),
        "profile":       _s(lambda: cfg.profile.enabled if hasattr(cfg, "profile") else "N/A"),
        "web_search":    _s(lambda: cfg.web_search.enabled if hasattr(cfg, "web_search") else "N/A"),
    }

    # ── Retry policy
    rp = agent._retry_policy
    sections["retry_policy"] = {
        "max_retries": _s(lambda: rp.max_retries if rp else "N/A"),
        "backoff":     _s(lambda: repr(rp.backoff) if rp and hasattr(rp, "backoff") else "N/A"),
    }

    # ── Process
    sections["process"] = {
        "pid":     os.getpid(),
        "threads": _s(lambda: threading.active_count()),
    }

    # ── Policy summary
    sections["introspection_policy"] = {
        "hidden_targets": sorted(policy.hidden_targets),
        "locked_targets": sorted(policy.locked_targets),
        "locked_fields":  {k: sorted(v) for k, v in policy.locked_fields.items()},
    }

    return sections


# ── Patch 执行器 ───────────────────────────────────────────────────────────────

def _do_patch(agent, target: str, field: str, value: str, policy: IntrospectionPolicy) -> str:
    """执行 patch 操作，返回结果描述字符串。"""

    allowed, reason = policy.is_patchable(target, field)
    if not allowed:
        return json.dumps({"success": False, "error": reason}, ensure_ascii=False)

    try:
        if target == "config":
            tbl = _PATCH_WHITELIST["config"]
            if field not in tbl:
                return json.dumps({"success": False, "error": f"字段 '{field}' 不在 config 可修改白名单"}, ensure_ascii=False)
            coerce, validator = tbl[field]
            converted = coerce(value)
            if validator:
                err = validator(converted)
                if err:
                    return json.dumps({"success": False, "error": err}, ensure_ascii=False)
            old = getattr(agent.cfg, field, "<unknown>")
            setattr(agent.cfg, field, converted)
            # 同步到 guard（auto_approve/sandbox 影响权限）
            if field in ("auto_approve", "sandbox") and agent.guard:
                setattr(agent.guard, field, converted)
            return json.dumps({
                "success": True, "target": target, "field": field,
                "old": str(old), "new": str(converted),
            }, ensure_ascii=False)

        elif target == "retry_policy":
            if field == "max_retries":
                converted = int(value)
                if converted < 0:
                    return json.dumps({"success": False, "error": "max_retries 必须 >= 0"}, ensure_ascii=False)
                old = agent._retry_policy.max_retries
                agent._retry_policy.max_retries = converted
                return json.dumps({
                    "success": True, "target": target, "field": field,
                    "old": old, "new": converted,
                }, ensure_ascii=False)

        elif target == "stats" and field == "reset":
            import time as _time
            from mini_agent.config import SessionStats
            old_turns = agent.stats.turns
            agent.stats = SessionStats()
            return json.dumps({
                "success": True, "target": target, "field": "reset",
                "note": f"SessionStats 已重置（原 turns={old_turns}）",
            }, ensure_ascii=False)

        elif target == "tool_cache" and field == "clear":
            tc = agent._tool_cache
            if tc is None:
                return json.dumps({"success": False, "error": "tool_cache 未启用"}, ensure_ascii=False)
            if hasattr(tc, "clear"):
                tc.clear()
                return json.dumps({"success": True, "note": "tool_cache 已清空"}, ensure_ascii=False)
            # fallback: reset internal store
            if hasattr(tc, "_store"):
                with getattr(tc, "_lock", threading.Lock()):
                    tc._store.clear()
                return json.dumps({"success": True, "note": "tool_cache._store 已清空"}, ensure_ascii=False)

        elif target == "skill":
            # field 格式: "<skill_name>:active"
            if ":" not in field:
                return json.dumps({"success": False, "error": "skill field 格式应为 '<skill_name>:active'"}, ensure_ascii=False)
            skill_name, attr = field.split(":", 1)
            if attr != "active":
                return json.dumps({"success": False, "error": "skill 当前只支持 '<name>:active' 字段"}, ensure_ascii=False)
            sl = agent.skill_loader
            if sl is None:
                return json.dumps({"success": False, "error": "skill_loader 未初始化"}, ensure_ascii=False)
            activate = value.lower() in ("true", "1", "yes")
            if activate:
                if hasattr(sl, "activate"):
                    sl.activate(skill_name)
                elif hasattr(sl, "_active"):
                    sl._active.add(skill_name)
            else:
                if hasattr(sl, "deactivate"):
                    sl.deactivate(skill_name)
                elif hasattr(sl, "_active"):
                    sl._active.discard(skill_name)
            return json.dumps({
                "success": True, "target": target, "field": field,
                "new": activate,
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": f"patch 执行异常: {e}"}, ensure_ascii=False)

    return json.dumps({"success": False, "error": f"target='{target}' field='{field}' 未匹配到执行逻辑"}, ensure_ascii=False)


# ── 注册入口 ───────────────────────────────────────────────────────────────────

def register_introspection_tools(registry: "ToolRegistry", agent) -> None:
    """
    向 registry 注册三个自省工具，并在 agent 上挂载策略对象。

    agent._introspection_policy 可在运行时调整可见性/可改性范围：
        agent._introspection_policy.hidden_targets.add("memory")
        agent._introspection_policy.locked_targets.add("config")
    """
    policy = IntrospectionPolicy()
    # 挂载到 agent，方便外部运行时调整
    agent._introspection_policy = policy

    # ── Tool 1: agent_status ───────────────────────────────────────────────────

    def agent_status() -> str:
        """
        返回 agent 当前所有关键子系统的实时简报（JSON）。
        涵盖 LLM 配置、运行标志、会话信息、统计数据、技能、工具、子系统开关、进程信息等。
        快速了解自身全貌时首选此工具；需要深入某个子系统时再调用 agent_inspect。
        """
        data = _build_status(agent, policy)
        R.print_tool_use("agent_status", {})
        return _safe_json(data)

    registry.register_fn(
        agent_status,
        name="agent_status",
        description="获取 agent 当前所有关键子系统的实时简报（轻量只读）",
        input_schema={"type": "object", "properties": {}, "required": []},
        requires_approval=False,
        group="introspection",
    )

    # ── Tool 2: agent_inspect ─────────────────────────────────────────────────

    _visible_targets_desc = ", ".join(sorted(ALL_INSPECT_TARGETS))

    def agent_inspect(target: str) -> str:
        """
        深入查看 agent 指定子系统的完整状态。
        target 可选值：config, history, stats, skills, tools, memory,
                       providers, registry, session, perception,
                       retry_policy, mcp, env, process
        返回 JSON 格式的详细状态信息。
        """
        target = target.strip().lower()

        if target not in ALL_INSPECT_TARGETS:
            available = sorted(t for t in ALL_INSPECT_TARGETS if policy.is_visible(t))
            return json.dumps({
                "error": f"未知 target '{target}'",
                "available_targets": available,
            }, ensure_ascii=False)

        if not policy.is_visible(target):
            return json.dumps({
                "error": f"target '{target}' 当前不可见（被策略隐藏）",
            }, ensure_ascii=False)

        collector = _COLLECTORS.get(target)
        if collector is None:
            return json.dumps({"error": f"target '{target}' 暂无采集器"}, ensure_ascii=False)

        R.print_tool_use("agent_inspect", {"target": target})
        try:
            data = collector(agent)
            return _safe_json({"target": target, "data": data, "_ts": time.time()})
        except Exception as e:
            return json.dumps({"target": target, "error": str(e)}, ensure_ascii=False)

    registry.register_fn(
        agent_inspect,
        name="agent_inspect",
        description=(
            "深入查看 agent 指定子系统的完整详情（只读）。"
            f"target 可选: {_visible_targets_desc}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "要查看的子系统名称。可选值: "
                        "config, history, stats, skills, tools, memory, "
                        "providers, registry, session, perception, "
                        "retry_policy, mcp, env, process"
                    ),
                    "enum": sorted(ALL_INSPECT_TARGETS),
                }
            },
            "required": ["target"],
        },
        requires_approval=False,
        group="introspection",
    )

    # ── Tool 3: agent_patch ───────────────────────────────────────────────────

    _patchable_summary = "; ".join(
        f"{t}: [{', '.join(f for f in fields if f != '__dynamic__')}{'...' if '__dynamic__' in fields else ''}]"
        for t, fields in _PATCH_WHITELIST.items()
    )

    def agent_patch(target: str, field: str, value: str) -> str:
        """
        在运行时修改 agent 的指定配置或状态字段（热修改，无需重启）。
        target: 目标子系统名（config / retry_policy / stats / tool_cache / skill）
        field:  要修改的字段名；stats 使用 'reset'，tool_cache 使用 'clear'，
                skill 使用 '<skill_name>:active'
        value:  新值（字符串形式，工具内部自动转换为目标类型）
        修改立即生效，但不持久化到配置文件（重启后恢复原值）。
        """
        target = target.strip().lower()
        field = field.strip()

        R.print_tool_use("agent_patch", {"target": target, "field": field, "value": value})
        result = _do_patch(agent, target, field, value, policy)
        return result

    registry.register_fn(
        agent_patch,
        name="agent_patch",
        description=(
            "运行时热修改 agent 配置或状态（需用户确认）。"
            f"白名单: {_patchable_summary}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "目标子系统: config, retry_policy, stats, tool_cache, skill",
                    "enum": list(_PATCH_WHITELIST.keys()),
                },
                "field": {
                    "type": "string",
                    "description": (
                        "要修改的字段。config: auto_approve/sandbox/model/max_tokens/temperature/verbose/stream/max_turns/max_llm_calls; "
                        "retry_policy: max_retries; stats: reset; tool_cache: clear; skill: <name>:active"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "新值（字符串，内部自动类型转换）",
                },
            },
            "required": ["target", "field", "value"],
        },
        requires_approval=True,    # 写操作需要用户确认
        group="introspection",
    )

    # ── Tool 4: agent_policy ─────────────────────────────────────────────────
    # 让 agent 自己能查看和调整自省策略

    def agent_policy(
        action: str,
        target: Optional[str] = None,
        field: Optional[str] = None,
    ) -> str:
        """
        查看或调整自省系统的可见性/可改性策略。
        action:
          'show'                       — 显示当前策略
          'hide_target'   target=<t>  — 隐藏某个 inspect target
          'show_target'   target=<t>  — 取消隐藏某个 inspect target
          'lock_target'   target=<t>  — 锁定某个 target（禁止 patch）
          'unlock_target' target=<t>  — 解锁某个 target
          'lock_field'    target=<t> field=<f>  — 锁定具体字段
          'unlock_field'  target=<t> field=<f>  — 解锁具体字段
        """
        R.print_tool_use("agent_policy", {"action": action, "target": target, "field": field})
        action = action.strip().lower()

        if action == "show":
            return _safe_json({
                "hidden_targets": sorted(policy.hidden_targets),
                "locked_targets": sorted(policy.locked_targets),
                "locked_fields":  {k: sorted(v) for k, v in policy.locked_fields.items()},
                "all_inspect_targets": sorted(ALL_INSPECT_TARGETS),
                "patchable_targets": list(_PATCH_WHITELIST.keys()),
            })
        elif action == "hide_target":
            if not target:
                return json.dumps({"error": "需要 target 参数"}, ensure_ascii=False)
            policy.hidden_targets.add(target)
            return json.dumps({"success": True, "hidden_targets": sorted(policy.hidden_targets)}, ensure_ascii=False)
        elif action == "show_target":
            if not target:
                return json.dumps({"error": "需要 target 参数"}, ensure_ascii=False)
            policy.hidden_targets.discard(target)
            return json.dumps({"success": True, "hidden_targets": sorted(policy.hidden_targets)}, ensure_ascii=False)
        elif action == "lock_target":
            if not target:
                return json.dumps({"error": "需要 target 参数"}, ensure_ascii=False)
            policy.locked_targets.add(target)
            return json.dumps({"success": True, "locked_targets": sorted(policy.locked_targets)}, ensure_ascii=False)
        elif action == "unlock_target":
            if not target:
                return json.dumps({"error": "需要 target 参数"}, ensure_ascii=False)
            policy.locked_targets.discard(target)
            return json.dumps({"success": True, "locked_targets": sorted(policy.locked_targets)}, ensure_ascii=False)
        elif action == "lock_field":
            if not target or not field:
                return json.dumps({"error": "需要 target 和 field 参数"}, ensure_ascii=False)
            policy.locked_fields.setdefault(target, set()).add(field)
            return json.dumps({"success": True, "locked_fields": {k: sorted(v) for k, v in policy.locked_fields.items()}}, ensure_ascii=False)
        elif action == "unlock_field":
            if not target or not field:
                return json.dumps({"error": "需要 target 和 field 参数"}, ensure_ascii=False)
            policy.locked_fields.get(target, set()).discard(field)
            return json.dumps({"success": True, "locked_fields": {k: sorted(v) for k, v in policy.locked_fields.items()}}, ensure_ascii=False)
        else:
            return json.dumps({
                "error": f"未知 action '{action}'",
                "valid_actions": ["show", "hide_target", "show_target", "lock_target",
                                  "unlock_target", "lock_field", "unlock_field"],
            }, ensure_ascii=False)

    registry.register_fn(
        agent_policy,
        name="agent_policy",
        description="查看或调整自省系统的可见性/可改性策略（show/hide_target/lock_target 等）",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["show", "hide_target", "show_target",
                             "lock_target", "unlock_target",
                             "lock_field", "unlock_field"],
                },
                "target": {
                    "type": "string",
                    "description": "目标子系统名（hide/show/lock/unlock_target 时需要）",
                },
                "field": {
                    "type": "string",
                    "description": "字段名（lock/unlock_field 时需要）",
                },
            },
            "required": ["action"],
        },
        requires_approval=False,
        group="introspection",
    )
