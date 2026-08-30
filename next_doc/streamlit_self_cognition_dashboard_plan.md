# Streamlit 看板 · 自我画像 / 能力地图（自我认知信息）实施记录

## 背景 / 需求

用户需求：在 Streamlit 自我状态看板里，补充当前 agent 的**自我画像**信息，
以及**能力地图**之类的信息——让 agent 自我认知相关的内容、以及积累的历史
信息，都能在看板里看到（不单独新开一个"自我认知看板"，理由见下）。

## 现状盘点

代码库里"自我认知"相关的数据实际上已经分散存在，只是从未在看板里聚合
展示过：

| 数据 | 来源 | 粒度 |
|---|---|---|
| identity（purpose）/ self_assessment（strengths/weak_areas/confidence_by_domain）/ operating_state（autonomy_level/累计 session 数/涉足项目数） | `perception/global_knowledge.py::SelfProfile`（落盘于 `self_profile.json`） | 跨 session、跨项目，慢变量 |
| 当前 workdir 实测能力地图（domain → success/failure/confidence） | `evolution/consolidation.py::build_capability_map()` | 单 workdir，实时重算 |
| 能力弱项数量走势（日频快照） | `evolution/self_model_snapshot.py`（落盘于 `self_model_history.jsonl`） | 时间序列，90 天保留窗口 |
| 已发现技能目录（含未激活） | `skills/__init__.py::SkillLoader.get_catalog()` | 当前 session |
| session 级实时聚合视图（AgentSelfModel，含 internal_state/affordance） | `perception/self_model.py` | 只在 system prompt 里用，非看板场景 |

`AgentSelfModel` 是 session 级、注入 system prompt 用的运行时对象，没有
独立的只读 HTTP 端点，也不适合直接暴露（其中 internal_state 是给模型自己
看的措辞，不是给人看的展示格式）；看板新增的聚合改为直接读
`SelfProfile`/`build_capability_map`/`self_model_history`/`SkillLoader`，
数据来源相同但走独立的展示格式。

## 方案：加区块，不新开 Tab

评估后选择在已有的"🧠 自我状态"Tab 末尾追加"🪞 自我画像 / 能力地图"区块，
而不是新建"自我认知看板"独立 Tab：

- 语义上这些数据本来就是"自我状态"的一部分，跟本 Tab 已有的自主循环
  摘要、诊断反馈、执行公平性等属于同一层级；
- 看板已有 9+ 个 Tab，新增 Tab 会进一步稀释导航，而用户想看"我现在是
  什么样子"时，认知上更希望在同一个地方看全，而不是在两个高度相似的
  Tab 之间切换判断该去哪个。

## 改动内容

### 1. 后端新增只读聚合端点

`GET /v1/self/portrait`（`src/mini_agent/api/routes.py`）：一次性聚合
`identity` / `self_assessment` / `operating_state`（读 `SelfProfile`）、
`capability_map`（当前 workdir 实测，`build_capability_map(paths, None)`
只读模式，不写回 memory）、`capability_trend`（`self_model_history.jsonl`
最近 30 条快照的弱项数量，不返回完整 `capability_snapshot` 避免响应过大）、
`skills`（`SkillLoader.get_catalog()`）。全部只读聚合，不触发任何计算/
写入；单用户模式下也可用（不依赖 SessionAgentPool）。

### 2. 看板客户端

`apps/mini_agent_kanban/client.py` 新增 `AgentClient.self_portrait()`，
薄封装 `GET /self/portrait`，与其余 `self_*` 方法同构。

### 3. 看板渲染

`apps/mini_agent_kanban/app.py` 新增 `_render_self_portrait()`，挂在
`render_self_tab()` 末尾（`_render_goal_stuck_stats()` 之后）：

- 顶部引用块：`identity.purpose`（未设置时不展示）；
- 四指标：累计运行 session 数 / 涉足项目数 / 自主等级 / 当前活跃项目；
- 历史强项 / 历史待加强领域（`self_assessment.strengths`/`weak_areas`）；
- 可展开：全局领域置信度（`confidence_by_domain`，global scope）；
- 可展开：当前项目能力地图（实测，workdir scope，🟢≥70% / 🟡50~70% /
  🔴<50%，按置信度排序，附成功/失败次数）；
- 弱项数量走势折线图（≥2 个快照点时才展示）；
- 可展开：已发现技能目录（含未激活，🟢/⚪ 标记激活状态）。

纯只读，不提供编辑/触发按钮——`self_profile.json` 的写入是巩固循环
（Stage 8）的职责，看板不接管。

### 4. 文档同步

- `docs/kanban-dashboard-guide.md`：「🧠 自我状态 Tab」小节末尾补充新区块
  说明。
- `next_doc/kanban_feature_inventory.md`：Tab 7 清单补充新区块与新端点。

## 验证

`python -m py_compile` 通过 `src/mini_agent/api/routes.py`、
`apps/mini_agent_kanban/app.py`、`apps/mini_agent_kanban/client.py` 三个
改动文件，无语法错误。未在真实 daemon 环境下做端到端联调（本次任务环境
不具备可运行的 daemon + 已积累历史数据的 workdir）——`self_assessment`/
`capability_map`/`capability_trend` 在全新 workdir 下预期均为空，区块会
按"暂无数据"分支展示而不报错，这一路径已在渲染函数里显式处理
（`if cap_map: ... else: st.caption("暂无...")`，`len(trend) >= 2` 才画
折线图）。建议后续在有历史数据积累的真实 daemon 上再做一次人工核对。

## 未做 / 后续可选

- 未把 `AgentSelfModel`（session 级 internal_state/affordance）接入本区块
  ——它是运行时对象，没有独立落盘/端点，接入需要新增一条"当前 session
  自省快照"的 HTTP 通路，超出本次"自我画像 + 能力地图"这个具体需求范围，
  留作后续独立需求。
- 未把 `capability_trend` 做成可点击查看某一天完整 `capability_snapshot`
  的交互——当前只用于画走势图，按需再加。
