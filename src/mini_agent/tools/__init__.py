"""
Tool Registry.
Each tool is a plain Python function decorated with @tool().
The registry collects them, generates Anthropic-compatible tool schemas,
and dispatches calls by name.

重构（v2）：增加命名空间 + 工具分组支持。
- register() 支持 group 参数，将工具归入分组（默认 "builtin"）
- override=True 允许显式替换已注册的工具（防止静默覆盖）
- subset(groups) 返回只包含指定分组工具的子 registry（用于 SubAgent 工具限制）
- 全局 _default_registry 保持向后兼容
"""

from __future__ import annotations

import inspect
import json
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ToolDef:
    name: str
    description: str
    fn: Callable
    input_schema: dict   # JSON Schema object
    requires_approval: bool = True
    group: str = "builtin"           # 所属分组，用于 subset() 筛选
    # [platform_filter] 平台/tag 限制：空 = 不限制，见 mini_agent.platform_filter
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self, namespace: str = "default") -> None:
        self.namespace = namespace
        self._tools: dict[str, ToolDef] = {}
        self._groups: dict[str, list[str]] = {}   # group_name -> [tool_name]

    # ── Registration ───────────────────────────────────────────────────────────

    def register(self, tool_def: ToolDef, override: bool = False) -> None:
        """
        注册工具。
        
        Args:
            tool_def: 工具定义
            override: True = 允许覆盖已注册工具；False = 重复注册时抛出 ValueError
        """
        # [platform_filter] 平台/tag 不满足时静默跳过注册：工具完全不会出现在
        # _tools / names / Anthropic tool schema 里（模型看不到它的存在）。
        from mini_agent.platform_filter import get_load_policy
        allowed, _reason = get_load_policy().is_allowed(
            platforms=tool_def.platforms, tags=tool_def.tags,
            kind="tool", name=tool_def.name,
        )
        if not allowed:
            return

        if tool_def.name in self._tools and not override:
            raise ValueError(
                f"Tool {tool_def.name!r} already registered in namespace {self.namespace!r}. "
                f"Use override=True to replace it."
            )
        self._tools[tool_def.name] = tool_def
        # 更新分组索引
        group = tool_def.group or "builtin"
        if tool_def.name not in self._groups.setdefault(group, []):
            self._groups[group].append(tool_def.name)

    def register_fn(
        self,
        fn: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[dict] = None,
        requires_approval: bool = True,
        group: str = "builtin",
        override: bool = False,
        platforms: Optional[list] = None,
        tags: Optional[list] = None,
    ) -> None:
        tool_name = name or fn.__name__
        desc = description or (inspect.getdoc(fn) or "").split("\n")[0]
        schema = input_schema or _infer_schema(fn)
        self.register(
            ToolDef(
                name=tool_name,
                description=desc,
                fn=fn,
                input_schema=schema,
                requires_approval=requires_approval,
                group=group,
                platforms=list(platforms or []),
                tags=list(tags or []),
            ),
            override=override,
        )

    # ── Lookup ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def names_in_group(self, group: str) -> list[str]:
        """返回指定分组内的工具名列表。"""
        return list(self._groups.get(group, []))

    @property
    def groups(self) -> list[str]:
        """返回所有已注册的分组名。"""
        return list(self._groups)

    # ── SubRegistry ───────────────────────────────────────────────────────────

    def filtered(self, names: Optional[list[str]] = None, groups: Optional[list[str]] = None) -> "ToolRegistry":
        """
        返回按工具名和/或分组筛选出的子 registry。

        - names: 显式允许的工具名列表
        - groups: 允许的分组列表
        - 两者都为空 -> 返回包含全部工具的子 registry（等价于全量拷贝）
        - 两者都给出 -> 取并集

        用于自定义子 agent（AgentProfile.tools / tool_groups）限制可用工具集。

        [BUGFIX] 这里"两者都为空"用的是 Python 真值判断（`not names`），
        意味着传入 `names=[]`/`groups=[]`（显式空列表，非 None）跟完全不传
        效果相同，都会返回全量工具——这是本方法从一开始就有意的设计（"没有
        限制条件时给全部工具"，供 tools_enabled=True 但调用方未指定
        allowed_tools/allowed_tool_groups 时使用），**不能改成"空列表=空
        registry"**，否则会破坏这条路径。如果需要的是"明确构造一个不含任何
        工具的 registry"，请用 `empty()`，不要指望 `filtered(names=[],
        groups=[])` 能表达这个语义——历史上 judge_factory.py /
        workflow/generator.py / workflow/session_summarizer.py 都曾经这样
        误用，导致"不给工具"的内部 Agent 实际拿到了全量工具集。
        """
        if not names and not groups:
            allowed = set(self._tools)
        else:
            allowed = set(names or [])
            allowed |= {t for g in (groups or []) for t in self._groups.get(g, [])}

        sub = ToolRegistry(namespace=self.namespace)
        for tool_name, td in self._tools.items():
            if tool_name in allowed:
                sub._tools[tool_name] = td
                sub._groups.setdefault(td.group, []).append(tool_name)
        return sub

    def empty(self) -> "ToolRegistry":
        """返回一个不包含任何工具的空 registry（同一 namespace）。

        用于"这个内部 Agent 不应该有任何工具"的场景——不要用
        `filtered(names=[], groups=[])` 代替，那个调用在两个参数都是空列表
        时会被当成"未筛选"，返回的是全量工具而不是空集（见 filtered() 的
        说明）。
        """
        return ToolRegistry(namespace=self.namespace)

    def subset(self, groups: list[str]) -> "ToolRegistry":
        """
        返回只包含指定分组工具的子 registry。

        用于 SubAgent 工具限制：主 Agent 拥有全部工具，
        SubAgent 只允许使用 "readonly"、"builtin" 等安全工具组。

        Example:
            sub_registry = registry.subset(["builtin", "readonly"])
            sub_agent = SubAgent(tool_registry=sub_registry, ...)
        """
        sub = ToolRegistry(namespace=self.namespace)
        allowed = {t for g in groups for t in self._groups.get(g, [])}
        for name, td in self._tools.items():
            if name in allowed:
                # 直接写入，绕过重复检查
                sub._tools[name] = td
                sub._groups.setdefault(td.group, []).append(name)
        return sub

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def call(self, name: str, tool_input: dict) -> Any:
        """Execute a tool by name. Returns the result (str or dict)."""
        td = self._tools.get(name)
        if not td:
            raise ValueError(f"Unknown tool: {name!r} (namespace={self.namespace!r})")
        return td.fn(**tool_input)

    # ── Anthropic API format ───────────────────────────────────────────────────

    def to_api_tools(self) -> list[dict]:
        """Return tool definitions in Anthropic Messages API format."""
        return [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.input_schema,
            }
            for td in self._tools.values()
        ]


# ── Decorator ─────────────────────────────────────────────────────────────────

# Module-level default registry
_default_registry = ToolRegistry()


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    schema: Optional[dict] = None,
    requires_approval: bool = True,
    group: str = "builtin",
    override: bool = False,
    platforms: Optional[list] = None,
    tags: Optional[list] = None,
):
    """
    Decorator to register a function as a tool in the default registry.

    Args:
        name:             Tool name (default: function name)
        description:      Tool description (default: first line of docstring)
        schema:           JSON Schema for tool input (default: inferred from type hints)
        requires_approval: Whether the tool requires user approval before execution
        group:            Tool group name, used for SubAgent tool filtering (default: "builtin")
        override:         Allow replacing an already-registered tool (default: False)
        platforms:        [platform_filter] 平台限制，空 = 不限制，如 ["termux"]
        tags:             [platform_filter] tag 列表，受 platform_policy.json 的 allow/deny 管辖
    """
    def decorator(fn: Callable) -> Callable:
        _default_registry.register_fn(
            fn,
            name=name,
            description=description,
            input_schema=schema,
            requires_approval=requires_approval,
            group=group,
            override=override,
            platforms=platforms,
            tags=tags,
        )
        return fn
    return decorator


def get_default_registry() -> ToolRegistry:
    return _default_registry


# ── Schema inference ───────────────────────────────────────────────────────────

_PY_TO_JSON: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _infer_schema(fn: Callable) -> dict:
    """
    Build a JSON Schema from Python type annotations and defaults.
    Supports str, int, float, bool, list, dict, and Optional[X].
    """
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        ann = param.annotation
        has_default = param.default is not inspect.Parameter.empty
        json_type, description = _annotation_to_json(ann, param_name)
        prop: dict[str, Any] = {"type": json_type}
        if description:
            prop["description"] = description
        # If Optional, we've already unwrapped; don't mark required
        if not has_default and json_type != "null":
            # Check if it was Optional
            import typing
            origin = getattr(ann, "__origin__", None)
            args = getattr(ann, "__args__", ())
            is_optional = origin is type(None) or (
                origin is getattr(inspect, "_empty", None)
            ) or _is_optional(ann)
            if not is_optional:
                required.append(param_name)
        properties[param_name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _is_optional(ann: Any) -> bool:
    import typing
    origin = getattr(ann, "__origin__", None)
    args = getattr(ann, "__args__", ())
    return origin is getattr(typing, "Union", None) and type(None) in args


def _annotation_to_json(ann: Any, name: str) -> tuple[str, str]:
    if ann is inspect.Parameter.empty:
        return "string", ""
    if _is_optional(ann):
        import typing
        inner = [a for a in ann.__args__ if a is not type(None)]
        if inner:
            return _annotation_to_json(inner[0], name)
    t = _PY_TO_JSON.get(ann, "string")
    return t, name.replace("_", " ")
