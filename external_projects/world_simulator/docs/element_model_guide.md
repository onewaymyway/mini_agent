# 统一元素模型（第二十三轮）

> 设计依据：仓库 `next_doc/world_simulator_element_causal_lines_plan.md`。
> **实施进度：E1（数据模型、存取层、兼容折叠）、E2（创建阶段元素展开）、E3（发现·去重·登记·补全）已完成**；
> E4–E6（分层与 prompt 预算 / 元素运维 / 联动与收尾）**尚未实施**。本文随各阶段增补，只写已落地的行为，不提前描述后续阶段。

## 它是什么

因果线原来是"领域级"的（`tech`/`finance`/`macro`），一条线背一棵未来树。统一元素模型把
`settings.causal_lines` 升级为"领域 + 元素"两层：领域线只做分组，每个关键元素（技术、项目、公司、
政策、资产……）是独立的一条线，各自有未来树、节奏、因果边。**存储不新开**——仍是
`causal_lines`（分支隔离、快照、未来树、下游全部按它工作），条目上新增一组**可选字段**。

## 当前能做什么（E1）

E1 **只改"数据放在哪、怎么读写"，不改任何 LLM 行为**：没有新的 prompt 段落、没有新的输出协议键、
没有新增 LLM 调用。

### 1. 条目上的可选字段

| 字段 | 含义 |
|---|---|
| `kind` | `domain`（分组）/ `element`（具体元素）；缺省 = 旧式独立线，行为与以前一致 |
| `parent` | 所属领域线 id；缺省 = 未归类（引擎不猜） |
| `element_type` | 自由文本，推荐词表 `technology` `project` `organization` `person` `asset` `policy` `market` `resource` `event_series`；只有 `technology` 有特殊含义（带生命周期） |
| `aliases` | 别名（含曾用 id、中英文名） |
| `origin` | `seed` / `discovered` / `split` / `merged` / `legacy_tech` |
| `born_step` | 登记时的 step |
| `profile_status` | `complete` / `pending_enrichment` / `fallback` |
| `status` | `alive`（缺省）/ `retired` / `merged`（配 `merged_into`） |
| `tier_pin` | 用户固定的分级（`active`） |
| `var_refs` | 该元素状态落在 `vars` 的哪些路径（只读展示） |
| `lifecycle` | 生命周期子对象；E1 里**只有技术元素有**，内容就是原技术节点（见下） |
| `relations` | （E3）`[{with, direction: affects\|affected_by, sign?, note?}]`，推进中发现时声明的元素关系；`with` 登记时解析成规范 id；经 `derived_edges()` 翻成因果边 |

没有这些字段的旧式线逐字节不变。`element_registry.normalize_element()` 只规整**出现了**的字段，
非法取值直接丢弃该字段（回到缺省语义）。

### 2. 技术节点并入元素 `lifecycle`

技术节点的全部字段（阶段/进度/前置/瓶颈/采用率/成本/典型停留……）整体搬进元素的 `lifecycle`；
节点 `id`/`name` 对应线的 `id`/`label`，节点原 `kind`（自由文本）改存 `lifecycle.sub_kind`（避免与线顶层
`kind` 冲突）。

`tech_model.get_nodes()` / `_write_nodes()` 改走**存取适配器**，由开关决定读写哪里：

| 开关 `settings.element_modeling_enabled` | 技术节点存放位置 |
|---|---|
| 未写入 / False（旧实例） | `settings.tech_state.nodes`，与以前逐字节一致 |
| True | `settings.causal_lines[].lifecycle` |

**技术裁决规则（R1–R7、T1–T10、修复调用）一字不改**——规则看到的节点 dict 完全一样，契约测试
（`tests/test_element_tech_adapter.py`）保证同一组输入走两种存储，审计、违规、节点读出结果一致。

LLM 输出协议里的 `tech_updates` 键名不变，按元素 id 寻址；推进中新登记的技术同时建一条技术元素线
（`element_type=technology`、`origin=discovered`、`profile_status=pending_enrichment`、带默认三分支未来树）。
`pending_enrichment` 的补全流程属于 E3，E1 只把状态记下来。

### 3. 开关与默认值

- **新实例**：`materialize_simulation()` 显式写入 `element_modeling_enabled: True`（调用方明确传
  True/False 则尊重）。创建时带的 `tech_state` 种子在落盘时折叠进元素 `lifecycle`；"至少有一条核心
  因果线"的兜底（`main_line`）不受影响。
- **旧实例**：manifest 里没有这个 key = 关闭，一切照旧。可以在设置页手动开启：
  「高级：技术发展模型」折叠区里的"启用统一元素模型"复选框。开启时现有技术节点折叠进因果线（幂等），
  关闭时还原为独立的 `tech_state` 并去掉线上的 `lifecycle`。
- `tech_model_enabled`（技术**规则**开关，要求每步输出 `elapsed_days`、会产生违规记录）保持独立、
  默认仍为关。`element_lifecycle_enabled` 是它的同义别名。

### 4. 折叠旧 `tech_state`（`fold_legacy_tech_state`）

幂等；发生在三个边界：创建落盘、恢复分支快照（`dynamic_state.apply_to_settings`）、元素模式下的
首次写入。分叉/回滚到旧形态快照后会就地折叠那份刚深拷贝出来的工作副本，快照本身不被改写，分支互不污染。
读路径还有一道兜底：元素模式下若仍残留旧 `tech_state` 节点，`get_nodes()` 会并入（线上的优先），所以
任何读路径都不会丢数据。

id 冲突：旧技术节点 id 与已有**非领域**线相同 → 视为同一个东西，把 `lifecycle` 挂上去（不覆盖该线的
label）；与**领域**线相同 → 节点改名 `<id>_tech`（重名再加序号），旧 id 写进 `aliases`，其它节点
`requires[].tech_id` 里对旧 id 的引用同步改写。

### 5. 注册表工具（`element_registry.py`，纯 Python、不调 LLM）

- `resolve(lines_or_settings, ref)`：按 精确 id → 规范化 id → 规范化 label/别名 解析引用，先到先得；
  **只做精确的规范化匹配（NFKC、小写、去空白与标点），不做模糊匹配**——宁可漏合并，不误合并。
- `domains()` / `children_of()` / `group_by_domain()`：领域→子元素查询与展示分组（没有领域线时等价于平铺）。
- `is_alive()`：`status` 缺省视为存活。

### 6. 其它接入点

- 快照：`DYNAMIC_KEYS` 新增 `element_candidates`（E3 才开始写，空值不产生快照）；元素模式下技术节点随
  `causal_lines` 一起快照，不再单独带 `tech_state`。
- 修复调用的回滚点：`tech_model.capture_storage()` / `restore_storage()` 对调用方透明（旧模式回滚
  `tech_state`；元素模式只回滚技术相关部分，不动其它线）。
- 条件写法：`{"element": "id", "min_stage": ...}` 是 `{"tech": ...}` 的同义写法（两者都写以 `tech` 为准）。
- 回测：`extract_tech_nodes()` 同时读旧形态和元素形态的快照，结果一致。
- 设置页：技术节点文本框在元素模式下读写 `lifecycle`；保存时文本框是权威（留空 = 清空，与旧行为一致）。

## 创建阶段：先划领域，再展开元素（E2）

**提示词**：`spec_generator._resolve_causal_lines_hint(stage="create")` 在元素模式下（创建时缺省视为开启，
只有显式 `element_modeling_enabled=False` 才关）改为两步：先规划领域线（`kind=domain`，数量不再限制 2~4），
再对每个领域列出关键元素（`id`/`label`/`element_type`/`parent`/可选 `aliases`、`time_granularity`、
`lifecycle_seed`，以及初始 `future_tree`），`declared_causal_graph` 的端点可以是元素 id。单次调用和
拆分调用（`causal_space_builder`）共用同一个提示，所以两条路径一致。开关显式关闭时仍是原来的"2~4 条线"提示。

**引擎后处理**（`element_registry.prepare_created_lines`，不调 LLM）：规整元素字段 → 按规范化 id/别名去重
（先到先得；**只看 id 和别名，不看 label**，宁可漏合并）→ `parent` 能解析到领域线就改写成领域 id、解析不到
就清空（未归类，不猜）→ 有 `parent` 无 `kind` 视为元素 → 盖创建期戳（`origin=seed`、`born_step=0`、
`profile_status=complete`，只补缺省）→ 技术元素的 `lifecycle_seed` 经 `tech_model.normalize_node` 落成
`lifecycle`（给了 seed 就视为 `technology`）→ 按预算裁剪。没有 `kind` 的旧式草稿原样通过。

**预算**（`settings.element_params`）：

| key | 默认 | 说明 |
|---|---|---|
| `create_max_elements` | 20 | 创建时元素总数（不含领域线）；`null` = 不限 |
| `create_max_per_domain` | 6 | 每个领域下的元素数；`null` = 不限 |

`null` 才是不限，**0 就是 0 个**；负数、非整数、布尔、文本一律回退默认（`invalid_param_keys` 可查）。
裁剪优先级：先验边端点 > `technology` 类型 > LLM 给的顺序；领域线不计数、不裁；保留项和候选都保持原顺序。
**被裁掉的不丢**：进入 `ScenarioDraft.element_candidates`（只在向导里存在，不落盘）。
`declared_causal_graph` 的边不随裁剪过滤，用户加回候选后边仍然有效。

**创建向导**：新增"元素数量上限"折叠区（每项一个"不限"勾选 + 数字框，生成时写入 `settings.element_params`
并随实例保存）；因果线声明文本框下方在存在领域线时显示"领域 → 元素"分组预览（`group_by_domain`），
存在候选时显示"候选元素（勾选加回）"多选框，勾选的在保存时并入因果线（已存在的不重复）。原 JSON
文本框保留作为高级编辑。没有领域线也没有候选时向导和以前视觉一致。

## 推进阶段：发现、去重、登记、补全（E3）

元素模式开启时，`advance()` 在 `_apply_tree_updates` 之前用 `engine/causal_lines.py::_update_element_registry()`
（内部调 `element_registry.process_step()`）替换旧的 `_auto_register_causal_lines`；**未开启（旧实例）仍走旧函数，行为不变**。
全部是纯 Python，**不新增 LLM 调用**。

### 新增的两个可选输出键（写在 `advance_step`/`world_evolve` 的 `{element_hint}` 里）

| 键 | 作用 |
|---|---|
| `discovered_elements` | 这一步出现的新关键对象：`id`/`label`/`element_type`/`parent`/`aliases`/`why_key`/`relations`/`future_tree`/`lifecycle_seed` |
| `element_enrichments` | 给"待补全"元素补信息，字段同上，只补缺的 |

不认识的字段、非数组、缺 id 的项一律忽略，不报错。

### 处理顺序

1. **补全**：命中**待补全/兜底**元素才生效；只补缺（类型/归属只在缺省时填；label 以补全为准；树只在仍是通用兜底模板时替换），
   对已完整元素记 `enrichment_ignored`，对未知 id 记 `enrichment_unknown`。
2. **发现**（每项）：
   - id/label/别名按 `norm_key` 精确规范化命中已登记元素 → **合并**（补别名与关系，记 `merged_alias`），不新建；`gpt5`/`gpt_5` 是同一个；
   - 否则进**候选池** `settings.element_candidates`（随分支）：有关系连到已登记 alive 元素 → 立即登记（`registered`）；
     否则需要 `why_key` 且被**不同步骤**提到 `candidate_promote_mentions`（默认 2）次才转正（`promoted`），同一步重复提只算一次；
   - 预算 `max_total_elements`（默认不限）已满 → 留候选池（`budget_blocked`），**不挤掉已有元素**；领域线与已退场元素不占预算。
3. **引用即登记**：`line_updates` 的 key、`causal_links[].line_id`、`tree_updates[].line_id`、新分支 `effects_if_active[].to_line_id`、
   结构化条件里的 `{"tech"|"element": id}` 先按别名/规范化解析成规范 id（审计 `alias_resolved`，key 被改写；两个 key 落到同一元素时
   先到优先、后到补缺，记 `alias_collision`）；解析不到 → 登记成**待补全桩元素**（`ref_registered`）；命中候选池 → 转正；预算已满 → 保留原 key、id 进候选池。
   `tech_updates` 的 id 只做别名解析、**不登记**（新技术仍由技术裁决登记并同时建元素线）。
4. **兜底**：待补全元素超过 `enrich_grace_steps`（默认 2）步仍未补全 → `fallback`：保留通用树、`parent` 保持空（"未归类"），**不猜领域**；之后任何时候补全仍可转 `complete`。
5. **`lifecycle_seed`**：技术类 + 技术模型开着 → 转成一条合成的 `tech_updates` 登记提议，交给**同一套**技术裁决（T5 阶段夹值、`preexisting` 声明只有一个出口）；LLM 自己对同 id 给了提议则以它为准；技术模型没开只记 `lifecycle_seed_ignored`。

### 关系 → 因果边

`relations` 存在**元素线上**（随分支快照），不写进 `declared_causal_graph`（它不是分支作用域，会让分支 A 的发现漏到分支 B）。
`element_registry.derived_edges()` 把它们翻成边，由 `causal_engine.get_edges()` 与先验结构提示（`resolve_causal_graph_hint`）并入；
与 `declared_causal_graph` 同 id/同端点的以后者为准。只在元素模式、两端都是 alive 元素时产出。

### 审计与 prompt

- `SimState.element_audit`（空时不输出）：`registered/promoted/candidate/budget_blocked/merged_alias/enriched/fallback/ref_registered/
  alias_resolved/alias_collision/enrichment_unknown/enrichment_ignored/lifecycle_seed_queued/lifecycle_seed_ignored/error`。
- `{element_hint}`（`element_registry.build_hint`）：输出协议、可用领域 id、已登记元素索引（上限 `INDEX_MAX`=60，E4 起由分级接管）、待补全请求
  （只在 `born_step+1 … born_step+grace` 的步里问）、候选池前 5 条、**疑似重复提示**（名称词元 Jaccard ≥ 0.5 或较短名 ≥4 字符被包含；只提示不合并）。
- 引擎管线任何异常 → 退回旧最小登记，审计留 `error`，`manifest.settings` 不留半截状态。

### 新增设置（`settings.element_params`，`null`=不限，非法回退默认）

| key | 默认 | 说明 |
|---|---|---|
| `max_total_elements` | `null`（不限） | 运行中 alive 元素总数上限 |
| `candidate_promote_mentions` | 2 | 候选池转正所需被提次数（≥1） |
| `enrich_grace_steps` | 2 | 补全宽限步数（≥0；0 = 不索要，登记当步即按兜底） |

与 E2 的创建期两个键共用同一个 `element_params` 字典，但 `get_params()`（创建期）与 `get_runtime_params()`（运行期）分开读取。

## 当前**不**做什么（后续阶段）

分级与 prompt 预算（E4）；split/merge/retire/reparent 与周期扫描（E5）；领域聚合联动、界面按领域折叠
分组、一致性守卫提示（E6）。因此引擎自己不会创建领域线（推进中发现的新元素 `parent` 只能挂到已有领域线，没有合适的就留空）。向导里已有"领域 → 元素"预览；
因果线总览和静态 HTML 导出的按领域折叠分组留到 E6。

## 已知边界（E3）

- **关键性只做结构校验**：引擎只看"有没有连到已登记元素的关系 / 有没有 `why_key` 且被再次提到"，判断不了"真的重不重要"；阈值 2 次是经验值，需用真实模拟调。
- **去重只做精确规范化**：语义相同但名字完全不同的元素不会自动合并（疑似重复只是提示，合并要等 E5 的 `element_ops.merge`）。
- **LLM 不补全**时元素落到 `fallback`（通用树、未归类），这是如实记录而非掩盖。
- **旧实例中途开启元素模式**：既有无 `kind` 的线仍是旧式独立线；"请 LLM 顺带给 `kind`/`parent`"**本阶段没做**（计划 §5.2），留 E4/E6。
- **引用源未覆盖**：事件 `affects`、`declared_causal_graph` 端点、跨线 `prerequisites` 仍未接入"引用即登记"（前两者是配置而非 LLM 步输出；统一接入留 E6）。
- **独立推进路径** `advance_lines()` 不经过元素管线（E6）。设置页尚无 `max_total_elements` 等运行期参数的编辑控件（只能改 `settings.element_params` JSON；E6）。
- prompt 里的已登记元素索引目前只按登记顺序截尾，元素很多时还没有分级；E4 解决。
- 所有行为以单元/契约测试 + 假 LLM 输出验证，**没有在真实 LLM 下运行过**；较弱的模型可能格式不稳，解析一律"不认识就忽略"。

## 已知边界（E2）

- 领域/元素的划分质量取决于 AI 是否照提示输出 `kind`/`parent`；不照做时退化为一批没有层级的线（与以前
  同类），引擎不会替它猜领域。
- 预算数字写进了提示，但只有引擎裁剪是确定性的；AI 超量输出时多出的部分进候选，需要用户手动加回。
- 去重只看 id 和别名；仅 label 相同的两个元素会同时保留。
- 候选元素只存在于向导的草稿会话里，创建完成后不再提供（要补加走因果线 JSON 编辑）。
- `app.py` 的新增向导代码只做了语法/import 级检查，没有 Streamlit 运行时验证；真实 LLM 下的提示效果未验证。

## 已知边界（E1）

- 新实例默认开启元素模式后，**带技术种子的新实例**里，每个技术节点会作为一条因果线出现在因果线总览
  和后续的 prompt 因果线列表里，且带默认三分支未来树（与既有"自发登记的线"同类）。这是统一 id 空间的
  直接结果；E4 的分级与预算才会控制它们在 prompt 里的展开程度。
- 元素模式下设置页"因果线 JSON"文本框会包含各技术线的 `lifecycle`（体积较大）；保存时技术节点文本框覆盖
  它们。两处同时手改 `lifecycle` 时以技术节点文本框为准。
- 旧实例在**中途**开启元素模式时，既有无 `kind` 的线保持旧式独立线语义；"请 LLM 顺带给出 `kind`/
  `parent`"属于 E3 的补全流程，E1 不做。
- `app.py` 新增的设置页代码只做了静态检查与语法校验，没有 Streamlit 运行时/浏览器级烟雾测试；保存逻辑
  依赖的 `tech_model.replace_nodes()` 有单元测试。
- 所有行为以单元/契约测试验证，**没有在真实 LLM 下运行过**。

## 相关

- 测试：`tests/test_element_registry.py`（28 个）、`tests/test_element_tech_adapter.py`（29 个）、`tests/test_element_creation.py`（28 个）、`tests/test_element_discovery.py`（82 个，E3）
- 设计/记录：`next_doc/world_simulator_element_causal_lines_plan.md`（§10 实施记录）
- 技术规则本身：[`tech_model_guide.md`](./tech_model_guide.md)
