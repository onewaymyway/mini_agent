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
- ~~不做自动周期性触发~~——这一条是模块最初版本（第九轮批次三）的
  取舍，第十轮批次一已经加了 `_safe_auto_scan_problems()` 这个自动
  周期触发的安全包装，本条描述已经不准确，保留删除线是为了不重写
  历史小节、只在这里显式标注过时，实际行为以 `_safe_auto_scan_
  problems()` 的 docstring 为准。
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
        "missing_capabilities": [...], "root_causes": [...],
        "candidate_solutions": [...], "depends_on": [...]}`——后三个
        是第十轮批次二新增的可选结构化字段（`next_doc/world_
        simulator_tenth_round_problem_discovery_automation_plan.md`
        批次二），LLM 判断不出来时留空数组，这里原样透传，不强行
        编造、不做任何自动判重/推断。LLM 回复里格式不对/缺字段
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
                    # 第十轮批次二：三个可选的结构化字段（`next_doc/
                    # world_simulator_tenth_round_problem_discovery_
                    # automation_plan.md` 批次二）——LLM 判断不出来
                    # 时给空数组，这里原样透传，不强行编造、不做任何
                    # 自动推断。
                    "root_causes": [
                        str(m) for m in (item.get("root_causes") or []) if str(m).strip()
                    ],
                    "candidate_solutions": [
                        str(m) for m in (item.get("candidate_solutions") or []) if str(m).strip()
                    ],
                    "depends_on": [
                        str(m) for m in (item.get("depends_on") or []) if str(m).strip()
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
    root_causes: Optional[List[str]] = None,
    candidate_solutions: Optional[List[str]] = None,
    depends_on: Optional[List[str]] = None,
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
        root_causes/candidate_solutions/depends_on: 第十轮批次二
            新增的可选结构化字段，同样由调用方原样传入；三者都是
            "提示而非强制"——不做任何 `id` 存在性校验、不做循环
            依赖检测，谁引用了谁完全由 LLM/用户自己判断，同
            `causal_links`/`relationships` 等既有结构化字段的一贯
            取舍。

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
            "root_causes": [str(m) for m in (root_causes or []) if str(m).strip()],
            "candidate_solutions": [
                str(m) for m in (candidate_solutions or []) if str(m).strip()
            ],
            "depends_on": [str(m) for m in (depends_on or []) if str(m).strip()],
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


# ── 第十轮批次一：自动触发（不再要求用户记得去点"扫描潜在问题"按钮）──


DEFAULT_AUTO_SCAN_INTERVAL = 5
"""`problem_discovery_auto_scan_interval` 未显式配置时使用的默认值
（第十九轮，用户反馈"模拟了几十步，问题关系图还是空的"——排查发现
这个开关此前默认是 `0`=关闭，新建实例从来不会自动打开它，用户也不
一定知道要去设置里手动开，`problems` 因此只能完全指望模型自己主动
声明，而 `advance_step` prompt 里这类"可选字段"有五六个之多，模型
大概率会全部跳过）。

改成"没有配置就当作 5"而不是直接把创建流程/旧数据的默认值改成 5，
是为了同时兼顾两件事：新建实例、以及从来没打开过"模拟设置"面板保存
过的旧实例，都能不做任何操作就自动获得这个默认值；已经在设置面板里
显式保存过 `0`（不管是特意关闭、还是看到默认显示的 0 直接点了保存）
的实例，尊重这个显式选择，不会被这次改动悄悄改回 5——`get_effective_
auto_scan_interval()` 用"这个 key 在 settings 里存不存在"而不是
"值是不是 0"来做这个区分，见该函数的说明。
"""


def get_effective_auto_scan_interval(settings: Optional[Dict[str, Any]]) -> int:
    """统一算出这个实例实际生效的自动扫描间隔——`_safe_auto_scan_
    problems()`（引擎侧，决定要不要真的发起一次扫描）和 `app.py`
    的"模拟设置"面板（展示层，决定数字输入框默认显示什么）都调用
    这一个函数，保证"UI 上看到的默认值"和"引擎实际使用的默认值"
    永远一致，不会出现 UI 显示 0、实际却按 5 生效的错位。

    `settings` 里完全没有 `problem_discovery_auto_scan_interval`
    这个 key（新建实例、或从未在设置面板里保存过的旧实例）→ 返回
    `DEFAULT_AUTO_SCAN_INTERVAL`；显式存了这个 key（不管是 0 还是
    其它正整数）→ 尊重这个显式值，解析失败（脏数据）时退回默认值。
    """
    if not settings or "problem_discovery_auto_scan_interval" not in settings:
        return DEFAULT_AUTO_SCAN_INTERVAL
    try:
        return int(settings.get("problem_discovery_auto_scan_interval"))
    except (TypeError, ValueError):
        return DEFAULT_AUTO_SCAN_INTERVAL


def _safe_auto_scan_problems(
    cfg,
    workspace_root: Path,
    store,
    manifest: "SimManifest",
    *,
    branch: str,
    next_state,
    auto_confirm: bool,
) -> None:
    """`suggest_problems()` 的自动触发安全包装（第十轮批次一，
    `next_doc/world_simulator_tenth_round_problem_discovery_
    automation_plan.md` 3 节）。

    只在 `manifest.settings["problem_discovery_auto_scan_interval"]`
    （整数，未配置时按 `DEFAULT_AUTO_SCAN_INTERVAL` 处理，见
    `get_effective_auto_scan_interval()` 的说明；显式设为 0 表示
    用户主动关闭）大于 0、且 `next_state.step` 是该间隔的整数倍
    时才发起一次扫描调用；未设置/为 0 时立即返回，不产生任何额外的
    LLM 调用——同项目一贯"新增开关默认不改变已有行为"的惯例，手动挡/
    自动挡都会经过这里（`engine.advance.advance()` 是两者共同的唯一
    落点），不需要在 `autopilot.py` 里重复一份触发逻辑。

    扫描结果写入 `manifest.settings["last_auto_problem_scan"]`
    （`{"step", "source": [sim_id, branch], "suggestions", "scanned_
    at"}`）；这里只**修改**调用方传入的 `manifest` 对象，不自己落盘
    ——`advance()` 本来就会在本次推进末尾统一调用一次
    `store.save_manifest(manifest)`，这里不需要、也不应该再额外触发
    一次 IO。

    `auto_confirm=True`（自动挡场景，由调用方按 `effective_chosen_by
    == "autopilot"` 判断，不需要 `autopilot.py` 显式传参）时，扫描出
    的建议会**额外**自动写入 `settings["confirmed_problem_
    suggestions"]`——自动挡下没有人来点"确认关注"按钮，这一步等价于
    代理替用户做了这个操作；`auto_confirm=False`（手动挡）时只写
    `last_auto_problem_scan`，等用户在看板上自己点"确认关注"，同现有
    手动扫描的既有流程完全一致。**无论哪种情况，这一步产出的都只是
    下一次 `advance_step` prompt 里的一句 hint**——真正体现进哪一步
    的 `problems` 输出，仍然完全由那一次的 LLM 自行判断，不会被这里
    强制写入，没有突破模块 docstring"范围克制"一节"建议而非强制"的
    底线。

    这是一次推进*落盘之后*的旁路操作（同 `engine.knowledge.
    _safe_record_causal_links`/`_safe_evaluate_reflexivity` 的既有
    约定），任何异常（workflow 未配置、LLM 调用失败、解析失败等）都
    不应该让本次推进本身失败，这里吞掉异常，只保留"尽力而为"的语义
    ——扫描失败时 `last_auto_problem_scan` 保持上一次的值不变，下次
    到达间隔时会自然重试。
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
        suggestions = suggest_problems(
            cfg,
            workspace_root,
            next_state.vars,
            manifest.settings.get("desired_state"),
            history,
        )
    except Exception:
        return

    manifest.settings["last_auto_problem_scan"] = {
        "step": next_state.step,
        "source": [str(manifest.sim_id), branch],
        "suggestions": suggestions,
        "scanned_at": now_iso(),
    }

    if not auto_confirm:
        return

    confirmed = list(manifest.settings.get("confirmed_problem_suggestions") or [])
    for category in _CATEGORIES:
        for item in suggestions.get(category) or []:
            symptom = str(item.get("symptom") or "").strip()
            if not symptom:
                continue
            confirmed.append(
                {
                    "id": f"confirmed_problem_{secrets.token_hex(3)}",
                    "category": category,
                    "symptom": symptom,
                    "blocked_goal": str(item.get("blocked_goal") or "").strip(),
                    "missing_capabilities": [
                        str(m) for m in (item.get("missing_capabilities") or []) if str(m).strip()
                    ],
                    # 第十轮批次二：三个可选结构化字段同样一并透传。
                    "root_causes": [
                        str(m) for m in (item.get("root_causes") or []) if str(m).strip()
                    ],
                    "candidate_solutions": [
                        str(m) for m in (item.get("candidate_solutions") or []) if str(m).strip()
                    ],
                    "depends_on": [
                        str(m) for m in (item.get("depends_on") or []) if str(m).strip()
                    ],
                    "confirmed_at": now_iso(),
                    "auto_confirmed": True,
                }
            )
    manifest.settings["confirmed_problem_suggestions"] = confirmed
