# 自动关联产出项目 — 实施记录 S4：目标树界面

> 对应计划：`goal_output_project_auto_link_plan.md` 第 6 章与第 10 章 S4
> 前序记录：`_s1.md`（账本）、`_s2.md`（执行期工具）、`_s3.md`（REST 端点）
> 状态：**已完成（代码与单元测试）**；浏览器端到端验证未做，见第 6 节

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/tree_links.py` | 新增 | 批量视图：整棵树每个节点的生效关联（含继承）与待确认 |
| `src/mini_agent/api/routes.py` | 修改 | 新增 `GET /v1/goals/tree/output_links`，插在 `/goals/tree` 之前 |
| `apps/mini_agent_kanban/client.py` | 修改 | 新增 `goal_tree_output_links()` |
| `apps/mini_agent_kanban/app.py` | 修改 | 标题徽标、「🔗 关联项目」子区块、详情弹窗中的关联列表、批量缓存 |
| `tests/test_goal_tree_output_links_s4.py` | 新增 | 8 个后端测试（含路由） |
| `tests/test_kanban_goal_tree_output_links_s4.py` | 新增 | 14 个看板纯函数 / 缓存 / 参数透传测试 |
| `next_doc/goal_output_project_auto_link_impl_s4.md` | 新增 | 本文件 |
| `next_doc/goal_output_project_auto_link_plan.md` / `next_doc/README.md` | 修改 | 状态与索引 |

## 2. 界面行为

**标题行徽标**（与 ⭐ / 💡 同风格，贴在节点自身标题后）
- `🗂️N`：该节点生效的正式关联数。子节点会显示其管理根的关联数（继承），与计划 §6.1 一致。
- `⏳N`：待确认数。**挂在管理根上**，不挂在触发待确认的 objective 上（见第 3 节）。

**「⚙️ 管理」内的「🔗 关联项目」子区块**（随管理区懒加载，不增加折叠前的渲染开销）
- 继承的关联：只读，提示"继承自管理根「…」，请到该管理根下修改"；
- 管理根自身的关联：每行一条，解除需先勾选确认；
- 待确认：每条显示 slug、建议角色、来源、理由；可在下拉中改角色后「✅ 确认」，或「✖️ 忽略」；
- 手动关联：从已登记项目中选择 + 角色，需勾选确认后建立。

**节点详情弹窗（📄）**：在「📂 产出」之前增加「🔗 关联的产出项目（N）」折叠区，列出生效关联及产出路径；有待确认时提示到「⚙️ 管理」处理。

**性能**：整棵树只请求一次 `GET /v1/goals/tree/output_links`，`_render_goal_tree_view` 取得后通过 `output_links_by_node` 参数逐层透传，与既有 `research_by_node` 同一模式。前端 TTL 20 秒（与其它侧缓存一致），确认/忽略/关联/解除后主动失效。错误结果不缓存。

## 3. 设计决策

- **待确认只挂在管理根上**。S2 写入账本时记录的 `management_id` 就是管理根，确认入口也在管理根的「⚙️ 管理」里，因此徽标与操作放在同一节点，用户不需要在触发的 objective 与管理根之间来回找。代价是：在 objective 上看不到 ⏳，需要沿树向上找到带 ⏳ 的节点。
- **批量视图只输出有内容的节点**（有生效关联、有待确认或本身是管理根），减小响应体积；继承节点通过 `inherited_from` 标注，由界面决定只读展示。
- **视图只读**：`tree_links.py` 不写 goals.json、账本或注册表。`goals.json` 不可读时返回带 `error` 的空结构，界面降级为无徽标，不影响树的其余渲染。
- **产出路径展示**：`output_dir` 由后端给出相对项目根的路径（如 `output_projects/alpha/output`），前端只展示。

## 4. 与计划的偏差与未完成项

1. **正式关联未记录来源**。计划 §6.2 要求已关联行展示来源（agent_tool / auto_detect / user）。当前正式关联结构（分表 `links`）不保存来源，只有待确认条目保存来源。补上需要在确认时把 `origin` 写入正式关联，属于注册表数据结构的改动，计划 §3 明确要求不改动现有结构，因此本阶段未做，已在界面上省略来源列。若要补，建议作为 S4.1 单独决定。
2. **「🧰 产出项目管理」的「🔗 待确认关联」子页**属于计划 §6.5，归入 S5，本阶段未做。
3. **巡检"待确认超过 N 天"提示**属于 §9 风险项，归入 S5。
4. **界面验证**：本环境只做了导入与纯函数/缓存/参数透传的单元测试，没有在浏览器中运行 Streamlit 页面，端到端点击路径未验证。

## 5. 验证

```
pytest tests/test_goal_tree_output_links_s4.py                 → 8 passed
pytest tests/test_kanban_goal_tree_output_links_s4.py          → 14 passed
pytest （S1–S3 相关 + goal_tree / kanban / output 相关，共 34 个文件）→ 489 passed, 2 failed
```

2 个失败位于 `tests/test_kanban_growth_dragdrop.py`（`FakeClient` 缺少 `get_latest_async_job` 等方法）。在原始上传包上运行结果相同（同样 2 个失败），因此与本次改动无关。该文件此前因缺少 `streamlit` 无法收集，本次为运行看板测试安装了 streamlit 1.66.0，才暴露出这些既有失败。

## 6. 下一步（S5）

- 第二层目录差异检测（`auto_link_detect_enabled`，默认关闭，只处理新增目录，按 §12-3 决议）；
- 「🧰 产出项目管理」的「🔗 待确认关联」子页（全局汇总、面包屑、批量确认/忽略）；
- 巡检新增"待确认超过 N 天"提示（只报告，不自动清理，按 §12-1 决议）。
