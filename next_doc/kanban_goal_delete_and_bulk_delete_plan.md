# 目标看板删除功能：单个删除 + 一键删除全部（kanban_goal_delete_and_bulk_delete_plan）

## 背景 / 问题现象

`GoalBacklog`（`perception/goal_backlog.py`）此前只有状态迁移能力
（`set_status()` 把 Goal/Objective 标成 `abandoned`/`completed` 等），
没有任何硬删除接口——`goal_cron_bridge._fire_goal_cycle()` 里甚至专门
留了一段注释说明"虽然当前 GoalBacklog 没有硬删除接口，但预留这个分支
防御未来变化"。用户在"📌 目标看板"Tab 上创建的 Goal 只能一路推进到某个
终态，永远留在 `goals.json` 里，长期使用后：

- 看板列表越积越多，即使是纯粹手滑创建的测试 Goal 也删不掉；
- 一个 Goal 被"放弃"之后，绑定的周期性 cron job（`recurrence_cron_job_id`
  指向的那个）不会自动清理，仍然会按计划触发、被
  `_fire_goal_cycle()` 判定"Goal 非 active"后静默跳过——用户在
  "⏰ Cron 任务"Tab 里能看到一个引用着"已放弃"Goal、看起来什么都不做的
  job，不知道能不能删、删了会不会影响别的东西；
- Goal 对应的 `.agent/daemon_run_outputs/goals/<goal_id>/`、
  `.agent/goal_execution_specs/<goal_id>.json`、
  `.agent/goal_execution_phase/<goal_id>.json`、
  `.agent/cycle_tuning_proposals/<goal_id>/` 四类外部文件/目录同样没有
  任何清理入口，只能靠用户自己去项目目录里手动找、手动删，容易漏。

用户诉求：给"📌 目标看板"加删除 Goal 的能力，删除时要把上面列出的关联
cron job、外部数据都一并清理干净，另外再加一个"一键删除所有现有 Goal"
的批量入口方便清空测试数据；同时如果 Goal 设置过 `user_output_dir`
（用户自己指定的正式产出目录），这个目录是用户资产，删除时绝对不能碰。

## 设计

### 1. `GoalBacklog.delete_goal(goal_id)`（`perception/goal_backlog.py`）

在既有的 `_locked()` 临界区（加进程间文件锁 → 重新 load 磁盘最新状态 →
改 → `save()`）里做级联删除：

- 只接受 `level == "goal"` 的节点，Objective 不提供单独硬删除入口——
  "取消一个子任务"已经有 `ObjectiveExecutor.cancel()`/`set_status()`
  覆盖，硬删除会破坏 `parent_id`/`children_ids` 的树形完整性。
- 从 `goal_id` 出发做一次 BFS，把 `children_ids` 递归收集全，逐个从
  `self._nodes` 里 `pop()`，最后统一 `save()`（由 `_locked()` 的
  `yield` 后自动触发）。
- 返回被删除的全部节点 id 列表（goal 本身在最前面），调用方（API 路由
  层）用这份列表去精确匹配"哪些 cron job 的 `goal_id` 指向了刚被删掉的
  节点"，不需要再单独查一次。

### 2. 外部文件清理：三个新的 `delete_*()` 函数

跟 `GoalBacklog` 一样的风格——各自模块只负责自己那份存储，不越界改别的
模块的文件：

| 函数 | 文件/目录 |
|---|---|
| `perception/goal_execution_spec.py::delete_spec()` | `.agent/goal_execution_specs/<goal_id>.json` |
| `perception/execution_phase.py::delete_phase()` | `.agent/goal_execution_phase/<goal_id>.json` |
| `perception/cycle_tuning.py::delete_proposals()` | `.agent/cycle_tuning_proposals/<goal_id>/`（整目录） |

三者都是幂等的（文件/目录不存在时直接返回 `True`），只有真正的 `OSError`
才返回 `False`，供路由层汇总进 `file_cleanup_errors` 列表展示给用户，
而不是让一次文件系统抖动直接把整个删除请求打成 500。

`.agent/daemon_run_outputs/goals/<goal_id>/` 这个目录（`notes`/`spec`/
`scratch` 三个内部子目录 + 默认 `output/`）没有单独抽函数，直接在路由层
用 `output_workspace.goal_output_base_dir()` 拿路径后 `shutil.rmtree()`——
理由是这个目录的"所有权"本来就分散在多个模块手里（`spec/` 由
`goal_execution_spec.py` 写，`notes/`/`scratch/` 由 `goal_cron_bridge.py`/
`objective_executor.py` 写），没有一个模块适合单独代表整个目录，删除
这个"目录整体"的责任天然属于调用方（路由层），不需要为此专门造一个新
模块。

### 3. `user_output_dir` 保护（安全边界，最高优先级）

`output_workspace.goal_output_dir(paths, goal_id, user_output_dir=...)`
的既有语义就是：设置了 `user_output_dir` 时，正式产出目录会解析到
`<project_root>/<user_output_dir>`，跟 `goal_output_base_dir(paths,
goal_id)`（`.agent/daemon_run_outputs/goals/<goal_id>/`）完全是两棵不
相交的路径——这本来就是"删内部记账目录不会动到用户产出目录"的天然
保证。但为了不让未来任何重构（比如以后改了 `goal_output_dir()` 的解析
规则）悄悄破坏这条保证而没人发现，`api/routes.py::_cascade_delete_goal()`
里显式做了一次核实：

```python
output_dir = ow.goal_output_base_dir(paths, goal_id).resolve()
if goal.user_output_dir:
    custom_dir = ow.goal_output_dir(paths, goal_id, user_output_dir=goal.user_output_dir).resolve()
    if output_dir == custom_dir or custom_dir in output_dir.parents or output_dir in custom_dir.parents:
        # 检测到路径重合/包含关系，跳过删除，报错而不是盲目 rmtree
        ...
```

只有确认两条路径互不包含时才真正 `shutil.rmtree(output_dir)`；一旦检测
到任何重合，直接跳过删除、把原因写进 `file_cleanup_errors`，不会因为
"删 Goal"这个操作误删用户自己的项目文件。已经写了一个独立脚本核实两条
路径在默认场景（未设置 `user_output_dir`）和自定义场景下确实完全隔离
（`output_dir` 在 project_root 下 `.agent/...`，`custom_dir` 在
`<project_root>/<user指定的相对路径>`，字面上不可能重合，除非用户把
`user_output_dir` 手动填成 `.agent/daemon_run_outputs/...` 本身——这种
边界情况也被上面的显式比较覆盖到，会被判定为"重合"而跳过删除）。

### 4. `_cascade_delete_goal()`：单删/群删共用的级联删除实现

`api/routes.py` 新增一个内部辅助函数，接收已经确认存在的 `GoalNode`，
依次执行：① `backlog.delete_goal()` ② 扫描 `CronScheduler.list_jobs()`
清理 `goal_id` 命中的 job ③ 清理四类外部文件/目录（含上面的
`user_output_dir` 保护）。返回一份结构化结果（`deleted_node_ids`/
`removed_cron_job_ids`/`failed_cron_job_ids`/`file_cleanup_errors`/
`user_output_dir_preserved`）。

`DELETE /v1/goals/{goal_id}`（单个）和 `DELETE /v1/goals`（一键全部）
两个路由都只是"怎么拿到要删的 Goal 列表 + 怎么组装响应"的薄封装，删除
本身的逻辑只有一份，不会出现"单删和群删各自实现一遍，改动漏掉一处"的
问题（这正是 `kanban_cron_delete_consistency_bugfix.md` 里踩过的坑，
这次设计上直接规避）。

`DELETE /v1/goals`（群删）額外注意点：先取一份 `goal_id` 快照列表再
逐个处理，不在遍历过程中直接改 `self._nodes`；单个 Goal 的级联删除
失败（比如 cron job 删除报错）不会中断其它 Goal 的处理，符合"一键清空"
场景下"尽量多删、把删不掉的报出来"优于"整批因一个失败全部中止"的直觉。

### 5. 前端（Streamlit `apps/mini_agent_kanban`）

- `client.py` 新增 `delete_goal(goal_id)` / `delete_all_goals()`，分别对
  应 `DELETE /v1/goals/{goal_id}` / `DELETE /v1/goals`。
- `app.py::_render_goal_card()`：只在 Goal 级卡片（`level != "objective"`）
  追加"🗑️ 删除目标"折叠区，交互风格与已有的"⏰ Cron 任务"Tab 删除 UI
  保持一致——先点"🗑️ 删除"进二次确认态（`session_state` 标记控制），
  再点"⚠️ 确认删除"才真正调用接口，中途可点"取消"退出确认态；确认
  文案列出会一并清理的内容，若该 Goal 设置过 `user_output_dir` 额外
  提示"该目录不会被删除"。
- `app.py::render_kanban_tab()`：Tab 顶部（新建目标表单之前）加一个默认
  收起的"🗑️ 一键删除所有目标"折叠区。因为是批量不可逆操作，确认门槛比
  单个删除更高——要求在文本框里输入固定短语「删除全部」才能点亮
  "⚠️ 确认删除全部"按钮，而不是像单个删除那样点两次按钮就行，降低误触
  清空整个看板的概率。

## 涉及文件

- `src/mini_agent/perception/goal_backlog.py`（`GoalBacklog.delete_goal()`）
- `src/mini_agent/perception/goal_execution_spec.py`（`delete_spec()`）
- `src/mini_agent/perception/execution_phase.py`（`delete_phase()`）
- `src/mini_agent/perception/cycle_tuning.py`（`delete_proposals()`）
- `src/mini_agent/api/routes.py`（`_cascade_delete_goal()`、
  `DELETE /goals/{goal_id}`、`DELETE /goals`）
- `apps/mini_agent_kanban/client.py`（`delete_goal()`、`delete_all_goals()`）
- `apps/mini_agent_kanban/app.py`（单个删除 UI + 一键删除全部 UI）
- `docs/http-api-guide.md`（`/v1/goals` 一节补充两个 DELETE 端点）
- `docs/kanban-dashboard-guide.md`（Tab 一览表 + "📌 目标看板 Tab"小节 +
  `AgentClient` 封装的 API 端点表）
- 本文档

## 验证

- `python3 -m py_compile` 全部改动的 `.py` 文件通过。
- 现有 `tests/test_goal_backlog.py`（9 项）全部通过，未破坏既有行为。
- 手写脚本验证 `GoalBacklog.delete_goal()` 能正确级联删除 Goal + 其全部
  子 Objective，且对不存在的 id 调用返回空列表、不做任何修改。
- 手写脚本验证：设置 `user_output_dir` 时，`goal_output_dir()` 解析出的
  自定义产出目录与 `goal_output_base_dir()`（内部记账目录）在文件系统
  路径上完全不相交，`_cascade_delete_goal()` 的显式核实逻辑不会误判为
  需要跳过（默认场景）也不会误删（自定义场景）。

## 后续建议（未在本次改动范围内）

- 目前"一键删除全部"没有提供"删除前先导出一份 `goals.json` 备份"的
  选项——如果之后有用户反馈误触了确认短语导致数据丢失，可以考虑在群删
  前自动把当前 `goals.json` 复制一份到 `.agent/backups/` 之类的位置，
  不在本次改动范围内。
- 当前 `file_cleanup_errors` 只是字符串标签列表（如 `"execution_spec"`），
  没有带具体异常信息——如果后续需要更精细的排障能力，可以考虑把
  `log_exception()` 落盘的异常和这个字段关联起来，方便直接从返回值定位
  日志。
