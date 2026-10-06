# 元素剖面（anatomy）指南

> 第二十四轮。设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md`。
> **本文只写已落地的行为**，随阶段增补。当前已落地：**A1（数据模型、存取层、兼容、只读档案）、A2（创建期拆解 + 向导审阅）、A3（联网证据研究 + 出处）**。
> A4 引擎骨架、A5 推进期深化、A6 蒙特卡洛、A7 预测简报、A8 指标回测尚未实施。

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
- `maps_to_stage` 只当字符串保存，不校验是否是 `tech_model.STAGES` 之一（A4 判定时处理）。
- `adoption_gates.unlocks` 只认 `adoption_cap`，其余键被丢弃，A4 定义更多解锁项时再放开。
- 跨元素引用（`元素#子项`）A1 不解析；解析复用 `element_registry.resolve`，在 A4 条件求值时接入。
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
