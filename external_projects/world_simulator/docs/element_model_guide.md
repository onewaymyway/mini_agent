# 统一元素模型（第二十三轮）

> 设计依据：仓库 `next_doc/world_simulator_element_causal_lines_plan.md`。
> **实施进度：E1 已完成**（数据模型、存取层、兼容折叠）；E2–E6（创建阶段元素展开 / 发现·去重·补全 /
> 分层与 prompt 预算 / 元素运维 / 联动与收尾）**尚未实施**。本文随各阶段增补，下面"当前能做什么"
> 一节只写 E1 已落地的行为，不提前描述后续阶段。

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

## 当前**不**做什么（后续阶段）

创建时先划领域再展开元素（E2）；推进中发现新元素、别名去重、关键性门槛、下一步 prompt 顺带补全（E3）；
分级与 prompt 预算（E4）；split/merge/retire/reparent 与周期扫描（E5）；领域聚合联动、界面按领域折叠
分组、一致性守卫提示（E6）。因此 **E1 里引擎自己不会创建领域线**，`group_by_domain` 目前只是工具函数，
界面与导出的"按领域分组"渲染接线留到 E2（那时才会出现领域线）。

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

- 测试：`tests/test_element_registry.py`（28 个）、`tests/test_element_tech_adapter.py`（29 个）
- 设计/记录：`next_doc/world_simulator_element_causal_lines_plan.md`（§10 实施记录）
- 技术规则本身：[`tech_model_guide.md`](./tech_model_guide.md)
