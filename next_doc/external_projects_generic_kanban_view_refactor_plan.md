# 外部项目通用看板视图机制（重构 stock_watch 专属实现为通用能力）

> **这篇文档管什么**：把 `stock_watch_pool_state_tracking_and_kanban_
> plan.md` 阶段4里实现的「📊 候选池状态跟踪」面板——目前是**认死
> stock_watch 这一个项目名、写死状态集合/字段名/entrypoint 名**的专属
> 代码——重构成任何外部项目都能通过 `project.yaml` 声明去接入的通用
> 看板能力，不用再让人给每个新项目单独写一块 UI。
>
> **不管什么**：不改动 `🗂️ 外部项目` tab 已有的通用框架（项目卡片、
> 健康徽标、执行账本、手动触发+参数表单、backlog、review 预览，见
> `external_projects_kanban_integration_plan.md`）——那部分本来就是
> 通用的，这次只重构"状态看板"这一块。不改动 `candidate_pool.py`/
> `run_pool_tracking.py` 的业务逻辑本身（状态机、区间收益计算），
> stock_watch 那边的改动只是"把输出数据套进新的通用 schema"。

## 1. 背景：现在的实现为什么不通用

`stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段4落地后，
`apps/mini_agent_kanban/app.py::_render_pool_tracking_panel()` 里有
三处硬编码：

```python
path = manifest.source_dir / "data" / "pool_tracking_latest.json"  # 文件名/路径写死
```
```python
_POOL_STATE_LABEL = {
    "watching": "👀 观察池", "focused": "🔎 重点关注", ...             # 状态集合写死
}
```
```python
client.trigger_external_project_run(name, "change_pool_state", ...)  # entrypoint 名写死
```

以及后端路由 `GET /v1/external_projects/{name}/pool_tracking` 本身
路径和函数名都带着 `pool_tracking` 这个 stock_watch 专属概念，其它
项目即使也想要一个"状态列看板"，得照抄一遍这一整条链路（新路由 + 新
`AgentClient` 方法 + 新渲染函数），看板代码量随接入项目数线性增长，
且没有复用空间。

## 2. 设计目标

任何外部项目，只要满足以下两个条件，就能不改一行看板代码，直接在
`🗂️ 外部项目` tab 里获得一个状态列看板：

1. 在 `project.yaml` 里声明一段 `dashboard.kanban_view`（下节详述
   schema），描述"数据在哪个文件、每张卡片认哪个字段做主键/标题/
   状态、状态有哪些取值、怎么改状态"。
2. 项目自己的某个 entrypoint 按声明的字段名产出对应的 JSON 文件。

看板前端读这段声明去动态画列、画卡片、画"变更状态"表单，不需要认
任何具体项目名或字段名。stock_watch 是**第一个接入方**，而不是"看板
认识的唯一项目"。

## 3. `project.yaml` 新增 schema：`dashboard.kanban_view`

```yaml
dashboard:
  kanban_view:
    data_file: "data/pool_tracking_latest.json"   # 相对项目根目录，看板读这个文件
    id_field: "code"                                # 每条记录的唯一标识字段名
    title_field: "name"                              # 卡片标题字段名
    state_field: "state"                              # 决定分栏的字段名
    states:                                            # 列的顺序 + 展示标签
      - value: "watching"
        label: "👀 观察池"
      - value: "focused"
        label: "🔎 重点关注"
      - value: "buy_suggested"
        label: "🟢 建议买入"
      - value: "holding"
        label: "📌 已建仓"
      - value: "sell_suggested"
        label: "🔴 建议卖出"
      - value: "dropped"
        label: "⚪ 已淘汰"
        collapsed: true                                # 可选，默认 false；折叠列不占列位，塞进一个 expander
    metric_fields:                                      # 卡片正文展示哪些字段，按顺序
      - field: "current_price"
        label: "当前价"
        format: "number"                                 # "number" | "percent" | "text"，决定展示格式
      - field: "score"
        label: "分数"
        format: "number"
    detail_list_field: "reasons"                          # 可选：展开卡片后展示的字符串列表字段（信号溯源类信息）
    change_state:                                          # 可选：不声明则看板不渲染"变更状态"表单
      entrypoint: "change_pool_state"                       # 必须是本项目已声明的 entrypoint key
      id_param: "code"                                      # 传给 entrypoint 的参数名，取值来自 id_field
      state_param: "state"                                   # 传给 entrypoint 的参数名，取值是目标状态
      note_param: "note"                                     # 可选：备注参数名，不声明则表单不带备注输入框
```

设计取舍：

- **`states` 用列表而不是字典**：字典在 YAML 里没有稳定的迭代顺序
  保证（虽然 Python 3.7+ dict 实际有序，但 YAML/JSON 语义上不应该
  依赖这一点），列表显式声明列的展示顺序，看板不用另外猜。
- **`change_state` 整体可选**：只读展示（没有状态变更需求的项目，比如
  纯监控类项目）可以只声明 `states`/`metric_fields` 不声明
  `change_state`，看板据此判断要不要渲染变更表单，不强迫每个接入方
  都得有一个能改状态的 entrypoint。
- **`change_state.entrypoint` 复用现成的 entrypoints 声明，不新增
  一套"看板专属命令"概念**：变更状态本质上就是"触发一次
  entrypoint、带上几个参数"，与「▶️ 手动触发」区块走的是同一条后端
  路径（`trigger_run`/`build_cmd_with_params`），只是看板给了一个更
  顺手的入口（选状态而不是手填字符串）。校验 `change_state.
  entrypoint` 引用的 key 确实存在于 `entrypoints` 里，是 manifest
  层解析时就要做的完整性检查（第4节）。
- **`detail_list_field` 只支持"一个字符串列表字段"这一种形状**：先
  覆盖 stock_watch 的 `reasons` 这个已验证有用的形状，不提前设计
  更复杂的"任意嵌套结构展示"——YAGNI，等真的出现第二种需要展示的
  详情结构时再扩展 schema，不要为了"看起来通用"而设计一堆当前没有
  消费方的字段。
- **不做字段级的数值格式化 DSL**（比如自定义千分位、自定义小数位）：
  `format` 只有 `number`/`percent`/`text` 三档，够 stock_watch 用，
  也是大多数看板类需求的最大公约数；更精细的格式化需求出现时再加
  枚举值，不需要引入模板语言。

## 4. `manifest.py` 改造

新增 dataclass（风格对齐既有的 `ParamSpec`/`EntrypointSpec`）：

```python
@dataclass
class KanbanStateSpec:
    value: str
    label: str
    collapsed: bool = False

@dataclass
class KanbanMetricSpec:
    field: str
    label: str
    format: str = "text"   # "number" | "percent" | "text"

@dataclass
class KanbanChangeStateSpec:
    entrypoint: str
    id_param: str
    state_param: str
    note_param: Optional[str] = None

@dataclass
class KanbanViewSpec:
    data_file: str
    id_field: str
    title_field: str
    state_field: str
    states: List[KanbanStateSpec]
    metric_fields: List[KanbanMetricSpec] = field(default_factory=list)
    detail_list_field: Optional[str] = None
    change_state: Optional[KanbanChangeStateSpec] = None
```

`ProjectManifest` 新增可选字段：

```python
kanban_view: Optional[KanbanViewSpec] = None
```

`parse_manifest()` 新增 `_parse_kanban_view(data.get("dashboard", {}).
get("kanban_view"))`，校验规则：

- `data_file`/`id_field`/`title_field`/`state_field` 必填非空字符串。
- `states` 必须是非空列表，每项 `value`/`label` 必填，`value` 不允许
  重复（重复的状态值会让分栏逻辑产生歧义）。
- `metric_fields` 可选，`format` 只能是三个枚举值之一，非法值直接
  `ProjectManifestError`（而不是静默降级成 `text`——沉默降级会让
  项目作者以为声明生效了实际上没生效，与 `_parse_params()` 里
  `required`/`default` 类型校验失败直接报错的既有原则一致）。
- `change_state.entrypoint` 声明的 key 必须存在于同一份
  `project.yaml` 的 `entrypoints` 里，**这一条校验必须放在
  `parse_manifest()` 里"先解析完 entrypoints 再解析 kanban_view"
  之后做**，因为它是跨字段的引用完整性检查（`_parse_entrypoint()`
  本身不知道后面还有没有 `kanban_view` 引用自己）；不存在的 key
  在 manifest 解析阶段就报错，而不是等用户在看板上点"变更状态"时
  才发现 entrypoint 找不到。
- `data_file` 不做路径穿越校验（不校验是否包含 `..`）：这与现有
  `entrypoints.*.cmd` 本来就是"任意 shell 命令、需要作者自己对
  project.yaml 的可信度负责"是同一个信任边界——`project.yaml` 本身
  就是项目作者控制的文件，不是不可信输入；真正需要校验的是"读取时
  最终路径必须落在 `manifest.source_dir` 内"，这一条放在**读取时**
  （下节的路由实现）而不是解析时做，因为解析时 `source_dir` 可能
  还没确定（`parse_manifest()` 支持从纯文本字符串解析，不一定绑定
  磁盘路径）。

## 5. 后端：通用路由替换 stock_watch 专属路由

删除阶段4引入的 `GET /v1/external_projects/{name}/pool_tracking`
（专属路由），替换为通用路由：

```
GET /v1/external_projects/{name}/kanban_data
```

实现要点：

- 项目未声明 `kanban_view` → 返回 `{"available": False}`（与阶段4的
  既有约定一致，前端据此决定要不要渲染这块面板）。
- 声明了但 `data_file` 不存在 → 同样 `{"available": False}`（文件
  还没产出，比如还没跑过对应 entrypoint，不是错误状态）。
- 文件存在但解析失败（损坏的 JSON）→ `{"available": True, "error":
  "..."}`（与阶段4既有约定一致：这种情况要明确报错，不能和"没有这个
  功能"混为一谈）。
- **路径校验**：`(manifest.source_dir / kanban_view.data_file).
  resolve()` 算出的绝对路径必须仍然落在 `manifest.source_dir.
  resolve()` 内部，否则拒绝读取并返回 400——防止恶意/写错的
  `data_file`（比如 `../../etc/passwd`）借着这条通用路由读到项目
  目录之外的文件。这条校验在阶段4的专属路由里因为路径是硬编码的
  `"data/pool_tracking_latest.json"`、没有用户可控输入，所以不需要；
  通用化之后 `data_file` 来自 `project.yaml`（虽然如第4节所说这不是
  不可信输入，但"防御性校验成本很低、且能拦住手滑写错路径"，仍然值得
  做，是纵深防御而不是信任边界判断的改变）。
- 读到的 JSON 内容原样透传，不做字段改写——`kanban_view` schema 里
  已经声明了字段名，前端按声明去取值，后端不需要、也不应该替换成
  统一字段名再返回（那样反而是后端在替前端做本该由 schema 驱动的
  解析工作，两边都要维护一份"字段名映射"容易漂移）。

`status.py::aggregate_status()` 里每个项目的返回字典新增
`kanban_view` 字段（`manifest.kanban_view` 序列化成 dict，`None` 时
为 `null`），供前端判断"这个项目有没有声明看板视图"而不必额外发一次
请求去探测——与现有 `entrypoints`/`params` 字段"manifest 里声明的
契约随聚合状态一起下发"是同一个模式。

## 6. `AgentClient`（`apps/mini_agent_kanban/client.py`）

```python
def external_project_kanban_data(self, name: str):
    """通用看板视图的结构化数据（`external_projects_generic_kanban_view_
    refactor_plan.md`）。项目未声明 `dashboard.kanban_view` 或数据文件
    尚未产出时返回 `{"available": False}`。"""
    return self._get(f"/external_projects/{name}/kanban_data")
```

删除阶段4引入的 `external_project_pool_tracking()`（专属方法，被上面
这个通用方法取代）。

## 7. 前端：通用渲染函数替换专属面板

`_render_pool_tracking_panel()` 整体删除，替换为
`_render_kanban_view_panel(client, name, kanban_view_spec)`：

- 从 `proj["kanban_view"]` 拿 spec（`aggregate_status()` 已经带出来，
  不用再单独请求一次去判断"有没有"），`None` 时函数直接返回，不渲染
  任何 UI（不发起 `kanban_data` 请求——省一次网络往返）。
- 有 spec 时才调 `client.external_project_kanban_data(name)` 拿实际
  数据，`available: False` 时同样不渲染（数据还没产出，与"没声明"
  视觉上一致，都是"这次看不到内容"，但原因不同——前者在 `st.caption`
  里简单提示"暂无数据，可能还没跑过对应任务"）。
- 按 `spec.states` 的顺序动态生成列（`collapsed=True` 的状态放进
  `st.expander` 而不是列），每条记录按 `spec.state_field` 分组，
  卡片标题取 `spec.title_field`，正文按 `spec.metric_fields` 声明的
  顺序展示（`format` 决定 `f"{v:.2f}"`/`f"{v:.2f}%"`/`str(v)`）。
- 有 `spec.change_state` 时才渲染变更状态表单，`st.selectbox` 的选项
  来自 `spec.states`，提交时调用 `trigger_external_project_run(name,
  spec.change_state.entrypoint, params={spec.change_state.id_param:
  <id_field 的值>, spec.change_state.state_param: <选中的状态>, ...})`
  ——**这一步复用的是已有的通用触发链路，本函数不新增任何执行逻辑**。
- 有 `spec.detail_list_field` 时，展开卡片展示该字段（字符串列表）。
- **不动**"各状态区间表现汇总"（阶段4的 4.4 回溯统计）这块——这是
  "对涨跌幅字段做聚合统计"，属于 stock_watch 这类"字段语义是价格/
  收益率"的项目才有意义的功能，不是所有 kanban_view 接入方都需要，
  暂不纳入通用 schema。留在 stock_watch 专属层面：如果 `metric_
  fields` 里某个 `format="percent"` 的字段值可以做"平均值/胜率"这类
  统计，可以在通用面板里加一个"数值型 metric 的简单汇总"作为**可选
  增强**（不是本次重构的必须项，见第9节"暂不做"）。

## 8. stock_watch 侧改动

`external_projects/stock_watch/project.yaml` 新增 `dashboard.
kanban_view` 声明（对照第3节 schema，字段名直接对应
`candidate_pool.py`/`report.py` 已有的输出字段：`code`/`name`/
`state`/`current_price`/`score`/`reasons`），**不改动**
`candidate_pool.py`/`run_pool_tracking.py` 的业务逻辑本身。

`report.py::write_pool_tracking_json()` 的输出结构本来就已经是
"列表，每条含 `code`/`name`/`state`/`current_price`/`score`/
`reasons`"，与通用 schema 的字段需求天然吻合，这次不需要改动输出
格式，只是给 `project.yaml` 补一段声明去"认领"这些字段名。

## 9. 明确暂不做的部分（避免过度设计）

- **不支持"一个项目声明多个 kanban_view"**：一个项目一个看板视图，
  多视图需求出现前不设计 `kanban_view` 数组形式的 schema。
- **不支持看板侧的自定义排序/筛选/搜索**：先满足"按状态分栏"这个
  最基本的诉求，排序/筛选是纯前端体验优化，效果不够时再加。
- **不支持看板侧编辑除"状态"外的其它字段**：`change_state` 只覆盖
  状态变更这一种写操作；如果某个项目需要"编辑分数""编辑备注"，仍然
  走「▶️ 手动触发」区块里通用的 entrypoint+params 表单，不纳入
  kanban_view 专属 schema。
- **不做数值型 metric 的自动汇总统计**（阶段4 4.4 那种"胜率/平均值"）
  ——如第7节所述，先不做，等确认这是通用需求而不是 stock_watch 特例
  后再补。

## 10. 迁移与兼容性

这是内部重构，不是面向外部用户的公开 API，采取**直接替换、不做
过渡期兼容**的策略（更符合仓库目前的迭代节奏——参照
`external_projects_kanban_integration_plan.md` 阶段5把"手填 entrypoint
key"直接改成按钮列表，没有保留旧交互）：

- 删除 `pool_tracking` 专属路由/client方法/渲染函数，替换为
  `kanban_data`/`external_project_kanban_data`/
  `_render_kanban_view_panel`。
- `stock_watch/project.yaml` 同一个 PR 里补上 `dashboard.kanban_view`
  声明，不存在"新旧看板视图并存"的过渡状态。
- 现有阶段4的测试（`tests/test_api_external_projects_routes.py` 里
  `test_pool_tracking_*` 系列）随之删除，替换为等价的
  `test_kanban_data_*` 测试用例（覆盖同样的场景：未声明/文件不存在/
  文件存在/解析失败/路径越界拒绝/项目未注册）。

## 11. 实施步骤（每完成一项打勾，完成后在第12节补一行变更记录）

### 阶段 A：`manifest.py` schema + 校验 ✅
- [x] `KanbanStateSpec`/`KanbanMetricSpec`/`KanbanChangeStateSpec`/
      `KanbanViewSpec` 四个 dataclass
- [x] `ProjectManifest.kanban_view` 字段
- [x] `_parse_kanban_view()` 及校验规则（第4节全部规则，含
      `change_state.entrypoint` 引用完整性检查）
- [x] 单元测试：合法声明解析成功 / 各必填字段缺失报错 / `states`
      重复值报错 / `format` 非法枚举报错 / `change_state.entrypoint`
      引用不存在的 key 报错 / 未声明 `dashboard.kanban_view` 时
      `manifest.kanban_view is None`（向后兼容，不影响没有这段声明的
      现有外部项目）
      → `tests/test_external_projects_kanban_view_manifest.py`，
      新增 45 项用例（含既有 `test_external_projects.py` 回归）全部
      通过。

### 阶段 B：后端路由 + status 聚合 ✅
- [x] `GET /v1/external_projects/{name}/kanban_data`（含路径越界防护）
- [x] 删除 `GET /v1/external_projects/{name}/pool_tracking`
- [x] `aggregate_status()` 新增 `kanban_view` 字段
- [x] 单元测试：未声明返回 available=false / 文件不存在返回
      available=false / 文件存在正常返回 / 解析失败返回 error 字段 /
      `data_file` 试图路径穿越时拒绝并返回 400 / 项目未注册 404
      → `tests/test_api_external_projects_routes.py`，`pool_tracking`
      系列测试替换为 `kanban_data` 系列，新增
      `test_status_route_includes_kanban_view_contract`。全部 28 项
      通过。

**实现中发现并处理的一处 gap（原文档未覆盖）**：`report.py::
write_pool_tracking_json()` 的实际输出是 `{"generated_at": ...,
"entries": [...]}`（记录数组包在 `"entries"` 键下），不是文档第8节
描述的"顶层直接是列表"。`kanban_data` 路由按"原样透传 dict"处理，
`data_file` 顶层若是 JSON 数组则包一层 `{"available": true, "entries":
[...]}` 与既有 `"entries"` 键约定对齐，保证两种顶层形状（dict/array）
在响应里都固定通过 `entries` 键取记录列表，前端（阶段C）不需要再区分
顶层形状。

### 阶段 C：`AgentClient` + 前端通用渲染 ✅
- [x] `external_project_kanban_data()`，删除
      `external_project_pool_tracking()`
- [x] `_render_kanban_view_panel()` 通用渲染函数，删除
      `_render_pool_tracking_panel()`
- [x] 状态列（含 `collapsed`）、卡片 metric 展示、`detail_list_field`
      展开详情、`change_state` 表单，均按 spec 动态生成，函数体内不出现
      任何 stock_watch/pool_tracking 相关的硬编码字符串

### 阶段 D：stock_watch 接入 + 回归验证 ✅
- [x] `external_projects/stock_watch/project.yaml` 补
      `dashboard.kanban_view` 声明
- [x] 全量回归：外部项目相关测试文件 + stock_watch 自身测试全部通过
      （`external_projects/stock_watch/tests/` 43 项、
      `tests/*external_projects*`/`test_api_external_projects_routes.py`
      等 128+28 项，均通过；另跑了 `-k "external_projects or kanban"`
      全量筛选，10 项失败均与本次改动无关——`test_goal_execution_spec_
      kanban_routes.py`/`test_notification_dispatcher.py` 因缺测试环境
      的 `external_input` 目录 fixture 失败，`test_kanban_growth_
      dragdrop.py` 一项因 `FakeClient` 缺 `get_latest_async_job` 方法
      失败，均为改动前既存的环境/fixture 问题，未触碰这几个文件）
- [x] 文档同步：本文件状态更新；`stock_watch_pool_state_tracking_
      and_kanban_plan.md` 第4节补充指引；
      `docs/kanban-dashboard-guide.md`「🗂️ 外部项目 Tab」一节改为
      描述通用 `kanban_view` 机制；
      `external_projects_kanban_integration_plan.md` 补变更记录

## 12. 变更记录

- 2026-08-27：文档创建，设计确认（第1-10节）。阶段 A-D 待开始。
- 2026-08-27：阶段 A 完成。`manifest.py` 新增
  `KanbanStateSpec`/`KanbanMetricSpec`/`KanbanChangeStateSpec`/
  `KanbanViewSpec` 四个 dataclass 及 `ProjectManifest.kanban_view`
  字段；`_parse_kanban_view()`（连同 `_parse_kanban_states()`/
  `_parse_kanban_metric_fields()`/`_parse_kanban_change_state()`）
  实现第4节全部校验规则，在 `parse_manifest()` 里于 entrypoints
  解析完成后调用，满足 `change_state.entrypoint` 的跨字段引用完整性
  检查顺序要求。新增
  `tests/test_external_projects_kanban_view_manifest.py`。
- 2026-08-27：阶段 B 完成。`api/routes.py` 用
  `GET /v1/external_projects/{name}/kanban_data` 替换阶段4的
  `pool_tracking` 专属路由（含 `data_file` 路径越界防护、
  available/error 语义与阶段4对齐）；`status.py::aggregate_status()`
  新增 `kanban_view` 字段序列化。测试同步更新为
  `test_kanban_data_*` 系列。
- 2026-08-27：阶段 C 完成。`apps/mini_agent_kanban/client.py` 新增
  `external_project_kanban_data()`，删除
  `external_project_pool_tracking()`；`app.py` 新增
  `_render_kanban_view_panel()`/`_kanban_metric_display()`，删除
  `_render_pool_tracking_panel()`/`_POOL_STATE_LABEL`/
  `_POOL_STATE_COLUMN_ORDER`，调用点改为
  `_render_kanban_view_panel(client, name, proj.get("kanban_view"))`。
  未纳入通用渲染的部分（阶段4 4.4"各状态区间表现汇总"）按第7节所述
  留在 stock_watch 专属层面，本次不做。
- 2026-08-27：阶段 D 完成。`external_projects/stock_watch/project.yaml`
  补 `dashboard.kanban_view` 声明（字段对应 `report.py::
  write_pool_tracking_json()` 已有输出，未改动业务逻辑），
  `load_manifest()` 验证解析通过、`change_state.entrypoint` 引用
  校验通过。全量回归：`external_projects/stock_watch/tests/`
  43 项、外部项目相关测试 128+28 项均通过。文档同步见下方各文件的
  对应更新。

**实现细节留档**：`kanban_data` 路由把 `data_file` 顶层内容统一整理成
`entries` 键下的记录数组（阶段B已记录的 gap 处理），前端
`_render_kanban_view_panel()` 固定读 `resp.get("entries")`，不需要
关心 `data_file` 顶层原本是 dict 还是 array。
