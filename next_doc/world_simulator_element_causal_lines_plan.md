# world_simulator 改进计划（第二十三轮）：因果线从"领域级"升级为"元素级"，并在模拟中持续发现新元素

> **状态（2026-10-03）：方案已确认，尚未实施。** 实施按 §6 分期（E1–E6）一个阶段一个阶段执行，
> 每阶段完成后更新相关文档并打包修改/新增文件（保持目录结构，可直接覆盖）。
> 本文只依据阅读代码得出结论，没有跑过真实 LLM，也没有运行时复现；每条"现状"都标了代码位置，
> "推断"会明说。
>
> 相关前置文档：`world_simulator_causal_line_future_tree_plan.md`（未来树，阶段二十六）、
> `world_simulator_realism_tech_and_causal_engine_plan.md`（技术模型/因果引擎，第二十二轮）、
> `world_simulator_event_driven_engine_and_full_architecture_plan.md`（独立推进，第三批）。

## 0. 一页纸结论

现在的因果线是**领域级**的：创建时 prompt 要求规划"2~4 条核心因果线"（`spec_generator.
_resolve_causal_lines_hint(stage="create")`），于是 `tech`/`finance`/`health`/`macro` 这样的线各自
背着一棵 `future_tree`、一条 `line_updates`。但 `tech` 里可能有十几个技术和项目，它们的发展节奏、
前置依赖、分叉可能性各不相同，被压成一条线之后：

1. 一棵树只能写几个笼统分支，具体技术/项目的分叉走向没处放；
2. 因果边只能连到"整条技术线"，看不出到底是哪个技术影响了哪个项目；
3. 模拟中冒出来的新关键对象，只有 LLM 碰巧给它起了新 line_id 才会被登记，且登记时 `label`=id、
   未来树套通用三分支模板（`causal_tree.auto_register_lines`），等于"登记了但没建模"；
4. 技术有一套独立的元素级模型（`tech_model.py`，`tech_state.nodes`），其它任何类型的元素（项目、
   公司、政策、资产……）都没有对应物，两套体系 id 空间不通。

本方案把"因果线"升级为**元素级**，并做三件事：

- **统一元素模型**：一个注册表、一个 id 空间。领域线只做分组；每个关键元素是独立的一条线，各自有
  未来树、节奏、因果边；技术节点并入元素（技术裁决规则一字不改，只换存取层）。
- **持续发现**：推进中出现的新关键元素自动登记（带去重和预算兜底），登记所需的补全信息在下一步
  prompt 里顺带让 LLM 补，**零额外 LLM 调用**。
- **分层预算**：元素会越来越多，靠"派生分级"（active/watch/dormant）控制进入 prompt 的规模，
  预算用户可调，也可设为不限。

## 1. 背景与现状诊断（读代码所得）

### 1.1 粒度由 prompt 写死在"领域"层

- `spec_generator._resolve_causal_lines_hint(stage="create")`：明确要求 skill"规划出 2~4 条相对
  独立、节奏可能不同的核心因果线"，每条线 2~3 个未来分支。示例就是"技术线/谈判线/个人线"。
- `advance` 阶段同一函数：要求"后续每一步请尽量延续使用同一批 id，不要每步都换一套新的"——
  这句话本意是防 id 漂移，但客观上抑制了新增。
- `state_model.SimManifest.settings.causal_lines` 的文档同样把线描述为"技术线按年演化、谈判线
  按轮次推进"。

### 1.2 新线发现是被动且空心的

- `engine/causal_lines.py::_auto_register_causal_lines()` → `causal_tree.auto_register_lines()`：
  只在 `line_updates`/`causal_links.line_id` 里碰巧出现未登记 id 时才登记；条目是
  `{id, label=id, time_granularity="", auto_discovered=True, future_tree=通用三分支}`。
  没有类型、所属领域、别名、与其他线的因果关系，label 是 id 本身。
- 没有去重：LLM 第 3 步叫 `gpt5`，第 8 步叫 `gpt_5`，就是两条线（推断，代码里没有任何别名/
  规范化逻辑）。
- 其它引用 id 的地方各自为政：`tree_effects` 对未知 `to_line_id` 直接过滤（`tree_effects.py`
  约 159 行附近），`declared_causal_graph` 端点不校验，事件 `affects` 同理。没有"引用即登记"。

### 1.3 技术是唯一的元素级建模，且在另一套存储里

- `tech_model.py`：`settings.tech_state.nodes`，节点有阶段/进度/前置/瓶颈/采用率/成本，由引擎做
  提议–审核裁决（R1–R7、违规码 T1–T10）。开关 `tech_model_enabled`。
- 它和 `causal_lines` 是两个 `DYNAMIC_KEYS`（`dynamic_state.py`），id 空间只在两处被"口头约定"
  打通：因果边端点"既可以是因果线 id，也可以是技术节点 id"（`causal_engine.py` 触发源 4），
  结构化条件 `{"tech": id, "min_stage": ...}`（`event_sampler.py`、`tree_grounding.py`）。
- 技术节点没有未来树；因果线没有阶段/进度。同一个"关键技术"要被完整建模，得在两处各登记一次。

### 1.4 没有规模控制

`_resolve_causal_lines_hint` 把**所有**线和**所有**分支（含已 invalidated 的）逐条拼进 prompt。
线数量从 2~4 条涨到几十条时，prompt 线性膨胀（推断）。`dynamic_state` 快照又是完整拷贝，
`causal_lines` 变大会让历史文件同步变大（`dynamic_state.py` 顶部文档已承认）。

## 2. 目标与非目标

### 目标

1. 每个**关键元素**（技术、项目、公司、政策、资产、市场、人物……）可以有自己的因果线：独立的
   未来树、节奏、因果边、（适用时）生命周期。
2. 领域仍然存在，作为分组与汇总，不再是建模的最小单位。
3. 模拟过程中发现的新关键元素**自动**升级为一条完整的元素线，而不是只留个空 id。
4. 元素数量增长不拖垮 prompt 和存储：分层进入 prompt，预算可调、可设为不限。
5. 技术模型并入统一元素模型，已稳定的技术裁决规则不变，旧数据可读、旧测试继续通过。

### 非目标（刻意不做）

- 不自动判断"一个元素在语义上重不重要"——引擎只做结构校验、去重、预算，语义判断仍归 LLM。
- 不改 `advance_step` 的单次调用结构，不新增常规路径上的 LLM 调用（周期扫描是单独 opt-in，§4.6）。
- v1 不把"生命周期"泛化成任意自定义阶段体系，只保留现有的 `tech_maturity` 6 档（§4.2、§8）。
- 不批量迁移旧实例数据（与第二十二轮 WP0 的既有决定一致）。
- 不做元素之间的自动数值传播（沿用 `relationship.py`、`causal_engine.py` 一贯的克制）。

## 3. 设计理念

| 原则 | 在本方案中的体现 |
|---|---|
| 一个模型，不并行体系 | 扩展 `settings.causal_lines` 这一个注册表；技术节点并入元素的 `lifecycle` 子对象；不新开一套 `elements` 存储 |
| 引擎持有结构，LLM 判断语义 | 引擎做 id 规范化、别名去重、预算裁剪、分级；"这是不是一个值得单独建模的新元素"由 LLM 提出 |
| 规则只给建议、不拦截 | LLM 随时可以更新任何元素（包括休眠的）；分级只决定"本步 prompt 里展示多详细"，不决定"能不能更新" |
| 引用即登记 | 任何位置引用了未登记 id（线更新、边、事件、树前置、条件）都走同一个 `resolve_or_register`，不再各自过滤或静默忽略 |
| 零额外调用 | 新元素的补全信息（label/类型/领域/别名/关系/种子树）在**下一步**的同一次推进调用里要求补 |
| 不编造 | 补不出来就如实标 `fallback`/未归类，不让引擎猜领域、猜类型 |
| 历史不可变、分支正确 | 分级由历史**派生**而非存储；改名/合并只增别名映射，不改历史里的 id；元素状态仍在分支作用域的 `causal_lines` 里 |
| 默认开启，旧实例不变 | 新实例默认开；未写入开关的旧实例保持原行为（§5） |

## 4. 方案

### 4.1 统一元素模型：领域 + 元素两层

**存储不变**：仍是 `manifest.settings.causal_lines`（已是 `DYNAMIC_KEYS` 成员，分支隔离、快照、
`ensure_future_trees`、树接地/树影响/HTML 导出等下游全部按它工作）。每个条目在现有字段
（`id`/`label`/`time_granularity`/`advance_every_n_steps`/`owned_vars`/`future_tree`/
`user_feedback`/`auto_discovered`/`local_step`）之上**新增可选字段**，全部向后兼容：

| 字段 | 含义 |
|---|---|
| `kind` | `domain`（分组与汇总）/ `element`（具体元素）。缺省 = 旧式独立线，行为与现在一致 |
| `parent` | 所属领域线 id；`None` = 未归类（不猜） |
| `element_type` | 自由文本，推荐词表：`technology` `project` `organization` `person` `asset` `policy` `market` `resource` `event_series`；引擎不依赖取值，仅用于展示/过滤，`technology` 例外（决定是否带生命周期） |
| `aliases` | 别名列表（含曾用 id、中英文名），用于去重和旧 id 解析 |
| `origin` | `seed`（创建时）/`discovered`（推进中发现）/`split`/`merged`/`legacy_tech`（旧技术节点并入） |
| `born_step` | 登记时的 step |
| `profile_status` | `complete` / `pending_enrichment`（已登记待补全）/ `fallback`（宽限期内未补全，已按兜底处理） |
| `status` | `alive`（缺省）/ `retired` / `merged`（配合 `merged_into`） |
| `tier_pin` | 用户固定的分级（`active`/`None`），**唯一存储的分级信息** |
| `var_refs` | 可选，该元素状态落在 `vars` 的哪些路径（只读展示用，非独占，区别于 `owned_vars`） |
| `lifecycle` | 可选子对象，见 §4.2；仅 `technology`（及显式声明的）元素有 |

领域线与元素线的关系：领域线保留自己的未来树（表达"这个领域整体怎么走"，如宏观周期），其汇总
视图由**规则**从子元素聚合（趋势、活跃数、最近进展），不额外要求 LLM 每步写领域摘要。

示例（"某 AI 公司三年发展"）：

```text
domain  tech      ├─ element gpu_supply        (technology, 有 lifecycle)
                  ├─ element model_v2_project  (project)
                  └─ element open_rival_x      (technology, origin=discovered, step 7 发现)
domain  finance   ├─ element series_b_round    (event_series)
                  └─ element cash_runway       (resource, var_refs=[vars.cash])
domain  macro     └─ element rate_cycle        (market)
```

### 4.2 技术模型并入：只换存取层，裁决规则不动

核心判断：`tech_model.apply_step()` 的全部规则（R1–R7、T1–T10、修复调用）操作的是"节点 dict"。
所以并入的做法是**让节点 dict 的来源/去向从 `tech_state.nodes` 变成元素的 `lifecycle`**，而不是改
规则：

- 节点字段整体搬进 `lifecycle`：`stage`/`progress`/`requires`/`bottleneck`/`bottleneck_severity`/
  `adoption`/`cost_index`/`market`/`substitutes`/`perceived_stage`/`typical_dwell_days`/
  `dwell_source`/`dwell_verified`/`investment`/`dwell_days`/`stalled_steps`/
  `last_transition_step`/`created_step`/`preexisting`；节点原 `kind`（自由文本）→
  `lifecycle.sub_kind`（避免与顶层 `kind` 冲突）；`name`→`label`；`id` 同 id。
- `tech_model.get_nodes(settings)` / `_write_nodes()` 改为经**存取适配器**读写：元素模式开启时读写
  `causal_lines[].lifecycle`；未开启时读写旧 `tech_state`（旧实例零变化）。`normalize_node()`
  原样保留，作为 `lifecycle` 的规整函数。
- LLM 输出协议里的 `tech_updates` **键名不变**，按元素 id 寻址；新技术登记（`tech_updates` 里出现
  未登记 id）同时创建对应元素线（`element_type=technology`、`origin=discovered`、标
  `pending_enrichment`），所以技术也走统一的"发现→补全"流程。
- `discovered_elements[].lifecycle_seed`（§4.4）会被转换成一条合成的 `tech_updates` 登记项，交给
  **同一套**技术裁决处理——新技术阶段夹值（T5）、`preexisting` 声明等规则只有一个出口。
- 生命周期**规则子开关**沿用 `tech_model_enabled`（`tech_model.is_enabled` 增加别名
  `element_lifecycle_enabled`）。它要求每步输出 `elapsed_days`、会产生违规记录，成本和第二十二轮
  一致，所以子开关仍保持独立（默认值见 §9 待确认项）。元素登记/发现/分级不依赖它。
- 引用方适配（走同一个适配器/注册表，不复制逻辑）：`event_sampler`/`tree_grounding` 的
  `{"tech": id, "min_stage": ...}` 条件（同时接受新别名 `element`）、`causal_engine` 触发源 4、
  `backtest.extract_tech_nodes`（同时读旧快照 `tech_state` 和新快照 `lifecycle`）、
  `html_export_mechanisms`、`app.py` 设置页的技术节点编辑区。

**id 冲突**：折叠旧 `tech_state` 时，若技术节点 id 与已有线 id 相同——是同一个东西就合并（把
`lifecycle` 挂到该线上）；若已有线是领域线，则技术节点改名 `<id>_tech` 并把旧 id 写入 `aliases`，
引用处经别名解析。

### 4.3 创建阶段：先划领域，再展开元素

`generate_scenario`（及两次调用模式下的 `causal_space_builder`）的 create 阶段提示词改为两步：

1. 规划领域（`kind=domain`），数量不再限制 2~4；
2. 对每个领域列出该领域下的**关键元素**（技术、项目、公司、政策等），每个元素给：`id`、`label`、
   `element_type`、`parent`、初始 `future_tree`（2~3 个有区分度的分支）、可选 `aliases`，
   并在 `declared_causal_graph` 里给创建时就成立的先验边（端点可以是元素 id）。

预算裁剪由引擎做（`element_registry.clip_to_budget`），不依赖 LLM 自觉：创建时元素总数默认上限
**20**、每个领域默认 **6**（用户可改，可设不限）。超出部分按"有无先验边、是否 `technology`、LLM 给的
顺序"裁掉，被裁掉的**不丢**，进入创建向导里可见的"候选元素"列表，用户可手动加回。

创建向导（`app.py`）里把现在的 `causal_lines` JSON 文本框升级为领域→元素的树形编辑（仍保留 JSON
高级编辑），并提供预算设置（含"不限"勾选）。

### 4.4 推进阶段：发现新元素（本方案重点）

**① 输出协议（新增可选键，均可省略）**

`advance_step`/`world_evolve`（拆分调用模式）的输出新增：

```text
discovered_elements: [
  {
    "id": "open_rival_x",              # 简短英文/拼音 id
    "label": "开源竞品 X",
    "element_type": "technology",
    "parent": "tech",                   # 必须是已登记领域 id；没有合适领域就省略，不要编
    "aliases": ["X-OSS", "竞品X"],
    "why_key": "一句话：为什么它值得单独建模（与哪些现有元素有因果关系）",
    "relations": [{"with": "model_v2_project", "direction": "affects"|"affected_by",
                   "sign": "positive|negative|mixed", "note": "..."}],
    "future_tree": {"branches": [...]},     # 可选，种子树
    "lifecycle_seed": {"stage": "developer", "preexisting": true}   # 可选，技术类
  }
]
element_enrichments: [ { "id": ..., 补全字段同上 } ]   # 补全已登记但 pending_enrichment 的元素
```

**② 引擎处理（新模块 `world_simulator/element_registry.py`，纯 Python、不调 LLM）**

1. **规范化 + 别名去重**：id/label/aliases 统一规范化（小写、去空白与标点、全半角归一）后与已登记
   元素的 id/label/aliases 比对；命中即视为**同一元素**——合并（补 aliases，不新建线）并记审计。
   只做精确的规范化匹配，**不做模糊匹配**（避免误合并两个相近但不同的东西）。
2. **疑似重复只提示不合并**：规则算出的近似项（如 token 重叠高）作为一句建议喂给下一步 prompt，
   由 LLM 判断是否用 `element_ops.merge`（§4.5）处理，沿用 `suggest_status_transitions` 的风格。
3. **关键性门槛**：新元素需满足 `relations` 至少连到一个已登记的 alive 元素，或 `why_key` 非空且被
   后续步骤再次引用。不满足的进入**候选池**（`settings.element_candidates`，分支作用域），累计被提到
   的次数，达到阈值（默认 2 次）或补上关系后自动转正。这样既自动登记，又不让偶然提到一次的名词
   变成一条线。
4. **预算**：若设置了 `max_total_elements`（默认**不限**），超出时新元素留在候选池，不挤掉已有元素。
5. **登记**：写入 `causal_lines`（`origin=discovered`、`born_step`、`profile_status` = 信息齐全则
   `complete` 否则 `pending_enrichment`）；`relations` 并入 `declared_causal_graph`（标 `origin:
   discovered`，沿用边升级字段）；`lifecycle_seed` 走技术裁决（§4.2）。种子树缺省时用
   `build_default_future_tree` 兜底。
6. **审计**：本步新增/合并/补全/转正写入新字段 `SimState.element_audit`（为空不输出，同
   `tech_updates` 的 `to_dict` 处理方式）。

**③ 补全（零额外调用）**

`pending_enrichment` 的元素会出现在**下一步** prompt 的专门段落里："以下元素是上一步登记的，请在本步
`element_enrichments` 补全 label/类型/领域/别名/关系/未来树"。补全经同一套校验合并。宽限期
（默认 2 步）内仍未补全，标 `fallback`：保留兜底树，`parent` 保持空（显示在"未归类"），不由引擎猜。

**④ 引用即登记（通用兜底）**

新增 `resolve_or_register(ref, source)`：`line_updates` key、`causal_links.line_id`、`tree_updates.
line_id`、`declared_causal_graph` 端点、事件 `affects`、`tree_effects.to_line_id`、树前置的跨线引用、
`{"tech"|"element": id}` 条件里出现的 id，先按规范化/别名解析到已有元素；解析不到的，登记成
`pending_enrichment` 的桩元素（而不是过滤或静默忽略），并走上面的补全流程。这就是替换
`_auto_register_causal_lines` 的新逻辑（元素模式关闭时仍走旧函数，零变化）。

**⑤ 在 `advance()` 里的位置**

`engine/advance.py` 约 794 行现在是 `_auto_register_causal_lines(manifest, next_state)`；元素模式
开启时换成"发现 → 补全 → 元素运维（E5）"，仍在 `_apply_tree_updates` 之前（新元素必须先存在，
同一步的 `tree_updates` 才能引用它）。`tech_updates` 的新节点登记在 `mechanisms.apply_post_llm`
里的技术裁决处同步创建元素线，因此两条推进路径（`advance()`/`advance_lines()`）共用。

### 4.5 元素运维：拆分、合并、退场、改归属（E5）

输出新增可选 `element_ops`：

- `split`：一个元素分化成多个（如项目分叉）。原元素保留并标 `origin` 链路，新元素带 `parent` 继承。
- `merge`：把 A 并入 B。B 的 `aliases` 并入 A 的 id/label/aliases；`declared_causal_graph` 中引用 A 的
  边端点改写为 B；A 标 `status=merged, merged_into=B`，A 的未来树分支以前缀 id 追加到 B 并保留状态。
  **历史里的 `line_updates` key 不改**，视图层经别名映射解析（历史不可变）。
- `retire`：元素退场（技术被淘汰、项目终止）。`status=retired`，不再入队新的待兑现因果；已入队的
  不撤销（那是已发生事实的后果）。不删除，历史与树保留。
- `reparent`：改归属领域。

全部由引擎做结构校验（id 存在、不成环、不自合并），语义是否该合并/拆分由 LLM 判断。

### 4.6 分层与 prompt 预算：派生分级（E4）

**分级由历史派生，不存储**（唯一例外是用户的 `tier_pin`）。这样天然分支正确，也避免每步因分级变化
导致 `causal_lines` 快照被反复重写（派生值不进快照）。

规则（纯规则，参数可调）：

- **active**：`tier_pin=active`；或最近 `active_window` 步内有 `line_updates`/`tree_updates`/技术阶段
  迁移；或有分支处于 `emerging`/`active`；或有到期的待兑现因果指向它；或本步被事件 `affects` 命中；
  或被 active 元素通过因果边触发。
- **watch**：不满足 active，但在 `watch_window` 步内有过动静。
- **dormant**：其余。
- active 超过 `max_active_in_prompt`（默认 **12**）时，按（固定 > 有到期压力 > 有活跃分支 > 最近进展 >
  边入度）排序，其余降为 watch。预算设为不限则全部 alive 元素都按 active 展示。

prompt 展示：active 给完整信息（id/label/类型/领域/节奏/**未终态分支**/生命周期状态/`var_refs` 当前值，
终态分支只给计数）；watch 一行摘要；dormant 不展开，但保留一份**精简索引**（仅 id+label，数量上限
`dormant_index_max`，默认 60，按最近动静截断），目的是让 LLM 优先复用已有 id、不重复"发现"。

**不拦截**：LLM 对任何分级的元素输出 `line_updates`/`tree_updates` 都照常接受，并在下一步自动升为
active。分级只是展示细节，不是权限。

### 4.7 联动（E6）

- `causal_engine`：因果边端点统一为元素 id/技术 id/领域 id。源头为领域 id 时，任一 active 子元素有进展
  即视为触发（新触发源 `domain_child`）；目标为领域 id 时，到期压力展示给 LLM 时列出其下 active 子元素，
  **不自动逐个扇出**（扇出就是在替 LLM 做语义判断）。
- `event_sampler`：`affects` 支持元素 id/领域 id；领域 id 展开为其下 alive 元素；被命中的元素在本步
  升为 active。
- `tree_grounding`/`tree_effects`：跨线前置、`to_line_id` 统一经注册表解析；未知 id 不再过滤，走
  `resolve_or_register`。
- `consistency_guard`：树状态合法性检查按线工作，自动覆盖元素线；新增一条只读提示——"元素数量接近
  预算""存在长期 `pending_enrichment`/`fallback` 的元素"。
- 独立推进 `advance_lines()`：元素默认不拥有 `owned_vars`、不单独发起 `line_evolve` 调用（避免调用数
  随元素数线性增长）；`line_evolve` 的输出同样接受 `discovered_elements`，经 `register_discovered`
  按"先到先采纳"合并（同第二十二轮 P8 的 T10 思路）。
- 界面与导出：`app.py::_render_causal_lines_overview`、`html_export::_render_causal_lines_breakdown`
  按领域折叠分组，元素显示类型/分级/`pending_enrichment` 徽标，支持按类型过滤；详情页设置可编辑
  元素的 `parent`/`aliases`/`tier_pin`/`status` 和预算。

### 4.8 周期扫描（E5，独立 opt-in，默认关）

补全靠 LLM 主动提；万一 LLM 长期漏掉某类对象，提供**可选**的周期扫描：`element_scan_interval`
（默认 0 = 关），开启后每 N 步用单独的 `element_discovery.yaml` workflow 读最近几步叙事/事件/
`vars.entities`，列出"反复出现但未建模"的对象，作为发现建议（走与 `discovered_elements` 相同的
校验与登记）。形态参照 `capability_discovery`（含手动"扫描遗漏元素"按钮）。这是**额外的 LLM 调用**，
所以不作为默认。

## 5. 配置项、默认值与兼容

### 5.1 新增设置

| key | 默认 | 说明 |
|---|---|---|
| `element_modeling_enabled` | **新实例 `True`**；旧实例（未写入该 key）保持关闭 | 总开关：元素登记/发现/分级/领域分组 |
| `element_params.create_max_elements` | 20 | 创建时元素总数上限；`null` = 不限 |
| `element_params.create_max_per_domain` | 6 | 创建时每个领域上限；`null` = 不限 |
| `element_params.max_active_in_prompt` | 12 | 同时以完整信息进 prompt 的元素数；`null` = 不限 |
| `element_params.max_total_elements` | `null`（不限） | 运行中元素总数上限；超出的新元素留候选池 |
| `element_params.active_window_steps` / `watch_window_steps` | 3 / 10 | 分级窗口（按线自身 `advance_every_n_steps` 缩放） |
| `element_params.candidate_promote_mentions` | 2 | 候选池转正所需被提次数 |
| `element_params.enrich_grace_steps` | 2 | 补全宽限步数 |
| `element_params.dormant_index_max` | 60 | 休眠索引上限 |
| `element_scan_interval` | 0（关） | 周期扫描间隔 |

所有数值上限用户都可以在创建向导和详情页设置里修改；**`null` 表示不限制**，界面以"不限"勾选呈现，
不用 0 表示不限（避免与"0 个"混淆）。非法值（负数、非整数）回退默认并在设置页提示。

### 5.2 兼容与迁移（沿用 WP0 "不迁移旧数据"的决定）

- **旧实例**：没有 `element_modeling_enabled` → 视为关闭，`tech_state`、`causal_lines`、
  `_auto_register_causal_lines` 全部按原样工作，输出与现在逐字节等价。用户可在设置里手动开启。
- **新实例**：`materialize_simulation()` 显式写入 `element_modeling_enabled: True`；创建时若带有
  `tech_state` 种子，在落盘时折叠进元素 `lifecycle`。
- **中途开启的旧实例**：折叠旧 `tech_state` 节点（`origin=legacy_tech`）；既有无 `kind` 的线保持"独立线"
  语义，并被标成"待归类"，由下一步 prompt 顺带请 LLM 给出 `kind`/`parent`（同补全机制，不额外调用，
  也可手动改）。
- **分叉/回滚到旧快照**：快照里若仍是旧 `tech_state` 形态，`dynamic_state.apply_to_settings` 之后调用
  幂等的 `fold_legacy_tech_state()`；之后新写入的快照不再含 `tech_state`。`DYNAMIC_KEYS` 保留
  `tech_state`（读旧快照用），新增 `element_candidates`。
- **协议键名不改**：`line_updates`/`tree_updates`/`tech_updates`/`effect_dispositions` 全部保留，
  只是它们现在寻址同一个 id 空间。新增的 `discovered_elements`/`element_enrichments`/`element_ops`
  都是可选键，缺省行为不变。

## 6. 分期（一次一个阶段，每阶段更新文档并打包）

### E1 — 数据模型、存取层、兼容（不改 LLM 行为）

- 新增 `world_simulator/element_registry.py`：元素规整（`normalize_element`）、注册表读取、
  `resolve(ref)`（含别名）、领域→子元素查询、`fold_legacy_tech_state()`。
- `tech_model.get_nodes/_write_nodes` 改走存取适配器（元素模式开/关两种存储，规则代码不动）；
  `is_enabled` 增加别名。
- `causal_tree.ensure_future_trees`/`auto_register_lines` 透传新字段（不改行为）。
- `dynamic_state`：恢复快照后折叠旧形态；`DYNAMIC_KEYS` 增 `element_candidates`。
- `engine/materialize.py`：写入总开关、折叠技术种子。
- 引用方适配：`event_sampler`、`tree_grounding`、`causal_engine` 触发源 4、`backtest.extract_tech_nodes`、
  `html_export_mechanisms`、`app.py` 设置页技术节点编辑区。
- 展示：因果线总览/HTML 导出按领域分组（元素尚少时与现在视觉等价）。
- **测试**：`test_element_registry.py`（规整、别名解析、领域查询）；`test_element_tech_adapter.py`
  （契约测试：同一组输入分别走旧存储与元素存储，`apply_step` 的审计与违规完全一致）；扩展
  `test_branch_dynamic_state.py`（分叉到旧快照后折叠且分支互不污染）；现有 `test_causal_tree.py`、
  技术/因果引擎相关测试全部保持通过。
- **验收**：旧实例行为不变；新实例技术节点存取等价；分叉/回滚正确。

### E2 — 创建阶段元素展开

- `spec_generator._resolve_causal_lines_hint(stage="create")` 改为"领域→元素"两步；
  `ScenarioDraft` 解析新字段；`workflows/generate_scenario.yaml`、`causal_space_builder.yaml` 同步。
- `element_registry.clip_to_budget`、候选元素列表；创建向导树形编辑 + 预算设置（含"不限"）。
- 技术类元素按草稿里的 `lifecycle_seed` 落成 `lifecycle`。
- **测试**：预算裁剪（含 `null`）、候选保留、两次调用模式、旧草稿（无 `kind`）仍可落盘。

### E3 — 发现、去重、登记、补全（核心）

- `register_discovered`、`apply_enrichments`、`resolve_or_register`、候选池与转正、`pending_enrichment`
  宽限与 `fallback`。
- `engine/advance.py` 接入（替换 794 行附近调用，元素模式关时保留旧函数）；`mechanisms.apply_post_llm`
  里技术新节点同步建元素；`SimState.element_audit`；`advance_step.yaml`/`world_evolve.yaml` 输出协议
  与 prompt 段落（补全请求、疑似重复提示、已登记元素索引）。
- **测试**：别名去重与 id 漂移（`gpt5`/`gpt_5`）、关键性门槛与转正、补全合并与宽限、引用即登记
  （各引用源）、`lifecycle_seed` 经技术裁决（T5 夹值生效）、元素模式关时与旧行为等价。

### E4 — 分层与 prompt 预算

- `derive_tiers(history, settings)`（只扫最近 `watch_window` 步）、排序与降级、prompt 渲染（active 完整/
  watch 一行/dormant 索引）、升级触发、`var_refs` 当前值展示、`tier_pin`。
- **测试**：分级规则逐条、预算 `null` 与数字、分支正确（两个分支派生不同分级）、**prompt 规模**
  （登记元素从 10 增到 100，active 固定 12 时，prompt 增量只来自 watch 摘要与休眠索引，且索引受上限约束）。

### E5 — 元素运维与周期扫描

- `element_ops`（split/merge/retire/reparent）与校验；别名映射让历史视图解析旧 id；边端点改写。
- 周期扫描：`element_scan_interval`、`workflows/element_discovery.yaml`、手动扫描按钮。
- **测试**：合并后的边/树/别名、历史 key 不变且视图可解析、退场不入队新压力、扫描结果走同一校验。

### E6 — 联动与收尾

- `causal_engine` 领域端点（`domain_child` 触发源、目标领域展示）、`event_sampler` 领域展开、
  `consistency_guard` 只读提示、`advance_lines()` 的 `line_evolve` 接入 `discovered_elements`。
- 界面/导出完整化（过滤、徽标、设置编辑）。
- 文档：新增 `docs/element_model_guide.md`；更新 `docs/README.md`、`docs/overview.md`、
  `docs/tech_model_guide.md`、`docs/causal_engine_guide.md`、`PROJECT.md`（变更记录/已知限制）；
  回填本文 §10 实施记录。

## 7. 改动清单（预估）

新增：`world_simulator/element_registry.py`、`workflows/element_discovery.yaml`、
`docs/element_model_guide.md`、测试若干（见各阶段）。

修改：`tech_model.py`（存取适配）、`causal_tree.py`、`dynamic_state.py`、`state_model.py`
（`element_audit`、设置文档）、`spec_generator.py`、`engine/advance.py`、`engine/causal_lines.py`、
`engine/materialize.py`、`engine/mechanisms.py`、`engine/advance_independent.py`、`causal_engine.py`、
`event_sampler.py`、`tree_grounding.py`、`tree_effects.py`、`consistency_guard.py`、`backtest.py`、
`html_export.py`、`html_export_mechanisms.py`、`app.py`、`workflows/` 下
`generate_scenario`/`causal_space_builder`/`advance_step`/`world_evolve`/`line_evolve` 的 yaml。

## 8. 风险与已知限制

1. **快照体积**：元素变多后 `causal_lines` 变大，而 `dynamic_state` 是完整拷贝快照、每步有变化就写。
   总元素默认不限，所以长模拟里历史文件会增长。缓解：分级为派生值不入快照；休眠元素不变就不引起
   快照变化；如实测过大，再评估增量快照（不在本轮）。
2. **去重只做精确规范化**：语义相同但名字完全不同的元素不会被自动合并，靠 LLM 用 `element_ops.merge`
   处理；疑似重复提示只是建议。宁可漏合并，不误合并。
3. **关键性靠 LLM 的 `why_key` 与关系声明**：引擎只能校验"有没有关系/被不被再提到"，判断不了"真的重不重要"。
   候选池阈值是经验值（2 次），需要用真实模拟调。
4. **LLM 不补全**：`pending_enrichment` 在宽限期后落到 `fallback`（兜底树、未归类），元素是"登记了但建模
   粗糙"。这是如实记录，不是静默掩盖；`consistency_guard` 会提示数量。
5. **生命周期只覆盖 `tech_maturity` 一种**：项目/产品类可以挂这套 6 档，但金融、健康、人物等类型的元素
   v1 只有未来树和 `var_refs`，没有阶段/进度/前置裁决。泛化为可声明的阶段体系列入遗留。
6. **prompt 复杂度上升**：新增 `discovered_elements`/`element_enrichments`/`element_ops` 三个可选输出键，
   对较弱的模型可能出现格式不稳。所有解析都"不认识就忽略、不报错"，沿用既有风格；效果需真实 LLM 验证。
7. **LLM 更新休眠元素的成本**：休眠元素只在索引里出现 id+label，LLM 想更新它时缺少上下文，只能凭名字
   推断。引擎在其被更新后的下一步升为 active 并展示完整信息。
8. **折叠旧快照的边界**：折叠发生在恢复/加载边界；若有读路径绕过 `apply_to_settings` 直接读旧快照，
   需要同样走适配器。E1 会全局搜 `tech_state` 的读取点逐一核对（目前约 13 个文件有引用）。
9. **未在真实 LLM 下验证**：与第二十二轮一致，所有阶段以单元/契约测试 + 假 LLM 输出验证，
   真实效果需要用户用实际模拟观察。

## 9. 已确认决定与待确认项

### 9.1 已确认（2026-10-03 用户回复）

1. **元素模型**：✅ 合并成统一元素模型（不是"元素线与 `tech_model` 绑定"）。实现上落在 §4.2：统一数据与
   id 空间，技术裁决规则不动。
2. **新发现元素**：✅ 自动登记（有去重、关键性门槛、预算兜底）。
3. **补全信息**：✅ 在下一步 prompt 里顺带补，不额外调用 LLM。
4. **预算**：✅ 创建时总元素约 20、同时进 prompt 的 active 线约 12；**用户可修改，也要有"不设置限制"
   的选项**（§5.1：`null` = 不限，界面"不限"勾选）。
5. **开关**：✅ 新实例默认开启。

### 9.2 我按推荐值先写入本文，有异议请指出

1. **旧实例**：未写入开关的旧实例保持关闭，可手动开启（与 WP0 "不迁移旧数据"一致）。"默认开启"只作用于
   新建实例。
2. **生命周期规则子开关**（`tech_model_enabled`）：保持原默认（关）。原因是它要求每步输出 `elapsed_days`、
   会产生违规记录，成本不小；总开关默认开不应顺带把它打开。元素登记/发现/分级不依赖它。
3. **运行中元素总数上限**默认不限（你只指定了创建时和 prompt 内的预算）；代价是 §8 第 1 条的快照增长。
   如果更希望有个保守默认（比如 80），说一声。
4. **协议键名不改名**：`line_updates`/`tree_updates`/`tech_updates` 保留，只是寻址同一 id 空间；
   统一改名会连带改所有 workflow/skill 模板，收益不大，列入遗留。

## 10. 实施记录

（各阶段完成后在此回填：改动文件、测试、与计划的偏差、遗留问题。）

### 仍不属于本轮的遗留

- 生命周期泛化为可声明阶段体系（金融/健康/人物等类型）。
- `line_updates`/`tech_updates` 等协议键统一改名。
- 增量快照（视 §8 第 1 条的实测结果）。
- 元素级回测（把元素发现的时机与真实历史对照）。
