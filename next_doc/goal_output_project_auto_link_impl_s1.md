# 自动关联产出项目 — 实施记录 S1：待确认关联账本

> 对应计划：`goal_output_project_auto_link_plan.md` 第 3 章与第 10 章 S1
> 状态：**已完成**
> 范围：只实现账本模块与其单测。未接入 goal 执行、未改动工具列表、未改动界面，行为与现状完全一致。

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/pending_links.py` | 新增 | 待确认账本：登记、幂等、确认、忽略、查询 |
| `src/mini_agent/output_registry/__init__.py` | 修改 | 导出账本公共 API（纯增量，未改动已有导出） |
| `tests/test_output_registry_pending_links.py` | 新增 | 21 个单测 |
| `next_doc/goal_output_project_auto_link_plan.md` | 修改 | 状态、§10 S1 标记完成、§12 三点决议 |
| `next_doc/goal_output_project_auto_link_impl_s1.md` | 新增 | 本文件 |
| `next_doc/README.md` | 修改 | 索引追加本记录 |

账本文件位置：`<output_projects_root>/_registry/pending_links.json`，与 `index.json`、`projects/` 同级。

## 2. 关键设计落实

- **零破坏**：不改动 `index.json` 与分表结构。账本是独立文件。
- **确认才生效**：`confirm_pending` 才会调用 `mark_management_root` 与 `link_management_output`；`add_pending` 不标记管理根（计划 §9「管理根标记副作用延后」）。
- **路由不读账本**：`routing.py` 未修改，单测 `test_pending_does_not_affect_routing_until_confirmed` 与 `test_pending_on_sibling_goal_routes_only_after_confirm` 锁定此行为。
- **账本可删除**：删除 `pending_links.json` 后正式关联与路由结果不变（单测 `test_deleting_ledger_keeps_formal_links_and_routing`）。账本损坏按空账本处理，不抛异常（`test_corrupt_ledger_treated_as_empty`）。
- **幂等键**：
  - 同 `(slug, goal_id, role)` 已有待确认 → 返回 `existing`；
  - 同 `(slug, goal_id)` 已被忽略 → 返回 `rejected`，不再生成（计划 §3「忽略」）；
  - 同 `(slug, management_id, role)` 已有正式关联 → 返回 `confirmed`，不写账本。
- **确认失败可重试**：确认过程中任一步失败（如目录已被删除），账本条目保留（`test_confirm_failure_keeps_item_for_retry`）。
- **显式确认**：`confirm_pending` 要求 `confirm=True`，其他真值（如字符串 `"yes"`）一律拒绝。
- **锁顺序**：账本锁 → 注册表 index 锁。注册表模块不会反向获取账本锁，避免死锁。

## 3. 与计划的偏差与说明

1. **手动关联 / 解除关联**：计划 S1 文字中列入了这两项。二者在 `registry.py` 中已有实现（`link_management_output`、`unlink_management_output`），S1 直接复用，没有再包一层。账本模块只新增"待确认"相关的纯函数。
2. **`add_pending` 不校验管理根是否已标记**：计划未要求，且与"确认时才标记"的设计一致。
3. **`add_pending` 的 `valid_goal_ids` 为可选参数**：S1 无目标树上下文，因此默认不校验；S2 接入执行期时传入。
4. **确认后不保留 `confirmed` 记录**：计划 §3 已写明"确认后记录从账本移除"，实现与之一致。
5. **并发与 Windows**：沿用 `ExclusiveFileLock` 与 `atomic_write_json`，与注册表一致；未做额外的跨进程压测。

## 4. §12 三点决议（已落入计划文档）

| 议题 | 决议 | 对 S1 的影响 |
|---|---|---|
| 待确认是否自动过期清理 | 不清理，巡检提示（归入 S5） | 账本无 TTL 字段，S1 不处理 |
| 是否保留自动直接确认开关 | 不实现 | S1 的 `confirm_pending` 必须显式调用，无自动路径 |
| 第二层是否处理已有目录被修改 | 首版只处理新增目录（归入 S5） | 无影响 |

## 5. 验证

```
pytest tests/test_output_registry_pending_links.py                 → 21 passed
pytest tests/test_output_registry_links.py \
       tests/test_output_routing.py \
       tests/test_output_routing_characterization.py \
       tests/test_output_registry_adoption.py \
       tests/test_output_registry_routes.py \
       tests/test_output_registry_inventory.py \
       tests/test_output_patrol.py                                  → 83 passed（回归）
```

回归用例改造前后均通过，未修改任何既有测试。

## 6. 下一步（S2）

- `evolution/goal_exec_context.py`：thread-local 执行上下文（set/clear/get）；
- `tools/output_registry.py` 注册工具 `link_output_project`；
- 提示词注入（仅在 `auto_link_tool_enabled=true` 时）；
- 管理根解析（计划 §4.3）；
- 配置开关 `auto_link_tool_enabled`（默认 false）。

S2 按计划进入下一轮，不与 S1 合并提交。
