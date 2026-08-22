"""
skills/generative_capability/real_tools.py — 探索子agent的"真实底层操作原语"注册表。

背景（阶段十二）
----------------
`explorer_runtime.build_llm_explorer()` 早就是真实可用的 LLM 探索循环，但
`tools/capability_call.py` 此前**故意不注入** `explore_runner`/`tool_executor`
——因为各领域 skill 在 `explorer/tool_allowlist.json` 里声明的底层操作原语
（`browser-core`/`doc-core`/`text-core`）全都只是占位声明，没有一个真正
实现，注入了也是空转（explorer 一调用工具就拿到"未实现"，只能
`report_failure`）。

`text-transform-capability` 是三个 generative-capability skill 里唯一一个
"纯逻辑、不依赖外部服务/浏览器/API key"的领域，它声明的 `text-core` 工具集
（当前只有一个 `text_transform_apply`）完全可以用纯 Python 字符串操作真正
实现——不像 `browser-core`/`doc-core` 那样依赖尚未接入的浏览器/文档生成
运行时。本文件就是这个"确实能落地"的第一块真实底层原语实现，让
`text-transform-capability` 成为第一个能在真实对话里跑通完整
`resolve -> miss -> explore(真实 LLM 决策循环 + 真实工具执行) -> distill ->
落盘 -> 免探索复用` 全链路的 generative-capability skill，而不只是靠
`build_stub_explorer` 桩探索器验证接线。

设计
----
- `text_transform_apply(tool_input)`：一次调用只做一个原子字符串操作
  （upper/lower/reverse/strip/title/capitalize/swapcase/append/prepend/
  replace），探索子agent可以多次调用它组合出复杂变换（如"大写后再加感叹号"
  = 先 upper 再 append），不需要为每一种可能的变换单独声明一个工具。
- `build_default_tool_executor()` 返回一个通用的
  `tool_executor(tool_name, tool_input) -> dict`，按工具名分发到
  `REAL_TOOL_IMPLEMENTATIONS` 里的真实实现；工具名不在表里（如
  `browser-core`/`doc-core` 下的工具）时**如实**返回一条说明"该工具仍是
  占位声明、未接入真实执行器"的 `{"error": ...}`，而不是抛异常或悄悄
  返回空结果——探索子agent的 prompt 已明确要求看到这类失败时调用
  `report_failure` 如实说明，不编造数据，这是既有约定，本文件不改变它，
  只是让"能真正实现的那部分"从占位变成真的能跑。
- 后续如果要接入 `browser-core`/`doc-core`（如真的接一个无头浏览器/文档
  生成库），只需要在这里补一个新的实现函数、加进
  `REAL_TOOL_IMPLEMENTATIONS`，`capability_call.py` 与 explorer 侧代码都
  不需要改动。
"""

from __future__ import annotations

from typing import Callable


# --------------------------------------------------------------------------- #
# text-core: text_transform_apply
# --------------------------------------------------------------------------- #

_NO_ARG_OPS = {
    "upper": str.upper,
    "lower": str.lower,
    "reverse": lambda s: s[::-1],
    "strip": str.strip,
    "title": str.title,
    "capitalize": str.capitalize,
    "swapcase": str.swapcase,
}


def text_transform_apply(tool_input: dict) -> dict:
    """
    对一段文本执行一个原子字符串操作。

    input 约定: {"text": "...", "op": "upper", "args": {...}}
      - text: 待处理文本（必填）
      - op: 操作名，见 _NO_ARG_OPS 的 key，另支持 append/prepend/replace
      - args: 部分 op 需要的额外参数
          - append:  {"suffix": "..."}
          - prepend: {"prefix": "..."}
          - replace: {"old": "...", "new": "..."}

    返回: {"result": "<处理后文本>"} 或 {"error": "..."}（不抛异常，异常都
    转成 error 字段，交给探索循环的 tool_result 反馈机制处理）。
    """
    text = tool_input.get("text")
    if not isinstance(text, str):
        return {"error": "缺少 text 参数（需要 string）"}

    op = tool_input.get("op")
    args = tool_input.get("args") or {}
    if not isinstance(args, dict):
        return {"error": "args 参数必须是 object"}

    if op in _NO_ARG_OPS:
        return {"result": _NO_ARG_OPS[op](text)}

    if op == "append":
        suffix = args.get("suffix")
        if not isinstance(suffix, str):
            return {"error": "append 需要 args.suffix（string）"}
        return {"result": text + suffix}

    if op == "prepend":
        prefix = args.get("prefix")
        if not isinstance(prefix, str):
            return {"error": "prepend 需要 args.prefix（string）"}
        return {"result": prefix + text}

    if op == "replace":
        old, new = args.get("old"), args.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            return {"error": "replace 需要 args.old 和 args.new（string）"}
        return {"result": text.replace(old, new)}

    supported = sorted(list(_NO_ARG_OPS.keys()) + ["append", "prepend", "replace"])
    return {"error": f"不支持的 op={op!r}，支持的 op: {supported}"}


# --------------------------------------------------------------------------- #
# 分发表 + 通用 tool_executor 构造
# --------------------------------------------------------------------------- #

REAL_TOOL_IMPLEMENTATIONS: dict[str, Callable[[dict], dict]] = {
    "text_transform_apply": text_transform_apply,
}


def build_default_tool_executor() -> Callable[[str, dict], dict]:
    """
    返回一个通用 tool_executor(tool_name, tool_input) -> dict，供
    `CapabilityEngine(explore_runner=..., tool_executor=...)` 使用。

    命中 `REAL_TOOL_IMPLEMENTATIONS` 的工具名会真正执行；未命中的（当前是
    所有 `browser-core`/`doc-core` 下的工具）会如实返回"未接入真实执行器"
    的错误，不伪造成功——explorer_runtime 的 prompt 已要求模型遇到这种
    情况调用 `report_failure`，不需要本函数额外做特殊处理。
    """

    def _executor(tool_name: str, tool_input: dict) -> dict:
        impl = REAL_TOOL_IMPLEMENTATIONS.get(tool_name)
        if impl is None:
            return {
                "error": (
                    f"工具 `{tool_name}` 仍是 capability.yaml/tool_allowlist.json "
                    f"中的占位声明，尚未接入真实执行器。这不是一次偶发的工具执行"
                    f"失败，是该底层原语目前还没有实现；如果这条路径走不通，请调用 "
                    f"`report_failure` 如实说明，不要编造数据。"
                )
            }
        try:
            return impl(tool_input)
        except Exception as e:  # noqa: BLE001 - 工具执行异常需要转成可读的 tool_result
            return {"error": f"工具执行异常: {e}"}

    return _executor
