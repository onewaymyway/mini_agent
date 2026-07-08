# 轮次守门员指南（Turn Judge）

一个可选开关：每轮对话结束、真正把控制权交还给真人用户之前，先让一个轻量的
判定 Agent（TurnJudgeAgent）核查一次——这到底是主 Agent **真的完成了当前请求，
需要人类给出新指示**，还是主 Agent 其实**遇到了纯技术性问题**（模型输出格式
有问题、撞到 `max_turns` 硬顶需要 compact、上下文明显混乱需要先压缩等），
本不该打扰真人，应该由系统自动代替用户给出反馈，让主 Agent 继续处理。

只有 TurnJudge 判定为"确实需要真人"时，才会真正进入等待用户输入阶段。

与 [Goal 模式](goal-mode-guide.md) 里的 GoalJudge 的关系：两者是同一套设计
思路在不同触发点上的应用——

| | GoalJudge | TurnJudge |
|---|---|---|
| 触发点 | Goal 模式外层循环，每"一整轮任务尝试"结束时 | **任何一轮** `run_turn()` 结束、即将等待真人输入时 |
| 判断什么 | 对照验收标准清单，目标是否达成 | 这轮结束是"正常完成"还是"技术性卡壳" |
| 依赖 | 需要先 `/goal` 设定目标（GoalSpec） | 不依赖 Goal 模式，普通对话也能用 |
| 输出 | `GOAL_STATUS: DONE / CONTINUE / NEED_COMPACT` | `TURN_STATUS: NEED_USER / AUTO_CONTINUE / NEED_COMPACT` |

两者可以同时开启，互不冲突：Goal 模式管"任务级别"的达成判定，TurnJudge 管
"每一轮"是否需要人工介入。

---

## 设计思路

```
run_turn() 内部：
  ... _agentic_loop() 产出 result ...
        │
        ▼
  [SYS-HOOKS] TurnEnd hook（若配置了，且返回了替代输入）
        │ 未接管（无 hook / hook 未返回替代输入）
        ▼
  turn_judge.enabled ?
        │ 是
        ▼
  TurnJudgeAgent 核查（纯文本判定，不挂工具，零风险、低延迟）
        │
        ├─ NEED_USER      → 计数清零，正常等待真人输入
        ├─ AUTO_CONTINUE   → 提取具体反馈，自动作为"下一轮用户输入"注入，
        │                    复用现有 TurnEnd 注入机制继续跑
        └─ NEED_COMPACT    → 自动 compact_with_skills()，再注入续跑提示
        │
        ▼
  连续自动接管次数达到 max_auto_rounds → 强制交还真人（防止死循环）
```

**为什么不直接复用 TurnEnd hook？** TurnEnd hook 是通用的"外部脚本/另一个
进程决定下一步输入"的机制（见 [hooks.md](hooks.md)），适合队列消费、脚本化
测试等场景，但它本身不判断"是否该交还真人"——这件事需要理解本轮对话的
语义（格式错误 vs 正常结束 vs 需要人决策），单靠字符串规则很容易误判，
所以交给一个轻量 LLM 判定 Agent 来做。两者优先级上 TurnEnd hook 更高：
只有 hook 没有接管时才会触发 TurnJudge，避免和你自定义的 hook 冲突。

**保守原则（和 GoalJudge 一致）：**
- 判定/执行过程中任何异常都保守回退为 `NEED_USER`，绝不能让异常被当成
  `AUTO_CONTINUE`（自动接管出错的代价远比多打扰用户一次严重）
- 涉及主观决策的场景（"几个方案选哪个""是否要执行有风险的操作"）一律
  判 `NEED_USER`，TurnJudge 绝不会替用户做决定
- `TURN_STATUS` 解析失败时按 `NEED_USER` 处理

---

## 卡住恢复：和 GoalJudge 一样，先 compact 再给一次机会

`max_auto_rounds` 只是"总次数"上限，不区分这几轮自动接管到底有没有实质
进展——凑够次数就强制交还真人，哪怕其实一直在正常推进任务，只是任务本身
需要的轮次多一点。反过来，如果主 Agent 连续几轮给出**高度相似**的输出
（反复卡在同一个报错、同一种格式问题上），说明历史里堆积的信息可能已经
干扰了它的判断，这时候更好的做法是先压缩一次历史、提示它换个角度重新
尝试，而不是干等到 `max_auto_rounds` 耗尽才把烂摊子丢给真人。

这就是和 [Goal 模式](goal-mode-guide.md) 里 `max_stuck_recoveries` 完全
同一套思路的机制，只是触发对象从"GoalJudge 反馈文本"换成了"主 Agent 自己
的输出"：

1. 每轮 `_maybe_run_turn_judge()` 一开始（在真正调用 TurnJudge LLM 判定
   之前）就用 `difflib.SequenceMatcher` 比较本轮输出和上一轮输出的相似度
2. 连续 `consecutive_same_output_limit` 轮相似度都达到
   `same_output_similarity_threshold` → 判定"卡住"
3. 判定"卡住"后不直接强制交还真人，而是：
   - 执行一次 `compact_with_skills()`
   - 注入一条"你连续几轮输出高度相似，疑似卡住，请换个角度重新尝试"的
     提示，作为下一轮的用户输入
   - **这一轮不占用 `max_auto_rounds` 预算**，也不会真的调用 TurnJudge LLM
     （省了一次 API 调用）
4. 这样的"卡住恢复"最多连续尝试 `max_stuck_recoveries` 次；额度耗尽后
   再次判定卡住，才真正强制交还真人（等价于撞到 `max_auto_rounds`）
5. 一旦某一轮输出明显不同于上一轮（判定为"真实进展"），卡住计数和恢复
   额度都会被重置——额度是"每次卡住独立计算"，不是整个会话期间总共只能
   用一次

设置 `consecutive_same_output_limit: 0` 可以关闭这个机制，回到"只看
`max_auto_rounds`"的旧行为。

---

## 启用方式

`agent_config.json` 中新增 `turn_judge` 配置块（默认 `enabled: false`，
不影响任何现有行为）：

```json
{
  "turn_judge": {
    "enabled": true,
    "judge_model": null,
    "judge_provider": null,
    "max_auto_rounds": 3,
    "judge_show_prompt": false,
    "history_window": 6,
    "consecutive_same_output_limit": 3,
    "same_output_similarity_threshold": 0.9,
    "max_stuck_recoveries": 3
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 总开关，关闭时行为与现有版本完全一致 |
| `judge_model` / `judge_provider` | `null` | TurnJudge 用的模型，`null` = 复用主 `cfg.model`（建议配一个更便宜/更快的模型，因为这是高频触发点） |
| `max_auto_rounds` | `3` | 连续自动接管次数上限，达到后无论判定结果如何都强制交还真人，防止死循环刷屏；每次真正判定为 `NEED_USER` 或达到上限后计数会清零 |
| `judge_show_prompt` | `false` | 打印发给 TurnJudge 的完整输入 prompt（本轮产出 + 最近历史），排查判定依据用 |
| `history_window` | `6` | 供 TurnJudge 参考的最近历史消息条数 |
| `consecutive_same_output_limit` | `3` | 连续 N 轮主 Agent 的输出高度雷同 → 判定"卡住"（没有实质进展），见下方"卡住恢复"一节 |
| `same_output_similarity_threshold` | `0.9` | `difflib.SequenceMatcher` 相似度阈值，达到即计入"雷同" |
| `max_stuck_recoveries` | `3` | 判定"卡住"后先压缩历史+提示换思路，最多连续尝试几次（不占用 `max_auto_rounds` 预算），额度耗尽后再卡住才强制交还真人；设为 `0` 关闭这个机制 |

子 Agent（sub-agent / role agent 内部跑的 Agent 实例）永远不会触发 TurnJudge，
避免嵌套判定。

> **实现细节 / 已修复的坑：** TurnJudge 自身、以及 GoalJudge / EvaluatorAgent /
> CoachAgent / 自定义角色 Agent / GoalSpecBuilder 这些"内部 Agent"都是通过
> `load_config()` 重新从同一份 `agent_config.json` 加载配置构建的——如果不做
> 特殊处理，它们的 cfg 里 `turn_judge.enabled` 也会是 `True`，导致这些内部
> Agent 在跑自己的 `run_turn()` 时又对自己触发一次 TurnJudge 核查，引发无限
> 递归自我核查（表现为终端一直卡在反复打印"🧭 TurnJudge ❯"，永远不把控制权
> 交还真人）。所有这些内部 Agent 构造点都已经显式禁用了 `cfg.turn_judge` 并
> 标记 `is_subagent=True` 双重兜底，用户无需关心这个细节，但如果你在扩展
> 框架时新增了类似的"内部临时 Agent"，请照此模式处理。

---

## 使用体验

开启后，普通对话不会有任何变化——只有当一轮结束、即将等你输入时，才会看到
类似下面的自动核查过程（打印风格与 GoalJudge 一致）：

```
ℹ  [TurnJudge] 正在核查本轮是否需要真人介入…（第 1/3 次自动核查）

[🧭 轮次核查 · turn_judge]

**核查**
本轮助手的输出中包含未闭合的 <tool_use> 标签，JSON 也被截断，说明它本想调用
bash 工具但格式有误，工具没有被执行，回复戛然而止，不是任务真正完成。

**结论**
这是纯技术性的格式问题，不应该由用户来处理。

**反馈**
请重新输出一次工具调用，注意 JSON 必须完整闭合，<tool_use> 标签需要正确闭合。

TURN_STATUS: AUTO_CONTINUE

轮次状态：🤖 自动接管，代替用户继续推进

ℹ  [TurnJudge] 判定为 AUTO_CONTINUE，自动代替用户输入继续推进（第 1 次）。

You ❯ [TurnJudge 自动接管] 检测到技术性问题（而非任务真正完成），以下是系统
代替用户给出的下一步指令：

请重新输出一次工具调用，注意 JSON 必须完整闭合，<tool_use> 标签需要正确闭合。

[主 Agent 开始新一轮，正常打印回复...]
```

若判定为 `NEED_USER`（正常完成 / 需要人决策 / 无异常迹象），则不会有任何
额外提示，直接进入你熟悉的输入提示符，和关闭该开关时体验完全一致。

若连续自动接管次数达到 `max_auto_rounds`，会打印一条提示后强制交还真人：

```
ℹ  [TurnJudge] 已连续自动接管 3 次，达到上限（3），强制交还真人用户输入。
```

TurnJudge 每次判定都会作为一条 `role_agent` 类型的消息写入历史（与
GoalJudge 反馈的注入方式一致），保留可审计的判定痕迹，不影响后续对话的
连贯性。

---

## 运行时开关：`/turnjudge` 命令

除了在 `agent_config.json` 里配置默认开关，也可以在 REPL 会话中随时用
`/turnjudge` 命令开启或关闭，无需重启会话：

```
/turnjudge           不带参数 = toggle（和 /verbose 的交互习惯一致）
/turnjudge on        显式开启
/turnjudge off        显式关闭
/turnjudge status     只查询当前状态，不修改
```

示例：

```
> /turnjudge on
ℹ  TurnJudge: ON (轮次结束前将自动核查是否需要真人介入)

> /turnjudge status
ℹ  [TurnJudge] 当前状态：ON（max_auto_rounds=3，judge_model=(复用主模型)）

> /turnjudge off
ℹ  TurnJudge: OFF (轮次结束将直接等待真人输入，不做自动核查)
```

关闭时会同时清零"连续自动接管计数"，避免残留计数影响下次重新开启后的判断。
这个开关只影响运行时内存里的 `cfg.turn_judge.enabled`，不会回写
`agent_config.json`——如果希望下次启动默认就是开启/关闭状态，请直接修改
配置文件。

---

## 什么情况下建议开启

- 你在跑长时间无人值守的任务（比如配合 Goal 模式、cron 定时任务、daemon
  模式），希望模型偶发的格式错误 / 撞到 max_turns 不需要人工介入就能自愈
- 你发现模型有时会"半途而废"（输出到一半的工具调用格式错误就不再重试），
  希望系统自动帮它纠正格式再继续，而不是把半成品直接展示给你

## 什么情况下不建议开启（或需要谨慎调低 max_auto_rounds）

- 你希望每一轮都亲自确认再继续（比如高风险操作场景）——TurnJudge 已经对
  主观决策场景做了保守处理，但如果你想要**绝对**不被自动接管，请保持
  `enabled: false`
- 你的 `judge_model` 选用了较贵的模型——TurnJudge 是高频触发点，每轮对话
  结束都可能跑一次，建议配置更便宜/更快的模型
