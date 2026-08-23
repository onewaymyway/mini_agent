"""
tools/capability_call.py — generative-capability skill 的统一调用工具

对应文档: next_doc/generative-capability-skill-plan.md 第 2 节"Skill 类型声明"
          / 第 6 节"通用引擎：调度流程" / 阶段七实施记录。

背景
----
阶段一到阶段六把 `CapabilityEngine`（resolve/execute/explore/distill 全流程）
实现得很完整，但一直没有任何工具把它接到真正跑起来的 agent 上——`skill_type:
generative-capability` 的 skill（如 `browser-site-scraper`）之前只能靠人工
在 shell 里跑 `python capability_engine.py <skill_dir> --url ...` 手动验证，
agent 自己完全没有办法在对话里调用它。这个文件是这条链路补上的最后一块：
一个真正的工具，让 agent 可以在对话中调 `capability_call(skill_name, request)`。

设计要点
--------
- 只接受 `skill_type: generative-capability` 的 skill；普通静态 skill 应该走
  `skill_activate` 加载正文，不应该被这个工具处理，这里显式拒绝并给出正确的
  下一步提示，而不是静默尝试。
- 每次调用重新构造一个 `CapabilityEngine` 实例（构造成本很低——只是读
  `capability.yaml` + 两个 json 文件），不做跨调用的实例缓存，避免 registry/
  index 在多轮对话之间被其他进程修改后读到过期状态。
- 默认注入 `build_llm_resolver()`（第二级检索裁决），因为这一步不依赖任何
  领域特定的底层工具，本工具接入后就能 100% 生效。阶段九改造后，
  `build_llm_resolver()` 走框架统一的 `LLMHelper`（见
  `llm/service.py`），本工具复用 `tools/orchestration.py` 里已有的
  `_get_current_llm_helper()` thread-local 机制拿到当前 `Agent.llm_helper`
  （同一个约定 `run_ensemble_llm` 等已经在用，跟随 /model 切换），取不到时
  （理论上不会发生——本工具只会在已完成 `Agent.__init__` 的 agent 内被调用）
  才退化为报错，不会静默回退到某个写死的模型。
- **默认注入一个通用的真实 `tool_executor`/`explore_runner`**（阶段十二起，
  阶段十四扩展）：`build_default_tool_executor(skill_dir=skill_dir)` 按工具
  名分发——分发表由两层组成：项目内置的 `REAL_TOOL_IMPLEMENTATIONS`（目前
  只有 `text-core` 的 `text_transform_apply`），叠加当前目标 skill 通过
  `capability.yaml -> explorer.base_tools` 声明的各静态 skill 自带的
  `impl/tools_impl.py` 实现（阶段十四起，`browser-core` 已提供这份实现，见
  `.claude/skills/browser-core/impl/`）。命中已实现工具的会真的执行，未命中
  的（如仍未提供 `impl/tools_impl.py` 的 `doc-core` 下的工具）会得到一条
  如实的"未接入真实执行器"错误，反馈给探索子agent后由它自行判断调用
  `report_failure`——不是静默失败，也不会被伪造成功。这意味着：
  `text-transform-capability`、`browser-site-scraper` 这两类领域现在都能
  在真实对话里跑通完整的 `resolve -> miss -> explore(真实 LLM 决策循环) ->
  distill -> 落盘复用` 全链路（后者依赖使用者本机/服务器有可用的 Chrome，
  或提前手动启动好一个带调试端口的浏览器，见 `browser-core/SKILL.md`）；
  `doc-template-generation` 这类依赖真实文档生成能力的领域，探索路径依然
  会诚实地 `not_implemented`，直到有人把 `doc-core` 的真实实现也补进
  `.claude/skills/doc-core/impl/tools_impl.py`（同一套约定路径机制，见
  `real_tools.py` 顶部说明）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import ToolRegistry

if TYPE_CHECKING:
    from mini_agent.skills import SkillLoader


def _find_unwired_tools(skill_dir) -> list[str]:
    """
    据实检测某个 generative-capability skill 在 `explorer/tool_allowlist.json`
    里声明的工具名，有哪些在 `build_dispatch_table()` 里仍然缺真实实现（即
    调用会落到 `real_tools.py::build_default_tool_executor` 统一的"占位声明，
    尚未接入真实执行器"错误分支）。只用于给 not_implemented 结果生成准确的
    诊断提示。

    [阶段十八] 直接查表而不是真的调用一次 tool_executor 探测——像
    `browser_navigate` 这类工具一调用就会触发真实的浏览器会话/进程，用来做
    "是否已接线"这种诊断是不合理的副作用。
    """
    allowlist_path = skill_dir / "explorer" / "tool_allowlist.json"
    if not allowlist_path.exists():
        return []
    try:
        with open(allowlist_path, "r", encoding="utf-8") as f:
            allowed_tools = json.load(f).get("allowed_tools", [])
    except Exception:  # noqa: BLE001 - 诊断辅助函数，读取失败不应影响主流程
        return []

    from mini_agent.skills.generative_capability import build_dispatch_table

    dispatch_table = build_dispatch_table(skill_dir=skill_dir)
    return [name for name in allowed_tools if name not in dispatch_table]


def register_capability_tools(registry: ToolRegistry, skill_loader: "SkillLoader") -> None:
    """
    将 capability_call 工具注册到指定 registry。
    在 Agent.__init__ 中调用，将 skill_loader 绑定到闭包内
    （与 tools/skill_manager.py::register_skill_tools 是同一种组织方式）。
    """

    def capability_call(skill_name: str, request: dict) -> str:
        """
        Call a generative-capability skill (a domain capability pack whose member
        list is not loaded into context — e.g. site-specific scraping capabilities).
        Pass your goal and the expected result shape in `request`; you will get back
        either the result data or a clear, honest failure reason (never a fabricated
        success). Call skill_list first to see which skills are of type
        `generative-capability` and what `category_summary` describes their scope.
        Do NOT use this for ordinary skills — those should be loaded via skill_activate.
        """
        from mini_agent.skills.generative_capability.capability_debug import capability_debug_log

        capability_debug_log(
            "capability_call_invoked", {"skill_name": skill_name, "request": request},
            where="tools.capability_call.capability_call",
        )

        skill = skill_loader.get(skill_name)
        if skill is None:
            known = sorted(
                s.name for s in getattr(skill_loader, "_all", {}).values()
                if getattr(s, "is_generative_capability", False)
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"未找到名为 `{skill_name}` 的 skill。",
                    "available_generative_capability_skills": known,
                },
                ensure_ascii=False,
            )

        if not skill.is_generative_capability:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        f"`{skill_name}` 是普通静态 skill（skill_type 未声明为 "
                        f"generative-capability），应调用 skill_activate 加载其正文，"
                        f"而不是 capability_call。"
                    ),
                },
                ensure_ascii=False,
            )

        from mini_agent.skills.generative_capability import (
            CapabilityEngine, build_llm_resolver, build_subagent_explorer, build_default_tool_executor,
        )
        from mini_agent.tools.orchestration import get_current_llm_helper, get_task_manager

        skill_dir = skill.location.parent
        # 复用 run_ensemble_llm 等已经在用的 thread-local 约定，拿到当前
        # Agent.llm_helper（跟随 /model 切换），而不是构造一份写死配置的
        # 独立 LLMHelper。取不到时说明本工具是在非 Agent 主流程外被调用，
        # 如实报错而不是静默退化到某个固定模型。
        current_llm_helper = get_current_llm_helper()
        if current_llm_helper is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "无法获取当前 Agent 的 llm_helper，capability_call 依赖它驱动"
                        "第二级 LLM 检索裁决。这通常意味着本工具是在 Agent 主流程之外"
                        "被调用的（Agent.__init__ 未注册 llm_helper provider）。"
                    ),
                },
                ensure_ascii=False,
            )

        # [阶段二十] 探索子agent改为构造真实 SubAgent 驱动（见
        # explorer_runtime.py::build_subagent_explorer() 与
        # next_doc/generative_capability_explorer_rearch_plan.md）。SubAgent
        # 需要 base_cfg/session_id/session_dir/shared_tool_cache 才能正确落在
        # 当前 session 目录下——这些信息挂在全局 TaskManager 单例上（主程序
        # 启动时通过 init_task_manager(cfg) 注册，见 orchestration.py）。取不到
        # 时说明本工具是在没有 TaskManager 的环境下被调用（例如独立脚本），
        # 如实报错，不静默退化到某个假的默认配置。
        task_manager = get_task_manager()
        if task_manager is None:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "无法获取全局 TaskManager 实例，capability_call 的探索子agent"
                        "（SubAgent 驱动）依赖它提供 base_cfg/session 上下文。这通常"
                        "意味着当前环境未调用 init_task_manager(cfg)。"
                    ),
                },
                ensure_ascii=False,
            )

        try:
            # [阶段十二] 注入通用真实 tool_executor，用于领域声明的底层原语
            # （目前是 text-core/browser-core）桥接到探索用 SubAgent 的工具表；
            # 未实现的原语（如 doc-core）会在被调用时得到如实的失败反馈，
            # 见 real_tools.py 顶部说明。
            tool_executor = build_default_tool_executor(skill_dir=skill_dir)
            # [阶段十八修复] 之前 not_implemented 分支无条件贴一句"未接入真实
            # 执行器"的固定文案，哪怕该 skill 声明的 base_tools 其实已经全部
            # 提供了真实实现（如 browser-core）——这会把"探索时真的撞上了反爬/
            # 页面结构变化等正常失败"误导成"接线没做"，排查方向完全错误。这里
            # 改成据实检测：真正去问 dispatch table 里，这个 skill 在
            # capability.yaml -> explorer.base_tools 里声明的每个工具名是否有
            # 真实实现（未命中的会被 build_default_tool_executor 回落到统一的
            # "占位声明，尚未接入真实执行器"错误 dict）。
            unwired_tools = _find_unwired_tools(skill_dir)
            engine = CapabilityEngine(
                skill_dir,
                llm_resolver=build_llm_resolver(current_llm_helper),
                explore_runner=build_subagent_explorer(
                    task_manager.base_cfg,
                    tool_executor=tool_executor,
                    session_id=task_manager._session_id,
                    session_dir=task_manager._session_dir,
                    shared_tool_cache=task_manager._shared_tool_cache,
                ),
                tool_executor=tool_executor,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                {"status": "error", "error": f"引擎初始化失败（capability.yaml/registry.json 可能有问题）: {e}"},
                ensure_ascii=False,
            )

        try:
            result = engine.call(request or {})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"status": "error", "error": f"调用过程中发生异常: {e}"}, ensure_ascii=False)

        payload = {
            "status": result.status,   # "success" | "fail" | "not_implemented" | "invalid_request"
            "data": result.data,
            "error": result.error,
            "member_id": result.member_id,
            "resolve_reason": result.resolve_reason,
        }
        if result.status == "not_implemented":
            if unwired_tools:
                payload["note"] = (
                    "该请求命中或未命中已有能力，触发了探索子agent来补全/修复，"
                    f"但该 skill 声明的工具中有 {unwired_tools} 仍是占位声明、尚未"
                    "接入真实执行器（缺 impl/tools_impl.py 或对应实现），因此如实"
                    "返回 not_implemented，而不是伪造成功。这是该 skill 探索能力"
                    "尚未完全接线的已知限制，不是本次调用的偶发失败。"
                )
            else:
                payload["note"] = (
                    "该请求触发了探索子agent，且该 skill 声明的底层操作原语均已"
                    "接入真实执行器（不是接线缺失）——本次 not_implemented 是真实"
                    "探索/执行过程本身失败了（例如目标站点反爬拦截、页面结构变化、"
                    "登录墙等），具体原因见 error 字段。"
                )
        elif result.status == "invalid_request":
            payload["note"] = (
                "这次调用的 request 字段形状不满足该 skill 声明的任何一种输入格式"
                "（不是能力缺失，纯粹是参数传错了），因此没有消耗探索预算就直接短路"
                "返回。请查看 data.expected_formats：每一项都给出了 required_fields"
                "（必须存在且非空的字段路径）和一个可直接照抄改写的 example，按其中"
                "任意一种格式重新构造 request 后重试即可。"
            )
        capability_debug_log(
            "capability_call_returned",
            {"skill_name": skill_name, "status": result.status, "member_id": result.member_id,
             "resolve_reason": result.resolve_reason, "error": result.error},
            where="tools.capability_call.capability_call",
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=capability_call,
        name="capability_call",
        description=(
            "Call a `generative-capability` skill — a domain capability pack whose member "
            "list is deliberately NOT loaded into context (e.g. per-website scraping "
            "capabilities, per-template document generation). Pass `skill_name` (see "
            "skill_list, look for skill_type == 'generative-capability') and a `request` "
            "dict describing your target and, if relevant, the input data. Returns "
            "{status, data, error, member_id, resolve_reason}: status is 'success' when "
            "the data satisfies the skill's schema, 'fail' on a genuine execution error, "
            "'not_implemented' when the request would need the skill's exploration "
            "capability and that hasn't been wired up in this environment yet, or "
            "'invalid_request' when the request's shape doesn't match any format the "
            "skill declares (check data.expected_formats for required_fields + a copyable "
            "example per format, then retry with the right shape). Never fabricates "
            "success — always trust the returned status over your own guess."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Exact name of a generative-capability skill, from skill_list.",
                },
                "request": {
                    "type": "object",
                    "description": (
                        "Domain-specific request payload, e.g. "
                        "{\"text\": \"...\", \"target\": {\"url\": \"...\"}, \"query\": \"...\"} "
                        "for browser-site-scraper. Shape varies per skill; when unsure, "
                        "check the skill's category_summary or capability.yaml."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["skill_name", "request"],
        },
        requires_approval=False,
        override=True,  # 同 skill_manager.py 里其余工具：SubAgent 持有独立
                         # skill_loader 时会拿到 filtered() 出来的独立 registry
                         # 副本，该副本已含从全局 registry 复制来的同名占位条目，
                         # 必须允许覆盖（见 orchestrator/sub_agent.py 对应注释）。
    )
