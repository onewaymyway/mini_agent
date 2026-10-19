# 目标树：只删除子节点（保留自身）— 实施记录

## 需求

在已有的「只删自身」「连同子节点删除」之外，增加「只删除所有子节点，保留自身」。

## 设计

- 后端新增 `GoalBacklog.delete_children(node_id)`：删除全部子孙，节点本身与其父引用、自身字段保持不变，`children_ids` 清空。没有子节点时不做任何修改。根节点允许使用（只清空它下面的内容，根节点保留）。
- API 新增 `DELETE /v1/goal_tree/nodes/{node_id}/children`。被删子孙的 cron job 与 goal 层级的内部数据一并清理；**节点自身的 cron job、内部数据和产出目录保持不变**。没有子节点时返回 404。
- 看板：节点有子节点时，「🗑️ 删除节点」区块里多一个「🧹 只删除子节点（保留本节点）」，需先勾选确认框。

## 文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/perception/goal_backlog.py` | 新增 `delete_children()` |
| `src/mini_agent/api/routes.py` | 新增 `DELETE /v1/goal_tree/nodes/{node_id}/children` |
| `apps/mini_agent_kanban/client.py` | 新增 `delete_goal_tree_children()` |
| `apps/mini_agent_kanban/app.py` | 有子节点时显示「只删除子节点」按钮 |
| `tests/test_goal_tree_delete_node.py` | 新增 6 项（后端 3 + 路由 3）；文件共 19 项全部通过 |

## 验证

- 19/19 通过（含原有删除测试）。
- 目标树相关测试与原始代码对比：失败集合无新增。

## 已知限制

- 与「删除节点」一样，运行中的子 Objective 不会被阻止删除。
- 硬删除，不可恢复。
- Streamlit 按钮交互未在真实浏览器中验证。
