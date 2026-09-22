# 目标树"调研信息"N+1 请求修复——实施记录

**背景**：用户反馈看板"🌳 目标树"随目标数量增多明显变卡，"采纳"候选
之类的操作也要等很久才刷新。

## 一、根因分析

看板"🌳 目标树"（`apps/mini_agent_kanban/app.py::_render_goal_tree_view()`）
递归渲染树上每一个节点时，`_render_goal_tree_node_body()` 都会调用一次
`_render_goal_tree_research_section(client, node_id)`——这个调用挂在
`with st.expander("⚙️ 管理", expanded=False):` 块内，但 Streamlit 的
`st.expander` 只控制视觉上是否展开，`with` 块内的 Python 代码在**每次
整页脚本执行时都会照跑**，不管用户有没有点开这个节点的"⚙️ 管理"。

这一步触发的 `GET /goals/{node_id}/research` 端点内部本身很重：
1. `_goal_backlog_only()` 全量重新加载一次 `goals.json`（含对所有
   存量节点的产出目录提示正则扫描）；
2. `list_pending_research_candidates()` 新建一个 `GrowthBacklog`
   实例，全量读一次 `growth_backlog.jsonl`；
3. `FocusResearchTrigger.last_triggered_at()` 全量读一次触发时间戳
   状态文件；
4. `list_research_items_for_node()` **又**新建一个 `GrowthBacklog`
   实例、**再**全量读一次 `growth_backlog.jsonl`（同一个请求里读了
   两遍），命中报告的候选还要逐条再读一次报告文件。

树有 N 个节点，一次页面渲染就是 N 次 HTTP 往返、每次背后至少 4 次
全量磁盘读取——这是"目标越多越卡"的主因，量级上远超其它因素（比如
`load_goal_backlog()` 本身没有缓存、改父节点下拉框的子孙遍历等）。

"采纳"操作本身写盘很轻，真正慢的是操作完之后 `st.rerun()` 触发的
整页重新执行——把上述 N 次请求风暴原样重跑一遍。

## 二、修复方案（已实施）

批量化：新增一个批量端点，一次请求拿到树上所有节点的调研摘要，
替代"渲染每个节点都单独请求一次"。写法跟 `goal_tree_next_steps()`
已经在用的"一次性拉取全部候选、按 id 建字典、递归渲染时查字典"
是同一个模式。

1. **`src/mini_agent/evolution/focus_research_trigger.py`**：新增
   `list_research_summary_for_all_nodes(paths)`，只读一次
   `growth_backlog.jsonl`（`GrowthBacklog.load_all()`）+ 一次触发
   时间戳状态文件，在内存里按 `node_id` 分组，返回
   `{node_id: {"items": [...], "last_triggered_at": float | None}}`。
   `get_report_by_id()` 仍然是逐条候选各读一次（候选数量通常远小于
   树的节点总数，量级上不是主要瓶颈，未来如需要可以再优化）。
2. **`src/mini_agent/api/routes.py`**：新增
   `GET /goals/research_summary`，内部只调用上面这个批量函数。
3. **`apps/mini_agent_kanban/client.py`**：新增
   `AgentClient.goal_tree_research_summary()`。
4. **`apps/mini_agent_kanban/app.py`**：
   - `_render_goal_tree_view()` 渲染树之前，先调用一次
     `client.goal_tree_research_summary()`，拿到 `research_by_node`
     字典（批量请求失败时回退为 `None`，退化成旧的逐节点请求，不
     让批量端点的问题连累整个树打不开）。
   - `_render_goal_tree_node()`/`_render_goal_tree_node_body()`
     新增 `research_by_node` 参数，递归时原样透传。
   - `_render_goal_tree_research_section()` 新增 `summary` 参数：
     传了就直接用（不再发请求），`None` 时保留原来的单节点请求
     作为兜底（比如未来有调用方想单独渲染某一个节点）。

## 三、验收

新增 `TestListResearchSummaryForAllNodes`（`tests/test_focus_research_
trigger.py`，4 个测试）：
- 批量结果跟"对每个节点单独调用旧接口"完全一致；
- 没有调研历史的节点不出现在批量结果字典里；
- 空 backlog 返回空字典；
- **核心断言**：不管树上有多少节点，`GrowthBacklog.load_all()`
  只应该被调用一次——用 `mock.patch.object` 直接数调用次数，而不是
  只验证结果正确（结果正确不代表真的没有走回 N+1 老路）。

加上以下既有测试文件全部通过（共 128 个测试）：
`test_goal_backlog.py` / `test_goal_tree_phase2.py` /
`test_goal_node_page.py` / `test_goal_provenance.py` /
`test_kanban_client_goal_tree_extras.py` /
`test_goal_focus_research_nodes.py` / `test_focus_research_trigger.py`
（含新增部分）/ `test_focus_next_step_candidates.py` /
`test_goal_tree_research_action_phase4.py` / `test_goal_feedback_loop.py`。

## 四、未做的部分（留给后续阶段）

- **折叠区展开状态门控**：即使做了批量端点，"⚙️ 管理"折叠区没被
  展开过的节点仍然会渲染"📄 相关调研"这部分内容（只是不再发网络
  请求，改成查内存字典，开销已经很小），没有进一步用
  `st.session_state` 做"没展开过就整段跳过渲染"的门控——批量化后
  这一步的收益已经不大，暂不做。
- **`load_goal_backlog()` 缓存**：`goals.json` 每次 HTTP 请求仍然
  全量反序列化，没有加进程内缓存（比照 world_simulator 那次用
  mtime 做缓存 key 的思路）——这个服务是常驻的 FastAPI daemon 进程
  （不是 Streamlit 每次 rerun 都重启的模型），缓存失效逻辑要考虑
  跨请求并发写入的情况，比 world_simulator 那边复杂，需要单独评估
  再做。
- **`st.fragment` 局部刷新**：探索把"采纳"/pin 等操作隔离成局部
  刷新单元，不带动整页 `_render_goal_tree_view()` 重新执行——治本
  方案，需要先验证兼容性。
- **改父节点下拉框的子孙遍历**：`_collect_descendants()` 目前仍然
  对每个非根节点在每次 rerun 时都计算一次，没有改成"只在真正展开
  该表单时才算"。
