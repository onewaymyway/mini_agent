# Goal 自动创建机制与来源追溯（Goal Provenance）指南

> 前置阅读：
> [外部输入网关指南](external-input-gateway-guide.md)、
> [看板与自主性指南](kanban-dashboard-guide.md)、
> [Goal 与 Cron 绑定指南](goal-cron-binding-guide.md)
>
> 本文档记录一次真实的排查过程：用户观察到"即使把 `autonomy_level`
> 设为 `maintenance`，外部输入似乎仍然能自动创建 Goal"，据此逐条核对
> 了外部输入到 Goal 的全部链路，找到了一处不受 `autonomy_level` 门控的
> 触发路径，并补上了"这个 Goal 到底是被谁、在哪一轮对话里创建的"这一层
> 缺失的可观测性。

## 1. 外部输入到 Goal 的全部链路一览

外部输入（RSS/天气/其它 source）进入系统后，一共有 **四条** 可能最终
影响 GoalBacklog 的独立链路，职责边界见下表：

| 链路 | 模块 | 输入 | 能不能创建*新* Goal | 是否受 `autonomy_level` 门控 |
|---|---|---|---|---|
| ① IngestionPolicy | `external_input/policy.py` | 事件 | 不能（P8 起已移除 `goal_candidate` 落点，只剩 `notify_only`/`enqueue_turn`） | 否（在 `_tick_passive()` 里，见下） |
| ② GoalRelevanceEngine | `external_input/goal_relevance.py` | 事件 × 已有 Goal | 不能，只 `attach_external_context()`/`try_advance_goal()` | Stage①（候选生成）在 `_tick_maintenance()`；Stage②（LLM 判定，见 §2）挂在 cron job 上，**不受 autonomy_level 门控** |
| ③ NoveltyJudge | `external_input/novelty_judge.py` | 事件（不看 Goal） | 能，但**只有用户在看板上点"确认"才会创建**（`confirm_novelty_candidate()`），代码里没有自动确认的调用点 | 不适用（人工触发） |
| ④ SoftGoalDeriver | `evolution/soft_goal_deriver.py` | capability_map / work_index / lesson_review / 外部知识等信号 | 能，`commit_goals()` 直接 `add_goal(source="agent_derived")` | **是**，只在 `AutonomousLoop._tick_autonomous()` 里调用，即 `autonomy_level == "autonomous"` 时才跑 |

链路 ①②③④ 里，真正"凭空"创建新 Goal 的只有 ③（人工确认）和
④（受 `autonomy_level` 严格门控）。**但这不代表 `maintenance` 档位下
外部输入完全不会导致新 Goal 出现**——见下一节。

## 2. 被漏掉的一条路径：cron job 不受 `autonomy_level` 门控

`AutonomousLoop.tick()` 根据 `autonomy_level` 分发到三个档位专属方法：

```python
def tick(self) -> None:
    autonomy_level = self._get_autonomy_level()
    if autonomy_level == "passive":
        self._tick_passive(); return
    if autonomy_level == "maintenance":
        self._tick_maintenance(); return
    self._tick_autonomous()   # 只有这一档才会调 SoftGoalDeriver
```

但 `_tick_passive()`（`passive`/`maintenance`/`autonomous` 三档都会先
执行到它）里有这一行：

```python
if self._cron_scheduler is not None:
    triggered = self._cron_scheduler.tick()   # 检查所有到期的 cron job
```

**`CronScheduler.tick()` 本身完全不看 `autonomy_level`**——它只按每个
job 自己的 `schedule` 判断是否到期。而 `sys:goal_relevance_judge` 这个
cron job（`external_input/goal_relevance.py::ensure_goal_relevance_judge_job()`）
是 daemon 启动时**无条件注册**的，默认每 10 分钟触发一次，跟
`autonomy_level` 没有任何关联：

```python
def run_goal_relevance_judge_once(paths, *, llm_helper, goal_backlog, enqueue_fn, ...):
    ...
    decision = goal_backlog.try_advance_goal(goal_id, cooldown_seconds=cooldown_seconds)
    ...
    if decision.action == "enqueue_turn" and enqueue_fn is not None:
        message = f"外部信号显示与你正在跟踪的目标『{...}』相关的新进展：...请判断是否需要推进、以及下一步该做什么。"
        enqueue_fn(message, {...})   # 真的调用 InputQueue.enqueue(initiator="cron")
```

`try_advance_goal()` 只在两种情况下会走到 `enqueue_turn` 分支：LLM 判定
某条外部事件 `relevant=True` 且 `advance_worthy=True`，并且目标 Goal
已经是 `active` 状态（不是 `paused`，那种情况会走 `reactivated` 分支，
不提交对话）。一旦走到这里，`server.py` 里真实的 `enqueue_fn` 会把一条
消息以 `initiator="cron"` 提交进 `InputQueue`：

```python
def _goal_relevance_enqueue(message: str, meta: dict):
    return self._bridge.input_queue.enqueue(message=message, initiator="cron", meta=meta)
```

这条消息随后作为**完整的、带工具权限的正常对话轮次**被 Agent 处理——
跟用户手动打字提交没有任何区别。如果 Agent 在这轮对话里判断"下一步该
做什么"包括"应该单独建一个 Goal 跟踪这件事"，它会用自己能调用的
建 Goal 方式（CLI 命令 `/agent goals add`、看板 API 等）去创建，
**这一步完全在 `autonomous_loop.py` 的档位分支之外，不受
`autonomy_level` 拦截**。

### `attach_external_context()` 与 `try_advance_goal()` 的区别

两者是 `run_goal_relevance_judge_once()` 对同一条"相关"外部信号依次执行
的两个独立动作，粒度不同：

| | `attach_external_context()` | `try_advance_goal()` |
|---|---|---|
| 触发门槛 | `relevant == True` | `relevant == True` 且 `advance_worthy == True`，且不在冷却期内 |
| 做的事 | 把事件摘要追加成一条 `external_context` 记录，纯写入 | Goal 非 active → 直接 `set` 回 active（`reactivated`，无对话）；Goal 已 active → 返回 `enqueue_turn`，交给调用方决定要不要真的提交一轮对话 |
| 会不会触发 LLM/对话 | 不会 | `enqueue_turn` 分支会 |
| 会不会导致新建 Goal | 不会，也不可能 | 不会直接建，但提交的那轮对话里 Agent 自己有可能建 |

## 3. Goal 来源追溯（Goal Provenance）：两个正交维度

在定位到上述问题之后，我们发现 `GoalNode` 一直缺一层可观测性：即使
知道"这个 Goal 是 `source=user`"，也无法分辨它到底是用户亲手敲的命令，
还是 Agent 在处理一轮 `cron`/`external` 触发的对话时"顺手"创建的——
后者对用户来说观感上完全是"系统自动帮我建了个 Goal"，即使技术上它走的
是跟手动创建一样的 `add_goal(source="user")` 调用。

为此新增了 `GoalNode.source_initiator` 字段，跟已有的 `source` 是两个
正交维度：

| 字段 | 回答的问题 | 可能取值 |
|---|---|---|
| `source` | 谁负责**决定**要建这个 Goal | `"user"`（用户显式决定）/ `"agent_derived"`（SoftGoalDeriver 推导）/ `"novelty_candidate"`（NoveltyJudge 候选，用户确认后落地） |
| `source_initiator` | 这次 `add_goal()` 调用发生在**哪个轮次**里 | `"user"`（用户手动创建，或没有轮次上下文时的默认兜底）/ `"cron"`（cron 提交的一轮对话里，Agent 自己决定创建的）/ `"external"`（external_input 网关 `enqueue_turn` 落点提交的一轮对话里创建的）/ `"autonomous_loop"`（SoftGoalDeriver 在 tick 内部直接创建，不经过 InputQueue） |

一个 `source=user, source_initiator=cron` 的 Goal，意味着：这个 Goal
"看起来"是走用户创建那条路径建的（比如 Agent 调用了 `/agent goals add`
这类原本给用户用的命令），但触发它的那轮对话其实是 cron 提交的，不是
用户本人在打字——这正是本次排查要暴露出来的那类 Goal。

### 3.1 实现方式：thread-local 透传轮次 initiator

跟 `tools/user_memory.py::set_current_user()`（透传"这一轮是谁发的
消息"给 `remember_about_user` 工具用）完全相同的模式，新增
`perception/turn_context.py`：

- `AgentRunner._main_loop()` 在把 `cmd.message` 交给 Agent 处理之前，
  调用 `set_current_turn_initiator(cmd.initiator, turn_id)`，把这一轮
  `InputQueue` 的 `initiator` 写进 thread-local。
- 轮次处理结束（`finally` 块，跟 `clear_current_user()` 相邻）调用
  `clear_current_turn_initiator()`，避免残留到下一次非轮次触发的调用
  （比如同一条线程随后跑 `AutonomousLoop.tick()`）。
- `GoalBacklog.add_goal()` 新增 `source_initiator: Optional[str] = None`
  参数：调用方显式知道触发上下文时（`SoftGoalDeriver`、
  `NoveltyJudge.confirm_novelty_candidate()`、CLI 命令、HTTP API 路由）
  应该显式传入；不传时读取 `turn_context.get_current_turn_initiator()`
  的 thread-local 兜底值。

### 3.2 各创建入口的标记方式

| 入口 | `source` | `source_initiator` |
|---|---|---|
| `cli/commands/goals.py::_cmd_add_goal`（`/agent goals add`） | `"user"` | 显式 `"user"` |
| `api/routes.py::POST /v1/goals`（看板"新建 Goal"表单） | 请求体 `source`，默认 `"user"` | 请求体 `source_initiator`，默认 `"user"` |
| `soft_goal_deriver.py::commit_goals()` | `"agent_derived"` | 显式 `"autonomous_loop"` |
| `novelty_judge.py::confirm_novelty_candidate()` | `"novelty_candidate"` | 显式 `"user"`（用户点击确认） |
| **未预料到的路径**（比如 Agent 在处理 cron/external 触发的对话时，通过 shell/工具间接调用到 `add_goal()`，调用方自己不知道触发上下文） | 视调用路径而定，通常仍是 `"user"` | **自动**从 thread-local 读取实际 initiator（`"cron"`/`"external"`），不会被误标成用户手动创建 |

历史数据（本字段新增之前写入 `goals.json` 的 Goal）反序列化时兜底为
`"user"`——对旧数据保守估计为用户创建，不会把历史 Goal 误标成某种自动
触发来源。

## 4. 看板展示

`apps/mini_agent_kanban/app.py::_render_goal_card()` 原本就会展示
`来源:{source}`；本次改动补充了 `source_initiator` 的展示——只在它不是
默认值 `"user"` 时额外渲染一行（避免绝大多数正常手动创建的 Goal 卡片上
多一行没有信息量的提示）：

- `source_initiator == "cron"` → `⏰ 由 cron 触发的对话中创建`
- `source_initiator == "external"` → `📡 由外部输入触发的对话中创建`
- `source_initiator == "autonomous_loop"` → `🤖 由自主 tick 直接派生`

## 5. 如果想彻底关闭"cron 触发的对话里可能建出新 Goal"这条路径

当前版本里，`source_initiator` 只解决**可观测性**问题（能在看板上看出
一个 Goal 是不是间接由 cron 触发的对话创建的），**没有**新增一道强制
拦截。如果需要更硬的门控，可以在以下位置之一加判断（本次未实现，留作
后续方向）：

1. `run_goal_relevance_judge_once()` 里，`decision.action == "enqueue_turn"`
   分支前，读取当前 `autonomy_level`，非 `autonomous` 档位时跳过
   `enqueue_fn` 调用（只保留 `attach_external_context`，不真正提交对话）。
2. `ensure_goal_relevance_judge_job()` 注册 cron job 时，给 handler 包一层
   `autonomy_level` 检查，非 `autonomous` 档位直接 `return False`（不消费
   候选，等下次 tick 再判断）。
3. 更通用地：在 `GoalBacklog.add_goal()` 里，当解析出的
   `source_initiator not in ("user",)` 且 `source == "user"` 时，视为
   "Agent 在非用户轮次里创建了一个看起来像用户创建的 Goal"，可以选择
   直接拒绝（抛异常）或强制降级为 `source="agent_derived"` + 打
   `needs_review` 标签，复用 `soft_goal_deriver.py` 已有的人工复核流程。

## 6. 涉及文件

- 新增 `src/mini_agent/perception/turn_context.py`
- `src/mini_agent/perception/goal_backlog.py`：`GoalNode.source_initiator`
  字段 + 序列化/反序列化 + `add_goal()` 参数与解析逻辑
- `src/mini_agent/api/server.py`：`_main_loop()` 里设置/清空 thread-local
- `src/mini_agent/api/routes.py`：`POST /v1/goals` 透传 `source_initiator`
- `src/mini_agent/cli/commands/goals.py`：`_cmd_add_goal` 显式标记
- `src/mini_agent/evolution/soft_goal_deriver.py`：`commit_goals()` 显式标记
- `src/mini_agent/external_input/novelty_judge.py`：`confirm_novelty_candidate()` 显式标记
- `apps/mini_agent_kanban/app.py`：`_render_goal_card()` 展示 `source_initiator`
- `tests/test_goal_provenance.py`：新增测试
