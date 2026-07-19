"""
tools/introspection.py — Agent 自感知与运行时调整工具

提供三个层次的自省能力：
  agent_status()                    — 全局简报（轻量，一次了解全貌）
  agent_inspect(target, meta?)      — 按需深查具体子系统详情，可附带代码元信息
  agent_patch(target, field, value) — 运行时热修改（白名单写）
  agent_policy(action, ...)         — 调整可见性/可改性范围

元信息（meta=True 时附加在 agent_inspect 响应里）包含：
  - 对象类型、所在文件、起止行号、类 docstring
  - 构造方式（在 agent.py 中的赋值行号与代码片段）
  - 公开/私有方法列表及其 docstring
  - dataclass 字段类型与默认值（适用于 config/stats/session/retry_policy）
  - 关联文件（import 来源）

可见性与可改性通过 IntrospectionPolicy 统一控制：
  - hidden_targets : 哪些 target 对 agent 隐藏
  - locked_targets : 哪些 target 只读（inspect 可见但 patch 拒绝）
  - locked_fields  : 哪些具体字段不可 patch

默认策略：全部可见、白名单字段可改。

注册方式（在 _init_components 末尾调用）：
  from mini_agent.tools.introspection import register_introspection_tools
  register_introspection_tools(registry, self)
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.tools import ToolRegistry


# ── 常量：项目根推导（运行时动态确定，允许为 None）──────────────────────────────

def _project_root() -> Optional[Path]:
    """尝试从本文件位置推导项目根（src/mini_agent/tools/introspection.py → 上三级）。"""
    try:
        return Path(__file__).resolve().parent.parent.parent.parent
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.introspection._project_root')
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 可见性 / 可改性策略
# ══════════════════════════════════════════════════════════════════════════════

ALL_INSPECT_TARGETS = {
    "config", "history", "stats", "skills", "tools", "memory",
    "providers", "registry", "session", "perception", "retry_policy",
    "mcp", "env", "process",
}

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
        "reset": (str, None),
    },
    "tool_cache": {
        "clear": (str, None),
    },
    "skill": {
        "__dynamic__": True,
    },
}


class IntrospectionPolicy:
    """
    自省可见性与可改性策略。

    可在运行时通过 agent_policy 工具或直接操作 agent._introspection_policy 调整：
        policy.hidden_targets.add("env")       # 隐藏 env inspect
        policy.locked_targets.add("config")    # 锁定 config 不可 patch
        policy.locked_fields["config"] = {"sandbox"}  # 只锁 sandbox 字段
    """

    def __init__(self) -> None:
        self.hidden_targets: set[str] = set()
        self.locked_targets: set[str] = set()
        self.locked_fields: dict[str, set[str]] = {}

    def is_visible(self, target: str) -> bool:
        return target not in self.hidden_targets

    def is_patchable(self, target: str, field: str) -> tuple[bool, str]:
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


# ══════════════════════════════════════════════════════════════════════════════
# 代码元信息提取（META LAYER）
# ══════════════════════════════════════════════════════════════════════════════

# 每个 inspect target 对应的元数据静态描述
# 格式：{ target: { class_name, source_file（相对项目根）, agent_attr, agent_init_lines } }
_TARGET_META_STATIC: dict[str, dict] = {
    "config": {
        "class_name": "AppConfig",
        "source_file": "src/mini_agent/config/models.py",
        "agent_attr": "cfg",
        "description": "应用主配置，所有功能开关和参数的统一入口。子配置块按功能域聚合（memory/compress/skill/perception 等）。",
        "agent_init_context": "agent.__init__ 参数直接传入，或由 config/loader.py 从 agent_config.json 加载",
        "related_files": [
            "src/mini_agent/config/loader.py",
            "src/mini_agent/config/prompt_builder.py",
            "agent_config.json",
        ],
    },
    "history": {
        "class_name": "list[dict]",
        "source_file": "src/mini_agent/history_manager.py",
        "agent_attr": "_history",
        "description": "当前对话历史，OpenAI/Anthropic 消息格式列表。HistoryManager._history 与 Agent._history 共享同一对象。",
        "agent_init_context": "_init_components 中：self._hist = HistoryManager(...); self._history = self._hist._history",
        "related_files": [
            "src/mini_agent/history_manager.py",
            "src/mini_agent/history/entry.py",
            "src/mini_agent/history/compression.py",
            "src/mini_agent/history/raw_history.py",
        ],
    },
    "stats": {
        "class_name": "SessionStats",
        "source_file": "src/mini_agent/config/models.py",
        "agent_attr": "stats",
        "description": "本次会话的运行统计：turns/token 用量/工具调用次数/各工具明细。每轮 run_turn 结束后累积。",
        "agent_init_context": "agent.__init__：self.stats = SessionStats()",
        "related_files": ["src/mini_agent/config/models.py"],
    },
    "skills": {
        "class_name": "SkillLoader",
        "source_file": "src/mini_agent/skills/__init__.py",
        "agent_attr": "skill_loader",
        "description": "技能加载与激活管理器。负责发现 .claude/skills/ 下的 SKILL.md 文件、关键词匹配、context 注入。",
        "agent_init_context": "cli/app.py 构造并传入 Agent，或 Agent 外部使用者注入",
        "related_files": [
            "src/mini_agent/skills/__init__.py",
            "src/mini_agent/skills/tracker.py",
            "src/mini_agent/skills/usage_detector.py",
            "src/mini_agent/tools/skill_manager.py",
        ],
    },
    "tools": {
        "class_name": "ToolRegistry",
        "source_file": "src/mini_agent/tools/__init__.py",
        "agent_attr": "registry",
        "description": "工具注册表，持有所有可调用工具的定义（ToolDef）。SubAgent 通过 filtered() 获得工具子集。",
        "agent_init_context": "agent.__init__：self.registry = registry or get_default_registry()",
        "related_files": [
            "src/mini_agent/tools/__init__.py",
            "src/mini_agent/tools/builtin.py",
            "src/mini_agent/tools/skill_manager.py",
            "src/mini_agent/tools/introspection.py",
            "src/mini_agent/tools/evolution.py",
            "src/mini_agent/tools/orchestration.py",
        ],
    },
    "memory": {
        "class_name": "MemoryStore",
        "source_file": "src/mini_agent/perception/memory_store.py",
        "agent_attr": "_memory / _global_memory",
        "description": "长期记忆持久化（JSONL）。_memory 是项目级，_global_memory 是全局 (~/.agent/memory.jsonl)。TF-IDF 检索。",
        "agent_init_context": "agent.__init__：if cfg.memory_enabled: self._memory, self._global_memory = create_both_memory_backends(cfg)",
        "related_files": [
            "src/mini_agent/perception/memory_store.py",
            "src/mini_agent/perception/memory_base.py",
            "src/mini_agent/perception/memory_factory.py",
            "src/mini_agent/perception/lesson_rules.py",
        ],
    },
    "providers": {
        "class_name": "LLMClientPool",
        "source_file": "src/mini_agent/llm/client_pool.py",
        "agent_attr": "_client_pool",
        "description": "多套 LLM 配置的故障转移链 + 多 Key 轮转调度器。当主配置全部失败后按 llm_fallback_chain 顺序切换。",
        "agent_init_context": "agent.__init__：self._client_pool = LLMClientPool.from_config(cfg)",
        "related_files": [
            "src/mini_agent/llm/client_pool.py",
            "src/mini_agent/llm/base.py",
            "src/mini_agent/llm/factory.py",
            "src/mini_agent/llm/providers/anthropic.py",
            "src/mini_agent/llm/providers/openai.py",
            "src/mini_agent/llm/providers/_base_mixin.py",
            "providers.json",
        ],
    },
    "registry": {
        "class_name": "ToolRegistry",
        "source_file": "src/mini_agent/tools/__init__.py",
        "agent_attr": "registry",
        "description": "同 target='tools'，registry 视图侧重分组索引结构。",
        "agent_init_context": "同 tools",
        "related_files": ["src/mini_agent/tools/__init__.py"],
    },
    "session": {
        "class_name": "Session",
        "source_file": "src/mini_agent/session.py",
        "agent_attr": "_session",
        "description": "当前会话对象，持有 id/title/stats/history 等字段。由 SessionManager 管理读写到 .agent/sessions/。",
        "agent_init_context": "_init_session 中：self._session = self._session_mgr.new_session(...)",
        "related_files": [
            "src/mini_agent/session.py",
            "src/mini_agent/storage/paths.py",
        ],
    },
    "perception": {
        "class_name": "FileWatcher / ToolResultCache / ProjectScanner",
        "source_file": "src/mini_agent/perception/",
        "agent_attr": "_file_watcher / _tool_cache / _project_snapshot",
        "description": "感知子系统集合：文件变化监听（FileWatcher）、工具结果缓存（ToolResultCache）、项目结构扫描（ProjectScanner）。",
        "agent_init_context": "agent.__init__：按各自 cfg.perception.* 开关独立初始化",
        "related_files": [
            "src/mini_agent/perception/file_watcher.py",
            "src/mini_agent/perception/tool_cache.py",
            "src/mini_agent/perception/project_scanner.py",
            "src/mini_agent/perception/token_counter.py",
        ],
    },
    "retry_policy": {
        "class_name": "RetryPolicy",
        "source_file": "src/mini_agent/llm/retry.py",
        "agent_attr": "_retry_policy",
        "description": "LLM 调用重试策略：持有重试条件（EmptyOutputCondition 等）和退避策略（FixedBackoff/ExponentialBackoff）。",
        "agent_init_context": "agent.__init__：self._retry_policy = default_retry_policy(max_retries=..., backoff=...)",
        "related_files": ["src/mini_agent/llm/retry.py"],
    },
    "mcp": {
        "class_name": "MCPManager",
        "source_file": "src/mini_agent/mcp/manager.py",
        "agent_attr": "_mcp_manager",
        "description": "MCP server 生命周期管理和工具调用代理。连接配置来自 cfg.mcp（agent_config.json 的 mcp 节）。",
        "agent_init_context": "agent.__init__：if cfg.mcp.enabled: self._mcp_manager = MCPManager(cfg.mcp, ...)",
        "related_files": [
            "src/mini_agent/mcp/manager.py",
            "src/mini_agent/mcp/config.py",
            "src/mini_agent/mcp/transport.py",
            "agent_config.json",
        ],
    },
    "env": {
        "class_name": "os.environ",
        "source_file": None,
        "agent_attr": "os.environ (filtered)",
        "description": "运行时环境变量（过滤出 MINI_AGENT/ANTHROPIC/OPENAI/LLM/AGENT 等相关前缀）。KEY/TOKEN 类自动脱敏。",
        "agent_init_context": "stdlib os.environ，不由 agent 构造",
        "related_files": [".env", "agent_config.json"],
    },
    "process": {
        "class_name": "os / psutil",
        "source_file": None,
        "agent_attr": "os.getpid() + resource/psutil",
        "description": "当前进程信息：PID、内存(RSS)、CPU 时间、活跃线程列表。",
        "agent_init_context": "stdlib，不由 agent 构造",
        "related_files": [],
    },
}


def _get_class_meta(source_file: Optional[str], class_name: str, proj_root: Optional[Path]) -> dict:
    """
    用 AST 解析源文件，提取指定类的：
      - 起止行号
      - 类 docstring
      - __init__ 参数签名
      - 公开/私有方法名与首行 docstring
      - dataclass 字段（名称、类型注解字符串、默认值）
    不执行 import，纯静态分析，不会触发副作用。
    """
    if not source_file or not proj_root:
        return {}

    # 如果 source_file 指向目录，直接返回空（perception 是多文件）
    abs_path = proj_root / source_file
    if not abs_path.exists() or abs_path.is_dir():
        return {}

    try:
        src_text = abs_path.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        lines = src_text.splitlines()
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.introspection._get_class_meta')
        return {"_parse_error": str(e)}

    # 找目标类
    target_cls: Optional[ast.ClassDef] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            target_cls = node
            break

    if target_cls is None:
        # 可能是 "ClassName1 / ClassName2" 格式，取第一个
        first_name = class_name.split("/")[0].strip().split("[")[0].strip()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == first_name:
                target_cls = node
                break

    if target_cls is None:
        return {"_note": f"class {class_name!r} not found in {source_file}"}

    result: dict[str, Any] = {}
    result["class_line_start"] = target_cls.lineno
    result["class_line_end"] = target_cls.end_lineno

    # 类 docstring
    cls_doc = ast.get_docstring(target_cls) or ""
    result["class_docstring"] = cls_doc[:400] if cls_doc else ""

    # 方法列表
    methods_pub: list[dict] = []
    methods_priv: list[dict] = []
    init_sig: Optional[str] = None

    for node in target_cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        mname = node.name
        mdoc = (ast.get_docstring(node) or "").split("\n")[0][:120]
        args = [a.arg for a in node.args.args if a.arg != "self"]
        minfo = {"name": mname, "args": args, "doc": mdoc,
                 "line": node.lineno, "line_end": node.end_lineno}
        if mname == "__init__":
            init_sig = f"({', '.join(args)})"
        elif mname.startswith("__"):
            pass  # 跳过其他 dunder
        elif mname.startswith("_"):
            methods_priv.append(minfo)
        else:
            methods_pub.append(minfo)

    result["init_signature"] = init_sig or "()"
    result["methods_public"] = methods_pub
    result["methods_private"] = methods_priv

    # dataclass 字段（检查是否有 @dataclass decorator）
    is_dc = any(
        (isinstance(d, ast.Name) and d.id == "dataclass") or
        (isinstance(d, ast.Attribute) and d.attr == "dataclass")
        for d in target_cls.decorator_list
    )
    if is_dc:
        fields: list[dict] = []
        for node in target_cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                fname = node.target.id
                ftype = ast.unparse(node.annotation) if hasattr(ast, "unparse") else "?"
                fdefault = ast.unparse(node.value) if node.value and hasattr(ast, "unparse") else None
                fields.append({"name": fname, "type": ftype, "default": fdefault})
        result["dataclass_fields"] = fields

    return result


def _get_agent_init_snippet(attr_name: str, proj_root: Optional[Path], context_lines: int = 3) -> dict:
    """
    在 mini_agent/agent/ 包（core.py + 各职责 Mixin 文件）中搜索 self.<attr_name>
    的赋值行，返回：
      - 所有赋值出现的行号、所在文件及带行号标注的上下文代码片段（± context_lines 行）
      - is_declaration: True 表示仅类型声明（= None/[]/{}），非实际构造
      - assignment: 赋值行原始文本

    [Stage 12] agent.py 已拆分为 agent/ 目录（core.py 保留 __init__，其余
    方法按职责分散在 lifecycle.py / reflection.py / llm_control.py 等文件中，
    这些方法里同样可能出现 self.<attr> 赋值），因此需要遍历整个目录而不是
    单个文件；core.py 优先排在结果最前面（大多数属性的"首次赋值"仍在 __init__ 里）。

    snippet 中赋值行用 ">>>" 标注，其余行用空格前缀，方便定位。
    """
    if not proj_root:
        return {}

    agent_dir = proj_root / "src" / "mini_agent" / "agent"
    if not agent_dir.exists():
        return {}

    # core.py（含 __init__）优先，其余按文件名排序，保证结果顺序稳定
    py_files = sorted(
        (p for p in agent_dir.glob("*.py") if p.name != "__init__.py"),
        key=lambda p: (p.name != "core.py", p.name),
    )

    hits: list[dict] = []
    for py_file in py_files:
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.tools.introspection._get_agent_init_snippet')
            hits.append({"_error": f"{py_file.name}: {e}"})
            continue

        rel_file = f"src/mini_agent/agent/{py_file.name}"
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped.startswith(f"self.{attr_name}"):
                continue
            rest = stripped[len(f"self.{attr_name}"):]
            if "=" not in rest or stripped.startswith("#"):
                continue

            # 判断是否仅类型声明（无实际构造意义）
            rhs = rest.split("=", 1)[1].strip() if "=" in rest else ""
            is_declaration_only = (
                rest.lstrip().startswith(":") and
                rhs in ("None", "[]", "{}", '""', "''")
            )

            # 带行号前缀的 snippet，赋值行用 >>> 标注
            start = max(0, i - 1 - context_lines)
            end = min(len(lines), i - 1 + context_lines + 1)
            snippet_lines = []
            for j in range(start, end):
                marker = ">>>" if j == i - 1 else "   "
                snippet_lines.append(f"{marker} {j+1:4d}: {lines[j]}")

            hits.append({
                "file": rel_file,
                "line": i,
                "is_declaration": is_declaration_only,
                "assignment": stripped[:120],
                "snippet": "\n".join(snippet_lines),
                "snippet_start_line": start + 1,
            })

    return {
        "agent_dir": "src/mini_agent/agent/",
        "occurrences": hits,
        "total": len(hits),
    }


def _build_meta(target: str, proj_root: Optional[Path]) -> dict:
    """为指定 target 构建完整元信息。"""
    static = _TARGET_META_STATIC.get(target, {})
    if not static:
        return {"_note": f"no static meta defined for target '{target}'"}

    meta: dict[str, Any] = {
        "target": target,
        "class_name": static.get("class_name", ""),
        "agent_attr": static.get("agent_attr", ""),
        "description": static.get("description", ""),
        "agent_init_context": static.get("agent_init_context", ""),
        "source_file": static.get("source_file"),
        "related_files": static.get("related_files", []),
    }

    # 绝对路径（方便 IDE 跳转）
    if meta["source_file"] and proj_root:
        meta["source_file_abs"] = str(proj_root / meta["source_file"])

    # AST 提取类元信息
    source_file = static.get("source_file")
    # class_name 可能是 "A / B" 多类名，取第一个用于 AST 搜索
    primary_class = (static.get("class_name") or "").split("/")[0].strip().split("[")[0].strip()
    if source_file and primary_class and proj_root:
        class_meta = _get_class_meta(source_file, primary_class, proj_root)
        meta["class_meta"] = class_meta

        # 如果 source_file 是多文件目录，尝试为每个相关文件提取
        if source_file.endswith("/"):
            meta["_note"] = "perception 由多个文件组成，详见 related_files"

    # agent.py 中的赋值/构造片段
    attr = static.get("agent_attr", "")
    # attr 可能是 "a / b"，取第一个
    primary_attr = attr.split("/")[0].strip()
    if primary_attr and not primary_attr.startswith("os."):
        init_info = _get_agent_init_snippet(primary_attr, proj_root)
        meta["agent_construction"] = init_info

    return meta


# ══════════════════════════════════════════════════════════════════════════════
# 辅助：序列化与脱敏
# ══════════════════════════════════════════════════════════════════════════════

def _safe_json(obj: Any, indent: int = 2) -> str:
    def _default(o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        return repr(o)
    try:
        return json.dumps(obj, indent=indent, ensure_ascii=False, default=_default)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.introspection._safe_json')
        return json.dumps({"_error": f"序列化失败: {e}"}, indent=indent, ensure_ascii=False)


def _mask_secrets(d: dict) -> dict:
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.introspection._safe_get')
        return f"<error: {e}>"


# ══════════════════════════════════════════════════════════════════════════════
# 数据采集器（每个 target 一个函数）
# ══════════════════════════════════════════════════════════════════════════════

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

    # sl.available -> list[str]（名字列表）
    # sl.active    -> list[str]（名字列表）
    # sl._all      -> dict[str, Skill]（按名索引的 Skill 对象）
    all_names: list[str] = _safe_get(lambda: list(sl.available), [])
    active_names: list[str] = _safe_get(lambda: list(sl.active), [])
    skill_objs: dict = _safe_get(lambda: sl._all, {})

    skills_detail = []
    for name in all_names:
        sk = skill_objs.get(name) if isinstance(skill_objs, dict) else None
        skills_detail.append({
            "name": name,
            "active": name in active_names,
            "location": _safe_get(lambda s=sk: str(s.location) if s and hasattr(s, "location") else "N/A"),
            "trigger_words": _safe_get(lambda s=sk: list(s.trigger_words) if s and hasattr(s, "trigger_words") else []),
            "description": _safe_get(lambda s=sk: (s.description or "")[:200] if s and hasattr(s, "description") else ""),
            "confidence_score": _safe_get(lambda s=sk: s.confidence_score if s and hasattr(s, "confidence_score") else "N/A"),
            "requires": _safe_get(lambda s=sk: list(s.requires) if s and hasattr(s, "requires") else []),
            "conflicts_with": _safe_get(lambda s=sk: list(s.conflicts_with) if s and hasattr(s, "conflicts_with") else []),
        })

    return {
        "enabled": True,
        "total_available": len(all_names),
        "total_active": len(active_names),
        "active_names": active_names,
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
    # LLMClientPool 提供 snapshot() 而非 status()
    info = _safe_get(lambda: pool.snapshot())
    if isinstance(info, dict):
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
            "status": _safe_get(lambda: tc.stats_summary() if tc and hasattr(tc, "stats_summary") else "N/A"),
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
            if any(secret in k.upper() for secret in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                env_vars[k] = f"***({len(v)} chars)"
            else:
                env_vars[k] = v
    return {"relevant_env": env_vars, "cwd": os.getcwd()}


def _collect_process(_agent) -> dict:
    result = {
        "pid": os.getpid(),
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


# ══════════════════════════════════════════════════════════════════════════════
# agent_status 简报
# ══════════════════════════════════════════════════════════════════════════════

def _build_status(agent, policy: IntrospectionPolicy) -> dict:
    cfg = agent.cfg
    stats = agent.stats

    def _s(fn, default="N/A"):
        try:
            return fn()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.tools.introspection._build_status._s')
            return default

    sections: dict[str, Any] = {}

    sections["llm"] = {
        "provider":           _s(lambda: cfg.llm_provider),
        "model":              _s(lambda: cfg.model),
        "max_tokens":         _s(lambda: cfg.max_tokens),
        "stream":             _s(lambda: cfg.stream),
        "fallback_chain_len": _s(lambda: len(cfg.llm_fallback_chain)),
    }
    sections["runtime"] = {
        "sandbox":       _s(lambda: cfg.sandbox),
        "auto_approve":  _s(lambda: cfg.auto_approve),
        "verbose":       _s(lambda: cfg.verbose),
        "max_turns":     _s(lambda: cfg.max_turns),
        "max_llm_calls": _s(lambda: cfg.max_llm_calls),
        "is_subagent":   _s(lambda: agent._is_subagent),
    }
    sess = agent._session
    sections["session"] = {
        "id":           _s(lambda: sess.id if sess else "N/A"),
        "title":        _s(lambda: sess.title if sess else "N/A"),
        "created_at":   _s(lambda: sess.created_at if sess else "N/A"),
        "project_root": _s(lambda: str(cfg.project_root)),
    }
    sections["stats"] = {
        "turns":         stats.turns,
        "input_tokens":  stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "tool_calls":    stats.tool_calls,
        "elapsed":       _s(lambda: stats.elapsed()),
    }
    sections["history"] = {
        "message_count":    _s(lambda: len(agent._history)),
        "estimated_tokens": _s(lambda: __import__(
            "mini_agent.perception.token_counter", fromlist=["estimate_messages_tokens"]
        ).estimate_messages_tokens(agent._history)),
    }
    sl = agent.skill_loader
    sections["skills"] = {
        "enabled":   sl is not None,
        "active":    _s(lambda: list(sl.active) if sl else []),
        "available": _s(lambda: len(list(sl.available)) if sl else 0),
    }
    sections["tools"] = {
        "total_registered": _s(lambda: len(agent.registry.names)),
        "groups":           _s(lambda: list(agent.registry.groups)),
    }
    sections["subsystems"] = {
        "memory":       _s(lambda: cfg.memory.enabled),
        "compress":     _s(lambda: cfg.compress.enabled),
        "project_scan": _s(lambda: cfg.perception.project_scan_enabled),
        "file_watch":   _s(lambda: cfg.perception.file_watch_enabled),
        "tool_cache":   _s(lambda: cfg.perception.tool_cache_enabled),
        "mcp":          _s(lambda: cfg.mcp.enabled),
        "reminder":     _s(lambda: getattr(cfg, "reminder", None) and getattr(cfg.reminder, "enabled", False)),
        "profile":      _s(lambda: cfg.profile.enabled if hasattr(cfg, "profile") else "N/A"),
        "web_search":   _s(lambda: cfg.web_search.enabled if hasattr(cfg, "web_search") else "N/A"),
    }
    rp = agent._retry_policy
    sections["retry_policy"] = {
        "max_retries": _s(lambda: rp.max_retries if rp else "N/A"),
        "backoff":     _s(lambda: repr(rp.backoff) if rp and hasattr(rp, "backoff") else "N/A"),
    }
    sections["process"] = {
        "pid":     os.getpid(),
        "threads": _s(lambda: threading.active_count()),
    }
    sections["introspection_policy"] = {
        "hidden_targets": sorted(policy.hidden_targets),
        "locked_targets": sorted(policy.locked_targets),
        "locked_fields":  {k: sorted(v) for k, v in policy.locked_fields.items()},
    }
    return sections


# ══════════════════════════════════════════════════════════════════════════════
# Patch 执行器
# ══════════════════════════════════════════════════════════════════════════════

def _do_patch(agent, target: str, field: str, value: str, policy: IntrospectionPolicy) -> str:
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
            if field in ("auto_approve", "sandbox") and agent.guard:
                setattr(agent.guard, field, converted)
            return json.dumps({"success": True, "target": target, "field": field,
                               "old": str(old), "new": str(converted)}, ensure_ascii=False)

        elif target == "retry_policy":
            if field == "max_retries":
                converted = int(value)
                if converted < 0:
                    return json.dumps({"success": False, "error": "max_retries 必须 >= 0"}, ensure_ascii=False)
                old = agent._retry_policy.max_retries
                agent._retry_policy.max_retries = converted
                return json.dumps({"success": True, "target": target, "field": field,
                                   "old": old, "new": converted}, ensure_ascii=False)

        elif target == "stats" and field == "reset":
            from mini_agent.config import SessionStats
            old_turns = agent.stats.turns
            agent.stats = SessionStats()
            return json.dumps({"success": True, "target": target, "field": "reset",
                               "note": f"SessionStats 已重置（原 turns={old_turns}）"}, ensure_ascii=False)

        elif target == "tool_cache" and field == "clear":
            tc = agent._tool_cache
            if tc is None:
                return json.dumps({"success": False, "error": "tool_cache 未启用"}, ensure_ascii=False)
            if hasattr(tc, "clear"):
                tc.clear()
            elif hasattr(tc, "_store"):
                with getattr(tc, "_lock", threading.Lock()):
                    tc._store.clear()
            return json.dumps({"success": True, "note": "tool_cache 已清空"}, ensure_ascii=False)

        elif target == "skill":
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
            return json.dumps({"success": True, "target": target, "field": field,
                               "new": activate}, ensure_ascii=False)

    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.introspection._do_patch')
        return json.dumps({"success": False, "error": f"patch 执行异常: {e}"}, ensure_ascii=False)

    return json.dumps({"success": False,
                       "error": f"target='{target}' field='{field}' 未匹配到执行逻辑"}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# 注册入口
# ══════════════════════════════════════════════════════════════════════════════

def register_introspection_tools(registry: "ToolRegistry", agent) -> None:
    """
    向 registry 注册四个自省工具，并在 agent 上挂载策略对象。

    agent._introspection_policy 可随时调整可见性/可改性：
        agent._introspection_policy.hidden_targets.add("memory")
        agent._introspection_policy.locked_targets.add("config")
    """
    policy = IntrospectionPolicy()
    agent._introspection_policy = policy

    # 项目根（用于 meta 层的 AST 解析）
    proj_root = _project_root()
    # 也可以从 cfg 读取，更准确
    try:
        proj_root = Path(agent.cfg.project_root).resolve()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.introspection')
        pass

    # ── Tool 1: agent_status ──────────────────────────────────────────────────

    def agent_status() -> str:
        """
        返回 agent 当前所有关键子系统的实时简报（JSON）。
        涵盖 LLM 配置、运行标志、会话信息、统计数据、技能、工具、子系统开关、进程信息等。
        快速了解自身全貌时首选此工具；需要深入某个子系统时再调用 agent_inspect。
        """
        data = _build_status(agent, policy)
        R.print_tool_call("agent_status", {})
        return _safe_json(data)

    registry.register_fn(
        agent_status,
        name="agent_status",
        description="获取 agent 当前所有关键子系统的实时简报（轻量只读）",
        input_schema={"type": "object", "properties": {}, "required": []},
        requires_approval=False,
        group="introspection",
        override=True,
    )

    # ── Tool 2: agent_inspect ─────────────────────────────────────────────────

    def agent_inspect(target: str, include_meta: bool = False) -> str:
        """
        深入查看 agent 指定子系统的完整状态。
        target: config/history/stats/skills/tools/memory/providers/registry/
                session/perception/retry_policy/mcp/env/process
        include_meta: 为 True 时附加代码元信息（源文件位置、类结构、
                      构造方式、方法列表、dataclass 字段定义、相关文件）。
                      适合在修改代码前了解目标对象的完整背景。
        返回 JSON 格式的详细状态信息。
        """
        target = target.strip().lower()

        if target not in ALL_INSPECT_TARGETS:
            available = sorted(t for t in ALL_INSPECT_TARGETS if policy.is_visible(t))
            return json.dumps({"error": f"未知 target '{target}'",
                               "available_targets": available}, ensure_ascii=False)

        if not policy.is_visible(target):
            return json.dumps({"error": f"target '{target}' 当前不可见（被策略隐藏）"},
                              ensure_ascii=False)

        collector = _COLLECTORS.get(target)
        if collector is None:
            return json.dumps({"error": f"target '{target}' 暂无采集器"}, ensure_ascii=False)

        R.print_tool_call("agent_inspect", {"target": target, "include_meta": include_meta})
        try:
            data = collector(agent)
            result: dict[str, Any] = {
                "target": target,
                "data": data,
                "_ts": time.time(),
            }
            if include_meta:
                result["meta"] = _build_meta(target, proj_root)
            return _safe_json(result)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.tools.introspection.register_introspection_tools.agent_inspect')
            return json.dumps({"target": target, "error": str(e)}, ensure_ascii=False)

    registry.register_fn(
        agent_inspect,
        name="agent_inspect",
        description=(
            "深入查看 agent 指定子系统的完整详情（只读）。"
            "include_meta=true 时附加源文件位置、类结构、构造方式、方法列表等代码元信息，"
            "适合在修改代码前了解目标对象背景。"
            "target 可选: config, history, stats, skills, tools, memory, "
            "providers, registry, session, perception, retry_policy, mcp, env, process"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "要查看的子系统名称",
                    "enum": sorted(ALL_INSPECT_TARGETS),
                },
                "include_meta": {
                    "type": "boolean",
                    "description": "是否附加代码元信息（源文件、类结构、构造方式、方法列表）。修改代码前建议设为 true。",
                    "default": False,
                },
            },
            "required": ["target"],
        },
        requires_approval=False,
        group="introspection",
        override=True,
    )

    # ── Tool 3: agent_patch ───────────────────────────────────────────────────

    _patchable_summary = "; ".join(
        f"{t}: [{', '.join(f for f in fields if f != '__dynamic__')}{'...' if '__dynamic__' in fields else ''}]"
        for t, fields in _PATCH_WHITELIST.items()
    )

    def agent_patch(target: str, field: str, value: str) -> str:
        """
        在运行时修改 agent 的指定配置或状态字段（热修改，无需重启）。
        target: config / retry_policy / stats / tool_cache / skill
        field:  要修改的字段名；stats 使用 'reset'，tool_cache 使用 'clear'，
                skill 使用 '<skill_name>:active'
        value:  新值（字符串形式，工具内部自动转换为目标类型）
        修改立即生效，但不持久化到配置文件（重启后恢复原值）。
        """
        target = target.strip().lower()
        field = field.strip()
        R.print_tool_call("agent_patch", {"target": target, "field": field, "value": value})
        return _do_patch(agent, target, field, value, policy)

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
                        "要修改的字段。config: auto_approve/sandbox/model/max_tokens/temperature/"
                        "verbose/stream/max_turns/max_llm_calls; "
                        "retry_policy: max_retries; stats: reset; tool_cache: clear; "
                        "skill: <name>:active"
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "新值（字符串，内部自动类型转换）",
                },
            },
            "required": ["target", "field", "value"],
        },
        requires_approval=True,
        group="introspection",
        override=True,
    )

    # ── Tool 4: agent_policy ──────────────────────────────────────────────────

    def agent_policy(
        action: str,
        target: Optional[str] = None,
        field: Optional[str] = None,
    ) -> str:
        """
        查看或调整自省系统的可见性/可改性策略。
        action:
          'show'                        — 显示当前策略
          'hide_target'  target=<t>    — 隐藏某个 inspect target
          'show_target'  target=<t>    — 取消隐藏
          'lock_target'  target=<t>    — 锁定（禁止 patch 整个 target）
          'unlock_target' target=<t>   — 解锁
          'lock_field'   target=<t> field=<f>  — 锁定具体字段
          'unlock_field' target=<t> field=<f>  — 解锁
        """
        R.print_tool_call("agent_policy", {"action": action, "target": target, "field": field})
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
        override=True,
    )
