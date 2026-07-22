"""
workflow/session_summarizer.py — Session → Workflow 转换机制第①阶段：总结

对应 next_doc/session_to_workflow_design.md。把一次已完成 session 的 history
（可以来自内存里的活 Agent，也可以来自磁盘 history.json——两种来源结构完全
一致，本模块不关心来源）总结成结构化的 TaskSummary，供第②阶段
（generator.py::WorkflowGenerator.generate_from_summary）构建 workflow YAML。

不放进 agent/reflection.py：那个文件是"当前 Agent 自身会话结束时的反思"，
这里是"任意给定 session 的离线总结"，职责不同，复用的只是同样的
pm.render() + LLM 调用模式，不是同一个类。

两个承载函数：
  build_timeline_text(history_entries)          — 纯函数，拼时间线文本
  summarize_session_for_workflow(history_entries, cfg) — 起临时 Agent 跑一次总结调用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


# ── TaskSummary 数据结构 ──────────────────────────────────────────────────────

@dataclass
class TaskStage:
    """TaskSummary 里的单个阶段（对应 session_to_workflow_design.md 3.1 节）。"""

    id: str = ""
    purpose: str = ""
    approach: str = ""
    depends_on_stage_ids: list[str] = field(default_factory=list)
    had_retries: bool = False
    retry_note: str = ""
    gate_candidate: bool = False

    @staticmethod
    def from_dict(d: dict) -> "TaskStage":
        if not isinstance(d, dict):
            d = {}
        deps = d.get("depends_on_stage_ids") or []
        if not isinstance(deps, list):
            deps = []
        return TaskStage(
            id=str(d.get("id", "") or ""),
            purpose=str(d.get("purpose", "") or ""),
            approach=str(d.get("approach", "") or ""),
            depends_on_stage_ids=[str(x) for x in deps],
            had_retries=bool(d.get("had_retries", False)),
            retry_note=str(d.get("retry_note", "") or ""),
            gate_candidate=bool(d.get("gate_candidate", False)),
        )


@dataclass
class CandidateParameter:
    """TaskSummary 里建议参数化的值。"""

    name: str = ""
    example_value: str = ""
    source: str = ""

    @staticmethod
    def from_dict(d: dict) -> "CandidateParameter":
        if not isinstance(d, dict):
            d = {}
        return CandidateParameter(
            name=str(d.get("name", "") or ""),
            example_value=str(d.get("example_value", "") or ""),
            source=str(d.get("source", "") or ""),
        )


@dataclass
class TaskSummary:
    """
    session_to_workflow 第①阶段的产物：把一次 session 的执行过程提炼成的
    结构化摘要（session_to_workflow_design.md 3.1 节）。不是 WorkflowDef
    体系的一部分，只是这个功能内部的中间数据结构，不进 workflow/schema.py。
    """

    goal: str = ""
    final_outcome: str = ""
    stages: list[TaskStage] = field(default_factory=list)
    candidate_parameters: list[CandidateParameter] = field(default_factory=list)
    repeated_pattern: Optional[str] = None

    @staticmethod
    def from_dict(d: dict) -> "TaskSummary":
        if not isinstance(d, dict):
            d = {}
        stages_raw = d.get("stages") or []
        if not isinstance(stages_raw, list):
            stages_raw = []
        params_raw = d.get("candidate_parameters") or []
        if not isinstance(params_raw, list):
            params_raw = []
        repeated = d.get("repeated_pattern")
        return TaskSummary(
            goal=str(d.get("goal", "") or ""),
            final_outcome=str(d.get("final_outcome", "") or ""),
            stages=[TaskStage.from_dict(s) for s in stages_raw if isinstance(s, dict)],
            candidate_parameters=[CandidateParameter.from_dict(p) for p in params_raw if isinstance(p, dict)],
            repeated_pattern=str(repeated) if repeated else None,
        )

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "final_outcome": self.final_outcome,
            "stages": [
                {
                    "id": s.id, "purpose": s.purpose, "approach": s.approach,
                    "depends_on_stage_ids": s.depends_on_stage_ids,
                    "had_retries": s.had_retries, "retry_note": s.retry_note,
                    "gate_candidate": s.gate_candidate,
                }
                for s in self.stages
            ],
            "candidate_parameters": [
                {"name": p.name, "example_value": p.example_value, "source": p.source}
                for p in self.candidate_parameters
            ],
            "repeated_pattern": self.repeated_pattern,
        }

    def to_markdown(self) -> str:
        """3.4 节格式的人工确认展示文本。"""
        lines = ["## 这次 session 做了什么（摘要）", ""]
        lines.append(f"**目标**：{self.goal or '（未提取到）'}")
        lines.append(f"**最终结果**：{self.final_outcome or '（未提取到）'}")
        lines.append("")
        lines.append("### 主线阶段")
        if not self.stages:
            lines.append("（未提取到阶段信息）")
        for i, s in enumerate(self.stages, 1):
            retry_suffix = ""
            if s.had_retries:
                note = f"：{s.retry_note}" if s.retry_note else ""
                retry_suffix = f"（经历失败重试{note}）"
            lines.append(f"{i}. **{s.id}** — {s.purpose}{retry_suffix}")
            if s.approach:
                lines.append(f"   {s.approach}")
            if s.gate_candidate:
                lines.append("   ⚠️ 这个阶段的重试模式建议做成质检门（打分/验证 → 不通过就重跑）")
        if self.candidate_parameters:
            lines.append("")
            lines.append("### 建议参数化的值")
            for p in self.candidate_parameters:
                lines.append(f"- `{p.name}`（来源：{p.source}）：{p.example_value}")
        if self.repeated_pattern:
            lines.append("")
            lines.append(f"### 重复模式\n{self.repeated_pattern}")
        lines.append("")
        lines.append(
            "以上理解正确吗？确认后我会据此生成 workflow YAML；"
            "如果哪里理解错了，直接告诉我需要调整的地方。"
        )
        return "\n".join(lines)


# ── 2.1-2.3 节：拼时间线文本 ──────────────────────────────────────────────────

def _extract_tool_uses_with_results(history_entries: list[dict]) -> list[tuple[list[SimpleNamespace], list[str]]]:
    """
    按 assistant_reply 出现顺序，抽取每一批 (tool_use 序列, 对应 result_strs)。

    每个 assistant_reply 之后紧随的 tool_result 条目（如果存在）的 content 是
    render_tool_results() 渲染出来的 "<tool_result>{json}</tool_result>" 拼接
    文本，按 tool_use 出现顺序一一对应（history_manager.append_tool_results()
    与 render_tool_results() 都保证顺序不变）——按顺序切出每个 <tool_result>
    块解析出 "output" 字段即可得到该次调用的结果字符串。
    """
    import json as _json
    import re as _re

    result_pattern = _re.compile(r"<tool_result>\s*(\{.*?\})\s*</tool_result>", _re.S)
    batches: list[tuple[list[SimpleNamespace], list[str]]] = []

    for idx, m in enumerate(history_entries):
        if m.get("_type") != "assistant_reply":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        calls = [
            SimpleNamespace(name=block.get("name", ""), input=block.get("input") or {})
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if not calls:
            continue

        result_strs: list[str] = []
        if idx + 1 < len(history_entries):
            nxt = history_entries[idx + 1]
            if nxt.get("_type") == "tool_result" and isinstance(nxt.get("content"), str):
                for match in result_pattern.finditer(nxt["content"]):
                    try:
                        entry = _json.loads(match.group(1))
                        result_strs.append(str(entry.get("output", "")))
                    except Exception:
                        result_strs.append("")

        batches.append((calls, result_strs))

    return batches


def build_timeline_text(history_entries: list[dict]) -> str:
    """
    2.1-2.3 节逻辑：把 history_entries 拼成喂给总结 LLM 的时间线文本。

    交替拼接：
      [用户] 用户轮次文本（is_turn_boundary 判定的真实用户输入）
      [执行] ActionEvent.to_summary_text() 摘要（按意图分组的工具调用批次）
      [assistant] 同一批 content 里的文本 block（阶段性结论）

    不做 [-10:] 截断（覆盖 session 完整用户轮次；长 session 的截断策略见
    2.4 节，此处先实现基础版本，token 预算超限时的合并策略留待接入
    perception/token_counter.py 时再补）。
    """
    from mini_agent.history.entry import is_turn_boundary
    from mini_agent.perception.intent_action_mapper import IntentActionMapper

    lines: list[str] = []
    all_batches = _extract_tool_uses_with_results(history_entries)
    batch_iter = iter(all_batches)

    for m in history_entries:
        if is_turn_boundary(m) and isinstance(m.get("content"), str):
            lines.append(f"[用户] {m['content']}")
            continue

        if m.get("_type") != "assistant_reply":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue

        tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]

        if tool_uses:
            calls, result_strs = next(batch_iter)
            events = IntentActionMapper.group_calls(calls, result_strs or None)
            for ev in events:
                lines.append(f"[执行] {ev.to_summary_text()}")

        for t in texts:
            lines.append(f"[assistant] {t}")

    return "\n".join(lines)


# ── 承载函数：起临时 Agent 跑一次总结调用 ────────────────────────────────────

def summarize_session_for_workflow(history_entries: list[dict], cfg: "AppConfig") -> TaskSummary:
    """
    起一个干净的临时 Agent（复用 WorkflowGenerator.generate() 里
    "load_config(auto_approve=True) + 空 registry + system_extra 覆盖"
    的同一套搭建方式，max_turns=1，不给工具），跑一次总结调用，解析成
    TaskSummary。

    解析失败/关键字段（goal 与 stages 均为空）时抛 ValueError，由调用方
    （workflow/tools.py 的 @tool 层）转成用户可读的报错，不静默返回空结构。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry
    from mini_agent.prompts import pm
    from mini_agent.agent._helpers import _parse_task_summary

    timeline_text = build_timeline_text(history_entries)
    if not timeline_text.strip():
        raise ValueError("该 session 没有可总结的内容（未找到用户轮次或工具调用记录）")

    gen_cfg = load_config(
        project_root=cfg.project_root,
        verbose=False,
        sandbox=cfg.sandbox,
        auto_approve=True,
        model=cfg.model,
        llm_provider=cfg.llm_provider,
        llm_base_url=cfg.llm_base_url,
        debug_llm=getattr(cfg, "debug_llm", False),
        debug_llm_console=getattr(cfg, "debug_llm_console", False),
    )
    gen_cfg.api_key = cfg.api_key
    gen_cfg.max_turns = 1
    gen_cfg.stream = False
    gen_cfg.system_extra = pm.render("system/session_to_workflow_summary")

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=cfg.sandbox,
        project_root=cfg.project_root,
    )
    empty_registry = get_default_registry().empty()  # [BUGFIX] filtered(names=[], groups=[]) 会被当成"未筛选"返回全量工具，见 tools/__init__.py::ToolRegistry.empty() 说明
    agent = Agent(cfg=gen_cfg, guard=guard, registry=empty_registry)

    prompt = pm.render("user/session_to_workflow_summary_request", timeline_text=timeline_text)
    raw = agent.run_turn(prompt)

    data = _parse_task_summary(raw)
    summary = TaskSummary.from_dict(data)
    if not summary.goal and not summary.stages:
        raise ValueError(
            "总结失败：LLM 未返回有效的 TaskSummary 结构（goal 和 stages 均为空）。"
            "原始返回：" + raw[:500]
        )
    return summary


__all__ = [
    "TaskStage",
    "CandidateParameter",
    "TaskSummary",
    "build_timeline_text",
    "summarize_session_for_workflow",
]
