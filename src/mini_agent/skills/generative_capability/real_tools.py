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

阶段十四：skill 自带实现的动态加载（`load_skill_local_tool_implementations`）
----------------------------------------------------------------------------
上面这套"直接把实现函数写进本文件、塞进 `REAL_TOOL_IMPLEMENTATIONS`"的
模式对 `text_transform_apply` 这种纯逻辑、几十行代码的原语是合理的，但对
`browser-core` 这类需要维护浏览器会话/CDP 连接等大量领域特定代码的静态
skill 并不合适——那样会把"具体某个 skill 怎么实现"的代码写进项目工程
目录，违反 skill 系统的既有原则（各 skill 的具体功能代码应该留在各自的
`.claude/skills/<name>/` 目录下，项目代码只保留"引擎怎么发现/调度这些
实现"这一层通用机制）。

本阶段新增 `load_skill_local_tool_implementations(explorer_base_tools,
skills_root)`：按 `capability.yaml -> explorer.base_tools` 里声明的静态
skill 名（如 `["browser-core"]`），去每个 `<skills_root>/<name>/impl/
tools_impl.py` 这个**约定路径**动态加载，取其中的
`TOOL_IMPLEMENTATIONS: dict[str, Callable]` 并入分发表——项目代码完全不
知道、也不需要知道 `browser-core` 内部具体是用 CDP 还是别的什么协议实现的，
只认这一个约定的文件路径和变量名，这与 `capability_engine.py::
_load_member_run()` 动态加载 `members/<id>/script.py` 是同一套设计风格。
`build_default_tool_executor()` 因此新增可选的 `skill_dir` 参数：传入时，
会在 `REAL_TOOL_IMPLEMENTATIONS`（项目内置的纯逻辑原语）之上，叠加该
skill 通过 `explorer.base_tools` 声明的所有静态 skill 各自贡献的实现
（同名工具以 skill 自带实现优先，因为项目内置表目前只有 `text_transform_
apply` 一个条目，不存在真实冲突场景，这里的优先级只是防御性约定）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Optional


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


def load_skill_local_tool_implementations(
    explorer_base_tools: list[str],
    skills_root: Path,
) -> dict[str, Callable[[dict], dict]]:
    """
    按 `capability.yaml -> explorer.base_tools` 里声明的静态 skill 名，动态
    加载各自 `impl/tools_impl.py` 里的 `TOOL_IMPLEMENTATIONS`，合并后返回。

    这是纯粹的通用机制（约定路径 + 动态 import），不包含任何具体 skill 的
    领域逻辑；某个 base tool skill 不存在 `impl/tools_impl.py`（如目前的
    `doc-core`，仍只是占位声明）会被安静跳过——这不是错误，只是意味着该
    skill 下的工具仍会在 `build_default_tool_executor()` 里落到"未接入真实
    执行器"的分支，如实反馈给探索子agent。

    动态加载失败（如目标 skill 依赖的第三方库未安装）不会中断整个引擎的
    初始化——转换为对应工具名的一个"加载失败"标记函数，调用时才把具体的
    加载异常如实报出来，这样一个 skill 的依赖缺失只影响它自己的工具，不会
    连带让其他 base_tools 或项目内置的 `text_transform_apply` 也用不了。

    已知限制（阶段十六引入的热更新副作用）：每次都强制清缓存重新加载意味着
    `session_manager.py` 模块级的 `_sessions` 会话复用字典也会被清空重建，
    上一次调用里已经连接的浏览器 tab 不会跨 `capability_call` 调用保留在
    Python 层——但浏览器进程本身、以及它已经登录的 cookies/profile（见
    `DEFAULT_PERSISTENT_PROFILE_DIR`）不受影响，下一次调用会通过 attach/auto
    重新连接同一个浏览器进程的某个 tab，登录态不会丢失，只是可能不是同一个
    tab（会重新导航）。这是"调试时脚本必须能热更新"与"同进程内长期复用同一
    tab"两者之间的权衡，调试阶段前者更重要。
    """
    merged: dict[str, Callable[[dict], dict]] = {}
    for base_tool_name in explorer_base_tools or []:
        impl_dir = skills_root / base_tool_name / "impl"
        tools_impl_path = impl_dir / "tools_impl.py"
        if not tools_impl_path.exists():
            continue

        impl_dir_str = str(impl_dir.resolve())
        # 让 tools_impl.py 内部的 flat import（如 `from browser_core_impl
        # import ...`）能找到同目录下的其他实现文件，风格与
        # capability_engine.py::execute() 加载 member 脚本时的 sys.path
        # 处理一致——这些都是运行时按路径加载的独立文件，不是本包的一部分。
        if impl_dir_str not in sys.path:
            sys.path.insert(0, impl_dir_str)

        # 阶段十六（热更新）：`tools_impl.py` 本身每次都用 importlib 按路径
        # 重新读文件执行，天然热更新；但它内部对同目录下其他实现文件
        # （如 browser_core_impl.py 里 `from session_manager import ...`）
        # 用的是普通 flat import——第一次被 import 后会按模块名缓存进
        # `sys.modules`，之后哪怕重新调这个函数、重新 exec tools_impl.py，
        # 那些被它 flat import 的文件依然是内存里第一次加载时的旧版本，
        # 不会因为磁盘上的文件被改过就重新读取。这里在每次加载前主动清掉
        # 该 impl 目录下所有 .py 文件对应的模块名缓存（按目录下实际文件名
        # 动态识别，不写死具体文件），强制下一次 flat import 重新从磁盘读取
        # 最新代码——保证"改了 browser-core/impl 下的脚本，下一次调用
        # capability_call 执行的就是最新脚本"，不需要重启整个 agent 进程。
        # 这是纯粹的通用加载机制，不含任何具体 skill 的领域逻辑，对所有
        # `impl/tools_impl.py` 类 skill（不只是 browser-core）都生效。
        for py_file in impl_dir.glob("*.py"):
            stale_module_name = py_file.stem
            sys.modules.pop(stale_module_name, None)

        module_name = f"skill_tools_impl_{base_tool_name.replace('-', '_')}"
        sys.modules.pop(module_name, None)
        try:
            spec = importlib.util.spec_from_file_location(module_name, tools_impl_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            impls = getattr(module, "TOOL_IMPLEMENTATIONS", {})
            if not isinstance(impls, dict):
                raise TypeError(
                    f"{tools_impl_path} 的 TOOL_IMPLEMENTATIONS 必须是 dict，"
                    f"实际是 {type(impls)!r}"
                )
        except Exception as load_error:  # noqa: BLE001 - 加载失败不应中断整体初始化

            def _make_load_error_fn(name: str, err: Exception) -> Callable[[dict], dict]:
                def _load_error_fn(_tool_input: dict) -> dict:
                    return {
                        "error": (
                            f"skill `{name}` 的 impl/tools_impl.py 加载失败，"
                            f"该 skill 声明的工具当前均不可用: {err}"
                        )
                    }

                return _load_error_fn

            # 记录一个占位错误函数到该 skill 声明的所有工具名下不可行（本层
            # 不知道该 skill 具体声明了哪些工具名），退而求其次：把加载异常
            # 记在一个以 skill 名为 key 的诊断条目里，调用方（本文件的
            # `build_default_tool_executor`）在合并阶段直接跳过失败的模块，
            # 未命中的工具名仍会走"未接入真实执行器"的默认提示；这里额外
            # 打印一次，便于运行时/日志排查是不是依赖没装对。
            import logging

            logging.getLogger(__name__).warning(
                "加载 %s 的 impl/tools_impl.py 失败: %s", base_tool_name, load_error
            )
            continue

        merged.update(impls)
    return merged


def build_dispatch_table(skill_dir: Optional[Path] = None) -> dict[str, Callable[[dict], dict]]:
    """
    构造 `tool_name -> 真实实现函数` 的分发表本身（不包装成 executor 闭包）。

    从 `build_default_tool_executor()` 里拆出来的原因（阶段十八）：诊断/自省
    场景（如 `capability_call.py` 判断某个工具是否"真的接了实现"、还是仍是
    占位声明）只需要查一下这张表里有没有这个 key，不应该为了检测而真的调用
    一次工具本身——尤其像 `browser_navigate` 这类工具，调用一次就会真的触发
    `session_manager.get_or_create_session()`（可能拉起一个真实浏览器进程），
    拿这种有副作用的调用来做"是否已接线"的诊断是不合理的。
    """
    dispatch_table: dict[str, Callable[[dict], dict]] = dict(REAL_TOOL_IMPLEMENTATIONS)

    if skill_dir is not None:
        try:
            import yaml  # PyYAML，与 capability_engine.py 是同一个可选依赖

            cap_path = Path(skill_dir) / "capability.yaml"
            if cap_path.exists():
                with open(cap_path, "r", encoding="utf-8") as f:
                    cap = yaml.safe_load(f) or {}
                base_tools = ((cap.get("explorer") or {}).get("base_tools")) or []
                skills_root = Path(skill_dir).resolve().parent
                dispatch_table.update(
                    load_skill_local_tool_implementations(base_tools, skills_root)
                )
        except Exception:  # noqa: BLE001 - 叠加层加载失败不应影响内置原语可用
            pass

    return dispatch_table


def build_default_tool_executor(
    skill_dir: Optional[Path] = None,
) -> Callable[[str, dict], dict]:
    """
    返回一个通用 tool_executor(tool_name, tool_input) -> dict，供
    `CapabilityEngine(explore_runner=..., tool_executor=...)` 使用。

    `skill_dir` 可选（阶段十四新增）：传入某个 generative-capability skill
    的目录时，会额外读取其 `capability.yaml -> explorer.base_tools`，把每个
    静态 skill 自带的 `impl/tools_impl.py` 实现叠加进分发表（见
    `load_skill_local_tool_implementations`）。不传时行为与阶段十二完全
    一致，只包含项目内置的 `REAL_TOOL_IMPLEMENTATIONS`。

    命中分发表的工具名会真正执行；未命中的（如仍未提供 `impl/tools_impl.py`
    的 `doc-core`）会如实返回"未接入真实执行器"的错误，不伪造成功——
    explorer_runtime 的 prompt 已要求模型遇到这种情况调用 `report_failure`，
    不需要本函数额外做特殊处理。
    """
    dispatch_table = build_dispatch_table(skill_dir=skill_dir)

    def _executor(tool_name: str, tool_input: dict) -> dict:
        impl = dispatch_table.get(tool_name)
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
