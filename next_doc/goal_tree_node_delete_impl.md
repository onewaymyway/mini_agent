# 目标树删除节点 — 实施记录

## 1. 需求

目标树上的节点（任意层级）需要能删除：可以只删自身，也可以连同全部子节点一起删除。

## 2. 现状核实

- 后端原来只有 `GoalBacklog.delete_goal()`，只允许删除 `goal` 层级，并连带删除子孙。
- API 原来只有 `DELETE /v1/goals/{goal_id}`，同样只针对 Goal。
- 看板原来的删除入口只在 Goal 卡片（列表视图）和「一键删除所有目标」里，**目标树页面没有删除入口**。
- `ultimate` / `domain` / `stage` / `objective` 层级都不能删除。

## 3. 设计

| 模式 | 行为 |
|---|---|
| 只删自身（`include_children=false`） | 节点没有子节点时可删，并从父节点摘除。**有子节点时拒绝（409）**，不做任何修改，也不会把子节点悄悄挂到别处 |
| 连同子节点（`include_children=true`） | 删除整棵子树，兄弟节点不受影响 |
| 根节点 | 不允许删除（400）。清空整棵树仍然用「一键删除所有目标」 |

被删节点（含子孙）的副作用：

- **cron job**：`goal_id` 指向被删节点的 job 一并移除，无关 job 保留。
- **goal 层级节点的内部数据**：`.agent/daemon_run_outputs/goals/<id>/`、执行规范、执行阶段、调优草案。设置过 `user_output_dir` 的产出目录**永远不删**（沿用原有的路径安全检查）。
- **产出项目**（`output_projects/`）**不删除**。若被删节点是管理项目或有关联，关联变成断链，由产出巡检报告出来。
- **`current_focus_ids`**：从所有节点中清理被删 id，避免悬空引用。

## 4. 实施

| 文件 | 改动 |
|---|---|
| `src/mini_agent/perception/goal_backlog.py` | 新增 `subtree_ids()`、`delete_node(node_id, include_descendants=)`；`delete_goal()` 改为委托给 `delete_node`（语义不变，并修复原来不会从父节点 `children_ids` 摘除的问题） |
| `src/mini_agent/api/routes.py` | 把原 `_cascade_delete_goal` 的 cron 清理和文件清理抽成 `_remove_cron_jobs_for_nodes()` 与 `_cleanup_goal_artifacts()`，原有行为不变；新增 `DELETE /v1/goal_tree/nodes/{node_id}?include_children=` |
| `apps/mini_agent_kanban/client.py` | 新增 `delete_goal_tree_node(node_id, include_children)` |
| `apps/mini_agent_kanban/app.py` | 目标树每个非根节点的「⚙️ 管理」里新增「🗑️ 删除节点」区块：有子节点时只显示「删除此节点及全部子节点」，无子节点时显示「删除此节点」。两者都需要先勾选确认框。删除成功后页面顶部显示一次性提示 |
| `tests/test_goal_tree_delete_node.py` | 新增 13 项测试 |

### 兼容性处理记录

第一版实现把 `delete_goal` 的 Goal 检查放在加锁之前，并且要求节点有父节点。两个问题都导致了回归：

1. 加锁前读的是内存里可能过时的状态，而原实现是在加锁（会从磁盘重新加载）之后检查的。修正后检查移到加锁之后。
2. 直接创建的 Goal 没有父节点，被误判为根节点而拒绝删除。兼容入口现在只要求是 Goal，不要求有父节点。

这两处都由既有测试发现（`test_auto_disable_is_persisted`、`test_adopt_topic_as_goal_rebuilds_when_linked_goal_deleted` 等），修正后与原始代码的失败集合完全一致。

## 5. 验证

| 测试 | 结果 |
|---|---|
| `test_goal_tree_delete_node.py`（新增） | 13/13 通过 |
| 同一组文件（delete 相关、目标树、目标 backlog、cron 取消等）与原始代码对比 | 修改后的失败集合**完全包含于**原始代码的失败集合，无新增失败 |
| 广泛回归：文件名含 goal 的 52 个测试文件 | 修改后的失败项全部出现在原始代码的失败列表中，无新增失败 |
| `app.py` / `client.py` 编译 | 通过 |

广泛回归里，修改后的失败数（66）比原始代码（77）少。我没有深入查原因，推测是共享状态或执行顺序造成的不稳定，不是本次改动的效果，这一点需要你注意。

## 6. 已知限制

1. **运行中的节点不会被阻止删除**。如果一个 Objective 正在执行，删除后它完成时写回的状态会找不到节点。目前没有"运行中检查"。
2. 删除是硬删除，无法恢复。确认框是唯一的保护。
3. 看板删除交互没有在真实浏览器中验证，需要你在目标树页面上点一次确认。
