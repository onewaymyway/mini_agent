# 目标树系统改进 — 阶段四（看板树形 UI）实施记录

> 对应 `next_doc/goal_tree_system_plan.md` §五 分阶段实施规划第 4 项，
> 也是本方案最后一个阶段。依赖阶段一/二/三已落地的数据结构和函数
> （`GoalBacklog.get_tree()`/`add_node()`/`accept_candidate()`/
> `GoalTreeDecomposer.decompose()`/`reject_candidate()`/`set_focus_pin()`），
> 本阶段**没有新增任何数据模型或 cron 接线改动**，纯粹是"把已有后端能力
> 包一层 REST + Streamlit UI"。

## 一、改动范围

- `src/mini_agent/api/routes.py`：新增 6 个 `/v1/goals/...` 端点（见
  §2.1），路由文档头部的接口清单同步补充。
- `apps/mini_agent_kanban/client.py`：`AgentClient` 新增 6 个对应的
  Python 方法封装（`goal_tree()`/`add_goal_tree_node()`/
  `decompose_goal_node()`/`accept_goal_tree_candidate()`/
  `reject_goal_tree_candidate()`/`set_goal_tree_focus_pin()`）。
- `apps/mini_agent_kanban/app.py`：
  - `render_kanban_tab()` 顶部新增"📋 列表/看板视图" / "🌳 目标树"视图
    切换单选框，选中后者时调用新函数 `_render_goal_tree_view()` 并
    `return`，原有列表/看板视图代码完全不变（一行 `if` 分支接管，不是
    重写）。
  - 新增 `_render_goal_tree_view()`/`_render_goal_tree_node()`（递归）/
    `_render_goal_tree_candidates()`/`_goal_tree_flatten_titles()` 四个
    函数，以及层级图标/状态标签两个常量字典。
- `docs/kanban-dashboard-guide.md`：`### 📌 目标看板 Tab` 一节开头新增
  "🌳 目标树子页"说明。
- `docs/http-api-guide.md`：新增 `### /v1/goals/tree — 目标树 REST API`
  一节，紧接在 `### /v1/goals` 之后、执行规范一节之前。
- `next_doc/goal_tree_system_plan.md`：顶部实施进度说明更新为四个阶段
  全部完成。

没有改动 `GoalBacklog`/`GoalTreeDecomposer`/`GoalNode` 本体，没有新增
cron job，没有改动 `apps/mini_agent_kanban_x`（React 版，方案 §三 第 7
条明确本次不动）。

## 二、具体改动

### 2.1 REST 端点（对应方案 §4.5）

| 方法 | 路径 | 对接后端方法 |
|---|---|---|
| GET | `/v1/goals/tree?root_id=` | `GoalBacklog.get_tree()` |
| POST | `/v1/goals/nodes` | `GoalBacklog.add_node()` |
| POST | `/v1/goals/{id}/decompose` | `GoalTreeDecomposer.decompose()` |
| POST | `/v1/goals/{id}/candidates/{cid}/accept` | `GoalBacklog.accept_candidate()` |
| POST | `/v1/goals/{id}/candidates/{cid}/reject` | `GoalTreeDecomposer.reject_candidate()` |
| POST | `/v1/goals/{id}/focus_pin` | `GoalBacklog.set_focus_pin()` |

设计要点：

- **序列化**：`get_tree()` 返回 `{"node": GoalNode, "children": [...]}`，
  `node` 是对象不是 dict。新增 `_serialize_tree()` 递归转换成纯 JSON
  结构（`node.to_dict()` + 递归 `children`），是本阶段唯一的新增
  "转换逻辑"，其余端点都是直接调用已有方法后包一层 dict 返回。
- **分解走 async_jobs**：`decompose()` 内部调用 `LLMHelper.ask()`，耗时
  不可控，复用 `execution_spec/generate` 等既有端点的模式（
  `_async_jobs(request).start(...)` 立即返回 `{"job_id", "key"}`），不是
  同步等待到完成才响应——这也是本阶段唯一一个走异步任务机制的端点，
  其它五个都是同步的纯本地读写（`add_node`/`accept_candidate`/
  `set_focus_pin` 都是毫秒级临界区写入，`reject_candidate` 同理，`get_tree`
  是只读遍历）。
- **reject 走 `GoalTreeDecomposer.reject_candidate()` 而不是
  `GoalBacklog.reject_candidate()`**：前者除了移除候选，还会记一条 30
  天去重记录（`record_rejected_topic()`），跟 CLI `/agent goals
  candidates <id> reject` 是同一条路径；直接用后者会丢失去重语义，导致
  同一个被拒绝的主题可能很快又被生成一遍。
- **accept 的 overrides 只在非空时传入**：REST body 里 `title`/
  `description`/`level` 传空字符串（前端表单默认值）会被过滤掉，不会
  用空字符串覆盖候选原本的内容——`{k: v for k, v in body.items() if k
  in (...) and v}`，空值不进 `overrides` dict，`accept_candidate()` 内部
  "键不存在则取候选原值"的兜底逻辑自然生效。
- **`_goal_backlog_only()`/`_spec_paths()` 复用既有辅助函数**：跟
  `execution_spec` 系列端点用同一套 `project_root` 解析逻辑，没有另起
  一套。
- **路由文档头部清单**（文件顶部 `"""..."""` docstring 里的接口一览表）
  同步补充了这 6 条，保持"这个文件顶部的清单是全部路由的唯一索引"这个
  既有约定不被破坏。

### 2.2 `AgentClient` 封装（`apps/mini_agent_kanban/client.py`）

6 个方法直接对应上表 6 个端点，参数形状照抄各自 Body 字段，没有额外
逻辑。放在 `delete_all_goals()` 之后、长期方向分组方法之前，独立一段
`# ── 看板：目标树 ──` 注释分隔。

### 2.3 Streamlit UI（`apps/mini_agent_kanban/app.py`）

- **视图切换**：`render_kanban_tab()` 函数体第一行加
  `st.radio("视图", ["📋 列表/看板视图", "🌳 目标树"], horizontal=True, ...)`，
  选中"🌳 目标树"时调用 `_render_goal_tree_view(client)` 后立即
  `return`——原有几千行列表/看板视图渲染代码在这个分支完全不会执行，
  两个视图共享同一个 `client`，互不干扰。默认仍是列表/看板视图，符合
  方案 §四 4.4"与现有列表/看板视图并存，不替换"的要求。
- **`_render_goal_tree_view()`**：入口函数，拉取 `client.goal_tree()`；
  `tree is None`（还没有全局根节点）时展示创建根节点表单
  （`level="ultimate"`），否则调用递归渲染函数从根开始画。
- **`_render_goal_tree_node()`**：递归渲染单个节点+其全部子树。每个
  节点一行标题（缩进 + level 图标 + 标题 + 状态标签），下方一个
  "⚙️ 管理"折叠区（默认收起，避免树很深时页面被展开的表单撑爆），里面
  依次是：
  1. 编辑表单（标题/描述/优先级，对应 `client.update_goal()`——即已有
     的 `PATCH /v1/goals/{id}`，没有新建端点，因为该接口本来就不限制
     `level`，任意层级节点都能改）；
  2. 仅非叶子节点（`ultimate`/`domain`/`stage`）显示的"🪄 帮我拆解此
     节点"按钮 + `async_job_ui.start_async_job()`/`run_async_job()`
     轮询状态展示；
  3. 新建子节点表单（层级下拉框默认预选"父节点 level 的下一层"，用户
     可以改选，允许跳级——校验交给后端 `add_node()`，前端不重复实现
     §4.1.1 的层级顺序表）；
  4. 仅非叶子节点且有子节点时显示的"📌 pin/取消 pin"按钮列表（对每个
     直接子节点渲染一个按钮，按钮文案根据当前是否在 `current_focus_ids`/
     `focus_pinned_ids` 里动态调整）。
  折叠区之外，若该节点有 `decompose_candidates`，紧跟着渲染候选卡片；
  若某个子节点 id 出现在本节点 `current_focus_ids` 里，渲染该子节点前
  先插一行"⭐ 以下为当前焦点"提示，然后递归渲染全部子节点。
- **`_render_goal_tree_candidates()`**：候选卡片，`st.container(border=True)`
  包一层，标题斜体 + "🕓 候选（待确认）"前缀，展示描述和生成理由，三个
  按钮 ✅/✖️/✏️；"✏️ 编辑后采纳"点击后展开一个内联表单（标题/描述可改，
  提交即调用 `accept_goal_tree_candidate()` 并带上覆盖值），用
  `st.session_state` 记"当前是否展开编辑表单"，避免每次 rerun 都重新
  展开/收起。

### 2.4 与方案 §4.4 原文的差异

| 方案原文 | 本阶段实际交付 | 原因 |
|---|---|---|
| "用 `st.expander` 或自绘缩进树……具体实现方式在阶段四落地时再定" | 用 `st.markdown` 缩进（`&nbsp;` 拼接）+ 每节点一个 `st.expander("⚙️ 管理")` 装管理表单 | 缩进树本身信息密度不需要单独展开/收起，"管理表单"才是占空间的部分，拆开处理让默认视图更紧凑，符合方案"优先复用 Streamlit 原生组件"的取舍原则 |
| "修改 `parent_id`（下拉选择新父节点）" | 本阶段**未实现**该 UI，只做了新建/编辑/pin/分解/候选处理 | 方案原文本身把"拖拽"排除在外、改成下拉框，但下拉框迁移涉及"新父节点必须在 `_GOAL_TREE_LEVEL_ORDER` 里排在原父节点合适位置"的额外校验，且改父节点是相对低频操作（多数场景走"分解候选"自然生成新层级，不需要手动搬迁），本阶段优先交付更高频的核心闭环（创建/查看/分解/采纳/焦点），改父节点留待后续有实际需要时再补一个独立的 REST 端点 + UI |
| "候选……复用 `wiki_tab_async_changes` 里刚落地的 `start_async_job`/`run_async_job` 异步模式" | 候选生成（分解触发）走异步；候选**采纳/忽略**本身是同步端点 | 采纳/忽略只是本地写入（挪一条记录/删一条记录），不涉及 LLM 调用，没有理由走异步轮询；方案原文这句话本身也是针对"分解建议的 LLM 调用"，采纳/忽略动作不在其列 |

## 三、验证

由于 Streamlit UI 没有自动化测试基础设施（跟前三阶段一样，`apps/
mini_agent_kanban` 目前没有任何自动化测试覆盖），本阶段验证方式：

1. **回归测试**：`pytest tests/test_goal_tree_phase1.py
   tests/test_goal_tree_phase2.py tests/test_goal_tree_phase3.py
   tests/test_goal_backlog.py tests/test_goal_execution_fairness.py`，
   104 个用例全部通过，本阶段未改动任何后端数据结构/方法，符合预期。
2. **静态检查**：`python3 -m py_compile` 通过 `routes.py`/`client.py`/
   `app.py` 三个改动文件；补装本沙盒环境缺失的
   `fastapi`/`uvicorn`/`python-multipart`/`rich` 后，`import
   mini_agent.api.routes` 整体可正常加载（含本阶段新增的 6 个路由
   函数），与阶段二/三记录里"该模块依赖较重，此前只做语法校验"相比，
   本次因为补齐了缺失依赖，做到了真正的 `import` 级校验。
3. **端到端场景模拟**（脱离 HTTP 层，直接对 `GoalBacklog` 实例跑一遍
   REST handler 内部会执行的调用序列，验证 `_serialize_tree()` 与各
   端点的参数拼装逻辑）：
   - 建根节点 → 建 `domain` 子节点 → 建 `goal` 子节点（`priority=80`）→
     `set_focus_pin()` → `append_decompose_candidates()` → `get_tree()`
     → `_serialize_tree()`：确认输出是全 JSON 可序列化的 dict（无
     `GoalNode` 对象残留），字段（`current_focus_ids`/
     `focus_pinned_ids`/`decompose_candidates`）都正确出现在对应节点上。
   - 模拟 `accept_goal_tree_candidate` 端点的 overrides 过滤逻辑
     （`{"title": "new title", "description": "", "level": ""}` →
     过滤后只剩 `{"title": "new title"}`）→ 调用
     `accept_candidate(overrides=...)`：确认新节点标题被覆盖、
     `description`/`level` 沿用候选原值，验证了"空字符串不应覆盖"这个
     REST 层专门加的过滤逻辑符合预期。
   - `set_focus_pin()` 调用后立即读 `current_focus_ids`：确认新创建的
     节点已经出现在里面，对应 REST 端点"成功后立即返回重算后的父节点"
     的设计。

未做浏览器层面的 Streamlit 页面截图验证（沙盒环境没有可用的浏览器/
显示环境跑 `streamlit run`），后续用户在自己的 Windows/PowerShell
环境实际打开看板时如果发现渲染细节问题（如层级很深时的横向滚动、
`st.expander` 嵌套层数导致的视觉拥挤），请反馈后再针对性调整——这属于
"UI 细节留到实际使用中打磨"，不是本次的阻塞项。

## 四、后续（本方案已无待实施阶段）

`next_doc/goal_tree_system_plan.md` §五 分阶段实施规划的四个阶段至此
全部完成。方案 §六"待实施阶段确认的细节"里两个遗留问题已经在阶段三
记录 §2.4 给出结论，没有更多遗留项。

如果后续实际使用中发现确有"修改 parent_id"的高频需求，可以在不改动
现有数据结构的前提下补一个 `POST /v1/goals/{node_id}/reparent`
端点（内部做跟 `add_node()` 一样的 `validate_node_hierarchy()` 校验 +
从旧父节点 `children_ids` 移除、加入新父节点 `children_ids`）+ 看板下拉
框 UI，是一个独立的小增量，不影响本文档记录的四个阶段已完成的范围。
