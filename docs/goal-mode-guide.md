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
    "max_stuck_recoveries": 3,
    "persist_state": true,
    "auto_resume_prompt": true,
    "progress_judge_mode": "llm",
    "criteria_tracking_enabled": true,
    "stuck_recovery_attempted_paths_enabled": true,
    "failure_lesson_enabled": true,
    "stuck_recovery_ensemble_enabled": false,
    "fine_grained_execution_enabled": false
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
| `consecutive_same_feedback_limit` | `3` | 连续 N 轮判定为"没有实质进展" → 判定为"卡住"，提前终止（判定方式见 `progress_judge_mode`） |
| `same_feedback_similarity_threshold` | `0.9` | `progress_judge_mode="text_similarity"` 时使用：`difflib.SequenceMatcher` 相似度阈值，达到即计入"雷同" |
| `max_stuck_recoveries` | `3` | 判定"卡住"后先压缩历史+提示换思路、再给几次机会（见下方"卡住恢复"），额度耗尽后再卡住才真正终止；设为 `0` 等价于旧行为（一卡住就终止） |
| `judge_show_prompt` | `false` | 打印发给 GoalJudge 的完整输入 prompt（目标、验收标准、主 Agent 产出、上一轮反馈、上一轮验收标准状态），排查判定依据用 |
| `persist_state` | `true` | 是否在每个轮次边界落盘 `goal_state.json`（供异常中断恢复） |
| `auto_resume_prompt` | `true` | 启动 REPL 时若检测到未完成的 goal，是否主动提示 |
| `progress_judge_mode` | `"llm"` | **[改造项一]** 卡住判定方式：`"llm"` → 让 GoalJudge 在结构化输出里额外判断本轮是否有实质进展（`progress` 字段），比纯文本相似度更能识别"表述不同但本质相同"或"表述相似但确有进展"这两类规则算法的误判场景；解析失败/未按新 schema 输出时自动回退到 `"text_similarity"` 规则。设为 `"text_similarity"` 可一键恢复升级前的纯规则行为 |
| `criteria_tracking_enabled` | `true` | **[改造项三]** GoalJudge 每轮额外输出逐条验收标准的通过情况（`checklist`），`GoalRunner` 据此维护 `GoalState.criteria_status` 并回传给下一轮 GoalJudge，减少判定抖动、让反馈更聚焦"还差哪一条" |
| `stuck_recovery_attempted_paths_enabled` | `true` | **[改造项二]** 卡住恢复时，把最近几轮 GoalJudge 给出的失败原因（`progress_reason`）拼成"已验证无效的方向"清单一起注入提示，而不是只给一句通用的"换个角度" |
| `failure_lesson_enabled` | `true` | **[改造项五]** goal 因 `stuck` / `max_rounds_exhausted` 终止时，把已尝试路径 + 失败原因整理成一条 lesson 写入 memory，供未来同类目标参考，避免重复踩坑 |
| `stuck_recovery_ensemble_enabled` | `false` | **[改造项四，预留]** 尚未实现调度逻辑，仅占位配置；见 [`goal_mode_stage2_ensemble_and_fine_grained_plan.md`](../next_doc/goal_mode_stage2_ensemble_and_fine_grained_plan.md) |
| `stuck_recovery_candidates` | `3` | 同上，届时并行候选数量 |
| `fine_grained_execution_enabled` | `false` | **[改造项六，预留]** 尚未实现细粒度执行器，仅占位配置；见同上文档 |

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

> 实现说明：这套"卡住检测 + compact 恢复"逻辑现在和
> [TurnJudge 的同名机制](turn-judge-guide.md#卡住恢复和goaljudge一样先-compact-再给一次机会)
> 共用同一份实现——`role_agents/stuck_detector.py::StuckDetector`，详见
> [role-agents-guide.md](role-agents-guide.md#内部实现判官类-agent-的统一构造工厂judge_factorypy)。
> 两边的阈值配置（`consecutive_same_feedback_limit` 等 vs
> `consecutive_same_output_limit` 等）仍然完全独立，互不影响。

### "是否卡住"的判定方式：LLM 语义判断 优先于 文本相似度规则

> 对应 [`next_doc/goal_mode_completion_improvement_plan.md`](../next_doc/goal_mode_completion_improvement_plan.md) 改造项一。

早期版本纯粹靠 `difflib.SequenceMatcher` 比较本轮反馈文本和上一轮反馈文本
像不像，这是规则算法，不理解语义，会有两类误判：

- **假阴性**：agent 每轮换一种说法汇报同一个失败结果（"还是报错"→"问题依旧
  存在"），文本相似度被措辞差异拉低到阈值以下，永远判定不了"卡住"，一路
  空转到 `max_rounds` 耗尽
- **假阳性**：agent 在稳步推进同一类修复（"测试 A 通过，B 仍失败"→"测试 B
  通过，C 仍失败"），反馈文本结构高度相似，会被误判为卡住，触发不必要的
  compact

`progress_judge_mode="llm"`（默认）时，GoalJudge 在同一次结构化输出里（不
新增调用）额外给出一个 `progress` 判断：

| `progress` 取值 | 含义 |
|------------------|------|
| `SUBSTANTIVE_ADVANCE` | 相比上一轮有实质推进（哪怕验收标准仍未全部通过），重置卡住计数 |
| `SAME_APPROACH_NO_GAIN` | 本轮和上一轮本质是同一个策略/同一个错误，没有新进展，计入"卡住"信号 |
| `REGRESSED` | 本轮反而比上一轮更差（引入新错误、破坏了已通过的标准），计入"卡住"信号 |

GoalJudge 需要重点对比本轮产出与"上一轮反馈"中提到的失败点/错误信息是否
发生了实质变化，而不是单纯看文字像不像——这正是语言模型比规则算法更擅长
判断的地方。GoalJudge 还必须给出 `progress_reason`（具体依据，不能是空话），
既用于终端展示排查判定依据，也用于下面的"已尝试路径清单"。

**解析失败时的自动回退**：如果 GoalJudge 输出不是合法 JSON、或没有按扩展
schema 输出 `progress` 字段（比如用了尚未升级的自定义 profile），
`GoalRunner._check_stuck` 会自动回退到原有的 `difflib` 文本相似度规则，
不会因为字段缺失而报错或影响判定，鲁棒性与升级前完全一致。

设置 `progress_judge_mode: "text_similarity"` 可以完全恢复升级前的规则
判定行为（不再要求/解析 `progress` 字段）。

### 卡住恢复提示携带"已尝试路径清单"

> 对应改造计划文档改造项二。

`stuck_recovery_attempted_paths_enabled=true`（默认）时，触发卡住恢复注入
的提示不再只是一句通用的"换个角度"，而是会把最近几轮 GoalJudge 给出的
`progress_reason`（限 `progress` 为 `SAME_APPROACH_NO_GAIN` / `REGRESSED`
的轮次）拼成一份"已验证无效的方向"清单一起给主 Agent：

```
以下是最近几轮已经验证过、没有取得实质进展的方向，请不要重复：
1.（第 4 轮）尝试直接改配置文件，但报错信息没有变化
2.（第 5 轮）加了重试逻辑，仍然是同一个断言失败
3.（第 6 轮）换了另一个 API 调用方式，问题依旧存在
请基于以上信息，明确选择一个不同于以上的新方向，并说明为什么这次会不同，
而不是换个说法继续同一个思路。
```

这样"换思路"才有具体依据，避免主 Agent 只是换个说法继续同一个思路。
`progress_judge_mode="text_similarity"` 时没有 `progress_reason` 可用，
这条提示会退化为原来的通用话术。

---

## 验收标准逐条状态追踪

> 对应改造计划文档改造项三，`criteria_tracking_enabled` 控制（默认开启，
> 依赖 `progress_judge_mode="llm"` 的同一次扩展输出）。

早期版本 GoalJudge 每轮都要对全部验收标准重新整体核查，容易因为表述差异
导致同一条标准在不同轮次判定结果抖动（这一轮说标准1过了，下一轮换个说法
又说没过），而且每轮都要重新论证已经通过的条目，浪费上下文。

现在 GoalJudge 每轮额外输出一个 `checklist` 字段，逐条给出
`{"index": 序号, "passed": true/false, "evidence": "依据"}`。`GoalRunner`
把这些状态维护在 `GoalState.criteria_status` 里，并在下一轮把"上一轮各条
标准的通过情况"回传给 GoalJudge，同时约束它：**除非本轮有明确的相反证据
（比如新改动破坏了它），否则已经标记通过的条目不应无理由回退**。

这带来两个直接收益：

- 判定更稳定，不会因为措辞差异来回抖动
- `CONTINUE` 时的反馈可以更聚焦"还差哪一条"，不必每轮重复已经通过的部分

`criteria_status` 也会落盘（见下方"异常中断恢复"一节的 `GoalState` 示例），
跨进程重启后依然保留。

---

## 失败经验沉淀

> 对应改造计划文档改造项五，`failure_lesson_enabled` 控制（默认开启）。

goal 因 `stuck` 或 `max_rounds_exhausted` 终止时，此前的行为是"如实汇报"，
这次执行积累的所有试错经验就留在这一次 session 里，下次面对同一个 workdir
的类似目标，很可能会重新踩一遍同样的坑。

现在终止时，`GoalRunner` 会把：

- 最近几轮里 `progress` 为 `SAME_APPROACH_NO_GAIN` / `REGRESSED` 的
  `progress_reason`（即"已验证无效的方向"）
- 最终仍未通过的验收标准列表（来自 `criteria_status`）

整理成一条 `entry_type="lesson"`（`source="goal_mode_failure"`）写入主
Agent 的 memory（`agent._memory`），复用既有的 lesson memory 基础设施
（半衰期、检索等，见 [记忆与自我进化参考文档](memory-and-self-evolution-complete-reference.md)）。
未来 GoalSpecBuilder 生成新目标草案、或主 Agent 处理同一个 workdir 的类似
任务时，语义检索有机会命中这条 lesson，提前意识到"这个方向之前试过没成功"。

写入失败（比如 memory 后端未启用）不影响 goal 本身已经完成的终止流程，
只是静默跳过，并在终端打印一条不影响主流程的警告。

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

### 1.5 从当前对话历史自动生成目标

如果你已经和 Agent 聊了一阵、做了一些事情，不想再重新用一句话复述一遍目标，
可以直接：

```
/goal from-history
```

```
[Goal 模式] 正在根据当前 session 历史归纳目标…

── /goal from-history 发送给 GoalSpecBuilder 的输入（history_transcript 1234 字符，
has_compact_summary=True，truncated=False） ──
以下是当前会话到目前为止的对话记录（可能只截取了最近的部分）：
...
```

出于方便排查问题的目的（比如怀疑归纳结果不对，想确认到底是提取阶段丢了信息，
还是 LLM 判断有误），`/goal from-history` 每次都会先把实际发送给 GoalSpecBuilder
的完整输入（提取出的历史摘录 + 拼接的提示词）打印出来，再发起生成；如果触发了
JSON 解析失败重试，也会把重试用的输入单独打印一遍。这段调试输出目前没有开关，
默认一直打印——如果觉得干扰，后续可以按需加一个 `verbose`/`debug` 开关来控制。

框架会读取当前 session 的历史对话（user/assistant 轮次，跳过纯工具调用/结果），
自动归纳出"你现在实际在做的任务"，再按和 `/goal <文本>` 完全相同的方式生成
验收标准草案、展示、进入 `/confirm` / 修改意见 / `/cancel` 协商循环——协商和
执行阶段的行为没有任何区别，唯一的区别只是草案的**来源**。

```
[Goal 模式] 正在根据当前 session 历史归纳目标…

目标（第 1 版）：修复多 session 场景下 Client B 消息被静默丢弃的问题
验收标准：
  1. 每个 session 的 AgentBridge 实例都正确绑定了 asyncio 事件循环
  2. 多 session 并发场景下手动验证 Client B 能正常收到回复
验证方式：manual_review

输入 /confirm 确认并开始执行，输入修改意见继续调整草案，输入 /cancel 放弃。
```

一些边界情况：

- **当前 session 还没有任何历史**：直接报错提示，让你改用 `/goal <目标文本>`，
  不会浪费一次 LLM 调用。
- **历史里没有能归纳出的明确任务**（比如全是闲聊，或者刚打开还没展开，或摘要
  明确写着"所有任务已完成、没有待办"）：LLM 会被要求把 `goal_text` 留空，
  命令会据此报错并提示改用手动输入，而不是编造一个目标。
- **摘要里的 Pending / Next Steps 列出了多个平行的候选下一步，用户还没选定
  哪个**（比如一次调研/咨询类对话结束后，等着用户在几个方向里挑一个）：这种
  情况**不会**被当成"没有目标"直接留空——LLM 会挑其中最具体可执行的一项生成
  草稿目标，并在 goal_text 里注明"这只是候选项之一"。因为 `/goal from-history`
  生成的只是第一版草案，接下来还有 `/confirm` / 修改意见 / `/cancel` 的协商
  环节兜底，猜一个能直接改的草稿，比直接返回空、逼你把整段总结重新手打一遍
  更有用；如果猜的候选不是你想要的，直接在协商环节打字说明想执行哪一个即可。
- **LLM 输出解析失败**（比如输出格式跑偏、没有返回合法 JSON——历史被
  `/compact` 压缩过之后结构比较特殊，更容易触发这种情况）：这属于生成失败，
  不是"没有目标"，会自动带纠正提示重试一次；仍然失败会明确提示"生成目标
  草案失败，请重试或改用 /goal <文本>"，不会和"确实没有任务"混为一谈。
- **历史很长**：只会取最近的一部分消息（默认最近 40 条纯文本 user/assistant
  消息，且整体拼接后不超过约 6000 字符），超出部分会被丢弃，并在发给 LLM 的
  提示词里附加"这只是部分历史"的说明，避免模型误以为看到了完整上下文。如果
  归纳结果不准确（比如漏掉了早期的关键背景），直接在协商环节用修改意见补充
  即可，或者改用 `/goal <文本>` 手动描述。
- **先 `/compact` 再 `/goal from-history`**：这是专门优化过的场景。`/compact`
  之后历史会变成 `[session_resume 占位符, 结构化摘要, skill 上下文]`，其中
  占位符文本 `[Previous session summary]` 本身没有信息量，会被自动跳过；
  真正有价值的是 `/compact` 生成的结构化摘要（包含 Goal / Work Completed /
  Current State / Pending 等分节）——会被特别标注为「历史摘要（/compact 生成）」
  并优先依据其中的 Current State（当前进展）和 Pending / Next Steps（待办
  事项）来归纳目标，同时字符预算也会自动放宽一倍，避免这类信息密度更高的
  摘要被过早截断。

### 2. 执行

确认后自动进入 GoalRunner 循环，过程中会完整打印每轮 GoalJudge 的核查内容
（不只是一行 DONE/CONTINUE 状态）和每次 compact 的真实摘要文本：

```
[GoalRunner] 第 1/20 轮执行中…

[🎯 目标核查 · goal_judge]

请检查 test_foo.py 中的 xxx 用例，报错信息显示是 yyy 导致的空指针。

目标状态：🔄 尚未达成，需继续尝试

[GoalRunner] 第 2/20 轮执行中…

[GoalRunner] 正在压缩历史…

— Compact 摘要 —
（LLM 生成的对话摘要正文）

[GoalRunner] compact 完成（第 1 次）。

[🎯 目标核查 · goal_judge]
...
目标状态：✅ 目标已达成

[GoalRunner] 目标已达成（共 2 轮）。

Goal 执行结果： done
轮次：2  compact 次数：1
（GoalJudge 判定为 DONE 时的 feedback 文本）
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

每一轮 `run_turn` 结束后，GoalJudge 对照验收标准清单逐条核查，输出一个 JSON
对象（详见 [role-agents-guide.md](role-agents-guide.md#内部实现判官类-agent的结构化输出verdictpy)）：

```json
{"status": "DONE", "feedback": "验收核查：pytest 全部通过 —— 通过；lint 无报错 —— 通过。结论：目标已达成。"}
```

`status` 只能是 `DONE` / `CONTINUE` / `NEED_COMPACT` 三者之一；`feedback` 是
人类可读的核查依据/理由，CONTINUE 时约定在末尾给出具体、可执行的下一步指令
（而不是"请继续完善"这种空话）。

GoalJudge 的输出通过两层机制确保不会被误认成主 Agent 在说话：

1. **专属显示名**：GoalJudge / GoalSpecBuilder 内部都是独立的 `Agent` 实例，
   各自设置了专属的 `cfg.agent_name`（`🎯 GoalJudge` / `📋 GoalSpecBuilder`），
   而不是沿用主 Agent 的 `cfg.agent_name`。这样它们各自调用模型时，终端里
   打印的前缀（`print_assistant_prefix`）就是 `🎯 GoalJudge ❯ ...`，一眼能
   看出这是评估者/协商助手在说话，不会和主 Agent 的输出混在一起。
2. **结构化展示块**：GoalRunner 每轮结束后额外打印一份 `format_feedback()`
   格式化过的核查结果，带 `[🎯 目标核查 · goal_judge]` 标题——展示/注入
   主 Agent 历史时用的是解析出的干净 `feedback` 字段内容，不是原始 JSON
   字符串。

判定结果通过 `role_agents/verdict.parse_judge_verdict()` 解析（`feedback.
extract_goal_status()` 仍然可用，但已标记 deprecated，内部委托给
`parse_judge_verdict`，仅作为过渡期兼容），解析失败（非 JSON / 缺 `status`
字段 / `status` 不在白名单）时**保守按 CONTINUE 处理**（绝不会因为解析
异常被误判为 DONE）。

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

### 接入方式：经由 RoleAgentDispatcher 统一注册（阶段六）

[判官接线统一 阶段六] GoalJudge 不再由 `GoalRunner` 现场拼一个临时
`AgentProfile` 直接调用，而是作为一个内建判官 profile（`trigger_on:
goal_review`）注册进 [`RoleAgentDispatcher`](role-agents-guide.md#内建判官如何接入-dispatcher goal_review--turn_end_review)。
这意味着：

- **`role_agent.block: ["goal_judge"]`** 可以屏蔽 GoalJudge——但这是一个
  自相矛盾的配置（开了 `goal_mode.enabled` 却拉黑唯一的验收判官），
  `GoalRunner` 会在构造时直接报错拒绝启动，而不是静默降级。
- **`.agent/agents/goal_judge.md`** 若存在，会覆盖内建的 GoalJudge
  profile（磁盘优先），可以自定义 `model`/`system_prompt`，不受
  `prompts/system/goal_judge.md` 默认模板限制。
- **不需要额外打开 `role_agent.enabled=true`**：只要
  `cfg.goal_mode.enabled=true`，dispatcher 就会构造并注册内建
  GoalJudge，行为与升级前完全一致，不需要用户改动任何现有配置。

`judge_model`/`judge_provider`/`judge_tools_enabled` 等本节描述的所有
子配置字段含义不变，本次改造只统一了"谁来注册、谁来触发"这一层。

---

## 安全阀

防止真的死循环烧 token，多重兜底：

| 安全阀 | 触发条件 | 行为 |
|--------|----------|------|
| `max_rounds` | 外层循环轮次达到上限 | 终止，状态 `max_rounds_exhausted`，汇报最后一轮反馈 |
| `max_total_compacts` | compact 次数达到上限 | 终止，避免"跑几轮就 compact 一次"的压缩风暴 |
| 连续判定无实质进展 | 连续 N 轮被判定为"没有实质进展"（`progress_judge_mode="llm"` 时由 GoalJudge 语义判断；`"text_similarity"` 时按反馈文本相似度 ≥ 阈值） | 先花一次"卡住恢复额度"（`max_stuck_recoveries`）压缩历史+提示换思路（含已尝试路径清单），继续跑；额度耗尽后再次卡住才终止，状态 `stuck`，汇报"卡在同一个问题上"，并沉淀为 lesson（见上方"卡住恢复"/"失败经验沉淀"两节） |
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
  "consecutive_same_feedback": 1,
  "criteria_status": [
    {"index": 1, "text": "...", "passed": true, "evidence": "pytest 全部通过", "last_updated_round": 5},
    {"index": 2, "text": "...", "passed": false, "evidence": "lint 仍有 3 处报错", "last_updated_round": 7}
  ],
  "recent_progress_reasons": [
    {"round": 6, "progress": "SAME_APPROACH_NO_GAIN", "reason": "..."},
    {"round": 7, "progress": "SUBSTANTIVE_ADVANCE", "reason": "..."}
  ]
}
```

`criteria_status` / `recent_progress_reasons` 分别对应改造项三/一二，仅在
对应功能开启且 GoalJudge 成功输出扩展字段时才会被填充，功能关闭或解析失败
时保持空列表，不影响其余状态的落盘/恢复。

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

> 这一项和"卡住恢复从单路径重来升级为并行多路径择优"（对应
> `stuck_recovery_ensemble_enabled` / `stuck_recovery_candidates` 配置）
> 是改造计划里明确留到本次改造之后再评估的两项——工作量和风险都明显更高，
> 需要先观察改造项一~三/五上线后的真实触发频率再决定优先级。具体的后续
> 推进计划见 [`next_doc/goal_mode_stage2_ensemble_and_fine_grained_plan.md`](../next_doc/goal_mode_stage2_ensemble_and_fine_grained_plan.md)。

---

## 文件位置一览

| 文件 | 职责 |
|------|------|
| `goal_mode/spec.py` | `GoalSpec` 数据结构 + `GoalSpecBuilder`（自然语言→结构化验收标准，多轮修订；`build_from_history()` 从当前 session 历史归纳目标） |
| `goal_mode/executor.py` | `GoalStepExecutor` 接口 + `CoarseStepExecutor` |
| `goal_mode/state.py` | `GoalState` + `GoalStateStore`（原子落盘/恢复）+ `find_resumable_session` / `list_resumable_sessions`（全量列出，供 `/goal list`）/ `scan_goal_states`（诊断用） |
| `goal_mode/runner.py` | `GoalRunner` 外层驱动循环 |
| `role_agents/goal_judge.py` | GoalJudge：`build_goal_judge_prompt` / `run_goal_judge` |
| `role_agents/feedback.py` | `extract_goal_status()`，`RoleFeedback.goal_status` 字段 |
| `cli/commands/goal_mode_cmd.py` | `/goal` 系列 slash 命令（含 `/goal from-history`） |
| `config/models.py::GoalModeConfig` | 配置模型 |
| `storage/paths.py::session_goal_state()` | `goal_state.json` 路径 |
| `prompts/system/goal_judge.md` | GoalJudge 的 system prompt |
| `prompts/system/goal_spec_builder.md` | GoalSpecBuilder 的 system prompt |
| `prompts/user/goal_judge_request.md` | GoalJudge 每轮核查的 user 消息模板 |
| `prompts/user/goal_spec_initial_request.md` | GoalSpecBuilder 首次生成验收标准的 user 消息模板 |
| `prompts/user/goal_spec_revise_request.md` | GoalSpecBuilder 修订验收标准的 user 消息模板 |
| `prompts/user/goal_context.md` | 目标+验收标准"钉住"消息的模板 |
| `prompts/fragments/goal_mode.md` | `PRIOR_FEEDBACK_BLOCK`、`GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS`（改造项一/三）、`PRIOR_CHECKLIST_BLOCK`（改造项三）、`STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK`（改造项二）等细粒度文本片段 |

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
