# 元素剖面（anatomy）指南

> 第二十四轮。设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md`。
> **本文只写已落地的行为**，随阶段增补。当前已落地：**A1–A8 全部完成**（A5 见 §14，A6 见 §15，A7 见 §16，A8 指标级回测见 §17）。第二十四轮计划至此实施完毕。

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

- （A1 当时）没有生成剖面的路径；A2 起创建期会产生草稿，见 §11。
- `maps_to_stage` 只当字符串保存，不校验是否是 `tech_model.STAGES` 之一（A4 起由引擎判定，不合法的记 `A5` 并忽略，见 §13）。
- `adoption_gates.unlocks` 只认 `adoption_cap`，其余键被丢弃，A4 定义更多解锁项时再放开。
- 跨元素引用（`元素#子项`）A1 不解析；A4 起条件求值时按 `element_registry.resolve` 解析（见 §13.7）。
- 设置页勾选与档案折叠区只做了静态检查（`py_compile`），没有在真实 Streamlit 页面点过。
- 全部为纯逻辑，未在真实 LLM 下验证（A1 本身也不调用 LLM）。

## 10. 测试

`tests/test_anatomy_creation.py`（A2，17 个用例）：重点元素选择、种子落成草稿的强制改写、空壳/非重点丢弃/种子键不落盘、创建提示开关与前缀、单次与拆分两条路径一致、审阅与勾选纯函数、落盘；9 个变异全部转红。

`tests/test_anatomy.py`（31 个用例，无需真实 LLM）：规整（非法丢弃/不补缺省/幂等/体积上限/id 规则/数值与区间）、basis 规则、趋势无白名单、条件树形状与深度、
模板（返回拷贝、无数值）、参数回退（`null`/0/非法）、存取（领域线/退场拒绝、清除、旧式线可写）、`normalize_element` 契约、快照往返回滚、
新实例开关、合并保护（被并入方拒绝/保留方放行）、统计、体检、累计日、档案视图门控与内容。8 个变异（sourced 降级、区间颠倒、趋势白名单、合并保护、开关默认、规整接线、领域线拒绝、统计）全部转红。

## 11. 创建期拆解与向导审阅（A2）

**流程**：创建 prompt 末尾追加一段（`anatomy.create_hint`），要求 LLM 对最关键的元素在其 `causal_lines` 条目里额外给 `anatomy_seed`；
引擎在预算裁剪之后（`element_registry.prepare_created_lines` → `anatomy.apply_seeds`）选出重点元素并把种子落成**草稿**剖面。
单次创建与拆分创建（世界构建 + 因果空间构建）共用同一个提示入口，行为一致（测试覆盖两条路径）。

**开关**：创建时 `anatomy_enabled` **缺省视为开启**（同 `element_modeling_enabled` 的创建期约定），显式 `False`、元素模式创建被关、
或 `anatomy_params.key_element_count = 0` 时关闭——此时 prompt 是开启时 prompt 的**逐字节前缀**（只在末尾追加），落盘内容与没有本功能时一致。
`anatomy_seed` 键**任何情况下都不会落盘**（关闭时也会被摘掉）。推进阶段的 prompt 不受影响。

**重点元素选择**（`anatomy.select_key_elements`）：存活的 `kind=element` 线中，按 **先验边端点 > `technology` 类型 > LLM 给的顺序** 取前 `key_element_count`
（默认 5，`null` = 不限，`0` = 一个都不选）。领域线、没有 `kind` 的旧式独立线、已退场元素不参与。

**种子落成草稿**（`anatomy.seed_to_anatomy`）——LLM 没有权限声明以下内容，引擎强制改写：
- 所有 `basis` 清掉，一律 `llm_prior`（LLM 不能声称"有出处/已确认"）；
- 引擎状态字段被剥掉：瓶颈路径的 `status`/`resolve_at_day`、里程碑的 `reached_step`/`reached_sim_day`（A4 引擎才拥有）；
  瓶颈本身的 `status`（如起点就已解决）是合法声明，保留；
- `meta` 重写为 `{anatomy_status: draft, key: true, template}`。
重点元素没给种子（或种子规整后为空）→ 写一个**空壳** `{meta: {anatomy_status: none, key: true, template}}`，让 A3 的研究知道它是重点；
非重点元素的种子丢弃（只有重点元素享受拆解）；被预算裁掉的候选元素也不带种子。

**向导**（`app.py`）：
- 「重点元素拆解（A2）」折叠区：开关 + 重点元素个数（含"不限"），写入 `settings.anatomy_enabled` / `anatomy_params.key_element_count`；
- 生成草稿后新增审阅区（只有线里确实有剖面才出现）：重点元素多选（取消勾选**不删除**内容，只把 `key` 置 false；新勾选的无剖面元素得到空壳）、
  每个重点元素一个折叠区，**每条拆解各一个"保持/确认/驳回"**；确认 → `user_confirmed`（保留已有证据 id，指标现值一并确认），驳回 → 删除该条，保持 → 仍是 `llm_prior`。
  全部条目都经**用户**确认 → `anatomy_status = reviewed`，否则 `draft`（A3 修订：A2 时"有出处"也算已审阅，A3 起联网出处是 LLM 声称的、用户没看过，不再算，见 §12.6）。
- 落盘时一次性套用（`anatomy.apply_creation_review`）；用户没动过重点元素多选时（`None`）不改动。
- **编辑内容**请直接改向导里的因果线 JSON（改过的内容仍标 `llm_prior`）；A2 不提供逐字段编辑控件。

**纯函数（均不改入参）**：`select_key_elements` / `seed_to_anatomy` / `apply_seeds`（就地改刚创建的线）/ `apply_key_selection` / `apply_review` / `apply_creation_review`。

**与计划的偏差/细化（A2）**
1. 没有 `kind` 的旧式独立线不当作元素，不拆解（保守，且保持既有测试与旧草稿行为不变）。
2. 审阅粒度是"每条拆解"（组件/指标/瓶颈…各条），不到指标现值/参数这类叶子字段；指标的现值随条目一起确认。
3. 向导不提供逐字段编辑控件（见上）；"驳回"= 删除该条而不是"回到 llm_prior"，因为被驳回的内容没有保留价值。
4. 驳回某条不会自动清理引用它的其它条目；悬空引用由体检（`validate_anatomy`）报告，不静默改别处。
5. 向导的"重点元素个数"没有走 `element_params`，而是独立的 `anatomy_params`，与计划 §7 一致。

**已知边界（A2）**：未在真实 LLM 下验证 LLM 是否遵守 `anatomy_seed` 格式（解析失败/不合规时降级为空壳，不阻断创建）；
向导新控件只做了静态检查（`py_compile`），没有在真实页面点过；创建期拆解本身不联网；联网研究是创建**之后**的独立步骤（A3，§12）。

## 12. 联网证据研究与出处（A3）

设计依据：计划 §5.4、§8 A3、§9 风险 1。代码：`evidence.py`（证据层）、`element_research.py`（研究流程）、`workflows/element_research.yaml`。

### 12.1 联网能力实测边界（A3 前置核实，风险 1）

- 宿主 workflow 的 `type: agent` 步骤用 `get_default_registry()` 起 Agent，默认工具里**有内置 `web_search`**（`requires_approval=False`，Agent 初始化时注入搜索配置，默认 DuckDuckGo，可换 brave/serper/tavily）。
- `web_search` **只返回标题 / 链接 / 摘要**；全部内置工具里**没有网页抓取/读取工具**。所以证据**只能基于搜索摘要**，研究提示词明确要求"不要声称读过全文、不要用 bash 抓取"，界面也写明"仅基于搜索摘要，请点链接核对"。
- 这是**读代码的静态核实**，没有在真实网络/真实 LLM 下跑过：搜索结果质量、LLM 是否遵守"最多检索 N 次"（只能靠提示词约束，引擎无法计数）、实际耗时均未验证。

### 12.2 流程与触发

`research_element()` 调一次 workflow → `parse_output()` → `apply_research()`（**纯函数**）→ `research_and_save()`（先追加证据、再保存 manifest；失败时不写任何东西）。触发只有三处：
1. **创建后**：向导落盘后对"待研究的重点元素"逐个研究（`research_pending`），失败不阻断创建，没研究成的留在待研究里；
2. **设置页「补做联网研究」**（待研究列表 + 按钮）与两个开关（`research_on_create` / `research_on_register`）；
3. **元素档案里**：「🔎 刷新联网研究」，以及对非重点元素的「设为重点元素并联网研究」。

**不会**在 `advance()` 里自动联网（见 §12.8 偏差 1）。`pending_targets`：重点元素 ∧ 没有 `meta.researched_at`；`origin=discovered` 的受 `research_on_register` 控制，其余受 `research_on_create` 控制。研究失败不写 `researched_at`，所以失败的继续待研究；"成功但没找到出处"也写（不会反复重试），报告 `no_evidence=True`。

### 12.3 谁说了算（核心约束）

LLM 只提供**草稿与证据**，下面由引擎裁决，LLM 写什么都不算数：
- **来源状态**：草稿里的 `basis` / 状态声明一律丢弃；一个字段标 `sourced` 只看它引用的证据是否被接纳（非空要点 + 合法 `http(s)` 链接）；引用不存在/被拒的证据 → `llm_prior`；置信度取所引证据中**最低**的一档；
- **指标现值的出处要对得上**：现值要标 `sourced`，所引证据必须 ① 带数值 ② 指标有单位时证据也给同单位（大小写/空白不敏感）③ 数值相对误差 ≤ 1%。不满足任一条 → 现值仍是 `llm_prior`（报告里写原因；单位/数值不一致还会在证据上打 `unit_mismatch`/`value_mismatch` 标记）。证据数值超出指标声明的 `bounds` → 仍算有出处，但打 `out_of_bounds`（待审）并由体检的 `current_out_of_bounds` 提示；
- **引擎状态**：路径 `status`/`resolve_at_day`、里程碑 `reached_*` 一律剥掉；`meta`（重点/状态/研究时间）由引擎写；
- **用户的决定**：`user_confirmed` / `user_edited` 的条目**永不被研究覆盖**。

### 12.4 合并规则

草稿里**新 id** → 加入；**同 id** 的已有条目：仅当新条目是 `sourced` 且已有条目不是用户确认过的，才整条替换（继承已有的瓶颈 `resolved/exhausted`、路径 `status/resolve_at_day`、里程碑 `reached_*`，A4 起这些由引擎产生）；其余保留已有条目——研究的作用是**补出处**，不是用无出处的新猜测覆盖旧猜测，也不会冲掉向导里用户手改的内容。已有但草稿没提的条目原样保留。合并后 `anatomy_status` 由 `anatomy.derive_status` 重算。

### 12.5 证据库（`evidence.py`）

- 存放：`data/<sim_id>/evidence.jsonl`（`sim_dir` 根下，**不在 `branches/` 里**，不随分叉/回滚变化；`SimStore.evidence_path/load_evidence/append_evidence/set_evidence_status` 是薄封装）。
- **追加写**：记录一行一条；状态变更（`superseded`/`rejected`）也是追加一条 `{"_op":"status"}` 事件，读取时折叠，**不改写任何已有行**；坏行、孤儿事件、重复 id 读取时容错跳过。
- 记录字段：`ev_id`（`ev_NNNN`）、`element_id`、`field_ref`（首个引用它的字段，如 `metrics.cost.current`）、`claim`（改写后的要点，≤240 字、去控制字符）、`value/unit`、`source_url`（只认 `http(s)`，拒绝 `javascript:`/`data:`/`file:`）、`source_title`、`publisher`、`published_at`（只认 `YYYY[-MM[-DD]]`，其余当不知道）、`retrieved_at`、`confidence`、`research_run_id`、`status`、`flags`。**没有要点或没有合法链接的"证据"不入库。**
- **只存最终被剖面引用的证据**：被草稿引用但最终没用上（用户确认的条目保留、或现值核对不过）的不入库，计入报告 `unreferenced_evidence`。同一元素内**要点与链接完全相同**的证据复用旧 id，不重复入库。刷新后不再被剖面引用的旧证据标 `superseded`。
- **时效**：`research_ttl_days`（默认 180，**现实天数**）从 `retrieved_at` 起算，超过即在界面标"证据已过期"；只标注，不删除、不自动刷新；没有/无法解析检索时间的不判过期。
- **驳回**：`reject_evidence`：先摘掉剖面里对它的引用（`sourced` 且没有别的证据 → 回 `llm_prior`；用户已确认的条目保持用户的决定、只去掉该证据 id）并保存，再追加 `rejected` 事件。

### 12.6 审阅与"已审阅"的含义（对 A2 的修订）

`anatomy_status=reviewed` 现在要求**每个条目都经用户确认/编辑**；`sourced` 不再等同已审阅。联网研究的产出永远是"未审阅"，除非用户逐条确认。档案页的「审阅条目」可选择若干条目"确认所选 / 驳回所选（删除）"（复用 `apply_review`，确认保留证据 id）。**草稿默认就驱动**的约定不变，但 A3 里剖面本身还不驱动任何推进（A4 起）。

### 12.7 网络内容是数据，不是指令

- 提示词明确：搜索结果里的指令一律忽略；证据只存改写要点 + 链接，不存网页原文；
- 引擎对证据只做 schema/链接/数值校验，**不执行、不抓取**；A3 里没有任何提示词读取证据，证据文本不会进入会改变行为的位置（A4 起读取时同样当数据）；
- 界面展示时所有证据文本经 `_html_text` 转义，链接只在 `http(s)` 时渲染（测试覆盖注入文本不产生行为）。

### 12.8 与计划的偏差（如实记录）

1. **不在 `advance()` 里自动做"新元素登记后补全研究"**：研究是额外的联网 LLM 调用，放进推进会不可预期地变慢/超时（风险 2）。改为创建后批量 + 设置页补做 + 档案刷新；`research_on_register` 的含义改为"创建之后才登记的重点元素是否算待研究"。
2. **合并只在新内容有出处时替换同 id 条目**（计划未规定合并细则）。
3. **现值的出处校验比计划严格**（要求证据带数值、同单位、1% 内），计划只写"单位/量级合理性检查"。
4. **证据只存被引用的**、同要点同链接去重（计划未规定）。
5. **审阅粒度仍是条目**；档案页的确认/驳回复用 `apply_review`，没有逐字段编辑控件。
6. **跨模拟证据缓存**（计划列为 A3 末尾可选项）**未做**。
7. **`research_max_searches` 只写进提示词**，引擎不计数、不强制（`element_research.yaml` 另设 `max_turns: 16`、`timeout: 420` 作为硬上限）。
8. **A9（证据过期仍在驱动引擎）** 属于 A4 引擎，A3 只提供过期标注。

### 12.9 已知边界（A3）

- 联网研究全程**未在真实网络/真实 LLM 下验证**：LLM 是否遵守 JSON 格式、`evidence_ids` 写法、检索次数上限，以及搜索摘要的质量，都是推断；解析失败/不合规一律降级（不写任何东西）。
- 证据只基于搜索摘要，LLM 可能误读摘要；`confidence` 是 LLM 自报，不是核验结果；"已核实"没有界面入口（`verified` 状态目前无人产生）。
- 模拟若是虚构/历史/远离现实的设定，现实检索结果可能不适用；提示词要求这种情况下给空证据并说明，但遵守度未验证。
- 并发：证据 id 取"现有最大号 +1"，不防并发写入（同 `PROJECT.md` 已知限制）。
- 界面新增控件（档案证据区、审阅、研究按钮、设置页补做、创建后钩子）只做了 `py_compile` 与证据列表 HTML 的单测，**没有在真实 Streamlit 页面点过**。
- 编号可能出现空洞（被分配了 id 但最终未入库的证据），无害。

### 12.10 测试（A3）

`tests/test_evidence.py`（14）：URL 协议白名单、要点/链接必填、日期与数值清洗、id 规则、追加写不改写已有行、状态事件折叠、坏行/孤儿事件/重复 id 容错、按现实天数的过期判定、`SimStore` 封装不在分支目录下。
`tests/test_element_research.py`（45）：目标选择与开关/来源门控、输入构造与摘要预算、输出解析、LLM 无权声明来源/引擎状态/meta、现值出处四种不成立情形与容差、合并各规则（用户确认保护/先验不被覆盖/有出处替换/继承引擎状态/状态不因有出处变已审阅）、证据去重与取代、入参不被修改且畸形线原样保留、注入文本只是数据且展示转义、workflow 桩（围栏 JSON、各种失败）、落盘与失败不写入、批量研究逐个隔离、驳回证据、`set_key`/`strip_evidence`/`derive_status`、档案证据视图与过期。
**14 个变异**（单位/数值核对被删、LLM 自带 basis 不清、用户确认保护被删、无出处覆盖旧猜测、引擎状态不剥、sourced 算已审阅、URL 放开协议、重建 `causal_lines` 丢畸形条目、旧证据不标取代、未引用证据入库、过期恒假、状态事件不折叠、驳回不摘引用）全部转红。全量 1677 个用例通过（A3 前基线 1618）。

## 13. 引擎定量骨架（A4）

> 代码：`world_simulator/anatomy_engine.py`（纯 Python、不调 LLM）。设计依据：计划 §5.3。**A4 之前剖面在推进时不读不写；A4 起，剖面开着且元素带可结算内容（指标/组件/瓶颈/里程碑/门槛）时，引擎每步结算它。**

### 13.1 开关与"什么时候有事可做"

- `anatomy_engine.is_active(settings)`：`anatomy_enabled` 且至少一个存活的非领域元素带引擎能结算的内容。只有 `meta`/假设/信号的壳**不算**。
- 不 active 时：`{anatomy_hint}` 为空串、`SimState` 不输出 `anatomy_trace`/`anatomy_violations`、不写任何引擎字段、`elapsed_days` 也不额外索要（旧实例与没有剖面的实例行为不变，测试覆盖）。
- 注意：`advance_step.yaml` / `world_evolve.yaml` 里新增的是一段**固定说明 + `{anatomy_hint}` 占位符**（与此前各轮机制一样），所以"逐字节一致"指的是**解析后的输入与落盘数据**，不包括 yaml 里那段静态说明文字。

### 13.2 每步结算顺序（`apply_step`）

①初始化/重新锚定 → ②在**步初**启动可启动的路径 → ③推进指标与组件 → ④LLM 偏离 → ⑤到期日平移 → ⑥结算到期路径 → ⑦在**步末**启动新可启动的路径 → ⑧里程碑与门槛 → ⑨阶段（A3）与采用率（A7）→ ⑩记账、自洽核对、一次性写回。

所有计算在**工作副本**上完成，最后才写回；任何异常不留半改状态（`safe_apply_step` 再包一层，失败只记 `A0`，不影响本次推进）。同一步不会重复结算（`meta.last_step`）。

引擎状态存在剖面自己身上（随 `causal_lines` 快照/分叉，分叉即回滚，测试覆盖真实 `fork_branch`）：指标/组件的 `engine {v,t,a,off,src,dq,na,sf}`、路径的 `status/outcome/started_day/started_step/resolve_at_day`、瓶颈的 `resolved_step/resolved_day/resolved_by`、里程碑的 `reached_step/reached_sim_day`、门槛的 `open/opened_step`、`meta.clock_day/last_step`。这些字段 A1 的规整层都保留；**LLM 种子与研究草稿里的会被剥掉**（瓶颈 `status` 除外——创建/研究期可以如实声明"现实里已解决"，仍标 `llm_prior`）。

### 13.3 指标趋势（`trend`）

不设白名单，三层：**推荐库**（有引擎模型）/ **`custom_expr`**（白名单 AST 求值）/ **未知或 `llm_reported`**（不拒绝，引擎不推算，只记录 LLM 报告的值，记一次 `A10`，界面标"无引擎模型"）。

| `kind` | 必需参数（`params.<名>.value`） | 一步的推进（`dy` = 天数 / 365.25） |
|---|---|---|
| `linear` | `slope_per_year` | `v + slope·dy` |
| `exponential` | `rate_per_year`（年增长率，需 > -1） | `v·(1+g)^dy` |
| `saturating` | `cap`、`rate_per_year` | `cap − (cap−v)·e^(−k·dy)` |
| `mean_reversion` | `mean`、`rate_per_year` | 同上，目标是 `mean` |
| `logistic` | `cap`、`rate_per_year`（要求 0 < v < cap，否则保持不变） | 以 v 为起点的逻辑斯蒂 |
| `learning_curve` | `b`，加 `driver`（同元素或 `元素#指标` 的驱动量，如累计产量） | `v·(Q₁/Q₀)^(−b)`；驱动指标先于被驱动指标推进 |
| `random_walk_drift` | `drift_per_year`，可选 `vol_per_sqrt_year` | `v + drift·dy + σ·√dy·z`，`z` 由 `event_sampler.draw` 确定性产生（种子复现）；无 σ = 纯漂移 |
| `piecewise_table` | `trend.table = [[距锚点天数, 值], ...]`（天数严格递增） | 线性插值，两端保持 |
| `step_events` | `trend.events = [{event, delta?/factor?}]` | 平时保持；本步**命中**的事件（未被上限压掉）触发跳变 |
| `custom_expr` | `trend.expr`（+ 可选 `params`） | `expr(t, v0, params.*, metric.<id>)`，`t` = 距锚点天数 |

- 自治流（前几种）增量推进与一次推进**等价**（步长不影响结果，测试覆盖）；`piecewise_table`/`custom_expr` 是时间的绝对函数，rebase 时加偏移 `off`。
- **表达式只用白名单 AST 求值，不用 `eval`/`compile`**：只允许数字常量、`+ - * / % **`、比较、`and/or/not`、三元、`min/max/abs/exp/log/sqrt`（1–3 个参数、无关键字）、变量名、`params.<名>` 与 `metric.<id>`；禁止其他属性访问、下标、lambda、推导式、字符串……表达式长度 ≤ 300 字、节点 ≤ 64、嵌套 ≤ 24、指数绝对值 ≤ 64、结果必须是有限数。非法表达式 → 该指标退化为"不推算"+ `A10`，**不报错、不拖垮推进**。
- 组件的 `readiness_trend`（计划外新增，见 §13.9）用同一套趋势库，结果夹在 [0, 1]。
- 现值被用户编辑或联网研究更新后（`current.value` 变了），引擎**重新锚定**到新值并在流水里留一条 `source: anchor`。

### 13.4 瓶颈、里程碑、阶段、门槛

- **瓶颈路径**：路径**启动时**由引擎抽样——是否成功（`p_success`，缺省 = 必成功）与耗时（`duration_days` 三角分布，只给部分参数时：只有 mode = 定值、low+high = 中点为 mode），记下绝对到期日 `resolve_at_day`。种子 = `event_sampler.draw(sim_id, branch, step, "anatomy|<salt>", "<元素>/<瓶颈>/<路径>/ok|dur")`：同一输入逐位一致、分支之间独立、`event_sampling_common_random_numbers` 开启时分支共享。到期成功 → 瓶颈 `resolved`（记 `resolved_day` 为**精确到期日**）；到期失败 → 该路径 `failed`，按 `fallback` 顺序（再按声明顺序）在**到期日**启动下一条；全部失败 = `exhausted`。路径有 `requires` 判据时，满足才启动。缺 `duration_days` 的路径引擎无法调度（`A10`）。**路径启动后抽好的成败不会写进流水，也不会给用户看，只有到期那一刻才揭晓**；但"预计本步到期"的路径会通过 `{anatomy_hint}` 告诉 LLM（见 §13.8）。
- **里程碑**：判据满足即 `reached`（记步号与模拟日），**只增不减**，已达成的不会被重新达成或改写步号。没有判据的里程碑引擎无法判定，只能采纳 LLM 的 `milestone_claims`（体检里已有 `no_criteria` 提示）。
- **阶段派生**（仅技术元素，即有 `lifecycle`；要求技术模型已开启、`anatomy_drives_stage`（默认开）、且至少一个里程碑带合法的 `maps_to_stage`）：阶段 = 已达成里程碑里最高的一档，可一次跨多档；`progress` 变成**派生显示值**——下一档里最接近达成的那个里程碑已满足的叶子判据占比（上限 0.99）。这与计划写的"该档内已达成里程碑占比"不同，见 §13.9。
- **与 T 码的优先级（契约）**：LLM 声明的阶段迁移**先**过 `tech_model` 的 T1–T11，**再**过 A3；没有剖面/没有 `maps_to_stage` 里程碑的节点完全走旧逻辑。T 码放行的**上升**声明没有对应里程碑 → `A3` 驳回并**回滚**本步迁移写下的 `stage/progress/dwell_days/last_transition_step/stalled_steps`；T4 放行的**倒退**同样 `A3` 驳回（里程碑只增不减；要倒退请改剖面）。
- **采用门槛**：判据满足 = 门槛 `open`（可再关闭，`opened_step` 记首次打开）；`unlocks.adoption_cap` 约束技术节点的 `adoption`：只考虑 `market` 与节点 `market` 相同（忽略大小写）或没写 `market` 的门槛；有相关门槛但都没开 → 上限 0。**只夹"本步新增的超限部分"**：门槛前就已有的采用率不追溯（`A7` 夹到 `max(上限, 本步前的值)`）。

### 13.5 LLM 提议与裁决（`anatomy_updates`，A4 只实现裁决侧）

引擎持有结构，LLM 只能**提议**，且 **A4 还不向 LLM 索要这个输出**（提示词协议是 A5）——`data` 里没有 `anatomy_updates` 就是空操作；有就按下表裁决。形状：

```
{"metric_deviations":   [{"element", "metric", "value", "reason", "cause_ref"}],
 "bottleneck_proposals":[{"element", "bottleneck", "status": "resolved", "shift_days", "reason", "cause_ref"}],
 "milestone_claims":    [{"element", "milestone", "reason"}],
 "engine_edits":        [{"element", "ref", "field"}]}
```

`element` 省略时，只在**唯一**含该 id 的元素里解析，不唯一就不猜（`A5`）。

| 码 | 触发 | 结果 |
|---|---|---|
| A0 | 引擎本步出错 | 整步跳过、状态不变（info） |
| A1 | 报告值与模型值相对偏差 > 20%（`DEVIATION_THRESHOLD`）且没有 `reason`/`cause_ref` | 保留引擎值（warn） |
| A2 | 声称瓶颈已解决但路径未到期；要求平移到期日却没有进行中的路径或没有原因 | 驳回（warn） |
| A3 | 声称里程碑达成但判据未满足；T 码放行的阶段上升/倒退没有里程碑支撑 | 驳回（warn） |
| A4 | 指标/组件超出 `bounds`（组件固定 [0,1]） | 夹值（模型越界 info，LLM 报告越界 warn） |
| A5 | 引用了不存在的元素/指标/组件/瓶颈/里程碑，或 `maps_to_stage` 不是合法阶段名 | 仅记录，不阻断（info） |
| A6 | `engine_edits`：想改引擎持有的参数/路径概率/到期日 | 忽略（info） |
| A7 | 采用率本步新增部分超过已打开门槛的上限 | 夹值（warn） |
| A8 | 深度调用失败/降级 | A5 阶段才会产生 |
| A9 | 指标现值的证据已过期仍在驱动引擎 | info，每次锚定只提示一次（过期按现实天数，`research_ttl_days`） |
| A10 | 引擎缺少推算所需数据：趋势 `llm_reported`/未知/缺参数/表达式非法、路径缺 `duration_days`、本步没有 `elapsed_days` | info |
| A11 | 流水自洽核对失败；流水超过每步 120 条被截断（计划之外新增） | warn |

偏离被接受后默认 **rebase**（保持趋势斜率、把当前值平移到报告值；`deviation_policy: keep_model` 则只记录不采纳）。偏差在阈值内且没给原因也接受（记为小幅偏离）；没有引擎模型的指标，LLM 报告值直接被采纳（`source: llm_reported`）。`shift_days` 不能把到期日拉到"现在"之前。

### 13.6 流水与自洽核对（`SimState.anatomy_trace`）

每步一份流水，条目 `{element, kind, ref, field, value_before, value_after, reason, source, ...}`：`kind` ∈ `time`（本步时间来源，恒有一条）/`metric`/`component`/`bottleneck`/`milestone`/`gate`/`stage`/`adoption`；`source` ∈ `model`/`event`/`deviation`/`llm_reported`/`anchor`/`clamp`/`milestone`/`rejected`/`reported`/`fallback`。偏离条目带 `cause_ref`，指标条目带证据 id。**同一指标的数值流水必须首尾衔接**，且终点等于引擎当前值；不一致写 `A11`，**不静默修正**（测试里故意篡改流水/状态验证）。

时间：开启剖面的实例每步需要 `elapsed_days`；LLM 没给时按 `tech_params.fallback_days_per_step` 结算，流水里 `time.source = fallback` 并记 `A10`（后续 A6 的预测区间会据此标"时间精度降级"）。

### 13.7 条件语法扩展（事件先验 / 树分支 / 门槛共用一套求值器）

`event_sampler.evaluate_condition` 现在额外认 `{"metric": "<元素>#<指标>", "op", "value"}`、`{"component": ..., "min_readiness": ...}`、`{"bottleneck": ..., "status": ...}` 以及 `all`/`any`/`not` 组合节点，**委托给 `anatomy_engine.evaluate_leaf`**。树接地（`tree_grounding`）本来就调用 `event_sampler.evaluate_condition`，所以事件先验的 `condition` 与树分支的 `trigger_condition` 自动获得新写法，没有出现第三套求值器（计划要求"先核对两处再统一"——核对结论：只有这一处）。读的是**已落盘**的剖面；没写元素前缀时找唯一含该 id 的元素，找不到/不唯一/写法非法一律**不满足**并给原因，不抛异常。旧写法（`var`/`tech`/`element`）行为不变。

### 13.8 提示词 `{anatomy_hint}` 与"预计本步发生"

`advance_step.yaml`、`world_evolve.yaml` 新增 `{anatomy_hint}`；独立推进路径（`advance_lines`）的每条线提示里也会带上同一份（并让 `mechanisms.needs_elapsed` / `any_mechanism_enabled` 认识剖面）。内容：协议（引擎是权威、不要自行宣布瓶颈解决/里程碑达成/阶段跃迁；偏离请在叙事里写明原因）+ 每个元素的精简摘要（指标现值与趋势、瓶颈状态与进行中路径、已达成/待达成里程碑；受 `light_digest_chars` 约束、重点元素优先、最多 8 个）+ **"预计本步发生"**——引擎在**副本**上按估计跨度（最近几步 `elapsed_days` 的中位数，没有则占位天数）空跑一步（`preview_step`），把会发生的瓶颈解决/失败、里程碑、阶段变化、门槛打开作为既成事实喂给 LLM。抽样是确定性的：本步实际跨度等于估计值时，真实结算与预览逐项一致（测试覆盖）；不一致时以真实跨度重新结算，提示里已写明"到期的顺延、未到期的不会发生"。需要时在末尾索要 `elapsed_days`（技术模型/事件采样/因果引擎任一已开启则不重复索要）。提示词构造出错只退化为空串。

### 13.9 与计划的偏差（如实记录）

1. **组件 `readiness_trend`（计划外新增）**：计划只有指标有趋势，但里程碑判据常引用组件就绪度，没有它就永远只能靠用户手改。复用同一套趋势库，夹在 [0, 1]。
2. **`progress` 的派生口径不同**：计划写"该档内已达成里程碑占比"，但"阶段 = 最高已达成里程碑"下该档里几乎总是 1 个，占比没有信息量；改为"下一档里最接近达成的里程碑已满足的叶子判据占比"。
3. **下降声明也 `A3` 驳回**（计划只写了"迁移须有里程碑"）：里程碑只增不减，否则阶段会在下一步被重新抬回去。
4. **`A11`（流水自洽/截断）是新增码**，计划的 A0–A10 里没有对应项。
5. **`anatomy_updates` 只做了裁决侧，提示词协议留给 A5**（计划把协议归在 A5，而 A2/A3 验收又要求 A4 里 LLM 声明不能绕过 `A2/A3`——所以 A4 实现裁决函数并用测试直接喂提议验证；阶段声明走既有 `tech_updates`，那条路径 A4 就真实生效）。
6. **路径"投入允许"未做**：计划 §5.3.2 提了这个启动条件，但没有定义投入如何影响路径，A4 不猜。
7. **`custom_expr` 参数的 `low/high`、蒙特卡洛** 属于 A6，A4 只用 `value`。
8. **同一步里不同元素之间的 `driver` 是"先到先读"**：跨元素驱动量读的是该元素在本步推进之前或之后的值（取决于元素顺序）；同元素内按依赖顺序，不受影响。

### 13.10 已知边界（A4；A5 的更新见 §14.5/§14.6）

- 全程**未在真实 LLM 下验证**：LLM 是否会把"预计本步发生"的事项如实写进叙事、是否在没有 `anatomy_updates` 协议时仍擅自改写数值（A4 只靠提示词约束 + `next_vars` 本来就由 LLM 整体重写，**引擎的指标值不会写回 `vars.*`**，计划 §9 风险 11 的"不镜像"结论沿用）。
- 趋势参数大多来自 LLM 先验或创建期草稿，引擎结算得再精确也只是"在这些假设下的推演"，不是预测；区间与置信等级是 A6/A7 的事。
- 界面新增的「⚙️ 引擎推进」区（曲线/瓶颈/里程碑/逐步变化）只做了静态检查与视图函数单测，**没有在真实 Streamlit 页面点过**。
- 组件就绪度没有 LLM 提议入口；新增子项发现（`new_subitems`）、深度模式在 A5。
- 事件触发的趋势（`step_events`）依赖事件采样已开启并抽中对应 id。
- `deviation` 的小幅容忍（≤ 20% 不需要原因）理论上可被反复利用来缓慢拉偏数值；A4 没有协议向 LLM 索要偏离，真实风险要到 A5 才出现，届时再评估是否改成"一律要原因"。

### 13.11 测试（A4）

`tests/test_anatomy_engine.py`（110）：表达式（合法/恶意输入/运行时护栏/不用 `eval`）、每种趋势的数值与"步长无关"、学习曲线驱动顺序、随机游走种子复现与分支敏感、重新锚定、同一步只结算一次、瓶颈路径（精确到期日、`fallback` 顺序、耗尽、`requires`、缺时长、成功概率与三角分布的统计、种子隔离）、`A1`–`A11` 各码、偏离 rebase/keep_model/绝对函数偏移、里程碑只增不减与组合判据/跨元素引用、阶段派生/`progress`/A3 回滚/下降驳回/关闭与无映射时旧逻辑不变、门槛与 A7（含"不追溯"）、`require_review_to_drive`、时间降级、A9、流水自洽与篡改检测、原子写回与 `A0`、引擎输出被 A1 规整层原样保留、引擎状态不被种子/草稿带入、条件语法扩展（事件先验与树分支）、提示词与预览一致、`advance()` 端到端（持久化/快照/真实分叉回滚/可复现/LLM 无法绕过/失败不影响推进/开关关闭与无剖面实例不变）、档案推进视图。
**24 个变异**（A2/A3 驳回被删、阶段声明不回滚、里程碑可重复达成、`fallback` 顺序被忽略、成功概率被忽略、种子不含分支、A1 阈值被删、rebase 偏移被删、夹值被删、A7 夹值/不追溯被删、不重新锚定、同一步重复结算、被压掉的事件算命中、驱动顺序被删、非原子写回、A9 不去重、表达式属性检查/幂护栏被删、审阅开关被忽略、流水衔接核对被删、平移可到过去、预览改了真实状态）全部转红。全量 1787 个用例通过（A4 前基线 1677）。

## 14. 推进期深化（A5：轻量协议 + 深度模式）

> 代码：`anatomy_engine.py`（协议与二次裁决）、`anatomy_deepen.py`（深度模式）、`workflows/element_deepen.yaml`、`engine/mechanisms.py`（接线与多线合并）、`element_view.build_deep_view`。设计依据：计划 §5.5。
> 原则不变：**LLM 提议，引擎裁决**。深度模式没有任何特权——它的提议走与主调用 `anatomy_updates` **完全同一套**规则。

### 14.1 轻量协议：`anatomy_updates`

主调用（`advance_step` / `world_evolve` / `line_evolve`）的提示词里，`{anatomy_hint}` 末尾追加了可选输出协议；只有叙事里确有偏离或发现新子项/信号时才输出。四个键都可省略：

| 键 | 作用 | 引擎裁决 |
|---|---|---|
| `metric_deviations` | 指标偏离（必须带 `reason`） | `A1`（无原因的大幅偏离驳回）、`A2`、`A3`；小幅（≤20%）无原因可采纳 |
| `bottleneck_proposals` | 只能**平移**进行中路径的到期日 | 不能宣布"已解决"，是否解决由引擎按到期抽样裁决 |
| `new_subitems` | 新增组件/指标/瓶颈（`part` + `item` + 必填 `reason`） | 只增不改；`basis` 强制 `llm_prior`、引擎状态剥掉、瓶颈强制 `open`、**id 已存在不覆盖**、每次最多 `MAX_NEW_SUBITEMS=3` 条、受各部分条数上限约束；已 `reviewed` 的元素退回 `draft`；不合规记 `A12` |
| `signals` | 先行信号（`watch` + `means`） | 同 `watch` 去重，每次最多 `MAX_NEW_SIGNALS=3`；不合规记 `A12` |

流水里新增子项的 `kind` 为 `subitem`、`source` 为 `proposed`。**多线并行推进（`advance_independent`）**：各线的 `anatomy_updates` 由 `mechanisms.merge_anatomy_updates` 并成一份，**先到先采纳**（按线声明顺序），同一件事被多条线提议时后到的记 `A12`（info）。只有真有提议才给 `data` 加键，没有时与 A4 逐字节一致。

### 14.2 深度模式

每步轻量结算（`apply_step`）之后、树接地之前，`apply_post_llm` 调 `anatomy_deepen.safe_run_in_step`：

1. **触发**（`collect_triggers`，只看本步被引擎结算过的**重点**元素）：里程碑达成、瓶颈状态变化/路径到期、指标偏离或声明被驳回、事件命中、技术节点停滞 ≥ `STALLED_STEPS` 步、上一步被顺延（`pending`）、保底节奏（距上次深度调用 ≥ `deep_cadence_steps`，dormant 元素不算）。多个触发加权排序（`pending` 优先），同分按声明顺序。
2. **调用**：每个入选元素一次 `element_deepen` workflow（`type: agent`，`max_turns: 8`，`timeout: 100`，只输出 JSON）；输入是该元素的剖面摘要 + 本步引擎流水 + 命中的事件 + 叙事摘录 + 触发原因。`deep_allow_search`（默认关）才允许联网。
3. **上限**：每步 `deep_max_calls_per_step`（3）；实例总量 `deep_max_calls_total`（默认不限，用完记 `skipped` 并清顺延）；单步总耗时 `deep_time_budget_sec`（**120 秒**，A5 定的默认值）。超限的元素记 `deferred`，原因合并进 `meta.deep_pending`（最多 `MAX_DEEP_PENDING=4` 条、每条 ≤ `MAX_DEEP_REASON_LEN=60` 字），下一步优先。**失败的调用也计入上限，并重置保底节奏**，避免失败后每步重试。
4. **裁决**：回复里的提议**强制归到被调用的元素**（指向别的元素 → 丢弃并记 `A12`），然后走 `anatomy_engine.apply_adjudication`——同样的 0 天二次裁决，遍历全部引擎元素，所以跨元素的准则/门槛不会被受限视图翻转；叙事只当文本，不改任何状态。
5. **记录**：`SimState.anatomy_deep`（空则不输出字段）每元素一条 `{element, status: ok|failed|deferred|skipped, reasons, narrative?, accepted?: {proposed,changes,new_subitems,flagged}, elapsed_sec}`。

**降级**：调用失败 / 回复无法解析 / 既无叙事又无提议 → 状态 `failed`，记 `A8`（info），该元素本步保持轻量；提议裁决出错 → 丢弃提议、保留叙事、记 `A8`；`safe_run_in_step` 兜住一切意外。**绝不影响本次推进。**

**关闭**：`deep_enabled=false`（"快速模式"）或 `deep_max_calls_per_step=0` → 什么都不调、不写任何字段（状态 JSON 与 A4 逐字节一致）。分支回溯时 `anatomy_deep` 随状态节点走，`calls_used` 只数分支历史，所以回退不会"消耗"后来才发生的调用。

### 14.3 新增诊断码

| 码 | 含义 | 级别 |
|---|---|---|
| `A8` | 深度调用失败 / 裁决出错 / 本步深度模式出错，已降级为轻量 | info |
| `A12` | 提议被忽略：新增子项缺依据/形状非法/id 重复/超上限、信号重复/超上限、深度调用越权提议其他元素、多条线重复提议 | info/warn |

### 14.4 界面与参数

- 档案「⚙️ 引擎推进」下新增「🔬 深度分析」：最近一次的"本步调用 n/上限、顺延 m 个、失败 k 次"（L1）；最近 10 张该元素的变化卡片，标题是状态 + 叙述前 120 字（L2）；展开是完整叙述（L3，**`st.text` 纯文本，不解析 HTML**）与引擎裁决计数。数据来自 `element_view.build_deep_view`。
- 设置页（启用元素剖面之后）新增：启用深度模式、每步最多深度调用次数、单步耗时预算（秒）。
- `project.yaml`：`advance_simulation` 超时 300→600 秒、`batch_advance_daily` 900→1800 秒（深度模式每步最多多几次调用，计划风险 2）。

### 14.5 与计划的偏差

- 触发里"指标越过阈值"落成"本步有偏离/驳回"，没有再引入独立阈值参数；保底节奏从 0 起算，所以默认 3 步才首次触发保底。
- 深度调用放在 `apply_post_llm` 内而不是各推进路径各自调用，保证两条推进路径共用一个接入点。
- `deep_enabled` 沿用 A1 的默认 `true`；但只有剖面引擎有事可做且有重点元素时才会调用。
- 轻量协议的小幅偏离容忍（≤20% 无原因）**保持不变**：A5 起 LLM 才有入口利用它，真实影响需在真实 LLM 下观察后再决定是否收紧，本阶段不改。

### 14.6 已知边界

- **没有在真实 LLM / 真实 workflow 下验证**：`element_deepen` 的提示词是否稳定产出合规 JSON、`agent` 步骤 100 秒内能否完成，只做了桩测试与 yaml 解析/占位符检查。
- 现有端到端测试里的 `FakeStore` 只认 `advance_step`，深度调用在那里会失败并降级为 `A8`——这是有意的无害降级，但意味着端到端没有覆盖深度调用成功的路径（成功路径由 `test_anatomy_deepen.py` 的桩覆盖）。
- 耗时预算按**现实时间**，在调用之间检查；单次调用本身最长 100 秒，故一步最坏耗时可超过预算约一次调用。
- 界面新增部分只做了静态检查与视图函数单测，**没有在真实 Streamlit 页面点过**。

### 14.7 测试（A5）

`tests/test_anatomy_deepen.py`（40）：各触发分支与优先级、上限/顺延/总上限/耗时预算（假时钟）、失败降级（含 A0 跳过与兜底包装）、关闭零痕迹与状态往返、与主调用同一套裁决（A1）、越权提议 A12、新增子项/信号规则、多线合并先到先采纳、展示数据分层、分支回溯、参数校验。变异检查：删掉每步上限、不重置保底节奏均被抓出。全量 1827 通过。

## 15. 蒙特卡洛 / 敏感性 / 监测清单（A6）

> 代码：`world_simulator/forecast.py`（纯 Python，**不调 LLM、不读写磁盘**）；引擎侧新增 `anatomy_engine.simulate_step` / `set_series_value`；视图 `element_view.build_forecast_view`；界面在元素档案「🎲 预测（蒙特卡洛）」。设计依据：计划 §5.6 / §8 A6。**A6 不改推进行为**：没有新增任何会写入模拟状态的代码，预测结果只在本次会话里展示（不落盘）。

### 15.1 核心承诺与诚实标注

- 预测是对**同一份引擎骨架**（A4 的结算函数）反复推演，不是另一个模型。
- 所有区间旁固定标注：**区间由已声明参数的不确定性产生，不是校准过的概率**（`forecast.HONEST_NOTE`，结果 `meta.honesty`）。`meta.params.llm_prior_share` 给出参数里仍是 LLM 先验的比例；时间精度降级（最近几步 LLM 没给 `elapsed_days`）写进 `meta.time_precision_*`。可信度要靠证据（A3）和回测（A8），不靠这里。

### 15.2 入口

| 函数 | 作用 |
|---|---|
| `run_forecast(settings, history, runs, seed, horizon_days, steps, time_budget_sec, sim_id, element, point)` | 蒙特卡洛。`runs` 默认 `anatomy_params.mc_runs`（1000）、`seed` 默认 `mc_seed`；视野默认约 5 年（有更长的路径时自动拉长，上限 100 年），步数默认 60（步长 = 视野 / 步数）；`element` 只模拟该元素的**依赖闭包**；`point=True` 是点估计情景 |
| `sensitivity(settings, history, element, target, runs, ...)` | 一次改一个的敏感性（龙卷风数据） |
| `what_if(settings, history, edits, base=...)` / `apply_edits` | 改参数/假设/路径/事件频率后同一种子重跑并逐项对比 |
| `build_watchlist(settings, forecast, sens)` | 先行信号监测清单 |
| `signal_observations(checks, keys)` | 现实回填里勾选的信号统计 |

不能预测时返回 `{"ok": False, "reason": ...}`（开关未开 / 没有可结算元素 / 元素不存在）。

### 15.3 随机性来源

1. **参数不确定性**：趋势参数 `{value, low, high, dist}`，**两端都有**才抽样；`triangular`（缺省，众数 = `value`）/`uniform`/`lognormal`（`low/high` 当作约 P5–P95，**全部截断在 [low, high]**）。只给一端或没给 = **点估计**，列在 `meta.point_estimates`。
2. **瓶颈路径**：成败（`p_success`）与耗时（三角分布）。**真实推进里已经"进行中"的路径被重新抽样**：成败重抽，耗时条件于"已经过了这么久还没到期"（最多重试 8 次，仍不满足取"已过时长 + 1 天"），不泄漏真实运行里藏着的抽样结果。
3. **假设**：`prior_p_true` 的 Bernoulli；不成立时应用 `if_false.overrides`。支持的覆盖键：指标 `value` / 趋势参数名；组件 `readiness` / 趋势参数名；瓶颈 `status`（open/resolved/exhausted）、`duration_scale`（路径耗时 ×k）、`p_success_scale`（成功概率 ×k）。**不认识的键不报错，写进 `meta.warnings`**（例如 A2 示例里的 `paths: "slow_only"` 引擎无法解释，不会假装应用了）。
4. **事件先验**：泊松到达（沿用 `event_sampler` 的概率公式、冷却、条件、每步上限）；带 `rate_range` 时频率本身也在区间内抽样（**真实推进的抽样不用 `rate_range`**，这是 A6 的新用法）；可选 `effects` 见 §15.8。
5. **随机游走趋势**的噪声（引擎内部用同一套确定性哈希）。

**可复现**：每个随机数按**身份**（元素/参数/路径/假设名）+ `(seed, 第几次运行)` 做确定性哈希，与方案里别的东西无关。含义：① 同种子逐位一致；② 第 `r` 次运行与总次数无关，**时间预算截断后的结果 = 不截断时的前 N 次**；③ 限定依赖闭包后，闭包内元素的结果与全量运行一致；④ 敏感性/what-if 天然是公共随机数（同一次运行的随机流在不同情景间对齐，差异来自被改的那一项而不是抽样噪声）。

### 15.4 `run_forecast` 的输出

`milestones`（达成时间 P10/P50/P90 + `p_reached`；已达成的只报 `reached_day`；没有判据的标 `no_criteria`）、`metrics`（最多 24 个取点的 P10/P50/P90 分位带，起点 = 当前值）、`bottlenecks`（视野内解决概率、P10/50/90、**解决时各路径占比** `by_path_share`、全部路径失败概率 `exhausted`）、`resolution_order`（最先解决的瓶颈频率、最常见的完整解决顺序；瓶颈多于 8 个只给"最先解决"）、`gates`（打开时间分布）、`branches`（未来树分支触发条件首次满足的频率）、`stages`（终态阶段分布，仅有 `maps_to_stage` 里程碑的技术元素）、`conditional`（假设条件化）。所有时间都是**"从现在起"的天数**；P50/P90 为 `None` = 视野内未达成的运行超过了对应比例（`p_reached` 给出具体比例）。

**假设条件化**：每个带 `prior_p_true` 的假设给出"成立 / 不成立"各自对前 5 个受影响最大的里程碑的 P50 与达成概率；每侧样本少于 20 次（`MIN_COND_RUNS`）时 `sufficient=False`，不给结果。没有 `if_false.overrides` 的假设不成立时骨架不变，两侧自然没有差别（界面会提示）。

### 15.5 敏感性与 what-if

- **基线** = 点估计情景：参数取点值、假设取更可能的一侧（`prior_p_true ≥ 0.5` 视为成立）、事件频率取点值；路径成败与耗时、随机游走仍随机。
- **情景**（一次改一个）：带 `low/high` 的趋势参数、带 `low/high` 的路径耗时（整条取低端/高端）、带 `prior_p_true` 的假设（翻到另一侧，基线侧直接取基线结果）、带 `rate_range` 的事件频率。**路径 `p_success` 没有区间，不参与**，只列在 `point_estimates`（可用 what-if 手改）。
- 每个情景用同一批随机流跑 `runs` 次（默认 `mc_runs // 10`，夹在 50–200），对每个目标里程碑（最多 8 个，或 `target` 指定的一个）比较 P50 与达成概率；`score = max(P50 摆幅 / 视野, 概率摆幅)`，按它降序。
- **只模拟目标元素的依赖闭包**（`element=`）：引擎里跨元素只能靠显式 `元素#子项` 引用（本元素内的无前缀引用只在本元素解析），所以闭包是精确的。
- 时间预算用尽：没跑完的情景整体作废、列入 `meta.skipped`，**不给半截数据**；连基线都跑不完则明确失败。
- `what_if`：编辑种类 `param` / `current` / `duration` / `p_success` / `assumption` / `event_rate`（写法见 `apply_edits`），**写时复制不改入参**，解析不到/写法非法的编辑进 `errors` 而不抛异常；沿用基线的种子/视野/步数，结果与"对编辑后的 settings 直接重算"逐位一致（测试覆盖）。

### 15.6 先行信号监测清单与现实回填

- `build_watchlist`：① 元素剖面里已有的 `signals`（原样带出）；② **规则生成**（不调 LLM，最多 12 条，按不确定性排序）：多路径瓶颈（哪条路径先走通）、假设（成立/不成立对里程碑的影响）、临界里程碑（0 < `p_reached` < 1）、敏感性最大的参数。每项 `{key, watch, means, refs, source: anatomy|forecast, basis, score}`，`key` 稳定。
- **接入 `reality_check`**：`RealityCheck` 新增可选字段 `signals_observed`（回填时勾选"这次观察到了哪些先行信号"；空列表不落盘，旧记录逐字节不变；最多 24 个、去重）。`signal_observations` 统计每个信号被观察到的次数/最近一步/当时的 verdict。**只记录"观察到了"，不做任何自动判定，也不据此改预测**。
- 预测生成的监测项**不持久化、不可编辑**（计划写"可手改"，A6 只让已有的 `signals` 可改——在因果线 JSON 里改；想保留一条预测生成项，把它的 `watch/means` 抄进元素的 `signals`）。

### 15.7 性能（实测，沙箱 CPU，结论随机器而异）

直接循环 `apply_step` 每次运行要深拷贝整份剖面：20 元素 × 100 步 × 1000 次 ≈ **340 秒**，不可交互。A6 为此新增**精简模式** `simulate_step`：走同一批内部结算函数，但原地改一次性副本、不记流水、不做自洽核对、不结算采用率（测试保证与 `apply_step` 的里程碑日、指标值、瓶颈状态逐位一致，默认的 `apply_step` 仍是原子写回）。实测：

| 规模 | 耗时 |
|---|---|
| 20 元素 × 1000 次 × 60 步（默认） | ≈ 42 秒 |
| 20 元素 × 1000 次 × 100 步 | ≈ 68 秒（默认 60 秒预算下会被截断并标注） |
| 敏感性：限定一个元素的闭包（11 个情景 × 100 次） | ≈ 8 秒 |

所以默认 `time_budget_sec = 60`：用尽后至少跑完 50 次（`MIN_RUNS_BEFORE_TRUNCATE`）才收工，`meta.truncated` / `runs_done` 如实标出（界面也提示"区间噪声更大"）；`0` = 不限。没有做多进程并行（跨平台与 Streamlit 里的风险大于收益）。

### 15.8 事件先验的可选 `effects`

`event_sampler.normalize_prior` 新增可选 `effects`（最多 8 条，写法非法的条目静默丢弃；**没有 effects 时不输出该键，旧先验规整结果逐字节不变**）：

- `{"ref": "[元素#]metric:<id>"|"component:<id>", "op": "add|mul|set", "value": 数}`：命中时对该指标/组件的引擎当前值做加/乘/设（夹在边界内，绝对函数型趋势同步平移偏移，口径同"偏离 rebase"）；
- `{"ref": "[元素#]bottleneck:<id>", "shift_days": 数}`：把该瓶颈**进行中**路径的到期日推迟（正）/提前（负）。

**只有预测读它**：真实推进里事件仍只是喂给 LLM 的既成事实，引擎不据此改任何数（`step_events` 趋势照旧只看"命中了哪些事件 id"）。没写元素前缀的 `ref` 只在**唯一**含该 id 的元素里解析，否则不猜（`meta.warnings`）。

### 15.9 与计划的偏差（如实记录）

1. **预测不带 `vars`**：事件先验/树分支条件里用 `var` 的写法在预测里无法求值（事件永远不触发并列入 `warnings`；分支标"无法求值"）。只有 `metric/component/bottleneck`（和 `tech/element`）判据能在骨架状态上求值。
2. **树分支"激活" = 触发条件首次满足**，不模拟前置/互斥组裁决（计划写"复用 `tree_grounding` 的条件求值"——复用了条件求值，没复用完整裁决）。
3. **事件 `effects` 只有预测用**（计划写"给先验新增可选 `effects`"，没写真实推进是否使用；为不改现有行为，真实推进不使用）。
4. **路径 `p_success` 不参与不确定性/敏感性**（没有 `low/high`），只在 `point_estimates` 里列出——计划 §5.3.1 写的就是"缺 `low/high` 视为点估计并单独标出"，但这恰是最主要的不确定性之一，使用时要留意。
5. **监测清单中预测生成的项不持久化、不可编辑**（见 §15.6）。
6. **精简模式不结算采用率**，也没有 LLM 提议；预测里没有"偏离/平移/声称"这些 LLM 侧的东西。
7. **时间步长由预测自己选**（计划没有规定）：默认 60 步，里程碑时刻有一步的量化误差（`meta.time_resolution_days`）。
8. **不做指标之间的相关性采样**：各参数独立抽样；同一元素的参数在现实里可能相关，独立抽样会低估/高估联合不确定性。
9. **展示只做了元素档案里的预测区块**（计划 §5.8 的"预测简报""HTML 导出"是 A7）；龙卷风图用表格而不是图形。

### 15.10 已知边界

- 全程**未在真实 LLM / 真实模拟实例上验证**（预测本身不调 LLM，但输入的剖面来自 LLM 先验与研究草稿）：区间的宽窄首先取决于剖面里的 `low/high` 是不是诚实。参数多数是 LLM 先验时，输出再精确也只是"在这些假设下的推演"。
- 界面区块只做了静态检查与视图函数单测，**没有在真实 Streamlit 页面点过**。
- 结果不落盘：换页面/重启后需要重新运行。
- 路径 `started_day` 缺失时按 0 处理（条件化重抽会偏短）；旧数据里没有 `meta.clock_day` 的元素按 0 计。
- 条件化与敏感性在样本小（`runs` 小）时噪声很大，界面/文档都不应把小样本差异当结论。

### 15.11 测试（A6）

`tests/test_forecast.py`（57）：精简模式与 `apply_step` 逐位等价、默认路径仍原子、`set_series_value`；分位数（含 inf）；三种分布不越界；确定性三角分布的 P10/P50/P90、成功概率 ≈ 达成概率、全部失败；同种子逐位一致、不改入参；**预算截断 = 前 N 次**、下限、不限；已达成/已解决只报告；进行中路径重抽（不泄漏、条件于已过时长）；区间参数加宽分位带而点估计不加宽、点估计情景、`point_estimates`/`llm_prior_share`；假设频率与条件化（样本不足、无覆盖项、不认识的覆盖键、无先验）；事件 `effects`（add/mul/set、冷却、`var` 条件永不触发、引用解析不到、未确认先验忽略、`rate_range`）与 `normalize_prior` 向后兼容；树分支/门槛/瓶颈顺序/阶段；依赖闭包只跟显式引用且**限定后结果与全量一致**；敏感性排序/公共随机数/确定性/目标校验/预算作废；`apply_edits` 全部种类与坏输入；what-if 与直接重算逐位一致、空操作无差异；监测清单；`reality_check` 往返与旧记录不变；视图；时间精度降级；JSON 可序列化；自动视野与夹值。另有 `test_event_sampler`/`test_reality_check` 既有用例零修改通过。
**13 个变异**（路径重抽被删=泄漏、假设覆盖被删、点估计情景不固定假设、分位数 inf 处理、精简模式漏结算路径、随机数不按身份、事件 effects 不生效、`apply_edits` 改入参、预算下限被删、敏感性不排序、条件化样本门槛被删、点估计参数也被抽样、`var` 条件当可求值）全部转红（首轮有 4 个存活，已补断言）。全量 1884 个用例通过（A6 前基线 1827）。

---

## 16. 预测简报与导出收尾（A7）

A6 给出的是**按元素分散的**预测（分位带、敏感性、监测清单）。A7 把它们**按用户的问题重新组织**成一页：
**能不能成 / 大概什么时候 / 卡在哪 / 靠什么假设 / 该盯什么信号**，加一个**由引擎按规则算出**的置信等级；
同时让静态 HTML 导出带上元素档案与简报，并补齐服务接口。入口模块 `world_simulator/forecast_brief.py`。

### 16.1 核心承诺

1. **置信等级不由 LLM 评判**：规则、扣分、封顶都写在 `forecast_brief.confidence()` 里，随结果一起返回，界面/导出逐条展示；阈值在 `settings.anatomy_params`（`conf_*`）可配置。
2. **不重算业务**：预测数字全部来自 A6 的 `run_forecast / sensitivity / build_watchlist`，A7 只做切片、排序、措辞。`build_brief` 是纯函数；`run_brief` 才会调用预测，仍不调 LLM、不读写磁盘。
3. **措辞诚实**：区间不是校准概率（`forecast.HONEST_NOTE` 原样带出）；"达成概率"一律说成"多少比例的推演运行里达成"；没有回测支撑时明说。
4. **不静默**：元素预测失败 / 没有里程碑 / 判据缺失，都在对应段落里写明原因。
5. **不落盘**（同 A6）：每次现算；固定种子且没被时间预算截断时逐位可复现。

### 16.2 入口

| 入口 | 作用 |
|---|---|
| `forecast_brief.confidence(settings, element, forecast_meta=, evidence=, now=, backtest=)` | 一个元素的置信等级（纯函数） |
| `forecast_brief.overall_confidence([(id, 名, conf)])` | 整个模拟的等级：取各元素中**最低**的一档（保守） |
| `forecast_brief.select_elements(settings, elements=None)` | 简报覆盖哪些元素：显式传入 > 引擎可结算的重点元素 > 退回全部可结算元素（简报里会提示） |
| `forecast_brief.build_brief(settings, results, evidence=, now=, backtest=)` | 把预测结果组织成简报（纯函数） |
| `forecast_brief.run_brief(settings, history, ...)` | 对每个重点元素各做一次（依赖闭包内的）蒙特卡洛 + 敏感性，再 `build_brief` |
| `html_export_anatomy.sections_html(...)` | 导出用：元素档案 + 预测简报 |
| `tool_api.get_anatomy_profile / get_evidence / run_forecast / run_what_if / get_forecast_brief` | 服务接口（见 `service_api.md`） |

`run_brief` 的 `time_budget_sec` / `sens_budget_sec` 是**总**预算，平均分给各元素（`<= 0` 不限）。不设单元素下限，但每个元素的预测至少跑完 50 次（`forecast.MIN_RUNS_BEFORE_TRUNCATE`）；被截断会在简报里明示并扣 5 分。

### 16.3 置信等级规则

起点 100 分，按下表扣分；分数 ≥ `conf_high_min` → 高，≥ `conf_medium_min` → 中，否则低。再叠加两条**封顶**规则。

| 规则 | 条件 | 扣分 | 封顶 |
|---|---|---|---|
| `grounded_share` | 关键条目里「有依据」的比例 < `conf_grounded_ok`（默认 60%） | 20 | — |
| | 比例 < `conf_grounded_low`（默认 25%），或根本没有可核对的条目 | 35 | 最高「低」 |
| `unreviewed` | `anatomy_status` 是 `draft`/`none`（没人审阅过） | 10 | 最高「中」 |
| `stale_evidence` | 被字段引用的证据已过期（按 `research_ttl_days`；**只有传了证据库才检查**，不猜） | 10 | — |
| `wide_intervals` | 带区间参数的 `high/low` 中位数 ≥ `conf_wide_ratio`（默认 3；只统计 `low > 0` 的区间） | 10 | — |
| `point_estimates` | 趋势参数 + 路径耗时里「只有点估计」的占比 > 50% | 10 | — |
| `no_backtest` | 没有回测汇总（`backtest` 为空或 `cases < 1`；默认就是这样，回测反馈要手动开启，见 §17） | 10 | — |
| `backtest_narrow` | **A8**：同类回测里平均覆盖率 < 0.7（< 0.5 为严重）；同类案例不足时退回整体汇总、扣分减半 | 10 / 15（整体 5 / 8） | 严重且同类时最高「中」 |
| `backtest_thin` | **A8**：有汇总但案例 < 3，不下结论 | 5 | — |
| `time_precision` | 预测标了「时间精度降级」 | 10 | — |
| `truncated` | 预测被时间预算截断 | 5 | — |

- **关键条目** = 每个指标（条目本身与现值**各算一个**，与 `basis_stats` 口径一致）、每个瓶颈、每个假设；**有依据** = `sourced` / `user_confirmed` / `user_edited`。
- 参数（`anatomy_params`）：`conf_high_min`=75、`conf_medium_min`=45（1–100 的整数）、`conf_grounded_ok`=0.6、`conf_grounded_low`=0.25（0–1）、`conf_wide_ratio`=3.0（>1）。非法值回退默认并列入 `invalid_param_keys`；配反了（`medium > high`、`low > ok`）时计算处自动压到不超过对方，不会出现到不了的档位。
- 返回值含 `deductions[]`（每条 `rule/label/points/detail`）、`caps[]`、`inputs`（每个输入的实际数值）、`thresholds`，界面与导出逐条展示。
- **高**需要：关键条目大多有出处、剖面已审阅、区间不宽。没有开启回测反馈时任何元素**最高 90 分**（`no_backtest` 扣 10）。
- 整体等级 = 各重点元素中最低的一档，并展示最低元素的扣分原因与各元素的等级。

### 16.4 简报内容

每个元素一节，字段与五个问题的对应：

| 问题 | 字段 | 来源与规则 |
|---|---|---|
| 能不能成 | `answers.success` | **目标里程碑** = 该元素**最后一个有判据且未达成**的里程碑（列表顺序即声明顺序）；全部达成 / 没有判据 / 没有里程碑各有一句专门的说明。文案："在已声明的参数下，「X」在 N 年视野内：过半推演运行里达成（约 59% 的运行）" |
| 何时 | `answers.when` | 目标里程碑的 P10 / P50 / P90（从现在起）；P50 没出现 → 说明"视野内达成的运行不足一半" |
| 卡在哪 | `answers.blockers` | 该元素仍未解决的瓶颈，按「视野内解决比例」从低到高，最多 5 个；带最常走的路径与"全部路径失败"比例 |
| 靠什么假设 | `answers.assumptions` | A6 的条件化结果：成立 vs 不成立对目标的影响；样本不足 / 没有 `if_false` 覆盖项会如实说明；最多 5 个 |
| 盯什么信号 | `answers.signals` | 剖面自带的信号排在预测生成的前面；最多 6 个 |
| 最该担心什么 | `uncertainties` | 前三个：先取敏感性分析里对目标里程碑摆动最大的参数/路径/假设；不够再用预测自带信号（假设效应、瓶颈、目标本身）补齐，每条标注来源 |

另有 `milestones`（全部里程碑行）、`metrics`（指标分位带）、`sensitivity_rows`、`banners`（先验占比、时间精度、截断、敏感性被跳过的情景）、`evidence`（证据计数）和 `confidence`。

### 16.5 HTML 导出

`export_simulation_html(data_dir, sim_id, branch, with_forecast=True, forecast_runs=None, forecast_budget_sec=30.0)`：

- 剖面总开关未开 / 没有带剖面的元素 → 不输出任何东西，也不追加 CSS（**旧实例导出与接入前逐字节相同**，有测试）；
- 每个带剖面的元素一张「🧬 元素档案」卡（来源状态统计、各部分条目与来源标签、体检提示、引擎推进情况、证据表）；证据链接只允许 `http(s)`，所有自由文本转义；
- 引擎有可结算内容时，另有一整页「🔮 预测简报」：整体与各元素的置信等级及扣分原因、五个问题的回答、里程碑 P10–P90 区间条、指标分位带（含目标线）、敏感性龙卷风——全部是**内联 SVG**，页面自包含（无脚本、无外链、无图片，有测试）；
- 简报在**导出时现算**（不落盘）；总预算默认 30 秒（敏感性再占其 1/3），被截断会在页面上明示；页面**不写耗时**，所以固定种子且没被截断时同一份数据导出逐字节一致；
- `with_forecast=False` 只导出档案（快）；任一区块渲染异常降级成一行说明，不连累整个导出；
- 按分支取动态状态（与 P10 机制区块一致）。

### 16.6 服务接口

`tool_api` / HTTP / CLI 三层共用同一套返回结构，详见 `service_api.md` 的「元素档案 / 证据 / 预测」一节：档案、证据、预测重跑、what-if、预测简报，以及 `export-html` 的 `--no-forecast / --forecast-runs / --forecast-budget-sec`。这些接口都是**只读 + 现算**（不写盘，有测试逐文件比对）。

### 16.7 界面

- 详情页（只在开启剖面且引擎有可结算内容时出现）：**「🔮 预测简报」**——选运行次数 / 视野 / 总预算，点「生成预测简报」；结果只放在本次会话里，**推进一步后自动失效**。页面只用普通 markdown / 表格 / 图，不用 expander：不展开任何东西就能读到五个问题的答案。
- 开启了剖面的实例，原有机制面板（归因、质量信号、技术树、事件先验、因果引擎、树接地、体检）**一个不删、行为不变**，收进「⚙️ 显示高级机制面板」开关，默认折起；没开剖面的旧实例页面与之前一致。
- 设置页（开启剖面后）：置信等级的五个阈值，改动立即对之后生成的简报生效。

### 16.8 与计划的偏差（如实记录）

1. **新增两条封顶规则**（计划只写"按规则扣分"）：有依据比例过低 → 最高「低」；未审阅 → 最高「中」。纯扣分会让"全是 LLM 先验"的元素靠别的项得到「中」，与计划 §5.7 的本意（缺依据就不给高置信）冲突。
2. **回测一项在没有回测汇总时恒扣 10 分**，并在扣分里明说原因；A8 已通过 `backtest` 参数接入（见 §17）。
3. **"高级机制"分组范围比计划窄**：计划写"约三十个 expander 收进一个分组"。实际收进的是 5 个机制面板渲染器（归因、技术树、事件先验、因果引擎、树接地）加 3 个直接写在页面里的 expander（质量信号、体检等）。页面里其余的 expander（模拟复盘、问题图谱、能力成熟度等）没动。
4. **分组用开关而不是 expander**：expander 嵌套在旧版 Streamlit 里会报错（`requirements.txt` 写的是 `>=1.32`）；开关 + 缩进包裹不改变任何面板的行为。只对开启剖面的实例生效。
5. **目标里程碑由规则选**（最后一个有判据未达成的），不让用户逐个指定；全部里程碑仍在表里。
6. **简报覆盖重点元素**（引擎可结算的）；没有任何重点元素时退回全部并提示。
7. **修复 A6 遗留的 bug**：时间线「现实回填」表单里取先行信号清单时引用了不存在的变量 `manifest`，导致详情页的时间线对**所有**实例抛 `NameError`（A6 记录里"未在真实页面点过"正是这个原因）。已改为 `_reality_watchlist(sim_id)`，并由 AppTest 回归测试覆盖。

### 16.9 已知边界

- **等级是规则代理，不是校准结论**：阈值（75/45、60%/25%、扣分点数）是判断值，没有在真实结果上验证过；A8 的回测数字才能告诉我们等级与实际覆盖率是否一致。
- 导出时现算会让导出变慢（默认总预算 30 秒 + 敏感性约 10 秒）；大规模实例想快速导出请用 `with_forecast=False`。
- 每个重点元素各跑一次依赖闭包的蒙特卡洛，元素之间共享的上游会被重复计算（结果与全量运行一致，只是更慢）。
- 「有依据」按条目数平均，不按重要性加权；一个指标的现值与趋势各算一个单位。
- 界面只做了 Streamlit AppTest 级别的渲染验证（页面能渲染、按钮能出结果、没有异常），**没有在真实浏览器里目视检查过版式**。
- 导出的 SVG 是简单图形（分位带 / 区间条 / 条形），不含交互。

### 16.10 测试（A7）

- `tests/test_forecast_brief.py`（56）：`fmt_days` 边界；`conf_*` 参数默认/非法回退/配反；置信规则逐条（有依据比例三个区间、`user_edited` 计入、无条目、未审阅封顶、高档可达、回测、证据过期只在传证据且被引用时计、`research_ttl_days` 生效、区间宽度含 `low<=0` 跳过、点估计占比、时间精度/截断、阈值可配置、不改入参且可 JSON 序列化）；整体取最低；选元素（重点优先 / 退回全部 / 显式 / 跳过无可结算内容的）；简报回答五个问题、不确定性优先取敏感性再补、固定种子逐位一致、各元素只含自己的行、目标里程碑各状态、阻塞项排序与封顶、假设无覆盖/样本不足的说明、信号排序、截断标注、先验占比横幅、预测失败的元素仍有一节、预算平均分配且无下限。
- `tests/test_html_export_anatomy.py`（15）：旧实例导出逐字节不变；开关开着但无剖面 / 剖面开关关闭不输出；档案 + 简报 + CSS；`with_forecast=False`；引擎不可结算只出档案；**自包含**（无 script / link / img / src / @import / url()）；可复现；转义与不安全链接不生成 `<a>`；http 证据带 `noopener`；截断提示；区块失败降级；不写盘；三种 SVG（含空输入、目标线撑开纵轴、无 NaN）。
- `tests/test_service_anatomy_api.py`（14）：`tool_api` 五个接口（含 `not_found` / `validation_error` 分流、开关关闭不算错误、what-if 带每条无效原因、只读不写盘逐文件比对）、HTTP 路由与状态码、`export.html` 仅在偏离默认值时才传额外参数（旧调用形状不变）、CLI 子命令。
- `tests/test_app_detail_page_a7.py`（5，Streamlit AppTest 真渲染）：旧实例页面无新内容且不崩；开启剖面的实例有简报与默认折起的开关；点按钮出五段内容与扣分原因；推进一步后简报失效；设置页阈值可改并落盘。
- **12 个变异**（`_reality_watchlist` 还原成原 bug、未审阅封顶改错、目标里程碑改取第一个、有依据区间判错、区间宽度判错、整体改取最高、转义被删、CSS 无条件追加、what-if 丢失原因、HTTP 默认参数多传、预算被改成不限等）：11 个转红；1 个存活（去掉 `sections_html` 里的 `is_enabled` 前置检查）是**等价变异**——下游 `build_profile` / `run_brief` 已各自挡住，输出完全相同。
- 全量 **1974** 个用例通过（A7 前基线 1884，新增 90）。

---

## 17. 指标级回测（A8）

> 代码：`world_simulator/backtest_fit.py`（机械拟合）、`world_simulator/backtest_metrics.py`（案例/资料包/评分/汇总/置信衔接）、`backtest_cases/metric/*.yaml`（6 个案例）、`entrypoints/backtest.py` 的 `metric-check` / `metric-run`。
> 另见 `docs/backtest_guide.md` 末节。**不调 LLM、不联网、不写数据目录**，固定种子可复现。

### 17.1 它回答什么、不回答什么

回答：**骨架（A4 引擎 + A6 蒙特卡洛）给出的 P10–P90 区间，在真实历史序列上是偏窄还是偏宽？**

不回答：LLM 研究出的参数好不好。回测里的参数区间是 `backtest_fit` 从**截止日之前**的历史点**机械拟合**出来的（规则与常数事先声明、没有拿案例调过），所以测的是"骨架 + 机械拟合参数"这条路。它是"参数区间驱动的预测普遍偏自信还是偏保守"的**旁证**，不是对某个具体元素的校准。

### 17.2 防泄漏

- 没有实时检索；"截止日前资料包"= 案例里 `t ≤ 截止日` 的点 / `date ≤ 截止日` 的公告；截止日之后的真值只在评分时出现。
- **运行期金丝雀**：每次运行前把案例里截止日之后的数据全部改成别的值再建一次资料包，两份 `settings` 必须逐字节相同，否则抛 `BacktestLeak`（`metric-check` 也会跑它）。测试里还有一个故意多取 3 年历史的"泄漏版建包函数"，金丝雀必须抓到。
- 预测视野的长度由"最后一个真值的距离"决定（评分窗口），不携带真值内容；`Pack` 的摘要包含视野，金丝雀固定同一视野比较。

### 17.3 拟合规则（`backtest_fit`，全部只用截止日前的点）

| 趋势 | 参数 | 规则 |
|---|---|---|
| `exponential` | `rate_per_year` | `ln v` 对 `t` 最小二乘；区间 = 斜率 ± `t₈₀(n-2)`·标准误 |
| `logistic` | `cap`、`rate_per_year` | `cap` 在 `[1.02·max v, min(物理上限, 5·max v)]` 几何网格上逐个在 logit 空间拟合；残差平方和 ≤ 2× 最小值的 `cap` 都"可接受"，区间取其两端 |
| `learning_curve` | `b` | `ln 成本` 对 `ln 驱动量` 最小二乘（按相同 `t` 配对）；驱动量本身按指数拟合并作为另一条指标推进 |
| 项目工期 | 路径 `duration_days` | `low` = 公告剩余工期；`mode` = 剩余 × `f`；`high` = 剩余 × `f²`；`f = max(1.25, 终版公告总工期 / 初版公告总工期)`（复利式超期启发式） |

### 17.4 评分与判定

每个"运行"= 一个案例 × 一个截止日。指标：区间覆盖率（名义 0.8）、分位损失（对数序列在 ln 空间、线性序列按截止日值归一）、中位预测的 MAPE / 对数误差、区间宽度比；里程碑：真值落在 P10–P90 与否、落在哪一档、中位误差。预测分位带只有 24 个检查点，真值落在点间时插值（对数序列在 ln 空间，对指数曲线精确）；覆盖判定留 1e-4 相对容差（吸收区间退化为点时的离散误差）。

判定看**按运行平均的覆盖率**（每个运行一票）：< 0.5 严重偏窄；< 0.7 偏窄；≤ 0.95 基本合适；> 0.95 偏宽；**案例数 < 3 → 样本不足，不下结论**。

### 17.5 与置信等级衔接（opt-in）

- 设置 `anatomy_params.backtest_feedback`（**默认 false**；设置页有勾选）。关闭时预测简报行为与没有 A8 时逐位一致。
- 开启后，`forecast_brief.run_brief` 读 `reports/backtest_metric/summary.json`（用你自己跑 `metric-run` 得到的；**随仓库的 `baseline_summary.json` 不会被自动使用**，它只是参考基线）。读不到 / 损坏 → 当作"没有回测"。
- 按元素的趋势族（`families_for_anatomy`：指标趋势类型 → 族；有带解决路径的瓶颈 → `project_schedule`）找同类案例：同类 ≥ 3 个案例用同类汇总，否则退回整体汇总（扣分减半、不封顶）；整体也 < 3 个 → `backtest_thin`。
- 旧格式汇总（只有 `{"cases": n}`）按原样只当"有回测"，不扣分。
- 扣分说明里固定附一句：回测里的区间是机械拟合的，LLM/证据给出的区间可能更宽，这是旁证不是校准。

### 17.6 案例与基线结果

| 案例 | 族 | 截止日 | 数据来源 |
|---|---|---|---|
| `cpu_transistors` | exponential | 1982、1989 | Wikipedia Transistor count |
| `solar_module_price` | learning_curve | 1990、2000 | OWID（价格 + 累计装机，2024 美元/瓦、MW） |
| `internet_users_share` | logistic | 2010、2013 | World Bank IT.NET.USER.ZS（World） |
| `mobile_subscriptions` | logistic | 2008、2010 | World Bank IT.CEL.SETS.P2（World） |
| `project_vogtle3` / `project_vogtle4` | project_schedule | 2018-02-28、2021-10-31 | Wikipedia Plant Vogtle 的公告时间线 |

基线（`runs=1000, seed=2024`，2026-10-08 生成，12 次运行）：

| 范围 | 案例 | 运行平均覆盖率 | 判定 |
|---|---|---|---|
| 整体 | 6 | **0.33**（名义 0.8；按点汇总 0.11，14/127） | 严重偏窄 |
| exponential | 1 | 0.00 | 样本不足 |
| learning_curve | 1 | 0.02 | 样本不足 |
| logistic | 2 | 0.23 | 样本不足 |
| project_schedule | 2 | 0.75（4 个里程碑里 3 个落在 P10–P90，中位误差平均 +22%） | 样本不足 |

读法：序列类案例上，拟合误差给出的区间极窄，而外推期里曲线会拐弯（CPU 增速放缓、光伏组件 1990–2004 年价格停滞、移动订阅饱和、2020 年互联网统计口径修订），真值几乎都落在区间外；项目工期这条启发式规则则较宽。**同类案例都不到 3 个，所以只有"整体"一行能下结论。**

### 17.7 与计划的偏差（如实记录）

1. **参数来自机械拟合而非"截止日前资料包里的 LLM/证据参数"**：计划写"案例文件提供截止日前资料包作为证据来源"。让写案例的人手填区间会引入后见之明；所以资料包只放历史点，参数由确定性规则算。代价：测不到 LLM 研究参数的质量（§17.1）。
2. **案例 6 个，覆盖 4 个趋势族**（计划 4–6 个、学习曲线/S 曲线/项目工期三类）。S 曲线两个；项目工期两个但同属一个项目的两个机组、强相关。
3. **没有"里程碑时点误差"的独立聚合排名**，只报每个里程碑的位置与中位误差；案例太少不值得做更细的聚合。
4. **回测反馈默认关闭**（计划写"反馈到置信等级规则"，没说默认）：按保守 opt-in，避免在案例这么少时让所有元素默认被扣分。
5. 新增判定里"案例数 < 3 → 样本不足"与"同类退回整体"两条保守规则（计划未写）。

### 17.8 已知边界

- 案例少且策展（容易找到数据的公开序列）；项目案例是公认的超期项目（选择偏差）；同一案例的两个截止日、同一项目的两个机组强相关，覆盖率的统计功效很弱。
- 数值来自 WebFetch 摘要抓取，**未逐点核对原始资料**（全部 `verified: false`）；Vogtle 的公告日期只到月，按约定补到日（±0.5 月）；World Bank 的 World 聚合 2000–2004 年为空，2025 年是估计值。
- 拟合常数（t 分位 80%、logistic 容忍 2×、cap 上界 5×、超期下限 1.25、窗口）是事先声明的默认值。**看到结果后不要用这几个案例去改它们**（会过拟合），应当加新案例。
- `logistic` 的 `cap` 与 `rate` 在引擎里按独立参数抽样，实际负相关；区间的联合支撑偏宽，但序列案例的区间仍偏窄。
- 项目案例的 `p_success` 固定为 1（没有取消风险），预测视野取 `max(high×1.1, 真值×1.1)`。
- 严重偏窄 + 同类时置信等级封顶「中」只是规则代理，不代表对该元素区间的校准结论。

### 17.9 测试（A8）

- `tests/test_backtest_metrics.py`（53 个）：各拟合规则（恢复已知参数、退化为点、点数不足/非法值）、案例解析（8 类非法输入）、6 个真实案例逐个过金丝雀、资料包忽略未来、**泄漏版建包被金丝雀抓到**、项目案例只取截止日前公告、分位带插值与出视野、分位损失、分位带评分的 P10/P90 边界、里程碑四档位置、精确指数序列全覆盖且逐位可复现、错误制度被报告为未覆盖、判定阈值、汇总与失败记录、`assess_for_element` 四种范围（同类/整体/样本不足/旧格式）、置信扣分与封顶（含整体减半）、`feedback_summary` 的 opt-in、坏文件容忍、随仓库基线与案例一致、简报端到端、CLI。
- **12 个变异**（覆盖下界改成 P50、阈值改错、金丝雀篡改函数改成空操作、对数插值改线性、整体减半去掉、开关被忽略、多取 3 年历史、t 分位改 0、超期下限去掉、封顶去掉、样本不足规则去掉、物理上限去掉）：全部转红（其中前三项的初版测试有缺口，已补用例后再测）。
- 全量 **2027** 个用例通过（A7 后基线 1974，新增 53；沙箱里需 `PYTHONPATH=<repo>/src` 才能让依赖主框架的 39 个既有用例导入 `mini_agent`，与本阶段无关）。既有测试零修改。
