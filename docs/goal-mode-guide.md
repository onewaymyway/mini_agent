# Goal 模式指南（Goal Mode）

设定一个目标，Agent 自动多轮尝试直至达成，或在触发安全阀时如实汇报未完成的原因。

与 [Role Agent 系统](role-agents-guide.md) 的 EvaluatorAgent 修订循环不同：
Evaluator 循环发生在**一次 `run_turn` 内部**，受 `cfg.max_turns` 硬顶约束；
Goal 模式是**跨多次 `run_turn` 的外层驱动循环**，能在撞到轮次/上下文上限时
自动压缩历史后继续，并且支持进程被杀死后从上一个完整轮次恢复。

---

## 设计思路

```
用户 /goal <目标文本>
        │
        ▼
GoalSpecBuilder 生成第 1 版验收标准草案（JSON 结构化，独立 LLM 调用，
不占用主 Agent 上下文；若检测到生成结果"几乎照抄"用户原话，会带纠正提示
自动重试一次，见下文「验收标准生成质量保障」）
        │
        ▼
展示给用户 ──反馈──► 修订生成新版本（版本号+1，展示 diff）──┐
        │                                                    │
        └────────────────── 循环，直到 /confirm ─────────────┘
        │
        ▼ /confirm
GoalRunner.run()  ← 外层驱动循环
  ┌───────────────────────────────────────────────────┐
  │ loop（安全阀：max_rounds / max_total_compacts /     │
  │       consecutive_same_feedback_limit）             │
  │   1. 组装 prompt：目标+验收标准（首轮）或上一轮反馈    │
  │   2. CoarseStepExecutor.execute() → 一次完整 run_turn │
  │   3. 若撞到 max_turns 硬顶 → 显式 compact，重跑本步   │
  │      （不计入轮次预算）                               │
  │   4. 否则调用 GoalJudge 评审：                        │
  │        DONE          → 结束，返回成功                 │
  │        CONTINUE       → 反馈注入历史，回到 1           │
  │        NEED_COMPACT   → 显式 compact，回到 1（不计轮次）│
  │   5. CONTINUE 分支检测"是否卡住"（连续雷同反馈）：       │
  │        卡住 → 花一次恢复额度：compact+提示换思路，回到1 │
  │        额度耗尽后再卡住 → 终止，状态 stuck             │
  │   6. 每个轮次边界都落盘 GoalState                     │
  └───────────────────────────────────────────────────┘
```

**为什么不复用 Evaluator 的修订循环？** Evaluator 循环发生在 `_agentic_loop()`
内部，一次 `run_turn` 撞到 `cfg.max_turns` 就会硬性截断，没有机会先压缩历史
再继续；而 Goal 模式的目标往往需要跨越多次 `run_turn`（每次内部消耗完整的
`max_turns` 预算后，压缩历史，开始新的一轮）。两者可以同时存在、互不冲突：
Evaluator 仍然在每次 `run_turn` 内部做质量把关，GoalRunner 在更外层做"目标
达成"的把关。

---

## 启用方式

`agent_config.json` 中新增 `goal_mode` 配置块（默认 `enabled: false`）：

```json
{
  "goal_mode": {
    "enabled": true,
    "judge_tools_enabled": false,
    "max_rounds": 20,
    "max_total_compacts": 10,
    "consecutive_same_feedback_limit": 3,
    "same_feedback_similarity_threshold": 0.9,
    "max_stuck_recoveries": 1,
    "persist_state": true,
    "auto_resume_prompt": true
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 总开关，关闭时 `/goal` 命令报错提示未启用 |
| `spec_builder_model` / `spec_builder_provider` | `null` | GoalSpecBuilder 用的模型，`null` = 复用主 `cfg.model` |
| `judge_model` / `judge_provider` | `null` | GoalJudge 用的模型，`null` = 复用主 `cfg.model` |
| `judge_tools_enabled` | `false` | GoalJudge 是否挂载工具自己验证（见下节），默认关闭（最小权限原则） |
| `judge_yes_mode` | `false` | 仅当 `judge_tools_enabled=true` 时生效：工具调用是否真实执行（`--yes` 全放行），关闭时仍强制 sandbox 拦截 |
| `judge_allowed_tools` | `["bash","read_file","grep","glob"]` | `judge_tools_enabled=true` 时的工具白名单 |
| `judge_allowed_tool_groups` | `[]` | 同上，按工具组授权 |
| `max_rounds` | `20` | 外层循环轮次上限 |
| `max_total_compacts` | `10` | 单次 goal 执行期间最多允许几次 compact，防止"压缩风暴" |
| `consecutive_same_feedback_limit` | `3` | 连续 N 轮 Judge 反馈高度雷同 → 判定为"卡住"，提前终止 |
| `same_feedback_similarity_threshold` | `0.9` | `difflib.SequenceMatcher` 相似度阈值，达到即计入"雷同" |
| `max_stuck_recoveries` | `1` | 判定"卡住"后先压缩历史+提示换思路、再给几次机会（见下方"卡住恢复"），额度耗尽后再卡住才真正终止；设为 `0` 等价于旧行为（一卡住就终止） |
| `judge_show_prompt` | `false` | 打印发给 GoalJudge 的完整输入 prompt（目标、验收标准、主 Agent 产出、上一轮反馈），排查判定依据用 |
| `persist_state` | `true` | 是否在每个轮次边界落盘 `goal_state.json`（供异常中断恢复） |
| `auto_resume_prompt` | `true` | 启动 REPL 时若检测到未完成的 goal，是否主动提示 |

---

## 卡住恢复：先 compact 再给一次机会

之前的行为：GoalJudge 连续 `consecutive_same_feedback_limit` 轮给出高度相似的
反馈（`same_feedback_similarity_threshold` 判定相似度）就直接判定"卡住"、
终止整个 goal 执行。这其实经常是误判——历史里堆积了大量试错过程和中间产出，
干扰了主 Agent 的判断力，让它反复陷入同一个思路出不来，而不是目标真的做不到。

现在的行为：判定"卡住"后不直接终止，而是：

1. 执行一次 compact（复用现有的历史压缩机制，并自动重新钉住目标+验收标准）
2. 显式注入一条提示，点明"你连续几轮反馈高度相似，疑似卡在同一个问题上"，
   要求换一个角度、方法或先做诊断性检查，而不是重复同样的动作
3. 重置卡住计数，继续跑（不计入 `max_rounds` 预算）

如果压缩+提示之后还是给出高度雷同的反馈，说明这次恢复没有起作用，等
`max_stuck_recoveries` 额度耗尽后再次卡住就会真正终止（`status=stuck`）。
一旦某一轮出现明显不同于上一轮的反馈（判定为"真实进展"），卡住计数和
恢复额度都会被重置——额度是"每次卡住独立计算"，不是整个 goal 执行期间
总共只能用一次。

设置 `max_stuck_recoveries: 0` 可以关闭这个行为，回到"一卡住就终止"的旧逻辑。

---

## 使用方式

### 1. 设定目标 —— 验收标准协商

```
/goal 把测试覆盖率提上去
```

框架会生成第 1 版验收标准草案并展示：

```
目标（第 1 版）：把 src/ 目录下核心模块的单测覆盖率提升到可接受水平
验收标准：
  1. pytest --cov 输出的总覆盖率达到 80% 以上
  2. 新增测试全部通过，不破坏既有测试
验证方式：run_command
验证命令：pytest --cov=src --cov-report=term-missing

输入 /confirm 确认并开始执行，输入修改意见继续调整草案，输入 /cancel 放弃。
```

- 输入任意文字（比如"覆盖率目标改成 70%，另外要求不能改动 tests/ 目录下已有文件"）
  → 基于当前版本 + 你的反馈重新生成下一版，并展示版本 diff
- 输入 `/confirm` → 冻结当前版本，开始执行
- 输入 `/cancel` → 放弃本次协商，不进入执行

这个协商过程是**独立的会话态**，不会写入主 Agent 的对话历史，也不消耗
`max_rounds` / 上下文预算。

> **daemon connected 模式**：如果你是通过 `mini-agent daemon connect` 这样的远程
> 客户端发起的 `/goal <目标>`，协商过程会通过 daemon 的通用交互式提问网关
> （`/v1/interactions` 系列端点，见 [HTTP API 指南](http-api-guide.md#通用交互式提问)）
> 转发到你的客户端——每一版草案和 `/confirm`/`/cancel`/修改意见都在你自己的终端里
> 完成，不需要也不应该去 daemon 进程本身的终端上操作。

> **验收标准生成质量保障**：GoalSpecBuilder 的 system prompt（`prompts/system/
> goal_spec_builder.md`）明确要求把用户目标"加工"成具体、可客观核查、分维度
> 的标准，而不是照抄原话（例如禁止把"给函数加单测"直接当成一条标准）。代码层
> 面还加了一道兜底：如果生成结果与用户原话高度雷同（`_looks_like_verbatim_echo`），
> 会自动带纠正提示重试一次；`/goal <文本>` 的修订对话（`_looks_like_verbatim_echo`
> 同样应用于 `revise`）里，也会过滤掉直接照抄你反馈原句的"新标准"。如果生成
> 仍然不够具体，直接用修改意见让它继续调整即可。

### 2. 执行

确认后自动进入 GoalRunner 循环，过程中会完整打印每轮 GoalJudge 的核查内容
（不只是一行 DONE/CONTINUE 状态）和每次 compact 的真实摘要文本：

```
[GoalRunner] 第 1/20 轮执行中…

[🎯 目标核查 · goal_judge]

**验收核查**
- pytest 全部通过：不通过 —— 有 2 个用例报错
- lint 无报错：通过

**结论**
测试仍有 2 个用例失败，尚未达成目标。

**反馈**
请检查 test_foo.py 中的 xxx 用例，报错信息显示是 yyy 导致的空指针。

目标状态：🔄 尚未达成，需继续尝试

[GoalRunner] 第 2/20 轮执行中…

[GoalRunner] 正在压缩历史…

— Compact 摘要 —
（LLM 生成的对话摘要正文）

[GoalRunner] compact 完成（第 1 次）。

[🎯 目标核查 · goal_judge]
...
GOAL_STATUS: DONE

[GoalRunner] 目标已达成（共 2 轮）。

Goal 执行结果： done
轮次：2  compact 次数：1
（GoalJudge 的核查结论文本）
```

过程中可以 `Ctrl-C` 中断，状态会被保存为 `running`（可继续），之后可用
`/goal resume` 续跑。

> **`Ctrl-C` 中断 ≠ `/goal cancel`**：两者语义不同，不要混淆。
> `Ctrl-C` 中断的真实意图通常是"先停一下，之后还想继续"，所以状态会保持
> `running`（和轮次边界正常保存的状态一致），`/goal resume` 能找到它；
> `/goal cancel` 才是显式放弃，状态会被标记为 `cancelled`，`/goal resume`
> 默认不会恢复它（除非加 `--force` 强制恢复，见下）。

### 3. 恢复（进程被杀死 / 意外中断后）

```
/goal resume            # 自动找最近一个 status=running 的 goal
/goal resume <sid>      # 指定 session id 恢复
/goal resume <sid> --force  # 强制恢复非 running 状态的记录（比如 cancelled）
/goal list               # 列出所有可恢复的 goal 任务（status=running，可能不止一个）
```

重新打开 REPL 时，如果检测到未完成的 goal，也会主动提示：

```
[Goal 模式] 检测到未完成的目标任务（session: 871fae1b），
输入 /goal resume 871fae1b 可继续执行，或直接忽略进入正常对话。
```

> **多个进程各自 `/goal` 了不同目标、都被意外杀死怎么办？**
> `sessions_dir` 下每个 session 各有一份独立的 `goal_state.json`，都不会丢。
> 但启动提示默认只报告"最近更新的那一个"，避免刷屏。如果检测到不止一个可恢复
> 目标，提示会变成：
> ```
> [Goal 模式] 检测到 2 个未完成的目标任务，最近一个是 session: 871fae1b。
> 输入 /goal list 查看全部，或直接 /goal resume 871fae1b 恢复最近这个，
> 也可忽略进入正常对话。
> ```
> 用 `/goal list` 能看到全部 session_id（含各自的 round / updated_at），
> 再逐个 `/goal resume <sid>` 恢复即可，不会因为只显示一个而"看起来丢了"。

### 4. 查看 / 清理状态

```
/goal status    # 查看当前 session 的 goal 状态（轮次、compact 次数、最后判定）
/goal list      # 列出所有可恢复的 goal 任务（跨 session，可能不止一个）
/goal cancel    # 清理当前 session 的 goal 状态记录（不会中断正在运行的循环）
```

---

## GoalJudge：目标达成判定

每一轮 `run_turn` 结束后，GoalJudge 对照验收标准清单逐条核查，输出：

```
**验收核查**
- [标准1 摘要]：通过 / 不通过 —— 依据
- [标准2 摘要]：通过 / 不通过 —— 依据

**结论**
（简要说明）

**反馈**
（仅 CONTINUE 时必填：给主 Agent 的具体下一步指令）

GOAL_STATUS: DONE | CONTINUE | NEED_COMPACT
```

GoalJudge 的输出通过两层机制确保不会被误认成主 Agent 在说话：

1. **专属显示名**：GoalJudge / GoalSpecBuilder 内部都是独立的 `Agent` 实例，
   各自设置了专属的 `cfg.agent_name`（`🎯 GoalJudge` / `📋 GoalSpecBuilder`），
   而不是沿用主 Agent 的 `cfg.agent_name`。这样它们各自调用模型时，终端里
   打印的前缀（`print_assistant_prefix`）就是 `🎯 GoalJudge ❯ ...`，一眼能
   看出这是评估者/协商助手在说话，不会和主 Agent 的输出混在一起。
2. **结构化展示块**：GoalRunner 每轮结束后额外打印一份 `format_feedback()`
   格式化过的核查结果，带 `[🎯 目标核查 · goal_judge]` 标题。

判定结果通过 `role_agents/feedback.extract_goal_status()` 提取，解析失败时
**保守按 CONTINUE 处理**（绝不会因为解析异常被误判为 DONE）。

### 工具权限：纯文本判定 vs 自己跑命令验证

`judge_tools_enabled` 控制 GoalJudge 是否有能力自己验证：

- **`false`（默认）**：GoalJudge 不挂任何工具，纯读文本判断（与
  `role-agents-guide.md` 里的 EvaluatorAgent 行为一致）。零风险，但依赖主
  Agent 的自述和历史记录，存在"轻信"的可能。
- **`true`**：按 `judge_allowed_tools` / `judge_allowed_tool_groups` 白名单
  挂载工具（默认仍强制 `sandbox=True`），可以自己跑测试/lint 命令来验证验收标准，
  而不是单纯相信主 Agent 的自我汇报。适合验收标准是 `verification_method:
  run_command` 的场景。

  **注意**：`judge_tools_enabled=true` 默认仍然是 `sandbox=True`，意味着工具
  调用会被拦截、只显示"would have executed"、不会真正跑起来——对于"跑一遍
  测试确认真的通过"这种场景，这样等于形同虚设。如果需要 GoalJudge **真实执行**
  命令（比如真的跑一遍 `python xxx.py` 或 `pytest`），额外打开：

  ```json
  {
    "goal_mode": {
      "judge_tools_enabled": true,
      "judge_yes_mode": true
    }
  }
  ```

  `judge_yes_mode=true` 时 GoalJudge 的工具调用会以 `auto_approve=True` +
  不走 sandbox 的方式真实执行，等价于人工一直按 `--yes` 全部放行，不会逐条
  弹出确认。**请只在信任验收标准里的验证命令时开启**——这意味着 GoalJudge
  能不经确认地真实执行 `judge_allowed_tools` 白名单里的工具（默认是
  `bash`/`read_file`/`grep`/`glob`）。

两种模式下输出格式一致，只是判定依据不同。

---

## 安全阀

防止真的死循环烧 token，多重兜底：

| 安全阀 | 触发条件 | 行为 |
|--------|----------|------|
| `max_rounds` | 外层循环轮次达到上限 | 终止，状态 `max_rounds_exhausted`，汇报最后一轮反馈 |
| `max_total_compacts` | compact 次数达到上限 | 终止，避免"跑几轮就 compact 一次"的压缩风暴 |
| 连续雷同反馈 | 连续 N 轮 Judge 反馈相似度 ≥ 阈值 | 先花一次"卡住恢复额度"（`max_stuck_recoveries`）压缩历史+提示换思路，继续跑；额度耗尽后再次卡住才终止，状态 `stuck`，汇报"卡在同一个问题上"（见上方"卡住恢复"一节） |
| `hit_max_turns` | 单次 `run_turn` 撞到 `cfg.max_turns` | 不终止，显式 compact 后重跑本步（不计入轮次预算） |

所有安全阀触发时都会**如实汇报**已尝试的轮次、compact 次数、最后一次反馈，
不会静默失败。

---

## 目标上下文的"钉住"机制

`GoalSpec` 的目标 + 验收标准会作为一条特殊类型的消息
（`HType.GOAL_CONTEXT`，见 [History 类型化设计](history-typed-design.md)）
重新附加到历史末尾：

- 每次显式 compact（`hit_max_turns` 兜底 / `NEED_COMPACT`）之后
- **每一轮结束后都无条件重新钉一次**——因为 `agent.py` 内部的自动 compact
  （`CompositeTrigger` 命中，见 [Compact 设计文档](compact-design.md)）可能
  在 GoalRunner 不知情的情况下，已经把之前钉住的目标信息压缩掉了

这保证了目标信息不会因为压缩策略的选择而被稀释或丢弃。

---

## 异常中断恢复

`GoalState` 落盘到 `.agent/sessions/<session_id>/goal_state.json`：

```json
{
  "status": "running",
  "session_id": "871fae1b",
  "goal_spec": { "...冻结后的 GoalSpec..." },
  "round": 7,
  "last_judge_feedback": "...",
  "compacts_done": 2,
  "consecutive_same_feedback": 1
}
```

**写入时机**（只在轮次边界写，不在轮次内部频繁写）：

1. GoalSpec 确认冻结时
2. 每轮 `run_turn` 完成 + Judge 判定完成后
3. compact 完成后
4. 结束时（DONE / 安全阀触发 / 用户取消）

**写入方式**：原子写（先写 `.json.tmp` 再 `os.replace()`），防止写入过程中
被 kill 导致状态文件本身损坏成半截 JSON。

**最坏情况**：进程被 kill 只会丢失"正在进行中的那一轮"，不会丢失整个 goal
或损坏对话历史——主 Agent 的对话历史本身走的是既有的 session 持久化机制
（`agent.session_id` / `agent.load_session()`），`goal_state.json` 只存
"指向 session 的引用 + goal 专属元数据"，不重复保存一份历史。

如果恢复时发现状态文件本身损坏（`GoalStateStore.load()` 返回 `None`），
`/goal resume` 会明确报错，不会静默地用空状态继续跑。

### 排查"重启后找不到可恢复的 goal"

`/goal resume`（不带参数）找不到可恢复目标时，现在会打印具体原因而不是只回
一句"没找到"：

```
没有找到可恢复的 goal 任务。（扫描目录：/path/to/project/.agent/sessions）
该目录下没有任何 goal_state.json 记录——如果你确定之前跑过 goal，
请检查是否在跟当时相同的项目目录下启动（--project 参数 / 当前工作目录是否一致）。
```

或者（目录下有记录，但都不是 running 状态）：

```
找到 2 条 goal_state.json 记录，但状态都不是 running：
  session=871fae1b  status=done  round=5
  session=a3f8c210   status=cancelled  round=2
```

**最常见的原因是项目目录不一致**：`goal_state.json` 存在
`<project_root>/.agent/sessions/<session_id>/`，`project_root` 由启动时的
`--project` 参数或当前工作目录（`Path.cwd()`）决定。如果上次启动和这次启动
时的工作目录不同（比如上次是从某个 IDE 终端启动、这次是双击一个不同工作
目录的快捷方式启动），扫描到的 `.agent/sessions/` 就会是不同的目录，自然
"找不到"——**并不是状态丢失了，只是扫描错了地方**。建议始终用同一个
`--project <绝对路径>` 参数启动，避免依赖隐式的当前工作目录。

如果确认目录一致但仍然找不到，可以直接用 `find_resumable_session()` /
`scan_goal_states()`（`goal_mode/state.py`）在 Python 里手动排查：

```python
from mini_agent.goal_mode.state import scan_goal_states
from pathlib import Path
print(scan_goal_states(Path("/你的项目目录")))
```

---

## 为细粒度版本预留的扩展点

当前是**粗粒度版本**：每一步调用一次完整的 `agent.run_turn()`，跑完才评审。
缺点是无法在过程中（比如工具调用到一半）就中断纠偏。

`goal_mode/executor.py` 把"跑一步"抽象成了 `GoalStepExecutor` 接口：

```python
class GoalStepExecutor(ABC):
    @abstractmethod
    def execute(self, agent: "Agent", prompt: str) -> GoalStepResult: ...
```

`GoalStepResult` 现在就把 `tool_calls_made` / `turns_used` 等字段填上
（即使粗粒度版本目前用不到），未来做细粒度版本（在 `_agentic_loop` 内部、
每次工具调用后就有机会插入 Judge 判断）时，只需要新增一个
`FineGrainedStepExecutor` 实现同样的接口，`GoalRunner` 的主循环完全不需要
改动。

---

## 文件位置一览

| 文件 | 职责 |
|------|------|
| `goal_mode/spec.py` | `GoalSpec` 数据结构 + `GoalSpecBuilder`（自然语言→结构化验收标准，多轮修订） |
| `goal_mode/executor.py` | `GoalStepExecutor` 接口 + `CoarseStepExecutor` |
| `goal_mode/state.py` | `GoalState` + `GoalStateStore`（原子落盘/恢复）+ `find_resumable_session` / `list_resumable_sessions`（全量列出，供 `/goal list`）/ `scan_goal_states`（诊断用） |
| `goal_mode/runner.py` | `GoalRunner` 外层驱动循环 |
| `role_agents/goal_judge.py` | GoalJudge：`build_goal_judge_prompt` / `run_goal_judge` |
| `role_agents/feedback.py` | `extract_goal_status()`，`RoleFeedback.goal_status` 字段 |
| `cli/commands/goal_mode_cmd.py` | `/goal` 系列 slash 命令 |
| `config/models.py::GoalModeConfig` | 配置模型 |
| `storage/paths.py::session_goal_state()` | `goal_state.json` 路径 |
| `prompts/system/goal_judge.md` | GoalJudge 的 system prompt |
| `prompts/system/goal_spec_builder.md` | GoalSpecBuilder 的 system prompt |
| `prompts/user/goal_judge_request.md` | GoalJudge 每轮核查的 user 消息模板 |
| `prompts/user/goal_spec_initial_request.md` | GoalSpecBuilder 首次生成验收标准的 user 消息模板 |
| `prompts/user/goal_spec_revise_request.md` | GoalSpecBuilder 修订验收标准的 user 消息模板 |
| `prompts/user/goal_context.md` | 目标+验收标准"钉住"消息的模板 |
| `prompts/fragments/goal_mode.md` | `PRIOR_FEEDBACK_BLOCK` 等细粒度文本片段 |

所有发给模型的 prompt（system/user）都通过 `mini_agent.prompts.pm`（`PromptManager`）
统一加载渲染，不在 Python 代码里硬编码字符串——这是项目的统一约定，参见
[Prompt 管理模块](../src/mini_agent/prompts/manager.py) 顶部文档。

---

## 注意事项

- Goal 模式默认关闭，需要显式在配置文件中打开
- 验收标准的质量直接决定 GoalJudge 判定的可靠性——建议尽量选择可通过命令
  验证的标准（`verification_method: run_command`），而不是纯"感觉写得好不好"
- `judge_tools_enabled=true` 时 GoalJudge 会消耗额外的模型调用和工具调用
  预算，请根据实际需要开启
- GoalRunner 目前是**同步阻塞执行**（`/goal` 命令会一直跑到完成或安全阀
  触发才返回），过程中只能通过 `Ctrl-C` 中断，不能在执行期间发送其他消息
