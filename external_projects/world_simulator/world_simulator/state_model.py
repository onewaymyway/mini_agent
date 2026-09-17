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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChoiceOption":
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
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
