"""world_simulator/problem_discovery.py — Problem Discovery Engine
（第九轮批次三，2.4 节，`next_doc/world_simulator_problem_capability_
gap_plan.md`，对照《万能模拟器》参考文档第四十七、四十八节）。

**现状**：在这之前，"问题"只在 LLM 叙事/`advance_step` 的可选
`problems` 输出里被动出现，没有一个专门步骤系统性扫描"当前哪些目标
被什么挡住了"。本模块补上这一环——但**范围明显收窄**于参考文档原文
（详见下方"范围克制"）。

只有一个函数 `suggest_problems()`，仿照 `hypothesis.py::suggest_
experiment_design()` 的既有调用手法（`WorkflowStore`/`WorkflowRunner`
+ `extract_agent_json_output()`，一次轻量 `type: agent` workflow
调用，`workflows/problem_discovery.yaml`）：把当前 `vars` + 2.2 节的
`desired_state` + 最近几步历史喂给 LLM，要求输出两组列表——"当前可
观察到的问题"（Observed Problems）和"从现有结构可以推导但还没爆发
的潜在问题"（Latent Problems），对应参考文档原文四类问题里的前两类。

**范围克制**（对照方案 2.4 节）：
- 不做"结构性问题"/"冲突目标"两类细分（参考文档原文四类，这里只做
  Observed/Latent 两类）。
- 不做自动周期性触发——只在调用方（通常是用户在 UI 上点击"扫描潜在
  问题"）主动调用时才发起一次 LLM 调用，避免增加不必要的 LLM 调用
  成本。
- **只建议、不自动写入**：本函数只返回建议列表，不做任何 `problems`
  落盘。用户"采纳"一条建议，走的也不是"直接改写某条历史状态的
  `problems` 字段"这条路——`state_history.jsonl` 是只追加、不回改的
  存储（见 `store.py::append_state()` 的设计说明），倒回去改写一条
  已经落盘的历史状态不符合这个既有设计。改为复用已有的"确认后作为
  提示喂给下一次推进"机制（同 `structural_change`/
  `confirmed_structural_changes` 的既有取舍）：调用方把用户确认关注
  的建议存进 `manifest.settings["confirmed_problem_suggestions"]`
  （`app.py` 负责这一步，见对应 UI），下一次 `advance_step`/
  `world_evolve` 的 prompt 会通过 `{confirmed_problem_suggestions_
  hint}` 看到这些确认过的建议，由 LLM 自行判断要不要把它们体现进
  这一步的 `problems` 输出（可能这一步剧情走向已经让某个建议过时，
  LLM 有权不采纳，不强制写入）。
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProblemDiscoveryError(RuntimeError):
    """`suggest_problems()` 底层 workflow 调用失败/无法解析结果时
    抛出。"""


_CATEGORIES = ("observed", "latent")


def suggest_problems(
    cfg,
    workspace_root: Path,
    current_vars: Dict[str, Any],
    desired_state: Optional[Dict[str, Any]],
    history: List[Any],
    *,
    max_observed: int = 5,
    max_latent: int = 5,
    recent_steps: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """让 LLM 扫描一次"当前有哪些问题/潜在问题"，只返回建议，不做
    任何落盘。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根。
        current_vars: 当前状态的 `vars`（`SimState.vars`）。
        desired_state: `manifest.settings.get("desired_state")`，可以
            是 `None`/空字典——为空时 LLM 只能从 `current_vars` 和
            历史里找线索，提示词里会明确说明"没有声明理想状态"，不
            强行要求 LLM 编造目标。
        history: 完整历史（`SimStore.load_history()` 的返回值），本
            函数只截取最后 `recent_steps` 步喂给 prompt，避免上下文
            过长——扫描"现在"的问题不需要完整历史，近期走向足够。
        max_observed: 最多建议几条"当前可观察到的问题"（默认 5，
            同时是提示词里明确要求 LLM 遵守的上限，LLM 偶尔给多了时
            这里做最后一道截断兜底）。
        max_latent: 最多建议几条"潜在问题"，含义同上。
        recent_steps: 喂给 prompt 的最近步数（默认 5）。

    Returns:
        `{"observed": [...], "latent": [...]}`，每项形状同
        `SimState.problems` 的最小字段集去掉 `id`（建议阶段还没有
        被采纳，不需要 `id`；采纳时由调用方自己决定怎么呈现/存放，
        见模块 docstring）：`{"symptom": "...", "blocked_goal": "...",
        "missing_capabilities": [...]}`。LLM 回复里格式不对/缺字段
        的条目会被跳过，不中断其它条目的解析。`history` 为空且
        `current_vars` 也为空时仍然会发起调用（不像 `hypothesis.
        suggest_experiment_design()` 那样有"入参为空就不调用"的
        捷径——扫描"现在"不依赖某个特定的输入列表是否非空，`vars`
        为空本身也是一种可以让 LLM 判断"信息不足，暂时给不出问题"
        的合法输入）。

    Raises:
        ProblemDiscoveryError: 找不到 workflow 定义、workflow 执行
            未成功、或最终回复无法解析出 `observed`/`latent` 字段。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore
    from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

    wf_store = WorkflowStore(Path(workspace_root))
    wf = wf_store.load("problem_discovery")
    if wf is None:
        raise ProblemDiscoveryError(
            "找不到 workflow 定义 'problem_discovery'"
            f"（预期路径：{workspace_root}/workflows/problem_discovery.yaml）"
        )

    recent_history = [
        {
            "step": s.step,
            "summary": getattr(s, "summary", ""),
            "narrative": getattr(s, "narrative", ""),
        }
        for s in (history or [])[-max(recent_steps, 0):]
    ]

    runner = WorkflowRunner(cfg)
    result = runner.run(
        wf,
        {
            "current_vars_json": json.dumps(current_vars or {}, ensure_ascii=False),
            "desired_state_json": json.dumps(desired_state or {}, ensure_ascii=False),
            "recent_history_json": json.dumps(recent_history, ensure_ascii=False),
            "max_observed": max_observed,
            "max_latent": max_latent,
        },
    )

    if result.status != "done":
        failed = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        raise ProblemDiscoveryError(
            f"problem_discovery workflow 执行未成功：status={result.status}；" + "；".join(failed)
        )

    step_result = next(
        (sr for sr in result.step_results if sr.step_id == "problem_discovery"), None
    )
    if step_result is None:
        raise ProblemDiscoveryError("problem_discovery 步骤没有产出结果")

    try:
        data = extract_agent_json_output(
            step_result.output, required_keys=["observed", "latent"]
        )
    except AgentStepOutputError as exc:
        raise ProblemDiscoveryError(
            f"problem_discovery 步骤回复无法解析为问题建议：{exc}"
        ) from exc

    def _parse_list(raw: Any, limit: int) -> List[Dict[str, Any]]:
        parsed: List[Dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            symptom = str(item.get("symptom", "") or "").strip()
            if not symptom:
                continue
            parsed.append(
                {
                    "symptom": symptom,
                    "blocked_goal": str(item.get("blocked_goal", "") or ""),
                    "missing_capabilities": [
                        str(m) for m in (item.get("missing_capabilities") or []) if str(m).strip()
                    ],
                }
            )
            if len(parsed) >= limit:
                break
        return parsed

    return {
        "observed": _parse_list(data.get("observed"), max_observed),
        "latent": _parse_list(data.get("latent"), max_latent),
    }


# ── 确认/撤回：把一条建议记进 `settings`，供后续推进的 prompt 引用 ──


def _format_confirmed_problem_suggestions(raw: Any) -> str:
    """把 `manifest.settings.confirmed_problem_suggestions` 拼成一段
    人类可读的提示文本，喂给 `advance_step`/`world_evolve` 的 prompt
    （写法同 `engine.structural_change._format_confirmed_structural_
    changes()`）。留空（未声明/尚无已确认项）返回空字符串，不影响
    任何已有行为。
    """
    items = raw if isinstance(raw, list) else []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symptom = str(item.get("symptom") or "").strip()
        if not symptom:
            continue
        category = str(item.get("category") or "")
        blocked_goal = str(item.get("blocked_goal") or "").strip()
        suffix = f"（挡住：{blocked_goal}）" if blocked_goal else ""
        lines.append(f"- [{category}] {symptom}{suffix}")
    return "\n".join(lines)


def adopt_problem_suggestion(
    data_dir: Path,
    sim_id: str,
    *,
    category: str,
    symptom: str,
    blocked_goal: str = "",
    missing_capabilities: Optional[List[str]] = None,
) -> "SimManifest":
    """用户在 `suggest_problems()` 的建议列表里点"确认关注"，把这条
    建议追加进 `settings.confirmed_problem_suggestions`（模块 docstring
    "范围克制"一节说明的确认机制：不直接改写任何历史状态的
    `problems` 字段，只是让下一次 `advance_step`/`world_evolve` 的
    prompt 能看到它，由 LLM 自行判断要不要真正体现进这一步的
    `problems` 输出）。

    Args:
        category: `"observed"` 或 `"latent"`，对应 `suggest_
            problems()` 返回值的两个分组；非法取值会被归一化为
            `"observed"`（不中断这次确认，同项目一贯的"未知取值
            保守兜底"取舍）。
        symptom/blocked_goal/missing_capabilities: 对应
            `suggest_problems()` 建议项里的同名字段，由调用方
            （`app.py`）从展示的建议里原样传入。

    Returns:
        更新后的 `SimManifest`（已落盘）。
    """
    from world_simulator.store import SimStore, now_iso

    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    normalized_category = category if category in _CATEGORIES else "observed"
    confirmed = list(manifest.settings.get("confirmed_problem_suggestions") or [])
    confirmed.append(
        {
            "id": f"confirmed_problem_{secrets.token_hex(3)}",
            "category": normalized_category,
            "symptom": str(symptom or "").strip(),
            "blocked_goal": str(blocked_goal or "").strip(),
            "missing_capabilities": [
                str(m) for m in (missing_capabilities or []) if str(m).strip()
            ],
            "confirmed_at": now_iso(),
        }
    )
    manifest.settings = {**manifest.settings, "confirmed_problem_suggestions": confirmed}
    store.save_manifest(manifest)
    return manifest


def withdraw_confirmed_problem_suggestion(
    data_dir: Path, sim_id: str, suggestion_id: str
) -> "SimManifest":
    """从 `settings.confirmed_problem_suggestions` 里移除一条已确认的
    建议（用户判断"这个问题已经过时/已经通过其它方式处理，不需要再
    提醒 LLM"）。找不到该 id 时视为已经移除过，静默返回（幂等，同
    `structural_change.reject_suggested_causal_line()` 的既有取舍）。
    """
    from world_simulator.store import SimStore

    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    confirmed = list(manifest.settings.get("confirmed_problem_suggestions") or [])
    remaining = [c for c in confirmed if str(c.get("id")) != suggestion_id]
    manifest.settings = {**manifest.settings, "confirmed_problem_suggestions": remaining}
    store.save_manifest(manifest)
    return manifest
