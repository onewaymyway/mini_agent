# 具身智能改进指南（Embodied Agent）

> 对应 `next_doc/embodied_agent_design.md`（设计依据）与
> `next_doc/embodied_agent_improvement_plan_v3.md`（改进计划，含逐项实现取舍
> 说明）。本指南是面向使用者/开发者的功能说明，改进计划文档保留给未来
> 迭代时核对"原计划 vs 实际实现"的差异。

---

## 1. 这组改进解决什么问题

传统 Agent 循环是"感知-决策-执行"的单向管道：读取用户输入 → 决定调用什么
工具 → 执行 → 把结果原样塞回上下文。Agent 对自己"现在处于什么状态"没有
显式建模——不知道自己是不是在反复失败、不知道当前工作目录哪些方向还有
未完成的线索、不知道自己的工具或经验是不是已经过时、被打断后也不会主动
留下"当时在想什么"的痕迹。

这一组改进（A/B/C 三个优先级阶段 + 阶段 D 收尾）借用了具身认知
（embodied cognition）里几个朴素类比——本体感知（proprioception）、
余裕感知（affordance）、工具透明性（tool transparency）、自创生
（autopoiesis）——把它们落地成十一个具体、可独立开关的模块。**全部改进
均已实现**，状态总览：

| 项目 | 模块 | 优先级 | 状态 |
|------|------|-------|------|
| A1. Connected REPL 完整命令对等 | `cli/daemon.py` | P1 | ✅ |
| A2. Lesson source 区分（human_feedback） | `perception/correction_detector.py` | P1 | ✅ |
| A3. Reminder pre_tool 触发时机 | `reminders/manager.py`/`matcher.py` | P1 | ✅ |
| B1. 本体感知模块（ProprioceptionModule） | `perception/proprioception.py` | P2 | ✅ |
| B2. Lesson → Reminder 自动闭环 | `evolution/lesson_to_reminder.py` | P2 | ✅ |
| B3. Workflow 并发执行 | `workflow/runner.py` | P2 | ✅ |
| B4. 余裕感知层（AffordanceMap） | `perception/affordance_analyzer.py` | P2 | ✅ |
| 工具透明性（IntentActionMapper） | `perception/intent_action_mapper.py` | P2 | ✅ |
| C1. AgentSelfModel | `perception/self_model.py` | P3 | ✅ |
| C2. 时间加权记忆激活 | `evolution/memory_aging.py` | P3 | ✅ |
| C3. 认知锚点文件 | `agent.py` + `storage/paths.py` | P3 | ✅ |
| C4. 自维护模块（SelfMaintenanceModule） | `evolution/self_maintenance.py` | P3 | ✅ |

---

## 2. A1. Connected REPL 完整命令对等

**问题**：daemon 模式下 CLI 以"连接模式"接入（见 [Stage 9 指南](self-evolution-stage9-guide.md)），
但早期实现里 `DaemonClient` 只透传聊天消息，本地模式下能用的 `/skills`、
`/memory`、`/evolve` 等 slash 命令在连接模式下不可用。

**实现**：`cli/daemon.py::DaemonClient` 扩展命令分发，把连接模式下输入的
slash 命令路由到对应的 HTTP API 端点（而不是原样当作聊天消息发给 agent），
达到"连接模式与本地模式命令对等"。不依赖新协议，全部复用现有 HTTP API。

---

## 3. A2. Lesson source 区分（human_feedback）

**问题**：`MemoryEntry`（lesson 型条目）记录了 `source` 字段，但排查后发现
这其实是历史遗留能力——早就存在，只是没有被后续模块（提醒生成、时间
衰减）真正利用起来区分对待。

**实现**：`perception/correction_detector.py` 检测用户消息中的直接纠正
短语（"不对"、"应该用 xxx"、"下次记得..."等），立即生成
`entry_type="lesson"`、`source="human_feedback"` 的条目——区别于
`self_reflection`（SessionEnd LLM 反思生成）、`experiment_confirmed`
（ExplorationSandbox 验证通过）、`revert_record`（`/evolution revert` 自动
生成）。四种 source 目前被 C2（时间加权记忆激活）和 B2
（Lesson → Reminder 自动闭环）真正利用：human_feedback 衰减最慢、激活
所需样本量最低。

---

## 4. A3. Reminder pre_tool 触发时机（前馈控制）

**问题**：原有 Reminder 系统只在工具出错后（`tool_error`）或工具调用后
（`post_tool`）触发，属于"事后补救"；具身认知里前馈控制
（feedforward control）的价值在于"在动作发生前，根据已知的危险模式提前
提醒"，而不是等出错了再提醒。

**实现**：`reminders/loader.py` 新增 `TRIGGER_PRE_TOOL = "pre_tool"` 触发
类型，`reminders/manager.py::check_pre_tool()` 在工具真正执行前调用
`matcher.py::match_pre_tool()` 做匹配，命中则在工具调用前注入提醒（例如
"上次用 `bash rm -rf` 忘了先确认路径，这次执行前建议先 `ls` 确认"）。

---

## 5. B1. 本体感知模块（ProprioceptionModule）

**模块**：`perception/proprioception.py`（配置：`ProprioceptionConfig`，
`cfg.proprioception`，默认 `enabled=True`）

Agent 对自身状态的轮间快照——不调用 LLM，是 O(1) 纯计算：

- **认知负荷**（context 占用比例）
- **不确定性**（估算，基于回复中的迟疑措辞密度）
- **风险感知**（基于本轮工具名的敏感度）
- **剩余预算**（`max_turns` 消耗比例）
- **挫败感（frustration）**：连续工具调用失败会累积，成功一次会快速衰减

当 `frustration` 超过 `frustration_threshold`（默认 0.5）且连续失败次数
达到 `consecutive_failure_threshold`（默认 3）时，向模型注入一条元认知
提示——建议它停下来向用户汇报困境，而不是盲目重试同一种方法。

每轮快照可选写入 `traces.jsonl`（`trace_enabled`，默认开启），供后续
Phase G 分析趋势；C1（AgentSelfModel）也读取最新一次快照作为"此刻内部
感受"维度。

**→ Stage 9 信号桥接**：`ProprioceptionModule` 是每个 Agent 实例内存中的
状态，而 `evolution/resource_arbiter.py::ResourceArbiter` 跑在 daemon 后台
tick 里、不持有活跃 Agent 引用，之前两条链路是彻底断开的——一个正在反复
受挫的 Agent 会同时还在后台跑高置信度要求的自主探索。现在 `frustration`
有意义变化时会落盘到 `AgentPaths.proprioception_snapshot`
（`.agent/proprioception_snapshot.json`，单文件覆盖写），`ResourceArbiter.
can_run_autonomous()` 的规则 4 读取该快照，`frustration` 达到阈值时本次
tick 跳过自主任务提交；快照缺失或超过 10 分钟未更新视为无有效信号，不
阻塞。详见 [Stage 9 自主运行时指南](self-evolution-stage9-guide.md#8-资源仲裁evolutionresource_arbiterpy)。

测试：`tests/test_proprioception.py`

---

## 6. B2. Lesson → Reminder 自动闭环

**模块**：`evolution/lesson_to_reminder.py`

**问题**：Lesson Memory（SessionEnd 反思 / 规则触发 / 人类反馈检测生成的
经验条目）本身只是被动等待检索命中；没有机制把"反复出现的教训"主动
转化为会在恰当时机触发的 Reminder。

**实现**：`LessonToReminderBridge` 按 `trigger` 文本聚类同类 lesson——
`source="human_feedback"` 的经验只需 1 次即可直接激活写入 reminder 目录
（`enabled: true`）；`source="self_reflection"` 等来源需要达到 T1 门槛
（同类 lesson 出现次数）才生成，且先落在 `drafts/` 子目录（`enabled: false`），
需要 `promote_draft()` 手动提升为正式生效。反引号包裹的工具名会被自动
提取为 `condition.tool_name`。命令：`/evolution lessons-to-reminders`。

测试：`tests/test_lesson_to_reminder.py`

---

## 7. B3. Workflow 并发执行（depends_on 拓扑分析）

**模块**：`workflow/runner.py`

**问题**：Workflow 步骤即使互相没有依赖关系，也是严格串行执行的。

**实现**：`WorkflowRunner._compute_parallel_batches()` 对 `depends_on` 做
拓扑排序，把无依赖关系的步骤分到同一批次并发执行，有依赖的步骤放到下一
批次串行等待；`evaluator` 类步骤天然依赖被评估步骤，会落在不同批次，
不会被误并行。

测试：`tests/test_workflow_parallel.py`

---

## 8. B4. 余裕感知层（AffordanceMap）

**模块**：`perception/affordance_analyzer.py`（配置：`AffordanceConfig`，
`cfg.affordance`，默认 `enabled=True`）

**具身来源**：affordance（余裕/行动可能性）——环境不是中性的信息集合，
而是"对当前主体呈现出一组行动可能性"。AffordanceAnalyzer 在 session 开始
时构建一次（不是每轮 turn），交叉分析 `open_threads.json`（未完成线索）、
`capability_map`（Phase G 历史扫描的能力置信度，`use_capability_map`
可关）、lesson memory，生成"当前环境对我意味着哪些行动机会"的简短文本
块，拼进 `system_extra`。纯只读分析，不调用 LLM，不写入任何文件，失败
静默跳过不阻断 session 创建。

**接入点**：`perception/affordance_analyzer.py::inject_affordance_map()`
是唯一实现，daemon 多用户路径（`api/session_pool.py::SessionAgentPool.
_create_entry()`）与本地单 Agent 路径（`cli/app.py`，Agent 构造完成后
立即调用）共用同一份逻辑——此前"本地路径未接入"的已知不对称已修复。

**与用户行为感知层的可选交叉分析**（默认关闭）：`AffordanceConfig.
use_behavior_context` 与 `perception/behavior/` 的总开关 `enabled` 同时为
`True` 时，`inject_affordance_map()` 会额外只读查询最近 30 分钟的
`BehaviorEventStore`，压缩为 `BehaviorContext`（近期被其他终端触碰的
git 路径、应用切换频率、`is_actively_engaged` 等），追加成 1-2 条"用户
近期活动提示"。任一开关为 `False` 时该输入源视为缺失，不影响其余三路
分析；查询失败同样静默降级。详见 [用户行为感知指南](behavior-perception-guide.md)。

`inject_affordance_map()` 同时把这份 `BehaviorContext` 写回
`AgentSelfModel.user_presence`（见下方 C1），供下游程序化读取
`is_user_actively_engaged()`，而不必解析 system prompt 文本片段。

测试：`tests/test_affordance_analyzer.py`

---

## 9. 工具透明性（IntentActionMapper）

**模块**：`perception/intent_action_mapper.py`

**具身来源**：盲人手杖用熟了之后，使用者感知到的是"路面在这里有个坑"，
而不是"手杖碰到了什么"——手杖本身从意识中"消失"，感知对象前移到手杖
末端接触的世界。工具调用同理：`read_file` ×3 + `patch_file` ×2 这类原始
流水账，不如"做了一次代码重构"这种意图层面的总结有用。

**实现**：`IntentActionMapper.group_calls()` 纯规则匹配（不调用 LLM），
按"工具名所属意图类别"做连续游程分组——`exploration`（探索/检索）、
`code_edit`（代码编辑）、`test_run`/`env_setup`/`vcs_op`（`bash` 命令按
内容关键词细分）、`research`（`web_search`）、`other`。接入
`agent.py` 主循环的 `execute_tools` span：分组结果作为 `action_events`
字段写入 `traces.jsonl`，不改变 history 本身，只在可观测性侧补充语义
标注，供 `/diagnostics` 与后续 Phase G 扫描读取。

测试：`tests/test_intent_action_mapper.py`（17 个用例）

---

## 10. C1. AgentSelfModel——三个 Profile 概念的语义澄清与聚合

**模块**：`perception/self_model.py`

**问题**：代码库里有三个命名相近但职责完全不同的"profile"概念——
`UserProfile`（用户跨项目技术栈画像）、`RoleProfileManager`（多用户
角色/信任等级）、`AgentProfile`（SubAgent 角色定义模板）；此外
`global_knowledge.SelfProfile`/`SelfAssessment` 只反映跨 session 的慢
变化历史评估，没有"这一轮我现在感觉如何"的实时维度。

**实现**：不做破坏性重命名，而是新增 `AgentSelfModel` 作为聚合视图，
session 级构建一次（`AgentSelfModelBuilder`），之后每轮 turn 只更新
`internal_state` 这一个快变量：

- **慢变量**（session 级，构建一次）：来自 `SelfAssessment`（跨 session
  历史评估摘要引用，不重复注入全文）+ `capability_map`（当前 workdir
  技术领域置信度）+ `user_presence`（B4 `AffordanceMap` 交叉分析出的
  `BehaviorContext`，"用户当前在场/繁忙"的结构化信号，`use_behavior_context`
  关闭或行为感知未启用时为 `None`——通过 `is_user_actively_engaged()`
  访问，而不是从 system prompt 文本里反解析）
- **快变量**（每轮更新）：来自 ProprioceptionModule 最新 `sense()` 快照
  （B1）+ AffordanceMap（B4，当前 session 的余裕地图）

通过 `ContextBuilder` 新增的 `self_model_getter` callable 注入，与已有的
`profile_text_getter` 同构。

测试：`tests/test_self_model.py`

---

## 11. C2. 时间加权记忆激活

**模块**：`evolution/memory_aging.py`

**问题**：`MemoryStore._score_all()` 原本对所有条目用同一个全局半衰期
（30 天），不区分"被反复印证的旧知识"和"一次性的新猜测"，也不区分
"用户亲口纠正"和"Agent 自我反思猜测"。

**实现取舍**：原计划设想"Phase G tick 时批量预计算 `temporal_weight`
缓存字段"，但核对 `memory_store.py` 后发现时间衰减本来就是按
`entry.age_days`（属性，非缓存字段）在每次 `search()` 时实时计算——没有
"缓存过期"问题，批量预计算反而多一份一致性维护成本。改为新增纯函数
`compute_decay_factor(entry)`，由 `_score_all()` 直接调用替换原有的全局
`self._decay_lambda`：

| lesson source | 半衰期基准 |
|---|---|
| `human_feedback` | 90 天（最慢——用户亲自纠正的价值不因时间快速贬值）|
| `experiment_confirmed` | 60 天 |
| `self_reflection` | 30 天（默认）|
| `revert_record` | 14 天（最快——具体操作被回退的记录，环境变化后很可能不再适用）|

`occurrence_count`（同类经验重复出现次数）每 +1，半衰期额外延长 30%，
封顶 4 倍——被反复印证的知识更"抗遗忘"。非 lesson 条目（summary 等）
沿用构造时传入的全局半衰期配置，行为不变。

测试：`tests/test_memory_aging.py`（10 个用例，含 MemoryStore 端到端
排序验证：相同 age 下 human_feedback lesson 排序应该高于 revert_record）

---

## 12. C3. 认知锚点文件——思维状态重建指南

**模块**：`agent.py::_save_cognitive_anchor()` / `_maybe_load_cognitive_anchor()`
+ `storage/paths.py::AgentPaths.workdir_cognitive_anchor`
（`<project_root>/.agent/cognitive_anchor.md`）

**具身来源**：与自创生（autopoiesis）呼应——生物体被打断后不会丢失
"当时在想什么"，只是需要一点提示就能快速恢复状态。Agent 被 Ctrl-C
打断当前任务时，history 本身已经记录了"做了什么"，但没有记录"当时在
想什么、为什么这么做、下一步的直觉、还有哪些疑问没解决"——这些是恢复
思路时最难从原始 history 重建的部分。

**触发**：

- 本地纯 REPL 模式：用户 Ctrl-C 打断当前任务（`cli/repl.py::run_repl()`
  的 `KeyboardInterrupt` 处理分支），本地进程直接持有 Agent 实例，直接调用
  `agent._save_cognitive_anchor()`。
- daemon-connected 模式：`cli/daemon.py` 的 `DaemonClient` 进程不直接持有
  Agent 实例，Ctrl-C 到不了 Agent 那一层——`DaemonClient.
  save_cognitive_anchor(session_id)` 在客户端自己的 `KeyboardInterrupt`
  处理里 best-effort POST `/v1/sessions/{session_id}/save_anchor`
  （2.5s 短超时，失败/超时静默降级，不影响断开连接本身），服务端
  （`api/routes.py::save_cognitive_anchor()`）按 `session_id` 找到对应
  Agent 后调用同一个 `_save_cognitive_anchor()`。详见
  [HTTP API 指南](http-api-guide.md#stage-9-daemon-模式说明)（`/v1/sessions/{session_id}/save_anchor`）。

基于最近 12 轮 history，用 LLM 生成固定四段式格式的锚点内容
（`prompts/system/cognitive_anchor.md` + `prompts/user/cognitive_anchor_request.md`）：

```markdown
## 当时在想什么
## 为什么这么做
## 下一步的直觉
## 未解决的疑问
```

**恢复**：下次 session 启动时（`agent.py::_init_session()`），若锚点
文件存在则读取并注入 `system_extra`（"上次中断时留下的认知锚点（自动
恢复，仅供参考）"），随后立即归档（重命名为带时间戳后缀的文件），避免
同一份锚点被无限期重复注入到后续每个 session。

**开关**：`AppConfig.cognitive_anchor_enabled`（默认 `True`）。两条触发
路径共用同一个开关和同一个 `_save_cognitive_anchor()` 实现，行为一致。

测试：`tests/test_cognitive_anchor.py`（12 个用例，用 duck-typed fake
object 以未绑定方法方式调用 `Agent._save_cognitive_anchor` /
`_maybe_load_cognitive_anchor`，不构造完整 Agent 实例）

---

## 13. C4. 自维护模块（SelfMaintenanceModule）

**模块**：`evolution/self_maintenance.py`

**具身来源**：自创生（autopoiesis）——生物体不只是被动响应环境扰动，
还主动维持自身边界和内部一致性（细胞修复膜损伤、免疫系统清除异常
细胞）。Agent 原来对自身健康状况是纯被动的：工具失败了才知道工具可能
坏了，skill 内容过时了要等产生错误建议才会被发现，记忆库里出现自相
矛盾的经验也不会被主动揪出来。

**三项检查**（`SelfMaintenanceModule.health_check()`）：

1. **stale_tools**（可能失效的工具）：原计划设想"最近 N 天未被成功调用
   的工具"，但核对后发现不存在跨 session 持久化的"每个工具最后一次成功
   调用时间"。改用扫描最近 20 个 session 的 `traces.jsonl` 里
   `phase="tool_call"` 记录，统计每个工具近期失败率——样本量 ≥3 且失败率
   ≥60% 判定为"可能失效，建议排查 API/参数是否变更"。
2. **stale_skills**（长期未用的 skill）：复用 `phase_g.py::prune_skills()`
   同款 `skill_loader.tracker` 基础设施（角度不同：Phase G 是"高成本 +
   未使用 → 建议剪枝"，这里是"长期未使用 → 可能过时，建议复核"）。
3. **conflicting_lessons**（可能矛盾的经验）：复用
   `lesson_review.py::group_lessons()` 聚类结果，同一聚类内若同时出现
   正面关键词（成功/应该/建议/可以/有效/推荐）和负面关键词（失败/不行/
   不应该/出错/无效/不要/避免）信号，标记"可能矛盾，建议人工判断保留
   哪条"。这是启发式而非精确判断。

**只产出建议，不自动修复**——与其他自主化机制一致的"保留人类控制权"
原则：结果写入 `activity_digest.jsonl`（`type="health_report"`），下次
`/digest` 或连接时的晨报里展示。

**触发方式**：与 Phase G 同款"时间门控"模式（独立状态文件
`self_maintenance_state.json`，默认 24h 间隔）：
- `agent.py::_maybe_run_self_maintenance()`——SessionEnd 时检查
- 内置 cron job `sys:self_maintain`（`evolution/cron_scheduler.py`，
  `interval:86400`），daemon 模式下按计划触发

测试：`tests/test_self_maintenance.py`（22 个用例）

---

## 14. 配置一览

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `cfg.proprioception.enabled` | `True` | B1 本体感知开关 |
| `cfg.proprioception.frustration_threshold` | `0.5` | 触发元认知提示的挫败感阈值 |
| `cfg.proprioception.consecutive_failure_threshold` | `3` | 连续失败次数阈值 |
| `cfg.affordance.enabled` | `True` | B4 余裕感知开关（当前仅多用户路径生效）|
| `cfg.affordance.use_capability_map` | `True` | 是否纳入 Phase G 能力地图数据 |
| `cfg.cognitive_anchor_enabled` | `True` | C3 认知锚点开关（本地 Ctrl-C 与 daemon-connected `/save_anchor` 共用） |
| `evolution/memory_aging.py` 半衰期表 | 见上表 | C2，不走配置文件，代码内常量 |
| 自维护间隔 | `24h` | C4，`should_run_self_maintenance(interval_hours=24.0)` |
| `AgentPaths.proprioception_snapshot` | 不可配置 | B1 → Stage 9 信号桥接落盘路径，见 `ResourceArbiter` 规则 4 |

---

## 15. 与其他文档的关系

- **设计依据**：[next_doc/embodied_agent_design.md](../next_doc/embodied_agent_design.md)
- **改进计划与实现取舍详细说明**：[next_doc/embodied_agent_improvement_plan_v3.md](../next_doc/embodied_agent_improvement_plan_v3.md)——
  每一项在"原计划设计"与"实际接入点"有出入时，都在该文档里逐项写明原因
  （多数是核对代码库后发现已有更合适的基础设施可复用，而不是简单照抄
  伪代码）
- **相关既有机制**：[记忆管理指南](memory-management-guide.md)（Lesson
  Memory 基础）、[Phase G 后台循环指南](self-evolution-phase-g-guide.md)
  （C4 复用的 skill tracker / capability_map 基础设施来源）、
  [Stage 9 自主运行时指南](self-evolution-stage9-guide.md)（cron job /
  时间门控模式的原型）
