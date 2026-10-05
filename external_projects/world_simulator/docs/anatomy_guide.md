# 元素剖面（anatomy）指南

> 第二十四轮。设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md`。
> **本文只写已落地的行为**，随阶段增补。当前已落地：**A1（数据模型、存取层、兼容、只读档案）**。
> A2 创建期拆解、A3 联网证据、A4 引擎骨架、A5 推进期深化、A6 蒙特卡洛、A7 预测简报、A8 指标回测尚未实施。

## 1. 它是什么

剖面是元素线（`settings.causal_lines[i]`）上的**新增可选字段 `anatomy`**，把一个元素拆成：

| 部分 | 内容 |
|---|---|
| `components` | 构成/子系统（`readiness` 0–1、`requires`） |
| `metrics` | 关键指标（单位、现值+`as_of`、目标、边界、趋势模型） |
| `bottlenecks` | 瓶颈（类型、阻塞对象、严重度、状态、解决路径各带概率与时长区间） |
| `approaches` | 竞争路线 |
| `adoption_gates` | 采用门槛（结构化条件 + `adoption_cap`） |
| `milestones` | 里程碑（结构化判据、`maps_to_stage`） |
| `assumptions` | 关键假设（`statement`、`prior_p_true`、`if_false.overrides`） |
| `signals` | 先行信号（`watch` / `means`） |
| `meta` | `anatomy_status`（none/draft/reviewed/verified）、`key`（重点元素）、`researched_at`、`template` |

每个条目可带 `basis`（来源状态）：`sourced`（有出处）/`llm_prior`（LLM 先验）/`user_confirmed`/`user_edited`。
**没有 `basis` = `llm_prior`**，默认值不落盘（快照小）。

## 2. A1 做了什么、没做什么

**做了**：规整、存取、参数、只读体检、只读档案视图、设置页开关。
**没做**：**没有任何代码会自己写入剖面**，推进时不读、不改、不进 prompt。A1 的剖面只能通过 `anatomy.set_anatomy()`（后续阶段与手动编辑用）写入。
所以开启开关**不会改变任何模拟行为**。

## 3. 开关与兼容

- `settings.anatomy_enabled`：**未写入 = 关闭**。新实例由 `materialize_simulation()` 在**元素模式开启时**默认写入 `True`
  （调用方明确传值则尊重；元素模式关闭的新实例不写）。旧实例不迁移，可在设置页「🧩 元素管理与预算」里手动勾选（要求元素模式已开）。
- `anatomy.is_enabled(settings)` = `anatomy_enabled` **且** 元素模式开启。
- 没有 `anatomy` 键的线经 `normalize_element` **逐字节不变**（契约测试覆盖）；有的则被规整，规整后为空就摘掉该键。
- 剖面随 `causal_lines` 快照（`dynamic_state.DYNAMIC_KEYS` 已含），分叉即回滚，**没有新增接线**。
- **合并保护**：带剖面的元素不能作为 `merge` 的被并入方（与带 `lifecycle` 的规则一致），否则合并会丢掉其指标/瓶颈/里程碑；请改用 `retire`。保留方带剖面没问题。
- 累计模拟日：**复用**每步 `elapsed_days`（`causal_engine.elapsed_between`），`anatomy.clock_days()` 是薄封装，**不新增存储字段**。
- 字段命名：所有新键都收在一个 `anatomy` 键下，与现有 `causal_lines`/`lifecycle`/未来树节点字段**无重名**。

## 4. 规整规则（`anatomy.normalize_anatomy`）

- 只处理 8 个部分与 `meta`，未知键丢弃；**只规整出现了的字段，不补缺省、不猜值**；非法取值丢弃该字段/条目，不报错。
- 幂等：规整两次与规整一次结果相同。`normalize_anatomy_report()` 额外返回"被丢弃项说明"。
- 条目 `id`：空白折成 `_`；含 `#` 或 `:`（引用语法分隔符）、过长、重复则丢弃；缺 `id` 时由 `name` 派生。
- 数值必须是有限数（排除布尔/NaN/inf）；`readiness`/`p_success`/`prior_p_true`/`adoption_cap` 必须在 0–1。
- 参数 `{value, low?, high?, dist?, source?}`：`low > high` 丢弃两端（退为点估计）；`dist` 只认 triangular/uniform/lognormal。
- **`sourced` 必须带至少一个 `evidence_ids`，否则降为 `llm_prior`**（避免"有出处"徽标无法核对）。证据库在 A3 才有，所以 A1 不核对 id 是否真实存在。
- **趋势不设白名单**：任何非空 `trend.kind`（含 `custom_expr`、`llm_reported`、不认识的）都保留；`expr` 只截断，A1 不解析（A4 用白名单 AST 求值）。
  推荐库见 `anatomy.RECOMMENDED_TRENDS`，只影响展示与后续提示词偏好。
- 条件树（里程碑/门槛/路径前置）：`all`/`any`/`not` + 叶子（新增 `metric`/`component`/`bottleneck` 三种引用，及既有 `var`/`tech`/`element`），
  深度 ≤ 4、节点 ≤ 16；A1 只规整形状，**不求值**。
- 瓶颈解决路径：`duration_days` 给出的 `low ≤ mode ≤ high` 必须成立否则整个丢弃；`fallback` 只保留指向存在路径的项；
  路径的 `status`/`resolve_at_day` 与里程碑的 `reached_step`/`reached_sim_day` 是 A4 引擎状态字段，A1 存在即保留，免得被存取层吞掉。
- 体积上限（`MAX_*`）：每部分条目数（组件/指标/里程碑/假设/信号 24，瓶颈 16，路线/门槛 12）、每瓶颈 8 条路径、名称 80 字、描述 300 字等。超出丢弃/截断。

## 5. 模板（`anatomy_templates`）

按 `element_type`（大小写与空白不敏感；不认识的用 `default`）给出"该关注哪些槽位"的**提示**，覆盖
technology / project / organization / person / asset / policy / market / resource / event_series / default。
**不带任何领域事实或数值**；不参与校验（不会因为"不在模板里"丢内容）。A1 只提供 `get_template` / `slot_hint`，尚未接入任何提示词。

## 6. 参数（`settings.anatomy_params`）

约定同 `element_params`：`null` 只对 `key_element_count`、`deep_max_calls_total` 合法（= 不限），`0` 就是 0，非法值回退默认并出现在
`anatomy.invalid_param_keys()`（设置页会提示）。A1 声明的键与默认值见 `anatomy.DEFAULT_PARAMS`
（`key_element_count=5`、`deep_enabled=true`、`deep_allow_search=false`、`mc_runs=1000` 等），**目前没有任何代码读取它们**，各阶段落地时才生效。
`deep_time_budget_sec` 计划里标"待 A5 定"，A1 不预设。

## 7. 只读体检与统计

- `anatomy.validate_anatomy(anatomy, known_evidence_ids=None)`：只报告、不改数据、不阻断。类型：
  `dangling_ref`（本元素内引用不存在，跨元素引用不查）、`param_out_of_range`、`current_out_of_bounds`、`no_criteria`、`dangling_evidence`（传了证据 id 集合才查，A3 起接入）。
- `anatomy.basis_stats()`：每个条目各算一个单位，再加每个指标的 `current`；给出各来源状态数量与 `llm_prior_ratio`。
- `anatomy.counts()`：各部分条目数。

## 8. 界面（只读骨架）

- 因果线总览里，**剖面开关已开且该元素有剖面**时，该元素下多一个「🧬 元素档案」折叠区：表头（模板/审阅状态/是否重点）、
  来源统计（明确写"LLM 先验 = 没有外部出处"）、按模板顺序排好的各部分、体检提示。没有剖面或开关未开时**什么都不画**。
- 视图数据由纯函数 `element_view.build_profile(settings, element_ref)` 构造（界面与后续导出共用）。
- 设置页「🧩 元素管理与预算」新增"启用元素剖面"勾选与 `anatomy_params` 非法值提示。

## 9. 已知边界（A1）

- 没有任何生成剖面的路径（A2/A3 才有），实际使用只能经 `set_anatomy`；因此界面目前只有在有人/后续阶段写入后才会出现档案。
- `maps_to_stage` 只当字符串保存，不校验是否是 `tech_model.STAGES` 之一（A4 判定时处理）。
- `adoption_gates.unlocks` 只认 `adoption_cap`，其余键被丢弃，A4 定义更多解锁项时再放开。
- 跨元素引用（`元素#子项`）A1 不解析；解析复用 `element_registry.resolve`，在 A4 条件求值时接入。
- 设置页勾选与档案折叠区只做了静态检查（`py_compile`），没有在真实 Streamlit 页面点过。
- 全部为纯逻辑，未在真实 LLM 下验证（A1 本身也不调用 LLM）。

## 10. 测试

`tests/test_anatomy.py`（31 个用例，无需真实 LLM）：规整（非法丢弃/不补缺省/幂等/体积上限/id 规则/数值与区间）、basis 规则、趋势无白名单、条件树形状与深度、
模板（返回拷贝、无数值）、参数回退（`null`/0/非法）、存取（领域线/退场拒绝、清除、旧式线可写）、`normalize_element` 契约、快照往返回滚、
新实例开关、合并保护（被并入方拒绝/保留方放行）、统计、体检、累计日、档案视图门控与内容。8 个变异（sourced 降级、区间颠倒、趋势白名单、合并保护、开关默认、规整接线、领域线拒绝、统计）全部转红。
