"""world_simulator/state_model.py — State 数据结构

设计依据：`next_doc/world_simulator_external_project_plan.md` 第 6 节。

不做成单一大而全的 schema，而是"基础字段（id/时间步/摘要）+ 模板私有
字段（自由 JSON）"的组合：基础字段是引擎（`engine.py`/`store.py`）唯一
关心的部分，模板私有字段（`vars`）的结构语义由对应的 skill
（`skills/<template>-template/SKILL.md`）定义，引擎本身不关心具体
字段含义，因此新增一种模拟类型只需要新增一个 skill + 对应的模板私有
字段约定，不需要改这里的代码。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChoiceOption:
    """引擎给出的一个候选分支选项（供用户/代理选择）。"""

    id: str
    label: str
    description: str = ""
    risk_level: Optional[str] = None
    """这个选项的风险等级（阶段三十二，`next_doc/
    world_simulator_realism_transparency_and_retrospective_roadmap_v3_
    plan.md` 4.1 节）：`"low"`/`"medium"`/`"high"` 之一，选填。不认识
    的取值（skill 输出了拼写错误或其它自由文本）统一归一化为
    `"medium"`（延续项目"不做伪精确"的一贯风格：宁可退到中间档，也
    不把无法识别的值原样展示成一个奇怪的标签）。`None` 表示 skill
    没有声明——展示层按"未声明"处理，不补一个假的默认值。
    """
    reversibility: Optional[str] = None
    """这个选项的可逆性：`"reversible"`（可逆）/`"hard_to_reverse"`
    （难以逆转）/`"irreversible"`（不可逆）之一，选填，`None` 表示
    未声明。不做归一化兜底（取值集合比 `risk_level` 更明确，无法
    识别的值原样保留，交给展示层判断是否已知取值）。
    """
    affected_lines: List[str] = field(default_factory=list)
    """这个选项预计会牵动的因果线 id 列表，引用
    `manifest.settings.causal_lines` 里声明的 `id`（选填，不做校验，
    纯粹是给展示层的引用提示，同 `SimState.causal_links[].line_id`
    的既有取舍）。默认空列表，表示未声明。
    """
    key_uncertainty: str = ""
    """这个选项最大的不确定性是什么（一句话，选填）。空字符串表示
    未声明。
    """

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChoiceOption":
        risk_level = data.get("risk_level")
        if risk_level is not None:
            risk_level = str(risk_level).strip().lower()
            if risk_level not in ("low", "medium", "high"):
                risk_level = "medium"
        reversibility = data.get("reversibility")
        if reversibility is not None:
            reversibility = str(reversibility).strip().lower() or None
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            risk_level=risk_level,
            reversibility=reversibility,
            affected_lines=[str(x) for x in (data.get("affected_lines") or [])],
            key_uncertainty=str(data.get("key_uncertainty", "") or ""),
        )


@dataclass
class SimState:
    """一次模拟推进产生的一个状态节点。

    - `step`：从 0 开始的时间步序号（0 = 初始状态，由
      `spec_generator` 生成，不经过 `engine.advance()`）。
    - `summary`：本状态的一句话摘要，供时间线/看板展示，不要求覆盖
      `vars` 的全部细节。
    - `narrative`：本步推进产生的叙事文本（初始状态可为空）。
    - `vars`：模板私有字段，自由 JSON，结构由对应 skill 定义。
    - `options`：本状态下可选的候选分支（用于下一步推进时选择）；
      为空表示引擎判断"无需用户选择，直接给出默认走向"。
    - `chosen_option_id` / `chosen_by` / `chosen_reason`：产生*下一个*
      状态时实际选择了哪个选项、由谁选的（`user` | `autopilot`）、
      选择理由（自动挡代选时由 LLM 给出）。记在被选择所在的那个状态节点
      上（而不是下一个状态上），这样"某一步为什么会走到下一状态"的
      依据始终和该步的候选项摆在一起，便于时间线回放。
    - `major_decision`：这一步推进产生*本状态*是否被 skill 判定为
      "人生重大转折点"（阶段四自动挡的 `review_mode:
      pause_on_major_decision` 用它决定要不要暂停自动推进，见
      `engine.py`/`autopilot.py`）；阶段一手动挡场景恒为 False，不影响
      任何行为。
    """

    step: int
    summary: str
    narrative: str = ""
    vars: Dict[str, Any] = field(default_factory=dict)
    options: List[ChoiceOption] = field(default_factory=list)
    chosen_option_id: Optional[str] = None
    chosen_by: Optional[str] = None
    chosen_reason: Optional[str] = None
    major_decision: bool = False
    time_label: str = ""
    """这个状态对应"模拟内经过了多长时间"的人类可读描述（比如
    `起点`/`3 个月后`/`第 2 年`），由 skill 按 `manifest.settings`
    里的时间粒度设置给出，engine 本身不解析/计算，原样存取、原样
    展示——粒度是"1 步 = 1 年"还是"1 步 = 1 周"完全由 skill 按提示
    决定，engine 不假设任何具体单位。留空表示 skill 没给（旧数据/
    旧 skill 版本），展示层按"未知时间跨度"处理，不强行补一个假值。
    """
    time_granularity: str = ""
    """产生*本状态*这一步实际使用的时间粒度（比如 `1 个月`/`1 年`/
    `一轮谈判`），与 `time_label`（累计的时间点，如"第 3 年"）是两个
    不同维度：`time_label` 回答"现在是什么时候"，这个字段回答"刚才
    这一步跨越了多长/什么性质的时间"。

    存在的意义：`settings.time_granularity_mode == "auto"/"guided"`
    时，粒度不再是全实例固定的一个值，同一次模拟里可能"日常年度推进，
    遇到谈判时切到按轮次推进"——如果不单独记录每一步实际用的粒度，
    时间线上就没法直观看出"这里节奏变了"，也没法把"上一步用的粒度"
    喂给下一步作为延续性参考（见 `engine.py::advance()`）。

    `state0`（初始状态）由 `spec_generator` 给出的是这次模拟的*起始
    基准粒度*；之后每一步 `advance()` 默认延续上一步的值，除非 skill
    判断需要切换并给出新值（见 `granularity_changed`/`granularity_reason`）。
    留空表示 skill 没给（旧数据/旧 skill 版本/`fixed` 模式下始终不变），
    展示层按"未知/沿用设置"处理。
    """
    granularity_changed: bool = False
    """这一步用的 `time_granularity` 是否与上一步不同——由 engine 在
    落盘时对比算出（不依赖 skill 自报，避免不一致），仅用于时间线
    展示时高亮"这里切换了节奏"，不影响任何推进逻辑。`state0` 恒为
    False（没有"上一步"可比较）。
    """
    granularity_reason: Optional[str] = None
    """`granularity_changed` 为 True 时，skill 给出的切换理由（一两句
    话，比如"进入谈判环节，改为按轮次推进"）；未变化或旧数据没有则为
    None——只有变化时才要求这个字段，减少无意义的输出负担。
    """
    resource_violations: List[Dict[str, Any]] = field(default_factory=list)
    """产生*本状态*这一步，`engine.py::advance()` 对
    `manifest.settings.resource_fields` 里声明的资源类字段做下限校验时
    发现并纠正的越界项（阶段九，`next_doc/
    world_simulator_universal_world_model_upgrade_plan.md` 4.1 节）。

    每一项形如 `{"field": "cash", "llm_value": -500, "clamped_value": 0}`：
    `field` 是字段路径（支持一层嵌套，如 `resources.amount`），
    `llm_value` 是 skill 原始给出的值，`clamped_value` 是被夹到下限后
    实际落盘到 `vars` 里的值。**不代表这一步推进被拒绝**——engine 只是
    把越界的数值纠正到下限（默认 0），推进本身照常发生，这里只是留痕，
    供时间线展示"系统纠正了一处不合理的数值"，保持透明而不是静默篡改。

    默认空列表：`resource_fields` 未声明、这一步没有产生任何越界值时都是
    空列表，不影响旧数据/其它模板的行为（向后兼容）。
    """
    uncertain_fields: List[Dict[str, str]] = field(default_factory=list)
    """产生*本状态*这一步，skill 主动标注的"本质是主观估计、置信度不高"
    的字段（阶段十一，`next_doc/
    world_simulator_universal_world_model_upgrade_plan.md` 4.3 节）。

    每一项形如 `{"field": "startup_success_rate", "confidence": "low",
    "note": "基于同类创业项目的粗略经验判断"}`：`field` 是 `vars` 里的
    字段路径（不强制嵌套格式，展示层按原样匹配/展示，不像
    `resource_violations` 那样要求可执行的读写路径），`confidence` 只分
    `high`/`medium`/`low` 三档（不追求精确概率，避免"伪精确"本身重演），
    `note` 是一句话说明（可选，可以为空字符串）。

    不是对全量字段做标注——只有 skill 主动认为"这个字段本质是主观推断、
    用户不该无条件相信"时才会出现在这里，比如"创业成功率"这类数字；
    `age` 这类确定性字段不受影响，也不会被过度标注。默认空列表：skill
    没给出、旧数据、`generate_scenario`/`advance_step` 都可以为空——
    不影响任何已有行为，向后兼容。
    """
    key_drivers: List[str] = field(default_factory=list)
    """产生*本状态*这一步，skill 可选给出的"划重点"短语列表（阶段
    十三，`next_doc/world_simulator_universal_world_model_upgrade_plan.md`
    4.5 节，最小版因果摘要），比如 `["市场需求超预期", "现金储备见底
    被迫收缩"]`。1~3 条短语，不是完整的可点击因果调试器（那个投入
    产出比在现阶段太低，见演进计划第 6 节"本次不做的事"），只是给
    `narrative` 补一个比逐字读叙事更快抓住关键信息的结构化摘要。

    默认空列表：skill 没给出、旧数据、`state0`（初始状态一般没有"这一
    步的驱动因素"这个概念）都可以为空，不影响任何已有行为。
    """
    relation_violations: List[Dict[str, Any]] = field(default_factory=list)
    """产生*本状态*这一步，`engine.py::advance()` 对
    `manifest.settings.resource_relations` 里声明的 `transfer`（转移）
    关系做一致性检查时发现的不守恒项（阶段十六，`next_doc/
    world_simulator_universal_world_model_upgrade_plan.md` 4.8 节）。

    每一项形如 `{"from": "cash", "to": "inventory.value",
    "delta_from": -100, "delta_to": 20}`：`from`/`to` 是声明的字段路径，
    `delta_from`/`delta_to` 是这一步这两个字段各自的变化量（`next_vars`
    减去 `current_vars`）。**只是不一致提示，不修改任何数值**——和
    `resource_violations`（下限校验）不同，转移关系没有"应该是多少"的
    唯一正确答案，engine 没法像下限那样直接夹值，只能留痕供用户判断
    "这一步的资源转移不太守恒，可能是 AI 算错了或者有未说明的损耗"。

    默认空列表：`resource_relations` 未声明、这一步没有超出容差时都是
    空列表，不影响旧数据/其它模板的行为（向后兼容）。
    """
    background_entities_applied: List[str] = field(default_factory=list)
    """产生*本状态*这一步，`engine.py::advance()` 按
    `manifest.settings.background_entities` 声明、用简单线性趋势外推
    强制覆盖（而不是采纳 LLM 输出）的主体名字列表（Hierarchical
    Agent，4.10 节设计草案第一步）。默认空列表：`hierarchical_agent_
    mode`/`background_entities` 未声明、或这一步没有可外推的背景
    角色时都是空列表，不影响旧数据/其它模板的行为（向后兼容）。
    """
    causal_links: List[Dict[str, Any]] = field(default_factory=list)
    """产生*本状态*这一步，skill 可选给出的"划重点 + 具体影响"结构化
    摘要（阶段十五，`next_doc/world_simulator_universal_world_model_
    upgrade_plan.md` 4.7 节，Evidence Chain 进阶），是 `key_drivers`
    的可选进阶信息，把短语和"具体受影响的字段"、"具体的影响后果"关联
    起来。

    每一项形如 `{"driver": "现金储备见底", "affected_fields": ["cash",
    "stage"], "effect": "被迫从「自由职业」转为「求稳定工作」"}`：
    `driver` 通常对应 `key_drivers` 里的某一条短语（不强制一一对应，
    展示层按原样匹配即可）、`affected_fields` 是 `vars` 里受影响的
    字段名列表（自由文本，不要求是可执行的读写路径，`engine.py` 不
    对这里的字段名做任何解析/校验）、`effect` 是一句话说明具体后果。

    仍然是自由文本 + 字段名列表，不是可执行的因果图（完整的可点击
    因果调试器/图结构投入产出比依然偏低，见演进计划第 6 节）。
    `key_drivers` 与 `causal_links` 可以同时输出，也可以只输出
    `key_drivers`——`causal_links` 是给"想深入看一步"的用户的可选
    进阶信息，不强制每次都给。默认空列表：skill 没给出、旧数据都
    可以为空，不影响任何已有行为，向后兼容。

    每一项额外支持一个可选字段 `line_id`（阶段二十二，4.13 节），把
    这条因果链关联到 `manifest.settings.causal_lines` 声明的某条
    具体因果线上，供阶段二十五的"因果线 UI"按线筛选展示。不填表示
    "未归属到具体线，按旧行为展示"——`causal_lines` 未声明（多数
    既有实例）时所有 `causal_links` 都没有这个字段，完全向后兼容；
    `engine.py` 不对这里的 `line_id` 做任何校验（不检查是否真的在
    `causal_lines` 里声明过），纯粹是展示层的过滤依据。

    每一项再额外支持两个可选字段（阶段二十七，`next_doc/
    world_simulator_universal_simulator_gap_analysis_and_roadmap_v2_
    plan.md` 4.19 节，因果线耦合结构化）：

    - `relation_type`：`"one_way"`（单向，默认）/`"two_way"`（双向）/
      `"indirect"`（间接，通过中间因果线传导）/`"feedback_loop"`
      （反馈循环）之一，对应参考文档第九节的四种因果线关系。不填
      按 `"one_way"` 处理（展示层兜底，`state_model.py` 本身不做
      默认值填充，保持"未给出即为未知"的语义）。
    - `source_line_id`：这条影响关系的"发起线"，和 `line_id`（表示
      "影响落到哪条线/哪个归属"）配合，才能表达"从 A 线影响到 B
      线"；不填表示同线内部关系或发起线不明确。

    这两个字段和 `line_id` 一样，完全自由文本、不做任何校验，只是
    供 `causal_graph.py::build_causal_graph()` 做展示层的聚合统计，
    不改变 `causal_links` 本身"自由文本 + 字段名列表"的定位（仍然
    不是可执行的因果图）。
    """
    line_updates: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """产生*本状态*这一步，skill 按 `manifest.settings.causal_lines`
    声明自行判断"这一步哪些因果线实际推进了"的记录（阶段二十二，
    `next_doc/world_simulator_toward_universal_simulator_plan.md`
    4.13 节，多尺度因果线）。

    key 是 `causal_lines` 里声明的线 `id`，value 形如
    `{"time_label": "第 3 年", "summary": "AI 成本持续下降",
    "advanced": true}`：`time_label` 是这条线推进到的时间点（人类
    可读，和 `SimState.time_label` 是同一个维度但各自独立累计）、
    `summary` 是这一步这条线上发生了什么的一句话摘要、`advanced`
    标记这一步这条线是否真的往前走了（`false` 表示这条线这一步
    按兵不动，比如"经济线"这一步不需要更新——允许 value 里没有这
    个字段，缺省按 `True` 处理，因为出现在 `line_updates` 里通常就
    意味着"有动静"）。

    `engine.py` 只负责原样落盘 skill 给出的内容，不做任何调度决策——
    "这一步该更新哪些线、推进到哪"完全由 skill 自行判断（延续
    "LLM 负责推理，engine 负责编排"的既有分工，同
    `background_entities_applied` 的取舍）。默认空字典：
    `causal_lines` 未声明、skill 没给出、旧数据都可以为空，不影响
    任何已有行为，向后兼容。
    """

    structural_change: Optional[Dict[str, Any]] = None
    """产生*本状态*这一步，skill 可选给出的"模型结构性变化"报告
    （阶段二十三，`next_doc/world_simulator_toward_universal_simulator_
    plan.md` 4.14 节，Model Regime Detection / Emergence）。

    形如 `{"detected": true, "kind": "new_entity" | "new_mechanism" |
    "regime_shift", "description": "谈判各方之间形成了稳定的联盟
    结构", "proposed_fields": {...}, "accepted": false,
    "accepted_at": null}`：

    - `detected`：是否检测到结构性变化，恒为 `true`（字段存在即代表
      检测到；`None` 表示这一步没有输出，二者含义相同，只是为了
      兼容 skill 可能显式给出 `false` 的写法，展示层一律按
      `structural_change is not None and structural_change.get(
      "detected", True)` 判断）。
    - `kind`：`new_entity`（新实体）/`new_mechanism`（新机制）/
      `regime_shift`（整体运行规则切换）三选一。
    - `description`：一句话描述这个新结构是什么。
    - `proposed_fields`：skill 建议固化的具体字段内容（自由 JSON，
      结构由 `kind` 决定，`engine.py` 不解析其内部结构）。
    - `accepted`：这条结构性变化是否已被用户在详情页手动"采纳"
      （见 `engine.py::apply_structural_change()`）。**engine 不会
      自动把这里的内容写入 `manifest.settings`**——只有用户主动
      确认后，`apply_structural_change()` 才会修改
      `manifest.settings` 并把这个字段置为 `True`，这是"先发现
      展示，不自主决定并改写"的保守设计（见演进计划 4.14 节风险
      提示）。
    - `accepted_at`：采纳时间（ISO 字符串），未采纳为 `None`。

    默认 `None`：skill 没给出、旧数据、大多数步骤都是 `None`
    （只在"出现原模型没有预期的稳定新结构"时才输出，延续
    `granularity_changed`/`resource_violations` 这类"只在发生时才
    出现"字段的既有设计取舍），不影响任何已有行为，向后兼容。
    """

    tree_updates: List[Dict[str, Any]] = field(default_factory=list)
    """产生*本状态*这一步，对某条因果线的"未来树"（`manifest.settings.
    causal_lines[].future_tree`，见 `world_simulator/causal_tree.py`）
    实际生效的修正摘要（阶段二十六，`next_doc/
    world_simulator_causal_line_future_tree_plan.md`）。

    每一项形如 `{"line_id": "tech_line", "confirmed_branch":
    "tech_fast", "pruned_branches": ["tech_slow"], "new_branch_ids":
    ["tech_pivot"]}`：`confirmed_branch` 是这一步印证的分支 id（可能
    为空字符串，表示这一步没有印证任何分支）、`pruned_branches` 是
    这一步被排除的分支 id 列表、`new_branch_ids` 是这一步新长出的
    分支 id 列表——具体的分支内容（描述/likelihood/status）落在
    `manifest.settings.causal_lines` 上，这里只留一份"这一步动了
    哪条线、动了什么"的审计摘要，供时间线展示"这一步修正了未来树"，
    不重复存储分支全文。

    由 `engine.py::_apply_tree_updates()` 在合并 `advance_step` 可选
    输出的 `tree_updates` 后生成，engine 本身不做任何判断（哪个分支
    该确认/排除完全由 skill 输出决定，engine 只负责合并落盘，同
    `line_updates` 的既有分工）。默认空列表：skill 没给出、这一步
    没有任何有效的树修正、旧数据都可以为空，不影响任何已有行为，
    向后兼容。
    """

    field_provenance: Dict[str, str] = field(default_factory=dict)
    """初始状态（`state0`）里每个顶层字段的来源标注（阶段三十二，
    `next_doc/world_simulator_realism_transparency_and_retrospective_
    roadmap_v3_plan.md` 4.2 节）：key 是 `vars` 的顶层字段名，value
    是 `"fact"`（用户在意图描述里明确提到）/`"assumption"`（系统给的
    合理默认值）/`"inference"`（系统从上下文推断）/`"unknown"`（缺失、
    先给占位值）之一。

    只在 `generate_scenario` 生成 `state0` 时有意义，`advance_step`
    产生的后续状态不强制维护（世界持续演化，"最初是不是用户说的"这个
    标签的价值主要在创建时）；后续步骤的 `SimState` 这个字段一律为空
    字典。默认空字典：`generate_scenario` 未输出、旧数据都可以为空，
    展示层按"没有来源标注"处理，不影响任何已有行为，向后兼容。
    """

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["options"] = [o.to_dict() if isinstance(o, ChoiceOption) else o for o in self.options]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimState":
        options_raw = data.get("options") or []
        options = [
            o if isinstance(o, ChoiceOption) else ChoiceOption.from_dict(o)
            for o in options_raw
        ]
        return cls(
            step=int(data.get("step", 0)),
            summary=str(data.get("summary", "")),
            narrative=str(data.get("narrative", "")),
            vars=dict(data.get("vars") or {}),
            options=options,
            chosen_option_id=data.get("chosen_option_id"),
            chosen_by=data.get("chosen_by"),
            chosen_reason=data.get("chosen_reason"),
            major_decision=bool(data.get("major_decision", False)),
            time_label=str(data.get("time_label", "") or ""),
            time_granularity=str(data.get("time_granularity", "") or ""),
            granularity_changed=bool(data.get("granularity_changed", False)),
            granularity_reason=data.get("granularity_reason"),
            resource_violations=list(data.get("resource_violations") or []),
            uncertain_fields=list(data.get("uncertain_fields") or []),
            key_drivers=[str(x) for x in (data.get("key_drivers") or [])],
            causal_links=[
                dict(x) for x in (data.get("causal_links") or []) if isinstance(x, dict)
            ],
            relation_violations=[
                dict(x) for x in (data.get("relation_violations") or []) if isinstance(x, dict)
            ],
            background_entities_applied=[
                str(x) for x in (data.get("background_entities_applied") or [])
            ],
            line_updates={
                str(k): dict(v) for k, v in (data.get("line_updates") or {}).items()
                if isinstance(v, dict)
            },
            tree_updates=[
                dict(x) for x in (data.get("tree_updates") or []) if isinstance(x, dict)
            ],
            structural_change=(
                dict(data["structural_change"])
                if isinstance(data.get("structural_change"), dict)
                else None
            ),
            field_provenance={
                str(k): str(v) for k, v in (data.get("field_provenance") or {}).items()
            },
        )


@dataclass
class SimManifest:
    """一个模拟实例的元信息（对应 `data/<sim_id>/manifest.json`）。

    阶段一只落地"手动挡 + 进行中/已暂停/已结束"这三个最基础的字段；
    `pilot_mode` / `autopilot` 结构在第 4.1 节已有定义，阶段一先把字段
    预留出来（默认手动挡），具体的代理决策逻辑留给阶段四实现，避免
    阶段一为了"字段将来要用"而引入还没有消费方的复杂行为。
    """

    sim_id: str
    template: str
    intent: str
    title: str
    created_at: str
    updated_at: str
    status: str = "active"  # active | paused | ended
    pilot_mode: str = "manual"  # manual | autopilot
    autopilot: Dict[str, Any] = field(default_factory=dict)
    current_step: int = 0
    branch: str = "main"
    settings: Dict[str, Any] = field(default_factory=dict)
    """模拟级别的可配置项，自由 JSON，引擎不关心具体字段含义（同
    `SimState.vars` 的设计取舍），当前约定使用以下字段（见
    `spec_generator.py::resolve_hints()`）：
    - `options_count`：整数，每一步希望给出的候选方向数量（生成初始
      状态和每一步推进都用同一个值），用户在创建向导里设置，未设置时
      沿用 skill 里写的默认建议。
    - `time_granularity_mode`：字符串，`"fixed"` | `"auto"` | `"guided"`，
      决定"每一步的时间粒度怎么定"：
        - `fixed`：全实例固定用 `time_granularity` 这一个值（原有行为）。
        - `auto`（未设置时的默认值）：完全交给 skill 按情境自行判断，
          允许同一次模拟里出现多种粒度（比如日常按年推进，进入谈判时
          切到按轮次推进），每一步实际用的粒度记在
          `SimState.time_granularity` 上。
        - `guided`：介于两者之间，`time_granularity_guide` 给一段
          "偏好/基准"引导语（不是精确值），skill 仍自主判断但会参考
          这个偏好。
    - `time_granularity`：字符串，`fixed` 模式下每一步固定使用的粒度，
      比如 `"1 个月"`/`"1 年"`；其余模式下不使用这个字段。
    - `time_granularity_guide`：字符串，`guided` 模式下的偏好引导语，
      比如"日常按季度推进，遇到谈判/冲突等关键场景可以细到按轮次"；
      不是引擎解析的结构化值，只是原样喂给 skill 的一句话提示。
    - `resource_fields`：列表，声明 `vars` 里哪些字段是"资源类数值
      字段"（阶段九，`next_doc/
      world_simulator_universal_world_model_upgrade_plan.md` 4.1 节）。
      每一项要么是一个字段名字符串（如 `"cash"`，下限默认 0），要么是
      `{"field": "resources.amount", "min": 0}` 这种带自定义下限的字典
      （字段名支持一层嵌套路径，用 `.` 分隔）。`engine.advance()` 每次
      落盘 `next_vars` 前会对这里声明的每个字段做下限检查：低于下限时
      夹到下限并记入 `SimState.resource_violations`，**不拒绝这次
      推进**。留空（默认）表示不做任何校验，行为与未引入这个功能之前
      完全一致，向后兼容。由 `generate_scenario` 阶段的 skill 在生成
      初始 `vars` 时给出建议值（见 `spec_generator.ScenarioDraft.
      resource_fields`），用户在创建向导里可以看到并编辑，也可以在
      详情页的"模拟设置"里随时增删。
    - `objectives`：列表，声明这次模拟"主要关心的指标"（阶段十二，
      `next_doc/world_simulator_universal_world_model_upgrade_plan.md`
      4.4 节 Problem Compiler 雏形；阶段十四，4.6 节目标驱动排序）。
      每一项支持两种写法：
      - 纯字符串（阶段十二行为，如 `"资产净值"`），不要求是 `vars`
        里的精确字段路径，允许是一句话描述——只用于"给人看、给「对比
        实验」页面的关注字段做默认值参考"，不参与任何排序；
      - 结构化字典（阶段十四新增，向后兼容）：`{"label": "资产净值",
        "field": "resources.cash", "direction": "max"}`，`field` 是
        `vars` 里的精确字段路径（嵌套用 `.` 分隔），声明后「对比实验」
        页面会出现"按关注指标排序"的辅助展示区（见
        `world_simulator.analysis.rank_by_objectives()`）；`direction`
        只能是 `"max"`/`"min"`，缺省 `"max"`；`field` 留空则等价于
        纯字符串写法，只展示不参与排序。
      由 `generate_scenario` 阶段的 skill 给出建议值（见
      `spec_generator.ScenarioDraft.objectives`，skill 目前仍只输出
      纯字符串，结构化写法主要靠用户在创建向导"高级"折叠区手动声明），
      用户在创建向导里可以看到并编辑，也可以在详情页"模拟设置"里随时
      增删。留空（默认）表示没有声明，不影响任何已有行为——引擎本身
      不会因为声明或不声明这个字段而改变任何推进/校验逻辑，纯粹是
      "记录这次模拟想优化什么"；排序结果始终"仅供参考"，不代表系统
      认定的最优解，最终判断权留给用户。
    - `resource_relations`：列表，声明 `vars` 里资源字段之间的"转移"
      关系（阶段十六，`next_doc/world_simulator_universal_world_model_
      upgrade_plan.md` 4.8 节，通用规则引擎的最小可行版本）。每一项
      形如 `{"type": "transfer", "from": "cash", "to":
      "inventory.value", "tolerance": 0.1}`：`from`/`to` 是 `vars` 里
      的字段路径（支持一层嵌套，同 `resource_fields`），`tolerance` 是
      允许的相对误差比例（默认 0.1，即允许 10% 的"汇率损耗/交易成本"
      之类的合理偏差，不强制精确守恒）。**只做 `transfer`（转移，两个
      字段的变化量应大致相反）这一种关系类型**，`production`（生产/
      持续产出）暂不支持。`engine.advance()` 每次落盘 `next_vars` 前
      会对这里声明的每条关系做一致性检查：**不拒绝推进、不修改任何
      数值**（和 `resource_fields` 的下限校验不同，这里没有"应该是
      多少"的唯一正确答案），不一致时记入
      `SimState.relation_violations` 供时间线展示"不一致提示"。留空
      （默认）表示不做任何检查，行为与引入这个功能之前完全一致，向后
      兼容。由 `generate_scenario` 阶段的 skill 在生成初始 `vars` 时
      给出建议值（见 `spec_generator.ScenarioDraft.resource_relations`，
      写法同 `resource_fields`），用户在创建向导里可以看到并编辑，也
      可以在详情页"模拟设置"里随时增删。
    - `multi_entity_mode`：布尔值（默认 `False`），声明这次模拟是否
      采用"多主体私有信念"结构（阶段十七，`next_doc/
      world_simulator_universal_world_model_upgrade_plan.md` 4.9 节，
      Belief 与 State 分离 + Entity/Relationship 图结构的最小可行
      版本）。为 `True` 时，`vars` 的顶层按约定组织成
      `entities: Dict[str, Dict[str, Any]]`（每个主体一个 id，值是这个
      主体自己的私有 `vars`——比如"甲方对乙方底线的猜测"这类信息只出现
      在甲方自己的私有 `vars` 里）+ `shared_vars: Dict[str, Any]`（所有
      主体共享的公开信息，比如"当前谈判轮次"）。这是最小化的"Entity +
      私有 Belief"结构，**不是**完整的关系图数据库——不单独建模
      `Relationship`，主体之间的关系仍靠 `narrative` 自由文本表达；
      `advance_step` 仍然只是一次 LLM 调用（不会为每个主体单独调用一次，
      那样会成倍增加成本、也会割裂"大家在同一场景互动"的上下文），只是
      要求单次输出里区分"谁知道什么"。引擎本身（`engine.py`）不解析/
      不校验 `entities`/`shared_vars` 的具体结构，`vars` 对引擎而言
      始终是不透明的自由 JSON（同 `vars` 的一贯设计），这个约定完全由
      对应 skill（`skills/negotiation-template/SKILL.md`）和 `app.py`
      的展示逻辑消费。留空（默认 `False`）表示不启用，行为与引入这个
      功能之前完全一致，向后兼容——`life_sim`/`group_evolution` 两个
      既有模板都不需要这个能力，是否要开启完全由用户在创建向导/详情页
      "模拟设置"里选择（选择「多方谈判」模板时创建向导会自动带上
      `True`）。
    - `hierarchical_agent_mode`：布尔值（默认 `False`），声明这次
      模拟是否启用"分层认知调度"的第一步——把 `entities` 拆成"关键
      角色"（走完整 `advance_step` LLM 推理）和"背景角色"（用简单规则
      外推，不消耗额外 LLM 推理精度）（Hierarchical Agent / Dynamic
      Cognition Router，`next_doc/world_simulator_universal_world_
      model_upgrade_plan.md` 4.10 节设计草案的第一步，**不是**完整
      的"认知路由器"架构）。只有 `multi_entity_mode` 同时为 `True`
      且 `vars` 里确实有 `entities` 结构时才有意义，否则是空操作。
      需要配合 `background_entities` 一起声明才会实际生效。
    - `background_entities`：字符串数组（默认 `[]`），配合
      `hierarchical_agent_mode` 使用，列出 `vars.entities` 里应该被
      当成"背景角色"处理的主体名字（必须与 `entities` 的键完全一致）。
      `engine.advance()` 落盘前会用**简单线性趋势外推**（取这个主体
      上一步到这一步的每个数值字段变化量，按相同变化量再推一步；
      没有上一步可参考、或字段不是数值类型时原样保留这一步的值，
      不外推非数值字段）**强制覆盖** LLM 在这一步给这些主体的输出，
      不管 LLM 实际给了什么值——这是"不消耗额外 LLM 推理精度"这句话
      的真正含义：即使当前实现仍然只发起一次 LLM 调用（没有从
      prompt 里物理排除背景角色的输入/输出，那需要更复杂的动态
      prompt 拼装，这里没有做，见 4.10 节"实施记录"里的已知限制），
      但落盘的数值**必然**来自规则外推，不采纳 LLM 的推理结果，
      对这些主体而言"LLM 有没有认真推理"不影响最终状态，为将来
      "把背景角色从 prompt 里物理剔除"的进一步优化留了空间。哪些
      主体被处理、外推了哪些字段记入
      `SimState.background_entities_applied` 供时间线展示。留空
      （默认）表示不启用，行为与引入这个功能之前完全一致，向后兼容。
    - `calibration_notes`：字符串（默认空字符串），Reality Sync 轻量版
      （阶段十八，`next_doc/world_simulator_universal_world_model_
      upgrade_plan.md` 4.11 节）——用户手动填入的"真实世界参考信息"
      （比如"参考：2024 年国内一线城市应届硕士平均起薪 1.2~1.8 万/
      月"），原样喂给 `generate_scenario`/`advance_step` 的 prompt，
      要求 skill "参考但不照抄"这份信息。**不做任何自动数据抓取/
      更新**——是否采信、怎么融入完全由 LLM 判断，系统只负责原样
      传递这段文本，不做任何数值层面的强制校准，也不会验证这段文本
      的真实性。留空（默认）表示不提供任何参考信息，行为与引入这个
      功能之前完全一致，向后兼容。
    - `causal_lines`：列表，声明这次模拟里存在的"因果线"（阶段
      二十二，`next_doc/world_simulator_toward_universal_simulator_
      plan.md` 4.13 节，多尺度因果线：Causal Line 成为一等公民）。
      每一项形如 `{"id": "tech_line", "label": "技术线",
      "time_granularity": "年"}`：`id` 是这条线的唯一标识（供
      `SimState.line_updates`/`causal_links.line_id` 引用）、`label`
      是给用户看的中文名、`time_granularity` 是这条线大致的节奏
      描述（自由文本，不是引擎解析的结构化值，只是给 skill 的提示，
      同一条线实际推进时仍然可以按情境灵活给出更具体的 `time_label`）。
      留空（默认空列表）表示不启用"多因果线"这个视角——这次模拟仍然
      沿用单一 `time_granularity` 的既有行为，`SimState.line_updates`
      恒为空字典，完全向后兼容。由 `generate_scenario` 阶段的 skill
      按需给出建议值（见 `spec_generator.ScenarioDraft.causal_lines`），
      用户在创建向导里可以看到并编辑，也可以在详情页"模拟设置"里
      随时增删。**不引入因果线之间的调度器**（比如"技术线每 5 步才
      推进一次"这种强制节奏控制）——每一步该更新哪些线完全由 skill
      自行判断，engine 不做任何强约束，只负责落盘 `line_updates`。

      每一项还支持一个可选字段 `advance_every_n_steps`（阶段三十一，
      `next_doc/world_simulator_universal_simulator_gap_analysis_and_
      roadmap_v2_plan.md` 4.20 节，多尺度因果线的最小可行版本）：
      整数，默认 1（每步都可能有动静）。**不是强制约束**——只是
      `spec_generator._resolve_causal_lines_hint()` 据此算出"这一步
      哪些线预期会有动静、哪些线大概率维持不变"的提示信息喂给
      skill 参考，skill 仍然可以在任何一步更新任何线（避免"错误
      估计节奏导致该更新的线被拦住"）；`app.py` 因果线总览对节奏
      慢的线用更稀疏的视觉密度展示。`engine.py` 不对 `line_updates`
      做任何按节奏的强制过滤/校验。留空/不填按 1 处理，不影响任何
      已有行为，向后兼容。

      每一项额外支持一个可选字段 `future_tree`（阶段二十六，
      `next_doc/world_simulator_causal_line_future_tree_plan.md`，
      因果线的"未来因果树"）：`{"as_of_step": 0, "branches": [
      {"id": "tech_fast", "description": "AI 成本快速下降",
      "likelihood": "medium", "status": "open", "children": []}]}`
      ——`branches` 是这条线从"当前节点"出发的若干可能分支（不是只有
      一条延续路径），`status` 只分 `open`/`confirmed`/`diverged`/
      `pruned` 四档，不做概率归一化（同 `confidence: high/medium/
      low` 一以贯之的克制风格）；`children` 可选，支持多层分叉，但
      不强制。**这个字段不需要用户/skill 手动保证一定存在**——
      `materialize_simulation()` 落盘 step 0 之前会统一调用
      `world_simulator.causal_tree.ensure_future_trees()` 兜底：
      不管创建向导有没有手填、skill 有没有认真输出，每条因果线落盘
      时都保证有一棵合法的未来树（没有则用通用兜底模板生成），且
      因果线列表本身为空时也会兜底生成一条"主线"——这是"创建模拟
      就应该有核心因果线和未来因果树"这一要求的落地点，不再要求
      "先推进一步才看得到因果线"。推进过程中 `advance_step` 可选
      输出 `tree_updates`，由 `engine.py::_apply_tree_updates()`
      合并进对应线的 `future_tree`（标记某分支"已印证"/"已排除"、
      或追加一个原树上没有的新分支），合并结果记入
      `SimState.tree_updates` 供审计；用户也可以在详情页直接点击
      "标记为已印证/已排除"手动修正（`causal_tree.
      set_branch_status()`），不需要等 skill 输出。
    - `relationships`：列表，声明主体（`vars.entities` 的键，或因果线
      `id`）之间的关系（阶段三十一，`next_doc/
      world_simulator_universal_simulator_gap_analysis_and_roadmap_v2_
      plan.md` 4.24 节**最小起步版**——把关系从纯自由文本升级为一份
      可独立查看的结构化列表，**不是**参考文档设想的完整 Influence
      Field/Relationship 图（没有影响半径、传播路径、时间延迟、
      自动推进逻辑，见 `world_simulator/relationship.py` 模块开头的
      范围说明）。每一项形如 `{"from": "甲方", "to": "乙方", "kind":
      "rival", "strength": "high", "note": "对同一块市场份额有直接
      竞争"}`：`from`/`to` 是主体名字（自由文本，不校验是否真的在
      `vars.entities` 里存在）、`kind` 取
      `ally`/`rival`/`dependency`/`authority`/`other` 之一（不认识的
      值归一化为 `other`）、`strength` 取 `high`/`medium`/`low`
      （默认 `medium`，延续"不做伪精确"的一贯风格）、`note` 是一句话
      说明。`world_simulator.relationship.normalize_relationships()`
      负责清洗/归一化，`app.py` 在"模拟设置"里提供 JSON 编辑入口，
      不会推进任何传播计算，也不影响 `advance()` 的任何校验逻辑。
      留空（默认空列表）表示不声明任何关系，行为与引入这个字段之前
      完全一致，向后兼容。
    - `observer_mode`：布尔值（默认 `False`），"世界独立演化"的最小
      实验开关（阶段三十一，4.25 节**最小起步版**，不是参考文档设想
      的完整 Observer View 双视角架构——没有独立的推进循环，仍然是
      `batch_advance_daily`/自动挡代理发起的同步推进，只是多喂一段
      prompt 提示）。为 `True` 时，`autopilot._build_decision_context()`
      会在自动挡决策画像里追加一段提示，要求这一步优先让背景/宏观
      因果线自然演化，尽量不产生需要立刻打断、要求用户当下做出
      "人生重大决策"的新分支（**不强制**——同其它 hint 类字段一样，
      仍然由 skill 自行判断，不是代码层面的硬约束）。只在
      `pilot_mode == "autopilot"` 时才有实际效果，手动挡下这个字段
      是空操作。留空（默认 `False`）表示不启用，行为与引入这个字段
      之前完全一致，向后兼容。

    **字段分组索引**（阶段三十一，4.18 节末尾遗留的评估项——`settings`
    字段数量持续增加带来的复杂度负担，这里只做"分组索引"这种低风险
    的可读性改进，不做拆分成多个子对象之类的破坏性重构）：
    - 节奏/候选：`options_count`、`time_granularity_mode`、
      `time_granularity`、`time_granularity_guide`
    - 资源与守恒：`resource_fields`、`resource_relations`
    - 目标与归因：`objectives`
    - 多主体：`multi_entity_mode`、`hierarchical_agent_mode`、
      `background_entities`、`relationships`
    - 因果线：`causal_lines`、`suggested_causal_lines`
    - 校准与结构演化：`calibration_notes`、`confirmed_structural_changes`
    - 世界独立演化（实验性）：`observer_mode`
    """

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimManifest":
        return cls(
            sim_id=str(data["sim_id"]),
            template=str(data.get("template", "")),
            intent=str(data.get("intent", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            status=str(data.get("status", "active")),
            pilot_mode=str(data.get("pilot_mode", "manual")),
            autopilot=dict(data.get("autopilot") or {}),
            current_step=int(data.get("current_step", 0)),
            branch=str(data.get("branch", "main")),
            settings=dict(data.get("settings") or {}),
        )
