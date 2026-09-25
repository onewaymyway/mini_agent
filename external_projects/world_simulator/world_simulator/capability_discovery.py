"""world_simulator/capability_discovery.py — Capability Discovery
Engine（第二十一轮，用户反馈"模拟很久了，📈 能力成熟度时间线还是空的"
直接改进，未单独立项 next_doc）。

**背景**：`capabilities_gained` 此前完全靠 `advance_step`/`world_
evolve` 的 skill 在叙事当下自愿声明——而且判定门槛明确写着"不是单纯
变得更好一点，而是之前做不到的事，现在能做了"，比 `problems` 的门槛
更高，prompt 里可选子字段（`maturity_stage`/`first_occurrence`/
`behavior_change`/`structural_impact`/`capability_kind`）也更多，
模型系统性跳过的概率天然更高；而且不像 `problems` 那样有 Problem
Discovery Engine 兜底，`capabilities_gained` 此前没有任何后备发现
机制，完全押注在"模型当下主动、准确地判断出一次质变"这一件事上。

本模块是 `problem_discovery.py` 的姐妹实现——架构、函数命名、确认/
撤回/自动扫描机制**逐项对称**，读这个模块之前建议先读一遍
`problem_discovery.py` 的模块 docstring，这里只记录和它不同的地方，
相同的设计取舍不重复展开：

- **不分类别**：`problems` 天然分"已观察到"/"潜在"两类，`capabilities_
  gained` 没有类似的自然二分，这里只有一个建议列表，不做人为拆分。
- **必须和"已经记录过的能力"去重**：`suggest_capabilities()` 需要
  调用方传入 `already_recorded`（历史上所有 `capabilities_gained`
  声明过的能力名称，按精确字符串匹配去重——和 `spec_generator.
  resolve_capabilities_hint()`/`app.py::_collect_capability_
  maturity_timeline()` 用的是同一种归并口径，保持三处一致），并在
  prompt 里明确要求"不要重复建议这些已经记录过的能力"——`problems`
  没有这个强约束（同一个问题在不同阶段被反复提及是合理的，"已获得
  的能力"一旦确认就不应该被反复"发现"）。
- **建议字段集是 `capabilities_gained` 可选子字段的一个子集**：只取
  `capability`/`enables`/`limitations`/`maturity_stage`/
  `capability_kind` 五个，不包含 `first_occurrence`/`behavior_
  change`/`structural_impact`——这三个字段本质是"叙事当下的判断"
  （"这具体改变了什么行为模式""是不是历史上第一次"），脱离当时的
  叙事语境做事后回顾式扫描很难可靠判断，索性不强行让这个模块越俎
  代庖，留给 skill 在真正体现这次能力时自己判断（同项目一贯"范围
  克制"的取舍）。
- **只建议、不自动写入，确认机制完全对称**：`adopt_capability_
  suggestion()`/`withdraw_confirmed_capability_suggestion()`/
  `_safe_auto_scan_capabilities()` 分别对应 `problem_discovery.py`
  里的同名三个函数，行为逐项对称，包括"确认关注"落进
  `settings.confirmed_capability_suggestions`、自动挡下自动确认、
  以及最终"是否真的体现进 `capabilities_gained` 输出仍然完全由那
  一次的 LLM 判断，不强制写入"这条底线。
- **`get_effective_auto_scan_interval()` 默认值吸取上一轮教训，从
  一开始就是非零默认**（`problem_discovery.py` 那次是先上线一个
  默认关闭的开关、用了很久才被发现没人主动打开——这次直接按同样的
  "未配置=默认值"取舍上线，不重演一次"过很久才发现开关默认没开"）。
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

_MATURITY_STAGES = (
    "lab", "expert", "developer", "consumer", "cheap_at_scale", "infrastructure",
)
_CAPABILITY_KINDS = ("technology", "organization", "institution")


class CapabilityDiscoveryError(RuntimeError):
    """`suggest_capabilities()` 底层 workflow 调用失败/无法解析结果时
    抛出。"""


def suggest_capabilities(
    cfg,
    workspace_root: Path,
    current_vars: Dict[str, Any],
    history: List[Any],
    *,
    already_recorded: Optional[List[str]] = None,
    max_suggestions: int = 5,
    recent_steps: int = 5,
) -> List[Dict[str, Any]]:
    """让 LLM 回顾一次"有没有已经具备、但没被正式记录成
    `capabilities_gained` 的能力"，只返回建议，不做任何落盘。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根。
        current_vars: 当前状态的 `vars`（`SimState.vars`）。
        history: 完整历史（`SimStore.load_history()` 的返回值），本
            函数只截取最后 `recent_steps` 步喂给 prompt，理由同
            `problem_discovery.suggest_problems()`。
        already_recorded: 历史上所有已经声明过的 `capability` 名称
            （调用方按 `resolve_capabilities_hint()` 同款的精确字符串
            匹配口径去重后传入），用于在 prompt 里明确排除，避免
            重复建议已经记录过的能力；`None`/空列表表示还没有任何
            记录，LLM 可以自由判断。
        max_suggestions: 最多建议几条（默认 5，同时是提示词里明确
            要求 LLM 遵守的上限，这里做最后一道截断兜底）。
        recent_steps: 喂给 prompt 的最近步数（默认 5）。

    Returns:
        建议列表，每项：`{"capability": "...", "enables": [...],
        "limitations": [...], "maturity_stage": "..." 或 None,
        "capability_kind": "..." 或 None}`——字段集是 `capabilities_
        gained` 可选子字段的子集，理由见模块 docstring。`maturity_
        stage`/`capability_kind` 取值不在合法枚举里时归一化为
        `None`（同 `state_model.py::SimState.from_dict()` 对这两个
        字段的既有兜底处理一致，不强行编造一个可能错误的分类）。
        LLM 回复里格式不对/缺 `capability` 的条目会被跳过，不中断
        其它条目的解析。

    Raises:
        CapabilityDiscoveryError: 找不到 workflow 定义、workflow
            执行未成功、或最终回复无法解析出 `suggestions` 字段。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore
    from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

    wf_store = WorkflowStore(Path(workspace_root))
    wf = wf_store.load("capability_discovery")
    if wf is None:
        raise CapabilityDiscoveryError(
            "找不到 workflow 定义 'capability_discovery'"
            f"（预期路径：{workspace_root}/workflows/capability_discovery.yaml）"
        )

    recent_history = [
        {
            "step": s.step,
            "summary": getattr(s, "summary", ""),
            "narrative": getattr(s, "narrative", ""),
        }
        for s in (history or [])[-max(recent_steps, 0):]
    ]
    already_recorded_list = [str(c).strip() for c in (already_recorded or []) if str(c).strip()]

    runner = WorkflowRunner(cfg)
    result = runner.run(
        wf,
        {
            "current_vars_json": json.dumps(current_vars or {}, ensure_ascii=False),
            "recent_history_json": json.dumps(recent_history, ensure_ascii=False),
            "already_recorded_json": json.dumps(already_recorded_list, ensure_ascii=False),
            "max_suggestions": max_suggestions,
        },
    )

    if result.status != "done":
        failed = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        raise CapabilityDiscoveryError(
            f"capability_discovery workflow 执行未成功：status={result.status}；" + "；".join(failed)
        )

    step_result = next(
        (sr for sr in result.step_results if sr.step_id == "capability_discovery"), None
    )
    if step_result is None:
        raise CapabilityDiscoveryError("capability_discovery 步骤没有产出结果")

    try:
        data = extract_agent_json_output(step_result.output, required_keys=["suggestions"])
    except AgentStepOutputError as exc:
        raise CapabilityDiscoveryError(
            f"capability_discovery 步骤回复无法解析为能力建议：{exc}"
        ) from exc

    parsed: List[Dict[str, Any]] = []
    for item in data.get("suggestions") or []:
        if not isinstance(item, dict):
            continue
        capability = str(item.get("capability", "") or "").strip()
        if not capability:
            continue
        maturity_stage = item.get("maturity_stage")
        if maturity_stage not in _MATURITY_STAGES:
            maturity_stage = None
        capability_kind = item.get("capability_kind")
        if capability_kind not in _CAPABILITY_KINDS:
            capability_kind = None
        parsed.append(
            {
                "capability": capability,
                "enables": [str(m) for m in (item.get("enables") or []) if str(m).strip()],
                "limitations": [str(m) for m in (item.get("limitations") or []) if str(m).strip()],
                "maturity_stage": maturity_stage,
                "capability_kind": capability_kind,
            }
        )
        if len(parsed) >= max_suggestions:
            break
    return parsed


# ── 确认/撤回：把一条建议记进 `settings`，供后续推进的 prompt 引用 ──


def _format_confirmed_capability_suggestions(raw: Any) -> str:
    """把 `manifest.settings.confirmed_capability_suggestions` 拼成
    一段人类可读的提示文本，喂给 `advance_step`/`world_evolve` 的
    prompt（写法同 `problem_discovery._format_confirmed_problem_
    suggestions()`）。留空（未声明/尚无已确认项）返回空字符串，不
    影响任何已有行为。
    """
    items = raw if isinstance(raw, list) else []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        capability = str(item.get("capability") or "").strip()
        if not capability:
            continue
        enables_text = "、".join(item.get("enables") or [])
        suffix = f"（可能带来：{enables_text}）" if enables_text else ""
        lines.append(f"- {capability}{suffix}")
    return "\n".join(lines)


def adopt_capability_suggestion(
    data_dir: Path,
    sim_id: str,
    *,
    capability: str,
    enables: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    maturity_stage: Optional[str] = None,
    capability_kind: Optional[str] = None,
) -> "SimManifest":
    """用户在 `suggest_capabilities()` 的建议列表里点"确认关注"，把这
    条建议追加进 `settings.confirmed_capability_suggestions`（确认
    机制同 `problem_discovery.adopt_problem_suggestion()`：不直接
    改写任何历史状态的 `capabilities_gained` 字段，只是让下一次
    `advance_step`/`world_evolve` 的 prompt 能看到它，由 LLM 自行
    判断要不要真正体现进这一步的 `capabilities_gained` 输出）。

    Returns:
        更新后的 `SimManifest`（已落盘）。
    """
    from world_simulator.store import SimStore, now_iso

    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    confirmed = list(manifest.settings.get("confirmed_capability_suggestions") or [])
    normalized_maturity = maturity_stage if maturity_stage in _MATURITY_STAGES else None
    normalized_kind = capability_kind if capability_kind in _CAPABILITY_KINDS else None
    confirmed.append(
        {
            "id": f"confirmed_capability_{secrets.token_hex(3)}",
            "capability": str(capability or "").strip(),
            "enables": [str(m) for m in (enables or []) if str(m).strip()],
            "limitations": [str(m) for m in (limitations or []) if str(m).strip()],
            "maturity_stage": normalized_maturity,
            "capability_kind": normalized_kind,
            "confirmed_at": now_iso(),
        }
    )
    manifest.settings = {**manifest.settings, "confirmed_capability_suggestions": confirmed}
    store.save_manifest(manifest)
    return manifest


def withdraw_confirmed_capability_suggestion(
    data_dir: Path, sim_id: str, suggestion_id: str
) -> "SimManifest":
    """从 `settings.confirmed_capability_suggestions` 里移除一条已
    确认的建议。找不到该 id 时视为已经移除过，静默返回（幂等，同
    `problem_discovery.withdraw_confirmed_problem_suggestion()`
    的既有取舍）。
    """
    from world_simulator.store import SimStore

    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    confirmed = list(manifest.settings.get("confirmed_capability_suggestions") or [])
    remaining = [c for c in confirmed if str(c.get("id")) != suggestion_id]
    manifest.settings = {**manifest.settings, "confirmed_capability_suggestions": remaining}
    store.save_manifest(manifest)
    return manifest


# ── 自动触发（不再要求用户记得去点"扫描待发现能力"按钮）───────────


DEFAULT_AUTO_SCAN_INTERVAL = 5
"""`capability_discovery_auto_scan_interval` 未显式配置时使用的默认
值——直接采用非零默认上线（第二十一轮，吸取 `problem_discovery.
DEFAULT_AUTO_SCAN_INTERVAL` 那次"先默认关闭、跑了很久才被发现没人
主动打开"的教训），语义和 `get_effective_auto_scan_interval()` 的
区分逻辑完全对称，不重复展开。
"""


def get_effective_auto_scan_interval(settings: Optional[Dict[str, Any]]) -> int:
    """统一算出这个实例实际生效的能力自动扫描间隔，语义与
    `problem_discovery.get_effective_auto_scan_interval()` 完全对称：
    `settings` 里没有 `capability_discovery_auto_scan_interval` 这个
    key 时返回默认值；显式存了这个 key（含 0）时尊重显式值；解析
    失败时退回默认值。
    """
    if not settings or "capability_discovery_auto_scan_interval" not in settings:
        return DEFAULT_AUTO_SCAN_INTERVAL
    try:
        return int(settings.get("capability_discovery_auto_scan_interval"))
    except (TypeError, ValueError):
        return DEFAULT_AUTO_SCAN_INTERVAL


def _collect_recorded_capability_names(history: List[Any]) -> List[str]:
    """从历史里收集所有已经声明过的 `capability` 名称（精确字符串，
    不去重复但调用方会再套一层 `set`/直接传给 prompt 做展示——这里
    只负责"抽取"，和 `spec_generator.resolve_capabilities_hint()`/
    `app.py::_collect_capability_maturity_timeline()` 用的是同一个
    字段、同一种"精确字符串匹配"口径，保持三处一致，不引入第二套
    归并规则）。
    """
    names: List[str] = []
    for state in history or []:
        for item in getattr(state, "capabilities_gained", None) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("capability") or "").strip()
            if name:
                names.append(name)
    return names


def _safe_auto_scan_capabilities(
    cfg,
    workspace_root: Path,
    store,
    manifest: "SimManifest",
    *,
    branch: str,
    next_state,
    auto_confirm: bool,
) -> None:
    """`suggest_capabilities()` 的自动触发安全包装，逐项对称
    `problem_discovery._safe_auto_scan_problems()`，不重复展开设计
    说明——唯一的实质区别是这里在扫描前会先算一遍 `already_
    recorded`（历史上所有已经声明过的能力名称 + 已确认但可能还没被
    体现的建议）传给 `suggest_capabilities()`，避免自动扫描反复建议
    同一个已经记录过的能力。

    扫描结果写入 `manifest.settings["last_auto_capability_scan"]`
    （字段结构同 `last_auto_problem_scan`）；`auto_confirm=True`
    （自动挡场景）时额外自动写入 `confirmed_capability_
    suggestions`；任何异常都在这里吞掉，不影响本次推进本身。
    """
    try:
        interval = get_effective_auto_scan_interval(manifest.settings)
    except Exception:
        interval = 0
    if interval <= 0 or next_state.step % interval != 0:
        return

    try:
        from world_simulator.store import now_iso

        history = store.load_history(branch)
        already_recorded = _collect_recorded_capability_names(history)
        already_confirmed = [
            str(c.get("capability") or "").strip()
            for c in (manifest.settings.get("confirmed_capability_suggestions") or [])
            if isinstance(c, dict)
        ]
        already_recorded = already_recorded + [c for c in already_confirmed if c]
        suggestions = suggest_capabilities(
            cfg, workspace_root, next_state.vars, history, already_recorded=already_recorded,
        )
    except Exception:
        return

    manifest.settings["last_auto_capability_scan"] = {
        "step": next_state.step,
        "source": [str(manifest.sim_id), branch],
        "suggestions": suggestions,
        "scanned_at": now_iso(),
    }

    if not auto_confirm:
        return

    confirmed = list(manifest.settings.get("confirmed_capability_suggestions") or [])
    for item in suggestions:
        capability = str(item.get("capability") or "").strip()
        if not capability:
            continue
        confirmed.append(
            {
                "id": f"confirmed_capability_{secrets.token_hex(3)}",
                "capability": capability,
                "enables": [str(m) for m in (item.get("enables") or []) if str(m).strip()],
                "limitations": [str(m) for m in (item.get("limitations") or []) if str(m).strip()],
                "maturity_stage": item.get("maturity_stage"),
                "capability_kind": item.get("capability_kind"),
                "confirmed_at": now_iso(),
                "auto_confirmed": True,
            }
        )
    manifest.settings["confirmed_capability_suggestions"] = confirmed
