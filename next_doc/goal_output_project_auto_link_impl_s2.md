# 自动关联产出项目 — 实施记录 S2：执行期上下文 + 内置工具 + 提示词注入

> 对应计划：`goal_output_project_auto_link_plan.md` 第 4 章与第 10 章 S2
> 前序记录：`goal_output_project_auto_link_impl_s1.md`（待确认账本）
> 状态：**已完成**（开关 `output_registry.auto_link_tool_enabled` 默认 `false`）

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/evolution/goal_exec_context.py` | 新增 | 执行期上下文（thread-local）：set / clear / get，以及 `goal_exec_scope` 上下文管理器 |
| `src/mini_agent/tools/output_registry.py` | 新增 | 内置工具 `link_output_project`：管理根解析、角色判定、调用 S1 账本 |
| `src/mini_agent/tools/__init__.py` | 修改 | `ToolDef` / `register_fn` / `@tool` 新增可选 `available` 判定；`to_api_tools`、`call` 遵守该判定（默认 `None`，既有工具不受影响） |
| `src/mini_agent/agent/turn_loop.py` | 修改 | `_build_tool_schemas` 跳过不可用工具（每次 LLM 调用时求值） |
| `src/mini_agent/evolution/objective_agent_bridge.py` | 修改 | 两个 worker 的 `_run_step` 入口用 `goal_exec_scope(meta)` 包裹 step 执行 |
| `src/mini_agent/evolution/objective_executor.py` | 修改 | 新增 `_auto_link_step_context`；`_submit_step` 在开关开启时注入提示词、并把 `goal_exec` 放进 submit meta |
| `src/mini_agent/config/models.py` | 修改 | `OutputRegistryConfig.auto_link_tool_enabled: bool = False` |
| `agent_config.json` | 修改 | `output_registry` 块显式写入 `auto_link_tool_enabled: false` |
| `src/mini_agent/cli/app.py` | 修改 | 增加一行 `import mini_agent.tools.output_registry`（与其他内置工具同模式） |
| `tests/test_goal_output_auto_link_s2.py` | 新增 | 28 个单测 |
| `next_doc/goal_output_project_auto_link_plan.md` | 修改 | 状态与 §10 S2 标记 |
| `next_doc/goal_output_project_auto_link_impl_s2.md` | 新增 | 本文件 |
| `next_doc/README.md` | 修改 | 索引追加本记录 |

## 2. 与计划的偏差（重要）

计划 §4.1 写的是"`goal_cron_bridge._fire_goal_cycle` / `ObjectiveExecutor.start` 执行前设置上下文"。实际实现时发现两处不成立，因此做了调整：

1. **上下文设置点改为 bridge 的 worker 线程。**
   objective 的 step 由 `objective_agent_bridge` 的线程池 worker 执行，`start()` 与 `_fire_goal_cycle` 所在线程并不是执行 agent 的线程。thread-local 只对设置它的线程可见，因此必须在 worker 的 `_run_step` 入口设置。
   做法：`_submit_step` 把 `{trigger_node_id, project_root}` 放进 submit `meta["goal_exec"]`，worker 读取后用 `goal_exec_scope` 包裹执行，finally 中清除。
   这同时覆盖了周期性 goal（cron 生成的 objective）与普通 objective，两者走同一条 step 提交路径。

2. **提示词注入点改为 `_submit_step`，而不是 `_append_output_workspace_context`。**
   objective 的每个 step 消息都在 `_submit_step` 中拼接（已有 `[目标说明]`、`[产出路径规范]` 等片段），把关联提示放在这里，可以同时覆盖周期性 goal 与手动创建的 objective，并且每个 step 都会提醒（与计划 §5.3"每个 step 都追加"的思路一致）。`_append_output_workspace_context` 未改动。

3. **工具可见性需要一个通用钩子。**
   计划要求"开关关闭时工具列表与现状一致"。`ToolRegistry` 原本没有运行期可用性判定（`platform_filter` 是导入期静态过滤，无法跟随运行期配置）。因此新增了可选的 `available` 判定，默认 `None`。
   工具的 `available` 为"当前处于 goal 执行上下文中"。上下文只在开关开启时才写入，所以开关关闭时工具对模型完全不可见，`call` 也会拒绝。

4. **配置只新增一个开关。** `auto_link_detect_enabled` 属于 S5，尚未加入。`auto_link_auto_confirm` 按 §12-2 决议不实现。

## 3. 工具行为

`link_output_project(slug, role="", note="", create=false, title="")`

- 项目根与触发节点来自执行上下文，agent 不传 goal_id，避免传错；
- 管理根解析（§4.3）：沿 `parent_id` 从触发节点向上，先找最近的已标记管理根；找不到则取最近的 `goal` 祖先；仍找不到返回 `{"ok": false, "floating": true}`；
- 角色：未指定时默认 `build`；若该项目已被**其他**管理项目以 `build` 关联，则为 `iterate`；
- `create=true` 且目录不存在时受管新建（`create_output_project`，只创建不覆盖）；
- 其余全部交给 S1 账本：`add_pending` 的幂等、忽略、已确认语义直接复用；
- 目标树只读解析（读取 `.agent/goals.json` 原始 JSON，与 `routing.py` 相同），不调用 `GoalBacklog.load()`，无写盘副作用；
- `requires_approval=False`：工具只写待确认账本，不改变任何产出路径。

返回值：`{"ok": true, "state": "pending"|"confirmed"|"rejected", "slug", "role", "management_id", "created_project", "message"}`。

## 4. 提示词片段（开关开启时，追加在每个 step 消息末尾）

```
[产出项目关联]
本次执行属于目标 <goal_id>《<标题>》。如果你在 output_projects_root 下新建了独立产出项目，
或继续向已有项目写入，请调用 link_output_project(slug=..., role=...) 把它关联到本目标。
关联会先进入待确认，由用户在目标树里确认后才影响产出路径。
```

提示词中的 goal 标题取自 `_goal_id_of_objective` 解析出的父节点；标题查询失败时保留 goal id，不影响提交。

## 5. 容错

- `_auto_link_step_context` 包在 try/except 中，任何异常返回 `("", None)`，step 照常提交；
- 工具内部异常路径全部返回 `{"ok": false, ...}`，不抛给 LLM；
- 可用性判定函数抛异常时按不可用处理（`_tool_is_available`），不影响其他工具；
- 已知边界：若 `create=true` 新建目录成功而随后 `add_pending` 失败，会留下一个没有待确认条目的受管空项目目录。该目录可由巡检发现或用户手动处理，不会影响路由（无正式关联）。

## 6. 验证

```
pytest tests/test_goal_output_auto_link_s2.py                 → 28 passed
pytest tests/test_output_registry_pending_links.py (S1)       → 21 passed（回归）
```

另外对全量测试套件做了原包与改后包的对比（见第 7 节）。

## 7. 回归范围与结果

- 全量测试套件在原始包上同样有 7 个模块收集失败（`json_repair` 等依赖缺失、`_flock` 导入等既有问题），与本次改动无关。
- 本次回归选取了 50 个直接覆盖改动路径的测试文件（objective 执行器与 bridge、工具注册表、输出注册表、配置、goal-cron bridge、turn loop 相关、取消/并发/守护进程恢复），结果：**662 passed，0 failed**。
- 未完成全量套件的原包/改后包失败集对比（单核环境下全量耗时过长）。该对比留给发布前的整体验证。

## 8. 下一步（S3）

S3：REST 端点 `/v1/output_registry/pending_links`（列表、确认、忽略）与 client 方法，并补端点单测。
