# Goal/Cron 用户意见反馈、父级说明继承与产出路径规范改进方案

> 状态：待确认，未开始实施
> 关联代码：`src/mini_agent/perception/goal_backlog.py`、`src/mini_agent/evolution/cron_scheduler.py`、
> `src/mini_agent/evolution/goal_cron_bridge.py`、`src/mini_agent/evolution/cron_job_workspace.py`、
> `src/mini_agent/evolution/objective_executor.py`、`src/mini_agent/cli/commands/goals.py`、
> `src/mini_agent/cli/commands/cron.py`、`src/mini_agent/api/routes.py`、
> `apps/mini_agent_kanban/app.py`
> 新增文件：`src/mini_agent/evolution/output_path_policy.py`、
> `.agent/policies/output_path_policy.md`（运行时生成，用户可编辑）

## 0. 目标与非目标

**目标**：
1. 用户可以随时对一个 Goal 或 CronJob "提意见"，意见会**持久化合入该节点的描述**，此后所有
   基于这个 Goal/CronJob 派生的执行（新一轮子 Objective、下一次 cron 触发）都会带着这条意见，
   而不是像现有 `inject_guidance()` 那样只对"下一次提交的这一个 step"生效一次就消失。
2. Goal 派生出的子 Objective，必须携带父 Goal 的 `description`（约束条件常写在父 Goal 里），
   不能因为子任务自己有更具体的描述就把父级说明整个挤掉。
3. 建立一份**用户可编辑**的产出路径规范文件，在没有特殊说明时对所有 Goal/Cron 执行生效：
   - 禁止把产出代码写入主项目 `src/` 目录；
   - 禁止把产出代码写入 `tests/` 目录；
   - 和 skill 相关的产出放到对应 skill 目录下；
   - 任务本身声明了工作目录的，产出放到该工作目录下。

**非目标（本轮不做）**：
- 不做"路径规范"的强制拦截（比如 hook 里硬拦截写 `src/` 的工具调用）——本轮只做
  **prompt 层面的规则注入**，让 agent 在执行前就看到规范，不做运行时强制校验/拒绝执行，
  避免误伤"用户确实特殊说明要改 src/ 下代码"的合法场景。
- 不做"意见"的语义理解/冲突检测（比如新意见和旧意见互相矛盾时自动去重或提示）——本轮
  只做**追加**，历史意见全部保留，由 agent 自己在执行时综合判断。
- 不改变现有 `ObjectiveExecutor.inject_guidance()`（单次 step 级别的临时插话）的语义，
  两者并存：`inject_guidance` 管"这一步临时补一句"，本方案管"这个 Goal/CronJob 以后
  永久都要考虑这条意见"。

## 1. 现状回顾（问题清单）

| 编号 | 问题 |
|---|---|
| P1 | Goal/CronJob 没有任何"用户意见持久化"入口，唯一的反馈通道 `inject_guidance()` 只影响下一次提交的单个 step，且提交后立即清空（`objective_executor.py:1478`） |
| P2 | `GoalBacklog.add_objectives_for_goal()` 批量创建子 Objective 时完全没传 `description`（`goal_backlog.py:582-609`），父 Goal 的约束彻底丢失 |
| P3 | `goal_cron_bridge._fire_goal_cycle()` 创建周期子 Objective 时 `description=job.task_template or goal.description` 是"二选一"（`goal_cron_bridge.py:148`），一旦 CronJob 配了 `task_template`，父 Goal 里的约束就不会出现在子任务里 |
| P4 | 完全没有"产出路径规范"这类全局约束，`ObjectiveExecutor._submit_step()` 拼的 message（`objective_executor.py:1458`）和 `CronJobWorkspace.render_prompt()`（dedicated cron 的 `prompt.md`）都没有统一注入点 |
| P5 | CronJob 绑定 Goal（`run_mode="goal_cycle"`）时，两边的"用户意见"目前没有任何联动机制，用户对 cron 提的意见不会反映到 Goal，反之亦然 |

## 2. 数据结构改动

### 2.1 `GoalNode`（`perception/goal_backlog.py`）新增字段

```python
user_feedback: list[dict] = field(default_factory=list)
# 每条: {"text": str, "at": float}
# 只做追加历史记录，供 UI/CLI 回看；真正影响执行的是下面第 3 节里
# 被同步拼接进 description 的那部分。
```

`to_dict`/`from_dict` 同步加这个字段，缺省 `[]`，向后兼容旧 `goals.json`。

### 2.2 `CronJob`（`evolution/cron_scheduler.py`）新增字段

```python
user_feedback: list[dict] = field(default_factory=list)
# 结构同上。
```

`to_dict`/`from_dict` 同步加，缺省 `[]`。

## 3. 意见反馈机制（P1、P5）

### 3.1 `GoalBacklog.add_user_feedback(goal_id, text)`

新增方法，逻辑：
1. 加锁、重新加载最新状态（沿用现有 `_locked()` + 先 reload 再写的模式）。
2. 找到节点（Goal 或 Objective 均可，Objective 场景较少但不特意禁止）。
3. `node.user_feedback.append({"text": text, "at": time.time()})`。
4. 把意见追加进 `node.description`，格式：
   ```
   <原 description>

   [用户意见 2026-08-04 10:00] <text>
   ```
   （沿用 `progress_notes` 现有的"追加而非覆盖"风格，时间格式复用 `time_utils.ts_to_str`）
5. `last_touched_at` 刷新（意见本身算一种"实质更新"，参与 P3 老化加成的归零逻辑，
   语义上合理——用户主动干预了，不该继续被当作"停滞"）。
6. **联动（P5）**：如果 `node.is_goal and node.recurring and node.recurrence_cron_job_id`，
   额外调用 `CronScheduler` 一侧的联动方法（见 3.3），把同一条意见同步追加到绑定的 CronJob。
   反之，若在 CronJob 侧调用反馈且该 job 的 `run_mode == "goal_cycle"` 且 `goal_id` 有效，
   也要反向同步到 Goal。两个方向都做，但要**防重复循环**（下面 3.3 说明）。

### 3.2 `CronScheduler.add_user_feedback(job_id, text)`

新增方法，逻辑：
1. 找到 job。
2. `job.user_feedback.append({"text": text, "at": time.time()})`。
3. 追加进 `job.description`（人类可读展示用途，格式同上）。
4. **同时追加进 `job.task_template`**——这是关键：`task_template` 才是真正决定 `run_mode="message"`
   裸投递内容、以及 `_fire_goal_cycle()` 兜底内容的字段，只改 `description` 不会影响任何实际执行。
   格式同样用 `[用户意见 ...]` 区块追加在末尾。
5. 若该 job 是 dedicated-execution 模式（存在 `.agent/cron_jobs/<id>/prompt.md`，见
   `cron_job_workspace.py`），**同步追加进 `prompt.md`**——因为 dedicated 模式下 `render_prompt()`
   读的是 `prompt.md` 模板，不是 `task_template`，两条腿都要喂到。
   做法：`CronJobWorkspace.append_user_feedback(text)`，直接在文件末尾追加一段，
   不做模板占位符解析（避免破坏用户已有的自定义模板结构）。
6. **联动**：若 `job.run_mode == "goal_cycle" and job.goal_id`，调用 `GoalBacklog.add_user_feedback()`
   把同一条意见同步写到绑定的 Goal。

### 3.3 防重复循环

`GoalBacklog.add_user_feedback()` 和 `CronScheduler.add_user_feedback()` 互相调用对方一次即可
完成双向同步，不需要对方再回调回来。落地方式：两个方法都新增一个内部参数
`_sync: bool = True`，联动调用时对方内部传 `_sync=False`，跳过"继续往外联动"这一步，
天然阻断循环（跟现有 Track B 状态同步"单向回写，不做无限反弹"的思路一致）。

### 3.4 CLI 入口

`cli/commands/goals.py`：
```
/agent goals feedback <id> <text>
```
仿照现有 `_cmd_progress`（更新 `progress_notes`）的写法加 `_cmd_feedback`。

`cli/commands/cron.py`：
```
/cron feedback <id> <text>
```
同理新增。

### 3.5 REST 入口

`api/routes.py` 新增：
- `POST /v1/goals/{id}/feedback`  body: `{"text": str}`
- `POST /v1/cron/{id}/feedback`   body: `{"text": str}`

返回更新后的节点/job 摘要，供前端立即刷新。

### 3.6 kanban 面板

`apps/mini_agent_kanban/app.py`：在 Goal 详情面板和 Cron 详情面板各加一个"提意见"输入框
+ 提交按钮，调用上面的 REST 端点；同时在详情区展示 `user_feedback` 历史列表（时间+内容），
复用现有 P1-P4 观测面板（watchlist/goal-relevance/notification）的卡片样式，保持视觉一致。

## 4. 子目标继承父级 Goal 说明（P2、P3）

### 4.1 `GoalBacklog.add_objectives_for_goal()` 补齐 description

`goal_backlog.py:582-609`，创建每个子 `GoalNode` 时补上：
```python
description=goal.description,
```

### 4.2 `goal_cron_bridge._fire_goal_cycle()` 改"二选一"为"拼接"

`goal_cron_bridge.py:148`，原：
```python
description=job.task_template or goal.description,
```
改为拼接两者（父 Goal 说明在前，本轮具体任务模板在后，都保留）：
```python
description=_compose_parent_and_task(goal.description, job.task_template)
```
新增小工具函数（放 `goal_cron_bridge.py` 或 `goal_backlog.py` 均可，倾向后者，
供多处复用）：
```python
def compose_context(parent_desc: str, own_desc: str) -> str:
    parts = [p.strip() for p in (parent_desc, own_desc) if p and p.strip()]
    return "\n\n".join(parts)
```

### 4.3 `GoalBacklog.effective_context(node_id)` —— 执行侧兜底

新增只读方法，向上遍历 `parent_id` 链（当前模型最多两层 Goal→Objective，但写成循环以防未来
出现多级），拼出"从根 Goal 到当前节点"的完整说明链：
```python
def effective_context(self, node_id: str) -> str:
    chain = []
    node = self._nodes.get(node_id)
    seen = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        if node.description:
            chain.append(node.description)
        node = self._nodes.get(node.parent_id) if node.parent_id else None
    return "\n\n".join(reversed(chain))
```
这是**双保险**：即使某次创建 Objective 时忘了传 `description`（比如未来新增第三条创建路径），
执行侧仍能通过这个方法补上父级说明。

### 4.4 `ObjectiveExecutor` 注入点

`_submit_step()`（`objective_executor.py:1458` 附近）在拼 `message` 时，新增一段
`[目标说明]`，内容来自 `self._goal_backlog.effective_context(ex.objective_id)`
（`_goal_backlog` 已经是该类的既有依赖，`_attempt_redecompose` 里已经在用，见
`objective_executor.py:1278`）。放在 `[自主任务 - ...]` 标题之后、步骤描述之前。

## 5. 产出路径规范（P4）

### 5.1 规范文件

新路径：`<project_root>/.agent/policies/output_path_policy.md`

首次不存在时写入内置默认模板（幂等 ensure，仿照 `cron_job_workspace.py` 的
"已存在文件不覆盖"模式）：

```markdown
# 产出路径规范

在没有特殊说明的情况下，执行任务时请遵守：

1. 禁止把产出的代码写入主项目 `src/` 目录。
2. 禁止把产出的代码写入 `tests/` 目录。
3. 和 skill 相关的产出，放到对应 skill 的目录下。
4. 任务本身已经说明了工作目录的，产出放到该工作目录下。

如果任务描述中明确要求修改 `src/`、`tests/` 或指定了其他路径，以任务描述的
明确说明为准，本规范不覆盖显式指令。
```

用户后续可以直接编辑这个文件（比如加第 5 条规则），不需要改代码。

### 5.2 新增模块 `evolution/output_path_policy.py`

```python
DEFAULT_POLICY = "..."  # 上面的默认模板

def ensure_policy_file(paths) -> Path: ...   # 幂等创建
def load_policy(paths) -> str: ...           # 读取当前内容（含用户改动）
```

### 5.3 注入点

- `ObjectiveExecutor._submit_step()`：每个 step 提交时都追加一段 `[产出路径规范]\n{policy}`
  （每步都加而不是只加第一步，防止长任务多步执行中间步骤"忘记"）。
- `CronJobWorkspace`：`DEFAULT_PROMPT_TEMPLATE` 增加 `{{output_policy}}` 占位符；
  `render_prompt()` 渲染时替换为 `load_policy()` 的内容。已存在的、用户自定义的 `prompt.md`
  如果没有这个占位符则不受影响（不强行插入，保持向后兼容——用户如果想要这段规范，
  自己在 `prompt.md` 里加上 `{{output_policy}}` 即可，也可以在规范文件里自己维护，
  等价于用户主动选择不需要）。
- `CronScheduler._fire()`（`run_mode="message"` 裸投递路径）：投递前统一在消息末尾
  追加同一段规范文本，保持三条执行路径（dedicated cron / goal_cycle cron / 普通 objective）
  行为一致。

## 6. 实施顺序（Track 拆法）

| Track | 内容 | 依赖 |
|---|---|---|
| A | `GoalNode`/`CronJob` 加 `user_feedback` 字段 + `to_dict`/`from_dict` | 无 |
| B | `GoalBacklog.add_user_feedback()` / `CronScheduler.add_user_feedback()` + 双向联动 + `CronJobWorkspace.append_user_feedback()` | A |
| C | CLI `feedback` 子命令（goals + cron） | B |
| D | REST 端点（goals + cron feedback） | B |
| E | kanban 反馈输入框 + 历史展示 | D |
| F | `add_objectives_for_goal()` 补 description、`_fire_goal_cycle()` 拼接、`GoalBacklog.effective_context()` | 无（可与 A-E 并行） |
| G | `ObjectiveExecutor._submit_step()` 注入 `[目标说明]` | F |
| H | `output_path_policy.py` 新增模块 + 默认模板文件 | 无（可与 A-G 并行） |
| I | `_submit_step()` 注入 `[产出路径规范]`、`CronJobWorkspace` 占位符、`CronScheduler._fire()` 注入 | H |
| J | 文档补充：`docs/goal-cron-binding-guide.md` 加"用户反馈"和"产出路径规范"两节 | C, D, I |

每个 Track 落地后建议各自跑一次现有 `tests/test_goal_backlog*.py`、
`tests/test_cron_scheduler*.py`、`tests/test_objective_executor_*.py` 确认不回归，
再新增针对性测试（反馈追加/联动/防循环、父子说明拼接、规范文件读取）。

## 7. 待确认问题（已回收）

- CronJob 反馈是否联动同步到绑定 Goal：**需要**（见第 3 节双向联动设计）。
- 规范文件路径：`.agent/policies/output_path_policy.md`，**已确认**。
- kanban 面板本轮一起做：**是**（Track E）。
