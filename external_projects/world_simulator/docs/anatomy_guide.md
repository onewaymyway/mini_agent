# 元素剖面（anatomy）指南

> 第二十四轮。设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md`。
> **本文只写已落地的行为**，随阶段增补。当前已落地：**A1（数据模型、存取层、兼容、只读档案）、A2（创建期拆解 + 向导审阅）、A3（联网证据研究 + 出处）、A4（引擎定量骨架）**。
> A5 推进期深化、A6 蒙特卡洛、A7 预测简报、A8 指标回测尚未实施。

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

### 13.10 已知边界（A4）

- 全程**未在真实 LLM 下验证**：LLM 是否会把"预计本步发生"的事项如实写进叙事、是否在没有 `anatomy_updates` 协议时仍擅自改写数值（A4 只靠提示词约束 + `next_vars` 本来就由 LLM 整体重写，**引擎的指标值不会写回 `vars.*`**，计划 §9 风险 11 的"不镜像"结论沿用）。
- 趋势参数大多来自 LLM 先验或创建期草稿，引擎结算得再精确也只是"在这些假设下的推演"，不是预测；区间与置信等级是 A6/A7 的事。
- 界面新增的「⚙️ 引擎推进」区（曲线/瓶颈/里程碑/逐步变化）只做了静态检查与视图函数单测，**没有在真实 Streamlit 页面点过**。
- 组件就绪度没有 LLM 提议入口；新增子项发现（`new_subitems`）、深度模式在 A5。
- 事件触发的趋势（`step_events`）依赖事件采样已开启并抽中对应 id。
- `deviation` 的小幅容忍（≤ 20% 不需要原因）理论上可被反复利用来缓慢拉偏数值；A4 没有协议向 LLM 索要偏离，真实风险要到 A5 才出现，届时再评估是否改成"一律要原因"。

### 13.11 测试（A4）

`tests/test_anatomy_engine.py`（110）：表达式（合法/恶意输入/运行时护栏/不用 `eval`）、每种趋势的数值与"步长无关"、学习曲线驱动顺序、随机游走种子复现与分支敏感、重新锚定、同一步只结算一次、瓶颈路径（精确到期日、`fallback` 顺序、耗尽、`requires`、缺时长、成功概率与三角分布的统计、种子隔离）、`A1`–`A11` 各码、偏离 rebase/keep_model/绝对函数偏移、里程碑只增不减与组合判据/跨元素引用、阶段派生/`progress`/A3 回滚/下降驳回/关闭与无映射时旧逻辑不变、门槛与 A7（含"不追溯"）、`require_review_to_drive`、时间降级、A9、流水自洽与篡改检测、原子写回与 `A0`、引擎输出被 A1 规整层原样保留、引擎状态不被种子/草稿带入、条件语法扩展（事件先验与树分支）、提示词与预览一致、`advance()` 端到端（持久化/快照/真实分叉回滚/可复现/LLM 无法绕过/失败不影响推进/开关关闭与无剖面实例不变）、档案推进视图。
**24 个变异**（A2/A3 驳回被删、阶段声明不回滚、里程碑可重复达成、`fallback` 顺序被忽略、成功概率被忽略、种子不含分支、A1 阈值被删、rebase 偏移被删、夹值被删、A7 夹值/不追溯被删、不重新锚定、同一步重复结算、被压掉的事件算命中、驱动顺序被删、非原子写回、A9 不去重、表达式属性检查/幂护栏被删、审阅开关被忽略、流水衔接核对被删、平移可到过去、预览改了真实状态）全部转红。全量 1787 个用例通过（A4 前基线 1677）。
