# 周期性 Goal 状态完整性护栏与自愈方案

> 状态：已实施完成。新增/修改文件清单见文末"实施记录"一节。
> 前置背景：`next_doc/goal_cron_binding_plan.md`（周期性 Goal ⇄ Cron 绑定/
> 触发机制）与 `next_doc/goal_cron_visibility_and_intervention_improvement_
> plan.md`（可见性/干预能力）已经把"周期性 Goal 正常应该怎么转"的机制建
> 好了。本方案解决的是一个在长期运行中被观察到的问题：**周期性 Goal 的
> `GoalNode.status` 被某个非预期路径写成了非 `active` 值（如
> `completed`），导致 `goal_cron_bridge._fire_goal_cycle()` 从此静默跳过
> 该 Goal 的每一次 tick，`recurring=True` 和 cron job 本身都没变、也没有
> 任何报错或日志，表现为"周期性 Goal 莫名其妙不转了"，且不是用户在看板上
> 手动改的。**

## 1. 根因

`_fire_goal_cycle()`（`evolution/goal_cron_bridge.py`）只用一个条件判断是否
触发本轮：

```python
if goal.status != "active":
    return False
```

而能把 `GoalNode.status` 改成非 `active` 的入口，排查后发现只有一部分会
检查这个 Goal 是不是 `recurring`：

| 入口 | 是否检查 `recurring` |
|---|---|
| `perception/goal_backlog.py::maybe_close_goal_by_overall_criteria()` | ✅ 已检查，`if goal.recurring: return None`，不是根因 |
| `evolution/objective_executor.py::_sync_goal_status()` | 只回写子 Objective，不动父 Goal，不是根因 |
| `cli/commands/goals.py::_cmd_set_status()`（`/agent goals done` / `pause`） | ❌ 未检查 |
| `api/routes.py::update_goal()`（`PATCH /v1/goals/{goal_id}`） | ❌ 未检查 |

最可疑的触发场景：agent 在执行某一轮周期性子 Objective 时，把"这一步做完
了"误判成"这个 Goal 完成了"，或者把父 Goal id 和当轮子 Objective id 搞
混，调用了 `/agent goals done <goal_id>`，把父 Goal 直接标记为
`completed`。此后 cron 每次 tick 都在 `goal.status != "active"` 处静默
返回，没有任何留痕。

## 2. 目标与非目标

**目标**：
1. **护栏（防止再发生）**：通用状态写入口（CLI `/agent goals done`、REST
   `PATCH /v1/goals/{goal_id}`）对"周期性 Goal + 试图写入非法状态"的组合
   直接拒绝并提示正确操作方式，不再允许静默写入。
2. **自愈（兜底已发生的情况）**：`_fire_goal_cycle()` 判定时，只把
   `abandoned` 当作周期性 Goal 的真终态；除 `active`/`paused`/`abandoned`
   外的任何状态（`completed`/`failed`/`cancelled` 等）视为"被误写"，自动
   拉回 `active` 并继续本轮触发，同时写一条 `progress_notes` 留痕（不新增
   字段、不新增标记位，复用已有的 `set_status()` + `append_progress_note()`）。
3. **保留 `paused` 的既有语义**：`paused` 是用户主动的"先别跑"，自愈逻辑
   不动它——`_fire_goal_cycle()` 对 `paused` 仍然是"不触发、不报错、不自
   动拉回"，与改动前行为一致。

**非目标**：
- 不改 `recurring`/cron job 绑定关系本身的机制。
- 不新增字段区分"谁改的状态"——留痕只用一条 `progress_notes` 文本记录，
  足以支撑排查，不为这一具体问题引入新的持久化结构。
- 不改 `abandoned` 的语义或触发方式，它仍是唯一能真正让周期性 Goal 停止
  的状态。

## 3. 改动点

### 3.1 护栏：合法状态白名单

新增一个共享校验函数
`perception/goal_backlog.py::validate_status_write_for_recurring_goal()`：

```python
_RECURRING_GOAL_ALLOWED_GENERIC_STATUSES = {"active", "paused", "abandoned"}

def validate_status_write_for_recurring_goal(node, status: str) -> Optional[str]:
    """周期性 Goal 通过通用入口（CLI /agent goals done|pause、REST PATCH）
    写状态时的合法性校验。返回 None 表示允许；返回非空字符串表示拒绝，
    内容是给调用方展示的错误信息。

    只约束 level == "goal" 且 recurring=True 的节点——Objective 节点和非
    周期性 Goal 不受影响，沿用原有的"想改就改"行为。
    """
    if node is None or node.level != "goal" or not node.recurring:
        return None
    if status in _RECURRING_GOAL_ALLOWED_GENERIC_STATUSES:
        return None
    return (
        f"{node.id} 是周期性 Goal（recurring=True），不允许通过通用状态"
        f"写入口直接改成 {status!r}。如果确实要彻底结束这个周期性 Goal，"
        f"请先 `/agent goals unrecur {node.id}` 停止周期性，再执行本操作；"
        f"如果只是想暂停这一轮，请用 `/agent goals pause {node.id}`。"
    )
```

`cli/commands/goals.py::_cmd_set_status()` 与
`api/routes.py::update_goal()` 在真正调用 `set_status()`/`update_fields()`
之前先调这个函数，命中即拒绝（CLI 走 `R.print_error()`，REST 走
`HTTPException(status_code=409, ...)`）。

`/agent goals abandon`（走 `_cmd_abandon()`，固定写 `"abandoned"`）不受影
响，因为 `"abandoned"` 本来就在白名单里。

### 3.2 自愈：`_fire_goal_cycle()` 放宽终态判断

```python
if goal.status == "abandoned":
    return False
if goal.status == "paused":
    # 用户主动暂停，保留既有语义：不触发、不报错、不自动拉回。
    return False
if goal.status != "active":
    # 周期性 Goal 的 status 被非预期路径写成了其它值（多半是 completed/
    # failed 等本该只属于一次性 Goal 的终态）——对周期性 Goal 而言，只有
    # abandoned 才是真终态，其余一律视为异常写入，自动拉回 active 并继续
    # 本轮触发，同时留痕方便事后排查是哪一轮出的问题。
    stale_status = goal.status
    goal_backlog.set_status(goal.id, "active")
    goal_backlog.append_progress_note(
        goal.id,
        f"⚠️ 检测到周期性 Goal 状态被写成 {stale_status!r}，"
        f"已自动恢复为 active 并继续第 {goal.cycle_count + 1} 轮触发",
    )
```

放在原判断的位置，其后的逻辑（`skip_next_cycle` / 并发检查 / 派生子
Objective）不变。

## 4. 影响范围与兼容性

- 非周期性 Goal（`recurring=False`）的所有写状态行为完全不变。
- 周期性 Goal 的 `active`/`paused`/`abandoned` 三个状态的写入行为不变。
- 唯一的行为变化：周期性 Goal 一旦被写成 `completed`/`failed`/`cancelled`
  等其它值——以前是永久卡住，现在是下一次 cron tick 自动恢复并继续；同时
  `_cmd_set_status`/`PATCH` 这两个通用入口以后不会再允许直接把周期性 Goal
  写成这些值，从源头堵住新的误写。
- `maybe_close_goal_by_overall_criteria()` 本来就跳过 `recurring=True` 的
  Goal，不受本次改动影响。

## 5. 实施记录

新增文件：
- `next_doc/goal_cron_status_integrity_and_self_healing_plan.md`（本文档）

修改文件：
- `src/mini_agent/perception/goal_backlog.py`：新增
  `validate_status_write_for_recurring_goal()`。
- `src/mini_agent/cli/commands/goals.py`：`_cmd_set_status()` 接入校验。
- `src/mini_agent/api/routes.py`：`update_goal()`（`PATCH /v1/goals/
  {goal_id}`）接入校验。
- `src/mini_agent/evolution/goal_cron_bridge.py`：`_fire_goal_cycle()`
  拆分 `abandoned`/`paused`/其它三种分支，其它分支自愈拉回 `active`。
