"""
Tool Registry.
Each tool is a plain Python function decorated with @tool().
The registry collects them, generates Anthropic-compatible tool schemas,
and dispatches calls by name.
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


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    # ── Registration ───────────────────────────────────────────────────────────

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def

    def register_fn(
        self,
        fn: Callable,
        name: Optional[str] = None,
        description: Optional[str] = None,
        input_schema: Optional[dict] = None,
        requires_approval: bool = True,
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
            )
        )

    # ── Lookup ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def call(self, name: str, tool_input: dict) -> Any:
        """Execute a tool by name. Returns the result (str or dict)."""
        td = self._tools.get(name)
        if not td:
            raise ValueError(f"Unknown tool: {name!r}")
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
):
    """Decorator to register a function as a tool in the default registry."""
    def decorator(fn: Callable) -> Callable:
        _default_registry.register_fn(
            fn,
            name=name,
            description=description,
            input_schema=schema,
            requires_approval=requires_approval,
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
