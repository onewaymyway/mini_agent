"""world_simulator/spec_generator.py — 意图 → 模拟提案草稿

设计依据：`world_simulator_external_project_plan.md` 2.3 节。走
`workflows/generate_scenario.yaml`（`skill_agent` 类型），复用既有的
"LLM 输出 → 结构化校验 → 落盘 → 失败自动重试"整套机制，不自己重新写
一套"调 LLM + 解析 JSON + 校验"的轮子。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

from world_simulator.state_model import ChoiceOption


class ScenarioGenerationError(RuntimeError):
    pass


DEFAULT_OPTIONS_COUNT = 4
DEFAULT_TIME_GRANULARITY = (
    "自动——由你根据模拟对象的性质自行判断每一步合理的时间跨度"
    "（不必固定假设\u201c一步 = 一年\u201d，比如模拟一场谈判可能一步只是几分钟，"
    "模拟一个文明可能一步是几十年）"
)

_GRANULARITY_CONTINUITY_NOTE_ADVANCE = (
    "\n默认延续上一步用过的粒度（见 `{current_time_granularity}`），除非"
    "情境明显需要改变（比如从平稳时期进入密集博弈/谈判/危机，或者反过来"
    "从紧张情境恢复平稳）——不要没有情节依据就在粒度之间来回跳。请在输出里"
    "给出这一步实际使用的粒度 `next_time_granularity`；如果这个值和上一步"
    "不同，额外给一两句话的 `granularity_reason` 说明为什么现在要切换节奏，"
    "没变化则不需要这个字段。"
)
_GRANULARITY_CONTINUITY_NOTE_CREATE = (
    "\n这是初始状态，你给出的粒度会作为本次模拟的起始基准（输出字段"
    "`time_granularity`），之后每一步默认延续这个基准，只在情境明显需要"
    "时才会切换——请选一个能代表这次模拟\"日常节奏\"的粒度，不用考虑中途"
    "临时的特殊情境。"
)
_VALID_GRANULARITY_MODES = ("fixed", "auto", "guided")


_MULTI_ENTITY_MODE_HINT_ON = (
    "已启用多主体模式（阶段十七）——vars 顶层需要按以下约定组织：一个"
    "entities 对象，键是每个主体的名字，值是这个主体自己的私有信息（比如"
    "某一方对另一方底线的猜测、只有这一方自己知道的立场，这类信息只能放"
    "在对应主体自己的私有信息里，不能让其它主体的私有信息包含这些内容）；"
    "再加一个 shared_vars 对象，放所有主体都公开知道的共享信息（比如当前"
    "谈判轮次、公开报价）。不要为了省事把所有信息都塞进 shared_vars，也"
    "不要遗漏某个主体应该有的私有视角。"
)
_MULTI_ENTITY_MODE_HINT_OFF = "未启用（单一全局 vars，不需要区分多个主体的私有视角）"


def _resolve_multi_entity_hint(settings: "Dict[str, Any] | None") -> str:
    """把 `settings.multi_entity_mode` 转成喂给 prompt 的一句话提示
    （阶段十七，4.9 节）。默认关闭，不影响现有两个模板的行为。"""
    if bool((settings or {}).get("multi_entity_mode")):
        return _MULTI_ENTITY_MODE_HINT_ON
    return _MULTI_ENTITY_MODE_HINT_OFF


def _resolve_background_entities_hint(settings: "Dict[str, Any] | None") -> str:
    """把 `settings.hierarchical_agent_mode`/`background_entities` 转成
    喂给 prompt 的一句话提示（Hierarchical Agent，4.10 节设计草案第
    一步）。这只是"给 skill 减负"的提示——即使 skill 认真推理了这些
    背景角色，`engine.py` 落盘前仍然会用规则外推强制覆盖，不采纳 LLM
    对这些主体给出的值，所以提示词里明确说"给粗略值即可，不用深入
    推理"，避免浪费推理精度在一个反正不会被采纳的地方。"""
    settings = settings or {}
    if not bool(settings.get("hierarchical_agent_mode")):
        return "未启用（所有主体都按正常流程完整推理）"
    names = [str(n).strip() for n in (settings.get("background_entities") or []) if str(n).strip()]
    if not names:
        return "已声明启用，但没有列出任何背景角色名字，等同未启用"
    return (
        "已启用（Hierarchical Agent 设计草案第一步）——以下主体是背景角色，"
        "不需要深入推理它们的变化，给出粗略延续即可（无论你给什么，系统都会"
        "用简单规则重新计算它们这一步的数值，不采纳你的推理结果，把推理精力"
        "留给关键角色）：" + "、".join(names)
    )


def _lines_due_this_step_hint(lines: "List[Dict[str, Any]]", next_step: int) -> str:
    """把每条线的 `advance_every_n_steps` 换算成"这一步预期哪些线会动、
    哪些线大概率维持不变"的一句话提示（阶段三十一，`next_doc/
    world_simulator_universal_simulator_gap_analysis_and_roadmap_v2_
    plan.md` 4.20 节，多尺度因果线的最小可行版本）。

    只是提示，不是约束：`engine.py` 不会因为这里判断"预期不动"就拒绝
    或过滤 skill 实际给出的 `line_updates`——刻意避免"错误估计节奏
    导致该更新的线被拦住"。`advance_every_n_steps` 未声明或 <= 1 的线
    不出现在提示里（沿用旧行为，避免给"每步都可能动"的线增加无意义
    的噪音）。`next_step` 是即将产生的这个状态的 step 序号（不是
    `current.step`，因为这一步描述的是"接下来要走的这一步"）。
    """
    due, quiet = [], []
    for line in lines:
        raw_n = line.get("advance_every_n_steps")
        try:
            n = int(raw_n) if raw_n is not None else 1
        except (TypeError, ValueError):
            n = 1
        n = max(1, n)
        if n <= 1:
            continue
        line_id = str(line.get("id") or "").strip()
        if not line_id:
            continue
        label = str(line.get("label") or line_id).strip()
        piece = f"{line_id}（{label}，约每 {n} 步一动）"
        if next_step % n == 0:
            due.append(piece)
        else:
            quiet.append(piece)
    if not due and not quiet:
        return ""
    parts = []
    if due:
        parts.append("预期这一步有动静：" + "、".join(due))
    if quiet:
        parts.append(
            "预期这一步大概率维持不变（仅供参考，你仍可按实际情境自行判断"
            "是否更新）：" + "、".join(quiet)
        )
    return "\n" + "；".join(parts) + "。"


def _resolve_causal_lines_hint(
    settings: "Dict[str, Any] | None", *, stage: str = "advance", current_step: int = 0
) -> str:
    """把 `settings.causal_lines` 转成喂给 prompt 的一句话提示（阶段
    二十二，4.13 节，多尺度因果线；阶段二十六，`next_doc/
    world_simulator_causal_line_future_tree_plan.md`，因果线的"未来
    因果树"）。

    `stage == "create"`（`generate_scenario`，还没有历史）：不再是
    "可选、留空表示不需要"——明确要求 skill 规划出 2~4 条核心因果线，
    **且每条线必须同时给出一棵初始的未来因果树（`future_tree`，2~3
    个有实质区分度的分支，而不是一条延续路径）**。就算 skill 没有
    认真遵守，`spec_generator.generate_scenario()` 之后仍会用
    `causal_tree.ensure_future_trees()` 兜底补全，但 prompt 里明确
    要求，能让第一次生成的树更贴合具体情境，而不是全部退化成兜底的
    通用模板。

    `stage == "advance"`（默认）：列出每条线的 id/label/节奏/当前未来
    分支（以及用户在详情页对这条线提的修改意见，如果有），要求 skill
    在 `line_updates` 里用这些 id 作为 key 给出实际进展，并且可选给出
    `tree_updates`——这一步的走向印证/排除了哪个已有分支，或者催生了
    一个原来树上没有的新分支。因果线本身仍然不要求前置声明——没有任何
    声明时退化为"自己判断因果线并自行起 id"（`engine.
    _auto_register_causal_lines`/`causal_tree.auto_register_lines` 会
    在落盘时自动登记，且同样带上兜底的默认未来树）。
    """
    lines = [
        line for line in ((settings or {}).get("causal_lines") or [])
        if isinstance(line, dict) and str(line.get("id") or "").strip()
    ]

    if stage == "create":
        base = (
            "因果线是这次模拟的核心结构，不是可选项——请规划出 2~4 条相对"
            "独立、节奏可能不同的核心因果线（比如技术线按年演化、谈判线按"
            "轮次推进、个人线按月推进），自行给每条线起一个简短的英文/"
            "拼音 id 和一句话中文 label，填进输出的 `causal_lines` 数组。"
            "**每条线必须同时给出一个初始的未来因果树 `future_tree`**："
            '形如 {"branches": [{"id": ..., "description": ..., '
            '"likelihood": "high"|"medium"|"low"}, ...]}，每条线给 2~3 个'
            "相互之间有实质区分度的未来可能分支（比如\"快速发展\"/\"缓慢"
            "发展\"/\"遭遇阻力\"这类明显不同的方向，不要写成同义反复或"
            "换个说法的同一件事），不要求穷尽所有可能，覆盖目前能想到的"
            "主要分歧点即可。每个分支还可以选填 `semantic_event`（一句话"
            "现实语义）、`trigger_conditions`（触发条件）、"
            "`candidate_actions`（一旦激活通常对应的行动方向）等字段，"
            "创建阶段留空也完全可以，后续推进时再补充。"
        )
        if lines:
            refine_parts = [
                f'{str(line.get("id"))}（{str(line.get("label") or line.get("id"))}）'
                for line in lines
            ]
            base += (
                "上一版草稿已经给出以下因果线，如果本次是根据反馈意见修改，"
                "优先在这些线的基础上调整/补充未来树，而不是重新换一套 id："
                + "、".join(refine_parts) + "。"
            )
        return base

    if not lines:
        return (
            "因果线是这次模拟的默认基础机制，不需要用户提前声明——请你自己"
            "判断这次模拟里存在哪几条相对独立、节奏可能不同的因果线（比如"
            "技术线按年演化、谈判线按轮次推进、个人线按月推进），一般给出"
            "2~4 条即可，自行给每条线起一个简短的英文/拼音 id 和一句话中文"
            "label，在 line_updates 里用这些 id 作为 key 给出这一步有实际"
            "进展的线（没有进展的线不用出现）；后续每一步请尽量延续使用"
            "同一批 id，不要每步都换一套新的，除非这一步确实催生了一条全新"
            "的因果线——那种情况可以直接启用一个新 id，系统会自动记录为新"
            "增线，不需要额外操作。"
        )
    parts = []
    tree_parts = []
    for line in lines:
        line_id = str(line["id"]).strip()
        label = str(line.get("label") or line_id).strip()
        granularity = str(line.get("time_granularity") or "").strip()
        feedback = str(line.get("user_feedback") or "").strip()
        piece = f'{line_id}（{label}'
        if granularity:
            piece += f"，节奏参考：{granularity}"
        if feedback:
            piece += f"，用户对这条线的修改意见：{feedback}"
        piece += "）"
        parts.append(piece)

        future_tree = line.get("future_tree") if isinstance(line.get("future_tree"), dict) else {}
        branches = [b for b in (future_tree.get("branches") or []) if isinstance(b, dict)]
        if branches:
            branch_desc = "；".join(
                f'{b.get("id")}[{b.get("status", "dormant")}]：{b.get("description", "")}'
                for b in branches
            )
            tree_parts.append(f"{line_id} 当前未来分支——{branch_desc}")

    hint = (
        "已声明/已自动识别以下因果线，这一步哪些线有实际进展由你自行判断——"
        "有进展的线，在 line_updates 里用对应 id 作为 key 给出这条线的进展；"
        "没有进展的线不需要在 line_updates 里出现，不强制每条线每一步都"
        "更新；如果用户给某条线留了修改意见，请在推演这条线后续走向时认真"
        "纳入考虑，必要时在 narrative/summary 里说明是如何回应这个意见的："
        + "、".join(parts)
        + "。如果这一步确实出现了一条上面都没列出的新因果线，也可以直接用"
        "一个新 id 输出 line_updates，系统会自动登记为新线。"
    )
    due_hint = _lines_due_this_step_hint(lines, current_step)
    if due_hint:
        hint += due_hint
    if tree_parts:
        hint += (
            "\n以下是各条线当前的未来分支（状态标注在方括号里，6 态含义："
            "dormant 潜伏/emerging 正在形成/active 已激活/resolved 已解决/"
            "expired 错过窗口/invalidated 因世界变化而失效，阶段三十三第"
            "四批，4.9 节）："
            + "；".join(tree_parts)
            + "。如果这一步的实际走向印证/排除了某个分支，或者催生了一个"
            "上面没有的新可能性，可选输出 `tree_updates`（数组），每项形如 "
            '{"line_id": ..., "confirmed_branch": "分支id（可选，这一步'
            '印证了哪个已有分支，落盘为 resolved）", "pruned_branches": '
            '["分支id", ...]（可选，明显已经不可能发生的分支，落盘为 '
            'invalidated）, "status_updates": [{"branch_id": ..., "status": '
            '"emerging"|"active"|"dormant"|"resolved"|"expired"|'
            '"invalidated"}]（可选，声明 confirmed_branch/pruned_branches '
            "覆盖不到的状态变化，尤其是某个分支「正在形成」或「已激活」但"
            '还谈不上「已解决」）, "new_branches": [{"description": ..., '
            '"likelihood": "high"|"medium"|"low", "semantic_event": '
            '"（可选）一句话现实语义", "trigger_conditions": "（可选）触发'
            '条件", "candidate_actions": ["（可选）一旦激活通常对应的行动'
            '方向"], "urgency": "low"|"medium"|"high"|"critical"（可选）}]'
            '（可选，出现了原来树上没有覆盖的新可能性）, "expand_branches": '
            '[{"branch_id": ..., "sub_branches": [同 new_branches 里单个'
            "分支的形状]}]（可选，阶段三十三第四批 4.11 节渐进式展开——"
            "只有当某条线进入决策相关的活跃窗口、需要更细粒度的可能性时"
            "才展开，不要求每条线都展开）}——不强制每一步都输出，没有值得"
            "更新的树就不用给。"
        )
    return hint


def _resolve_belief_fields_hint(settings: "Dict[str, Any] | None") -> str:
    """把 `settings.belief_fields` 转成喂给 `advance_step` prompt 的
    一句话提示（4.3 节，State/Belief 分离子方案，`next_doc/
    world_simulator_belief_state_separation_plan.md`）。

    未声明 `belief_fields`（默认情况）返回空字符串——`advance_step.
    yaml` 里这段说明整体是条件性拼接的，不给没启用这个功能的模拟
    增加任何 prompt 噪音，同 `causal_lines_hint` 等既有条件提示的
    处理方式。
    """
    fields = [str(f).strip() for f in ((settings or {}).get("belief_fields") or []) if str(f).strip()]
    if not fields:
        return ""
    return "、".join(fields)


def resolve_causal_graph_hint(
    settings: "Dict[str, Any] | None", history: "Sequence[Any] | None"
) -> str:
    """把历史 `causal_links` 聚合出的"线到线"邻接关系反过来喂给下一次
    `advance_step` 的 prompt（阶段三十三第三批，`next_doc/
    world_simulator_potential_causal_space_and_decision_engine_plan.md`
    4.13 节，跨线级联的主动计算）。

    复用已有的 `causal_graph.build_causal_graph(history)`——阶段二十七
    起它已经能从历史 `causal_links` 聚合出这类邻接关系，此前只在
    `app.py` 展示层被调用，没有被喂回 prompt；本函数是这个聚合结果
    第一次被反馈进推进循环本身。

    只保留 `source_line_id` 是当前已声明因果线（`settings.
    causal_lines`）之一、且不是"同线内部"（`source_line == target_
    line`）、历史上出现次数达到一定阈值（`total >= 2`，避免单次巧合
    就被当成"经常连带影响"）的边，取出现次数最高的最多 3 条——这是
    "用历史统计做提示"的轻量级主动化，不是真正的因果推断引擎（不做
    贝叶斯网络/结构方程这类真正的因果计算，那超出当前项目"克制、不做
    伪精确"的一贯取舍，且单次模拟的历史长度也支撑不起这种计算量）。

    没有声明任何因果线、历史为空、或者聚合结果里没有满足以上条件的
    边时，返回空字符串——`advance_step.yaml` 里这句提示是条件性的，
    为空表示"暂无足够历史数据或没有声明因果线"，不代表出错，调用方
    不需要额外处理。
    """
    line_ids = [
        str(line.get("id")).strip()
        for line in ((settings or {}).get("causal_lines") or [])
        if isinstance(line, dict) and str(line.get("id") or "").strip()
    ]
    if not line_ids or not history:
        return ""

    from world_simulator.causal_graph import build_causal_graph, relation_type_label

    edges = [
        edge
        for edge in build_causal_graph(history)
        if edge.source_line in line_ids and edge.source_line != edge.target_line and edge.total >= 2
    ]
    if not edges:
        return ""

    top_edges = edges[:3]
    parts = [
        f"{edge.source_line} → {edge.target_line}"
        f"（{relation_type_label(max(edge.relation_counts, key=edge.relation_counts.get))}，"
        f"出现 {edge.total} 次）"
        for edge in top_edges
    ]
    return (
        "根据以往记录，以下因果线之间历史上经常连带影响："
        + "；".join(parts)
        + "。如果这一步对应的源线有实质进展，请考虑是否也需要在 "
        "line_updates/tree_updates 里体现对下游线的连带影响，不代表这次"
        "一定会发生，仅作为提示参考。"
    )


def resolve_hints(
    settings: "Dict[str, Any] | None" = None, *, stage: str = "advance", current_step: int = 0
) -> Dict[str, str]:
    """把 `manifest.settings`（或创建向导里还没落盘成 manifest 时的临时
    设置字典）转成喂给 workflow prompt 的提示字符串。

    单独抽成一个函数，是因为 `generate_scenario()`（创建阶段，这时候
    还没有 manifest）和 `engine.advance()`（推进阶段，从
    `manifest.settings` 读）两处都要用同一套"没设置就用什么默认值"的
    规则——不想两处各写一份，以后要改默认值/校验逻辑就得同步改两处、
    容易漏改一处。

    Args:
        stage: `"create"`（`generate_scenario` 场景，还没有"上一步"）
            或 `"advance"`（默认，`engine.advance()` 场景，有
            `{current_time_granularity}` 可以参考）——两种场景下
            "自动"/"引导"模式的提示文案略有不同（`create` 讲的是"建立
            基准"，`advance` 讲的是"延续上一步、变化需说明理由"），
            避免 `generate_scenario.yaml` 的 prompt 里出现一句引用了
            不存在的 `{current_time_granularity}` 占位符的说明文字。
        current_step: 即将产生的下一个状态的 step 序号（阶段三十一，
            4.20 节），仅用于 `_lines_due_this_step_hint()` 换算"这一步
            哪些线预期有动静"；`stage == "create"` 场景没有意义，
            调用方不传时按 0 处理（不影响任何提示内容，因为
            `create` 分支本身不消费这个参数）。

    `time_granularity_hint` 的内容按 `time_granularity_mode` 分三种：
    - `fixed`：明确要求"每一步都严格按这个值推进"，行为与阶段一/二
      完全一致（一次模拟从头到尾只有一种粒度）。
    - `auto`（未设置时的默认值）：交给 skill 按情境自行判断，同一次
      模拟允许出现多种粒度，附带"延续上一步、变化需给理由"的约束，
      避免毫无依据地来回跳。
    - `guided`：在 `auto` 的自由裁量基础上，多附一句用户给的偏好/基准
      引导语，不是精确值，skill 仍自主判断。
    """
    settings = settings or {}
    raw_count = settings.get("options_count")
    try:
        options_count = int(raw_count)
    except (TypeError, ValueError):
        options_count = DEFAULT_OPTIONS_COUNT
    options_count = max(1, min(options_count, 8))

    mode = str(settings.get("time_granularity_mode") or "").strip()
    if mode not in _VALID_GRANULARITY_MODES:
        mode = "auto"

    continuity_note = (
        _GRANULARITY_CONTINUITY_NOTE_CREATE if stage == "create" else _GRANULARITY_CONTINUITY_NOTE_ADVANCE
    )

    if mode == "fixed":
        fixed_value = str(settings.get("time_granularity") or "").strip() or DEFAULT_TIME_GRANULARITY
        time_granularity_hint = f"固定模式——每一步都必须严格按这个粒度推进：{fixed_value}"
    elif mode == "guided":
        guide = str(settings.get("time_granularity_guide") or "").strip()
        if guide:
            time_granularity_hint = (
                f"引导模式——用户给出的偏好/基准（不是精确值，仍需你自行判断）："
                f"{guide}{continuity_note}"
            )
        else:
            time_granularity_hint = f"{DEFAULT_TIME_GRANULARITY}{continuity_note}"
    else:  # auto
        time_granularity_hint = f"{DEFAULT_TIME_GRANULARITY}{continuity_note}"

    return {
        "option_count_hint": (
            f"最多不超过 {options_count} 个（软上限，用于防止候选列表"
            f"刷屏，不是目标数量——具体生成几个由这一步是否出现真正的"
            f"决策分岔点决定，见下方“候选选项生成流程”）"
        ),
        "belief_fields_hint": _resolve_belief_fields_hint(settings),
        "time_granularity_hint": time_granularity_hint,
        "multi_entity_mode_hint": _resolve_multi_entity_hint(settings),
        "background_entities_hint": _resolve_background_entities_hint(settings),
        "causal_lines_hint": _resolve_causal_lines_hint(
            settings, stage=stage, current_step=current_step
        ),
    }


@dataclass
class ScenarioDraft:
    """一份模拟提案草稿（对应 `generate_scenario.yaml` 的 result_file）。"""

    title: str
    summary: str
    vars: Dict[str, Any]
    options: List[ChoiceOption]
    time_label: str = ""
    time_granularity: str = ""
    """这次模拟的起始基准粒度（`settings.time_granularity_mode` 为
    `auto`/`guided` 时才有意义），落盘为 `state0.time_granularity`，
    之后每一步 `advance()` 默认延续这个基准，见
    `state_model.SimState.time_granularity` 的说明。"""
    resource_fields: List[Any] = field(default_factory=list)
    """skill 在生成初始 `vars` 时给出的"哪些字段是资源类数值字段"建议
    （阶段九，见 `state_model.SimManifest.settings` 里 `resource_fields`
    的格式说明），可选输出，留空表示 skill 认为这次模拟没有需要做下限
    校验的资源字段。创建向导里会把这个建议展示给用户、允许编辑，最终
    编辑结果随 `settings.resource_fields` 一起存进 manifest，这个字段
    本身只是"草稿阶段的建议值"，不直接落盘。"""
    uncertain_fields: List[Dict[str, Any]] = field(default_factory=list)
    """skill 在生成初始 `vars` 时主动标注的"本质是主观估计、置信度不高"
    的字段（阶段十一，见 `state_model.SimState.uncertain_fields` 的格式
    说明），可选输出，留空表示 skill 认为初始状态没有需要标注的估计值。
    这个字段会直接落盘到 `state0.uncertain_fields`（不像
    `resource_fields` 那样需要先经过用户编辑再存进 `settings`——置信度
    标注是"描述性"的，不是需要用户确认的配置项）。"""
    resource_relations: List[Any] = field(default_factory=list)
    """skill 在生成初始 `vars` 时给出的"哪些资源字段之间存在转移关系"
    建议（阶段十六，见 `state_model.SimManifest.settings` 里
    `resource_relations` 的格式说明），比如
    `[{"type": "transfer", "from": "cash", "to": "inventory.value"}]`，
    可选输出，留空表示 skill 认为这次模拟没有需要做转移一致性检查的
    字段对。用途与 `resource_fields` 一致：创建向导展示建议值、允许
    用户编辑，最终结果存进 `settings.resource_relations`，这个字段
    本身只是"草稿阶段的建议值"，不直接落盘。"""
    objectives: List[Any] = field(default_factory=list)
    """skill 在生成初始状态时给出的"这次模拟主要关心的指标"建议
    （阶段十二，`next_doc/world_simulator_universal_world_model_upgrade_
    plan.md` 4.4 节 Problem Compiler 雏形），比如
    `["资产净值", "工作满意度", "健康水平"]`。用途与 `resource_fields`
    一致：创建向导展示建议值、允许用户编辑，最终结果存进
    `settings.objectives`（见 `state_model.SimManifest.settings`），这个
    字段本身只是"草稿阶段的建议值"，不直接落盘。存下来之后，「对比
    实验」页面的关注字段默认会带出这里声明的指标。

    阶段十四（4.6 节）起，每一项除了纯字符串（阶段十二行为，只展示不
    参与排序）还可以是结构化字典
    `{"label": "资产净值", "field": "resources.cash", "direction": "max"}`
    ——声明了 `field` 的条目会在「对比实验」页面出现"按关注指标排序"
    的辅助展示区（见 `world_simulator.analysis.rank_by_objectives()`），
    但排序结果始终"仅供参考"，不会替用户自动选出最优解。类型放宽为
    `List[Any]` 就是为了同时兼容这两种写法，不强制转成字符串。"""
    causal_lines: List[Any] = field(default_factory=list)
    """skill 在生成初始状态时给出的"这次模拟是否值得拆成多条因果线"
    建议（阶段二十二，见 `state_model.SimManifest.settings` 里
    `causal_lines` 的格式说明），比如
    `[{"id": "tech", "label": "技术线", "time_granularity": "年"}]`，
    可选输出，留空表示 skill 认为这次模拟用单一时间线就够了，不需要
    拆分。用途与 `resource_fields` 一致：创建向导展示建议值、允许用户
    编辑，最终结果存进 `settings.causal_lines`，这个字段本身只是
    "草稿阶段的建议值"，不直接落盘。"""
    field_provenance: Dict[str, str] = field(default_factory=dict)
    """skill 在生成初始 `vars` 的同时，对每个顶层字段标注的来源
    （阶段三十二，4.2 节）：`"fact"`（用户在意图描述里明确提到）/
    `"assumption"`（合理默认值）/`"inference"`（从上下文推断）/
    `"unknown"`（缺了，先给占位值）。可选输出，留空表示 skill 没有
    做这个标注（旧 skill 版本/skill 认为不需要）；直接落盘到
    `state0.field_provenance`，不像 `resource_fields` 那样需要先经
    用户编辑再存进 `settings`——来源标注是"描述性"的，同
    `uncertain_fields` 的既有取舍。"""
    belief_fields: List[str] = field(default_factory=list)
    """skill 在生成初始 `vars` 时给出的"哪些字段属于角色认知可能与真实
    值不同"的建议（阶段三十四，4.3 节第二批，见 `next_doc/world_
    simulator_belief_state_separation_plan.md` 第 3.1 节
    `settings.belief_fields` 的格式说明），比如
    `["market_demand", "competitor_strength"]`，可选输出，留空表示
    skill 认为这次模拟没有值得标注认知偏差的字段。用途与
    `resource_fields` 一致：创建向导展示建议值、允许用户编辑，最终
    结果存进 `settings.belief_fields`，这个字段本身只是"草稿阶段的
    建议值"，不直接落盘。"""
    beliefs: Dict[str, Any] = field(default_factory=dict)
    """skill 在生成初始状态时，对 `belief_fields` 里声明的字段给出的
    "角色一开始就存在的认知偏差"估计值（阶段三十四，4.3 节第二批），
    格式同 `state_model.SimState.beliefs`（单一数值或
    `{"low": ..., "high": ..., "point": ...}` 区间估计）。可选输出，
    留空表示这次模拟的初始状态没有认知偏差（或 skill 没给）。直接
    落盘到 `state0.beliefs`，不像 `belief_fields` 那样需要先经用户
    编辑再存进 `settings`——这是对初始状态的描述性快照，同
    `field_provenance`/`uncertain_fields` 的既有取舍。"""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioDraft":
        return cls(
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            vars=dict(data.get("vars") or {}),
            options=[ChoiceOption.from_dict(o) for o in (data.get("options") or [])],
            time_label=str(data.get("time_label", "") or ""),
            time_granularity=str(data.get("time_granularity", "") or ""),
            resource_fields=list(data.get("resource_fields") or []),
            resource_relations=list(data.get("resource_relations") or []),
            uncertain_fields=list(data.get("uncertain_fields") or []),
            objectives=[
                (dict(o) if isinstance(o, dict) else str(o))
                for o in (data.get("objectives") or [])
            ],
            causal_lines=[
                dict(x) for x in (data.get("causal_lines") or []) if isinstance(x, dict)
            ],
            field_provenance={
                str(k): str(v) for k, v in (data.get("field_provenance") or {}).items()
            },
            belief_fields=[str(f).strip() for f in (data.get("belief_fields") or []) if str(f).strip()],
            beliefs=dict(data.get("beliefs") or {}),
        )


def _skill_name_for_template(template: str) -> str:
    """模板名 → skill 名的约定：`<template 里下划线替换成连字符>-sim-template`。

    对应方案 2.4 节"新增一种模拟类型 = 新增一个 skill，引擎本身不用改"：
    只要按这个命名约定在 `skills/` 下放一个新 skill 目录（如模板名
    `life_sim` 对应 `skills/life-sim-template/`），`generate_scenario` /
    `advance_step` 两个 workflow 定义完全不用改，见 `_load_and_bind_skill()`。
    skill 名按惯例用连字符（`SkillLoader` 目录名规范），模板名按 Python
    惯例用下划线（供 `--template` CLI 参数/内部变量使用），两者在这里
    做唯一一次转换。
    """
    return f"{template.replace('_', '-')}-template"


def _load_and_bind_skill(workspace_root: Path, workflow_name: str, template: str, step_id: str):
    """加载 workflow 定义，并把指定 step 的 `skill_name` 按 `template`
    动态改写。

    `skill_agent` 步骤的 `skill_name` 字段本身不支持 `{var}` 占位符替换
    （替换机制只作用于 prompt 文本），所以在触发前用 Python 直接改写
    已解析出的 `WorkflowStep` 对象——`WorkflowStore.load()` 每次都是
    重新从磁盘解析，不会污染磁盘上的 yaml 定义或影响其它并发调用。
    """
    from mini_agent.workflow.store import WorkflowStore

    store = WorkflowStore(workspace_root)
    wf = store.load(workflow_name)
    if wf is None:
        raise ScenarioGenerationError(
            f"找不到 workflow 定义 {workflow_name!r}"
            f"（预期路径：{workspace_root}/workflows/{workflow_name}.yaml）"
        )
    skill_name = _skill_name_for_template(template)
    found = False
    for step in wf.steps:
        if step.id == step_id:
            step.skill_name = skill_name
            found = True
            break
    if not found:
        raise ScenarioGenerationError(
            f"workflow {workflow_name!r} 中找不到 step id={step_id!r}"
        )
    return wf, skill_name


def generate_scenario(
    cfg,
    workspace_root: Path,
    *,
    template: str,
    intent: str,
    feedback: str = "",
    previous_draft: "ScenarioDraft | None" = None,
    settings: "Dict[str, Any] | None" = None,
    data_dir: "Path | None" = None,
) -> ScenarioDraft:
    """触发一次 `generate_scenario` workflow，返回结构化的提案草稿。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根（workflow/skill 私有
            目录 `<root>/workflows`、`<root>/skills` 从这里派生）。
        template: 场景模板名（如 `life_sim`），决定挂载哪个 skill。
        intent: 用户的一句话模拟意图。
        feedback: 用户对上一份草稿的补充意见（可选）；非空时表示这是
            一次"根据意见修改"的重新生成，而不是从零生成——
            `previous_draft` 应同时给出，供 skill 在原草稿基础上按
            意见调整，而不是完全无视原草稿重新编一份。
        previous_draft: 上一份草稿（`feedback` 非空时使用），用于把
            "改什么"锚定在"从什么改"上，避免每次修改意见都产出一份
            跟上次毫无关系的新草稿。
        settings: 创建向导里用户设置的 `options_count`/`time_granularity`
            （还没有 sim_id/manifest 时的临时字典，落盘后会原样存进
            `SimManifest.settings`，供之后每一步 `advance()` 沿用同一套
            配置）；为 None 时按 `resolve_hints()` 的默认值处理。
        data_dir: 跨模拟因果知识库（阶段二十，见 `knowledge_base.py`）
            所在的数据根目录；为 None 时（比如不关心历史积累的调用
            场景）跳过知识检索，`relevant_knowledge_hint` 退化为
            "暂无相关的已知因果知识"，不影响生成本身。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from world_simulator import knowledge_base

    wf, skill_name = _load_and_bind_skill(
        Path(workspace_root), "generate_scenario", template, "draft"
    )

    if data_dir is not None:
        try:
            relevant_knowledge_hint = knowledge_base.suggest_for_prompt(
                data_dir, f"{intent} {feedback}", template=template
            )
        except Exception:
            relevant_knowledge_hint = "（暂无相关的已知因果知识）"
    else:
        relevant_knowledge_hint = "（暂无相关的已知因果知识）"

    previous_draft_json = ""
    if previous_draft is not None:
        previous_draft_json = json.dumps(
            {
                "title": previous_draft.title,
                "summary": previous_draft.summary,
                "vars": previous_draft.vars,
                "options": [o.to_dict() for o in previous_draft.options],
            },
            ensure_ascii=False,
        )

    runner = WorkflowRunner(cfg)
    result = runner.run(
        wf,
        {
            "intent": intent,
            "feedback": feedback or "",
            "previous_draft_json": previous_draft_json,
            "calibration_notes": str((settings or {}).get("calibration_notes") or ""),
            "relevant_knowledge_hint": relevant_knowledge_hint,
            **resolve_hints(settings, stage="create"),
        },
    )

    if result.status != "done":
        failed = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        raise ScenarioGenerationError(
            f"generate_scenario workflow 执行未成功（skill={skill_name}）："
            f"status={result.status}；" + "；".join(failed)
        )

    draft_step = next((sr for sr in result.step_results if sr.step_id == "draft"), None)
    if draft_step is None or not draft_step.result_file:
        raise ScenarioGenerationError("draft 步骤未产出 result_file，无法解析提案草稿")

    data = json.loads(Path(draft_step.result_file).read_text(encoding="utf-8"))
    return ScenarioDraft.from_dict(data)
