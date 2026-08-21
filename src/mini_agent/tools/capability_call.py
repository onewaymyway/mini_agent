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
- 默认注入 `build_llm_resolver()`（真实调用 Anthropic Messages API 的第二级
  检索裁决），因为这一步不依赖任何领域特定的底层工具，本工具接入后就能
  100% 生效。
- **默认不注入 `explore_runner`/`tool_executor`**——探索子agent真正要用的
  底层操作原语（`browser-core` 等）仍是方案文档"已知遗留"里明确记录、尚未
  从各领域 skill 中独立拆分出来的部分，本工具没有能力代为实现它们。这意味着
  通过本工具调用时：命中已有 trusted/probation member 并执行成功的路径已经
  完全可用；miss 或命中全部失败后需要"探索"的路径，会得到
  `status: not_implemented` 及可读的原因说明，而不是被静默伪造成功——这是
  `CapabilityEngine.explore()` 本来就有的行为（未注入 explore_runner 时明确
  返回 not_implemented），本工具如实透传，不做任何掩盖。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import ToolRegistry

if TYPE_CHECKING:
    from mini_agent.skills import SkillLoader


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

        from mini_agent.skills.generative_capability import CapabilityEngine, build_llm_resolver

        skill_dir = skill.location.parent
        try:
            engine = CapabilityEngine(skill_dir, llm_resolver=build_llm_resolver())
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
            "status": result.status,   # "success" | "fail" | "not_implemented"
            "data": result.data,
            "error": result.error,
            "member_id": result.member_id,
            "resolve_reason": result.resolve_reason,
        }
        if result.status == "not_implemented":
            payload["note"] = (
                "该请求命中或未命中已有能力，但都需要触发探索子agent来补全/修复，"
                "而当前运行环境尚未接入真正的底层操作原语执行器（explore_runner/"
                "tool_executor），因此如实返回 not_implemented，而不是伪造成功。"
                "这不是本次调用的偶发失败，是该 skill 探索能力尚未完全接线的已知限制。"
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
            "or 'not_implemented' when the request would need the skill's exploration "
            "capability and that hasn't been wired up in this environment yet. Never "
            "fabricates success — always trust the returned status over your own guess."
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
