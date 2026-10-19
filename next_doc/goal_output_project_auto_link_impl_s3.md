# 自动关联产出项目 — 实施记录 S3：REST 端点与客户端方法

> 对应计划：`goal_output_project_auto_link_plan.md` 第 7 章与第 10 章 S3
> 前序记录：`_s1.md`（待确认账本）、`_s2.md`（执行期工具与提示词）
> 状态：**已完成**（端点单测通过；未做真实 daemon 上的 curl 验证，见第 5 节）

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/api/routes.py` | 修改 | 新增 5 个端点（见第 2 节），插在"外部项目管理"段之前 |
| `src/mini_agent/output_registry/pending_links.py` | 修改 | 新增公共函数 `read_goal_nodes()`（只读解析 goals.json） |
| `src/mini_agent/tools/output_registry.py` | 修改 | 删除私有 `_read_goal_nodes`，改用公共函数（行为不变） |
| `apps/mini_agent_kanban/client.py` | 修改 | 新增 5 个客户端方法，风格同 `output_registry_*` |
| `tests/test_output_registry_pending_routes.py` | 新增 | 18 个端点测试 |
| `next_doc/goal_output_project_auto_link_impl_s3.md` | 新增 | 本文件 |
| `next_doc/goal_output_project_auto_link_plan.md` / `next_doc/README.md` | 修改 | 状态与索引 |

## 2. 端点

| 方法 | 路径 | 确认门槛 | 说明 |
|---|---|---|---|
| GET | `/v1/output_registry/pending_links?state=pending\|rejected\|all` | — | 列表，附 `goal_title`、`management_title`；非法 state → 400 |
| POST | `/v1/output_registry/pending_links/{id}/confirm` | `confirm=true` | 标记管理根（用目标树标题）+ 写正式关联 + 删除条目；可带 `role` 覆盖 |
| POST | `/v1/output_registry/pending_links/{id}/reject` | 无 | 忽略；不再重复生成同 slug+goal |
| POST | `/v1/output_registry/links` | `confirm=true` | 手动关联：先标记节点（若未标记），再写正式关联 |
| DELETE | `/v1/output_registry/links` | `confirm=true` | 解除正式关联（请求体携带参数） |

全部端点沿用 `_output_registry_root`（owner 校验 + 项目根）与 `_registry_error`（`RegistryError` → 400，其余 → 500）。

**关于忽略不要求 confirm**：忽略只是把建议标为"不再提示"，不改变任何路由，计划 §6.2 也只要求解除与确认需要二次确认。若日后希望忽略也需确认，只需在 `post_output_registry_pending_reject` 中加一行检查。

**未在本阶段实现**：计划 §7 表格中的 `GET /v1/goals/tree/output_links`（目标树批量数据）归属 S4（界面与批量接口），不在 S3 范围。

## 3. 设计决策

- **确认时的管理根标题**：由端点从目标树读取节点标题并传给 `confirm_pending(title=...)`。这样确认后目标树与管理页能直接显示标题，而不是空白（rebuild 后的标题补齐同样依赖目标树）。
- **手动关联先标记再关联**：`link_management_output` 要求节点已标记。端点先 `mark_management_root`（带目标树标题，并用 `valid_goal_ids` 校验节点存在），与确认路径语义一致。
- **只读目标树**：所有读取走 `read_goal_nodes()`，不调用 `GoalBacklog.load()`，避免写盘副作用（与 routing.py 一致）。
- **标签不返回 goals 原始对象**：列表只补两个标题字段，不暴露完整节点，减少耦合。

## 4. 验证

```
pytest tests/test_output_registry_pending_routes.py          → 18 passed
pytest tests/test_output_registry_pending_links.py           → 21 passed（S1 回归）
pytest tests/test_goal_output_auto_link_s2.py                → 28 passed（S2 回归）
pytest tests/test_output_registry_routes.py                  → 5 passed（既有路由回归）
```

测试使用 FastAPI `TestClient` 直接驱动路由，覆盖：confirm 门槛（缺 confirm 时不写任何内容）、确认后路由生效、待确认期间路由不变、解除后回到无路由、非法 role/state、不存在的条目与节点返回 4xx 而非 500。

客户端方法经过签名检查（导入成功，参数与端点一致）。

## 5. 未完成的验证

- **真实 daemon 上的 curl 验证**未执行：当前环境只有单核且无法启动完整 daemon。计划 §10 S3 的退出条件中提到的"手动 curl 验证"应在可运行环境中补做，命令示例：
  ```
  curl -X GET "http://127.0.0.1:<port>/v1/output_registry/pending_links"
  curl -X POST ".../pending_links/<id>/confirm" -H 'Content-Type: application/json' -d '{"confirm":true}'
  ```
- **客户端方法的真实 HTTP 调用**未测试，仅做了签名检查。

## 6. 下一步（S4）

S4：目标树界面。包括节点标题徽标（`🗂️N` / `⏳N`）、「⚙️ 管理」区的「🔗 关联项目」子区块、批量接口 `GET /v1/goals/tree/output_links`（带 TTL 缓存，确认/忽略/解除后失效）、节点详情弹窗中的关联项目列表。
