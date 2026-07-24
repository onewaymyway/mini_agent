# 看板主界面化 + 自主执行/自主进化 改进方案

> 状态：部分落地 · 见 `next_doc/kanban_and_autonomy_improvement_implementation_record.md`
> 已完成：Track C（退化版）、Track A、Track D、Track B（含反向同步，完整版）、
> Track F（完整）、Track E、Track G（部分：`[ARTIFACTS]` 标记解析，未做
> tool_call 记录自动提取的更可靠版本）。
> 未完成：Track H/I/J/K，详见实施记录文档"未完成/待续"一节。
> 关联代码：`apps/mini_agent_kanban/`、`src/mini_agent/evolution/`、`src/mini_agent/perception/goal_backlog.py`
> 前置修复：本方案假设 [并发槽位卡死修复] 已落地（`ObjectiveExecutor.reap_stale_steps()`），
> 否则 Track B/C 的状态同步会把"假卡死"也当成正常状态展示，掩盖问题。

## 0. 目标与非目标

**目标**：把看板从"能看、能手动改状态的仪表盘"升级成"用户日常处理任务的主入口"，同时让自主
执行层更健壮、更能闭环自己的判断质量。

**非目标（本轮不做）**：
- 不重写前端框架（不把 Streamlit 换成 React/其他 SPA）——评估后认为收益不足以覆盖迁移成本，
  见 Track G 的"轻量改造"结论。
- 不做多租户级别的权限模型（RBAC/组织架构）——现状是单 owner + 可选协作者，本方案只做到
  "谁能看到什么、谁的操作要被记录"，不做角色分级审批。
- 不做自动合并进化提案到 `main` 分支的无人值守全自动化——只做到"低风险变更可以一键合并"，
  合并动作本身仍需人点一下。

## 1. 现状问题清单（按影响面排序，供后续章节引用）

| 编号 | 问题 | 根因 | 影响 |
|---|---|---|---|
| P1 | 后台权限请求跨 session 不可见 | `permission_req` 只在打开的 chat tab 渲染 | 用户可能完全不知道有 Objective 卡住等审批 |
| P2 | GoalNode.status 与 ObjectiveExecution.status 是两条独立数据线 | 两套状态机（`active/paused/completed/abandoned` vs `pending/running/paused/completed/failed`）没有同步机制 | 看板显示的"进行中"可能早已失败/完成，反之亦然 |
| P3 | 并行 Objective 之间无路径互斥检测 | `ResourceArbiter.check_path_conflict()` 只查用户路径，不查 Objective 间 | 并发=2 时两个 Objective 可能同时写同一批文件 |
| P4 | 看板只能看不能管 | 没有终止/重试/插话的 API + UI | 出问题还是得回终端敲 `/goals`/`/next` |
| P5 | 执行细节不可钻取 | `result_summary` 截断到 200~500 字，trace 数据存在但没接到看板 | 排查"这一步到底干了什么"很难 |
| P6 | 全局共享 Agent/session（daemon Phase1/2 遗留） | 多用户共享同一份对话历史 | 与"多人日常主界面"定位冲突 |
| P7 | Streamlit 轮询模型 | `st.rerun()` + `@st.fragment(run_every="2s")` | 非真正实时，Objective/会话一多开销线性增长 |
| P8 | 并发数硬编码 | `MAX_CONCURRENT_OBJECTIVES = 2` 常量 | 不随预算/资源状况自适应 |
| P9 | Step 失败重试是"原样重抛" | `on_turn_failed`/`reap_stale_steps` 重试用同一 prompt | 系统性失败（走不通的路）被当成偶发重试，浪费预算 |
| P10 | 跨步骤上下文靠纯文本摘要传递 | `_submit_step` 拼接 `result_summary` | 步骤一多容易丢失关键产出物信息（文件路径等） |
| P11 | Objective 失败无自动二级恢复 | 失败后停在 `failed`，需人工 `/goals` 重置 | 自治程度受限于人盯着看 |
| P12 | 效果回填未闭环到目标推导优先级 | `outcome_tracker.py` 只回填到 skill_propose，不影响 `soft_goal_deriver.py` 权重 | 反复推导出"历史失败率高"的同类目标 |
| P13 | 进化提案审核全人工、无分级 | `/evolve review` 命令行 + 无图形化 diff | 低风险变更（文档/规则调整）也要走全流程 |
| P14 | 资源门控是二元 block/allow | `ResourceArbiter` 第4/5条规则无中间态 | 检测到用户活跃就整体停摆，而非降级运行 |

## 2. 改进方案（分 Track，每个 Track 独立可交付）

---

### Track A（P0）：全局待办通知中心

**解决**：P1、部分 P4

**设计**：
- 新增后端聚合接口 `GET /v1/inbox`：跨所有 session 扫描 pending permission/interaction 请求 +
  `failed`/长时间无进展的 Objective execution，统一返回一个"待办列表"，每项带
  `{type, session_id, objective_id?, summary, created_at, action_url}`。
  - 权限请求：复用现有 `pending_permissions()` 逻辑，但改为遍历 `sessions_dir` 下所有活跃 session，
    而不是只查当前 `resolved_session_id`。
  - 卡住的 Objective：复用 `ObjectiveExecutor.get_status_summary()`，筛出
    `status in ("failed",)` 或 `steps` 里有 `error_msg` 非空的项。
- 看板侧：顶栏加一个待办徽标（数字角标），点开是一个下拉/弹层列表，每项点击后
  `st.query_params` 跳转到对应 session 的 chat tab 并定位到该请求（复用现有
  `query_params` 跳转的写法，见 app.py 里已有的 "不要在 query_params 赋值后紧跟手动 st.rerun()" 模式）。
- 徽标本身参与现有的 `auto_refresh` 轮询，2~3 秒刷新一次，不需要额外基础设施。

**验收标准**：
1. 在 A 会话的自主任务卡在权限审批上时，用户停留在"目标看板" tab 也能在 3 秒内看到待办数字。
2. 点击待办项能直接跳转到对应会话并展开权限卡片。
3. Objective 执行失败后，待办列表里出现对应条目，点击能看到失败原因（`error_msg`）。

**工作量**：中（新增 1 个后端接口 + 1 个前端组件），无破坏性改动。

---

### Track B（P0）：GoalNode 与 ObjectiveExecution 状态单向同步

**解决**：P2

**设计**：
状态源分两类，同步方向必须单向，避免循环覆盖：
- **执行态由 ObjectiveExecutor 主导**（`running/paused/completed/failed`），这是"事实"。
- **意图态由用户在看板上表达**（比如手动把卡片拖到"已放弃"），这是"决策"。

同步规则：
1. `ObjectiveExecutor._on_objective_completed()` / `_on_objective_failed()` 触发时，
   除了写 `activity_digest.jsonl`，新增一次对 `GoalBacklog.set_status()` 的回调：
   - `completed` → GoalNode.status = `"completed"`
   - `failed` → GoalNode.status 新增一个值 `"failed"`（当前 schema 里没有这个状态，
     需要扩展 `GoalNode.status` 的合法值集合，并检查看板 `GOAL_STATUS_COLUMNS` 是否要加一列，
     或者把 `failed` 映射到现有的某一列 + 一个醒目的失败角标）。
2. 反过来，用户在看板上手动把 Objective 状态改成非"运行中"（比如"已放弃"），需要触发
   `ObjectiveExecutor` 侧对应 execution 的 `pause`/`cancel`（见 Track D 的 `cancel()` API）——
   不能让 GoalNode 显示"已放弃"但后台 execution 还在跑。
3. 看板卡片渲染时，`status_key`（列位置）以 GoalNode.status 为准，卡片内部的执行详情
   （Track A 已实现的 `_render_objective_execution_detail`）以 ObjectiveExecution.status 为准，
   两者不一致时（比如 GoalNode 还是 active，但 execution 已经 failed 超过 1 小时）在卡片上
   加一个"⚠️ 状态可能过期"的提示，引导用户手动核对——这是过渡期的兜底，不是长期方案。

**验收标准**：
1. Objective 执行完成后，不需要用户手动操作，看板列自动挪到"已完成"。
2. Objective 执行失败后，看板上有清晰可见的失败态（不再是仍停留在"进行中"列）。
3. 用户手动把卡片改成"已放弃"，对应后台 execution 会被暂停/取消，不会继续消耗并发槽位。

**依赖**：需要 Track D 的 `cancel()` API 支撑第 2 条规则。

**工作量**：中。核心是给 `GoalNode.status` 加 `failed` 值，并在两处回调点各加一次跨模块调用。

---

### Track C（P0）：并行 Objective 路径互斥检测

**解决**：P3（这是目前唯一一个**数据安全**风险项，优先级应该视为最高，即使不做界面相关改动也该先做）

**设计**：
- 每个 `ExecutionStep` 提交前，先声明"预期会碰哪些路径"——这个信息目前不存在（LLM 拆解出的
  step 只是自然语言描述），需要新增一步：在 `_decompose()` 或 `_submit_step()` 之前，
  让轻量 LLM 调用额外产出一个"本步骤可能涉及的文件/目录"列表（可以不精确，宁可保守多列）。
- `ObjectiveExecutor` 维护一个全局的 `_active_step_paths: dict[str, set[str]]`
  （execution_id → 当前 running step 声明的路径集合）。
- 新 Objective/新 step 提交前，检查声明路径是否与其他 **正在运行** 的 execution 的路径集合
  重叠；重叠则该 step 不提交，标记为 `blocked`（新状态），等占用方的 step 完成后再提交，
  而不是硬失败——避免因为一次误判直接判 Objective 失败。
- 这套机制退化路径：拆解不出路径信息时，跟 `ResourceArbiter.check_path_conflict()` 现有的
  "tracing 未开启就保守当冲突"哲学一致——保守起见，同一时刻不允许该 Objective 与"任何"其他
  正在写文件的 Objective 并行，直接退化为串行执行这两个 Objective。

**验收标准**：
1. 构造两个都会写 `README.md` 的 Objective，并发提交，验证第二个的对应 step 会等待而不是
   立即并行执行导致互相覆盖。
2. 不冲突的两个 Objective 依然能正常并行（不能退化成变相把 `MAX_CONCURRENT_OBJECTIVES` 砍到 1）。

**工作量**：中大。核心复杂度在"路径声明"这一步的可靠性——建议先上"退化路径"（保守串行化）
作为 P0 立即上线，"精确路径声明 + 部分并行"作为 P1 的优化项。

---

### Track D（P1）：看板可操作能力（终止 / 重试 / 插话）

**解决**：P4

**设计**：
- `ObjectiveExecutor` 新增三个公开方法：
  - `cancel(execution_id) -> bool`：把 execution 标记为 `cancelled`（新状态，区别于 `failed`——
    是用户主动叫停，不是执行失败），释放并发槽位，不再重试。
  - `retry_current_step(execution_id) -> bool`：手动触发当前 step 重新提交（复用
    `reap_stale_steps()` 里的重试逻辑，但不检查是否超时，允许用户随时手动触发）。
  - `inject_guidance(execution_id, message) -> bool`：把用户的一句话作为"补充上下文"塞进
    下一次 `_submit_step()` 的 prompt（而不是直接开一个新 turn 打断当前 step），
    实现成本远低于真正的"打断正在跑的 turn"。
- 对应 REST 端点：`POST /v1/objectives/{execution_id}/cancel`、`/retry`、`/guidance`。
- 看板卡片增加三个按钮（仅在 execution 存在且状态为 running/failed 时显示对应按钮）。

**验收标准**：
1. 点"终止"后，对应并发槽位在下一次 `/v1/autonomous/status` 轮询里立即显示为已释放。
2. 点"重试当前步骤"后，`current_step` 的 `retry_count` 增加、`status` 变回 `running`。
3. 插话的内容确实出现在下一次提交给 agent 的 message 里（可通过 session trace 验证）。

**工作量**：小中。主要是给已有的状态机加几个转换入口，不需要新架构。

---

### Track E（P1）：执行细节可钻取

**解决**：P5

**设计**：
- 看板卡片的"步骤"展开后，每个 `done`/`failed` 的 step 增加一个"查看详情"的 expander，
  内容从对应 `turn_id` 关联的 session trace 里按 `step_id`（已经存在于 meta 里，
  见 `_submit_step()` 提交时写的 `meta={"execution_id":..., "step_id":...}`）过滤出该 turn 的
  完整 tool_call/tool_result 序列，而不只是 200 字摘要。
- 数据源：`traces.jsonl`（已存在，tracing 开启的前提下）；tracing 未开启时退化为只显示
  `result_summary`，并提示"开启 tracing 可查看完整过程"。

**验收标准**：某个失败 step 点开详情，能看到它实际调用过哪些工具、每次调用的入参和结果摘要。

**工作量**：小。数据都已存在，只是没接线。

---

### Track F（P1）：Step 失败重试策略升级

**解决**：P9、部分 P10

**设计**：
- `reap_stale_steps()`/`on_turn_failed()` 重试时，把失败原因（`error_msg`）和上一次尝试的
  `result_summary` 一并注入下一次 prompt 的"重试上下文"里，而不是原样重发同一句
  `step.description`：
  ```
  [重试 - 第 {retry_count} 次] 步骤 {i}/{n}: {description}
  上一次尝试失败原因：{error_msg}
  请根据失败原因调整方法后重试，不要重复同样的做法。
  ```
- 连续两次都失败（达到 `MAX_STEP_RETRIES`）后，不直接判 Objective 失败，而是先尝试一次
  "重新分解剩余步骤"（调用 `_decompose()`，但只针对从当前失败点往后的部分，把已完成步骤的
  `result_summary` 作为上下文喂给它）——这是 Track K（P11）的一部分，放在这里一起设计因为
  逻辑强相关。

**验收标准**：人为制造一个第一次会失败的 step（mock），验证第二次重试的 prompt 里包含第一次
的失败原因文本。

**工作量**：小。主要是改 prompt 拼装逻辑，不涉及新数据结构。

---

### Track G（P2）：跨步骤结构化产出物传递

**解决**：P10

**设计**：
- `ExecutionStep` 增加一个可选字段 `artifacts: list[str]`（本步骤产出/修改的文件路径列表），
  由 agent 在 step 完成时以约定格式在回复末尾声明（类似现有 `result_summary` 提取首句摘要的
  做法，改成解析一个固定标记 `[ARTIFACTS] path1, path2`）。
- `_submit_step()` 拼接前序上下文时，除了文本摘要，额外列出前序步骤声明过的 `artifacts`，
  让后续步骤能明确引用具体路径而不是"上一步生成的那个文件"这种模糊指代。
- 这个字段同时也是 Track C 路径互斥检测的输入来源之一（已完成步骤实际碰过的路径，
  可以用来校验/修正 LLM 声明的"预期路径"是否准确，长期看能让 Track C 的判断越来越准）。

**工作量**：小中，且与 Track C 有正向协同，建议实现顺序上 C 先上退化版本，G 之后再反哺 C 的精确度。

---

### Track H（P2）：效果回填闭环到目标推导优先级

**解决**：P12

**设计**：
- `soft_goal_deriver.py` 的 `derive()` 在生成候选 Goal 前，先查一次
  `outcome_tracker` 里同主题（同 lesson group / 同 capability 条目）历史 Objective 的
  完成率——如果某类主题过去 N 次 derive 出的 Objective 失败率超过阈值（比如 3 次里 2 次
  failed），则该主题优先级降级或直接跳过本轮 derive，避免反复产生同类型"做不到"的目标。
- 需要 `ObjectiveExecutor` 在 `_on_objective_completed`/`_on_objective_failed` 时，把结果
  也写一份到 `outcome_tracker` 能查询的存储里，按 `objective.source`（`agent_derived` 的
  来源主题标签，目前 `GoalNode` 里应该已经有类似字段，需要核实/补充）关联。

**工作量**：中。核心工作量在"主题"这个关联字段是否已经存在、粒度是否够用——需要先读
`GoalNode` 完整字段确认，不确定的部分标注为待细化。

---

### Track I（P2）：进化提案分级自治

**解决**：P13

**设计**：
- 给进化提案增加"风险分级"字段（复用现有 T0~T3 验证 + eval_runner 对比结果自动打分）：
  - **低风险**：只改文档/注释/lesson 规则一类不涉及代码执行路径的变更，且 T0~T3 全绿、
    eval 对比无回归 → 允许"一键合并"按钮，仍需人点，但不需要逐行审 diff。
  - **中/高风险**：涉及核心逻辑改动 → 维持现状全人工审核。
- 看板新增一个"进化提案" tab（或复用现有"诊断" tab 扩展），用 diff 视图（可以先用简单的
  `st.code` 展示 unified diff，不必一开始就做完整的 side-by-side）替代目前只能在
  `/evolve review` 命令行里看的方式。

**工作量**：中大，建议放在本方案最后做，收益不如前面几项直接。

---

### Track J（P2）：资源门控降级执行

**解决**：P14

**设计**：
- `ResourceArbiter` 第4/5条规则（frustration、user_presence）从布尔返回改成三态：
  `"full" | "degraded" | "blocked"`。
- `degraded` 态下：`AutonomousLoop` 把并发上限临时降到 1（而不是 0），且提交任务时在
  `submit_fn` 的 meta 里加一个 hint，允许 `Agent` 侧后续接入"用更便宜的模型跑自主任务"
  （这一步依赖 `LLMClientPool` 是否支持按 initiator 选择不同模型档位，需要先确认，标注为
  待调研项）。

**工作量**：中，且有一个明确的前置调研项（模型池是否支持按场景切换档位），建议放在
本方案 P2 阶段，先立项调研再排期。

---

### Track K（P2）：并发数自适应

**解决**：P8

**设计**：把 `MAX_CONCURRENT_OBJECTIVES` 从硬编码常量改成从 `self_profile.json` 的
`resource_budget` 派生：`min(2, floor((budget - used_today) / avg_objective_cost))`，
`avg_objective_cost` 可以从 `outcome_tracker`/历史 execution 的 token 消耗统计里滚动计算。
不设上限硬编码为 2，而是设一个配置项 `max_concurrent_objectives_cap`（默认 2）作为安全阀。

**工作量**：小中，依赖 Track H 已经在做的历史统计基础设施，建议排在 Track H 之后。

---

## 3. 路线图与依赖关系

```
P0（本迭代，风险/正确性优先）
 ├─ Track C：并行路径互斥（退化版：保守串行化）  ← 数据安全，最高优先级
 ├─ Track A：全局待办通知中心
 └─ Track B：状态单向同步（依赖 Track D 的 cancel()，но 可以先做 completed/failed 单向部分）

P1（下一迭代，体验闭环）
 ├─ Track D：看板可操作能力（cancel/retry/inject_guidance）→ 解锁 Track B 完整版
 ├─ Track E：执行细节可钻取
 └─ Track F：失败重试策略升级

P2（后续迭代，自治程度提升）
 ├─ Track G：结构化产出物传递（反哺 Track C 精确度）
 ├─ Track H：效果回填闭环到目标推导
 ├─ Track K：并发数自适应（依赖 H 的统计基础设施）
 ├─ Track I：进化提案分级自治
 └─ Track J：资源门控降级执行（有前置调研项）
```

**建议启动顺序**：C（退化版）→ A → D → B → E/F → 其余 P2 按团队带宽排。
理由：C 是唯一的数据安全风险，必须最先堵；A/D/B 三者互相依赖度最高，凑成一个迭代能让
"看板主入口"这个体验先立起来；E/F 属于加分项，随时可以插空做；P2 里的项目彼此独立，
可以并行分配给不同的人。

## 4. 待确认/待细化项（不阻塞启动，但需要在对应 Track 开工前拍板）

1. `GoalNode.status` 是否要真的新增 `"failed"`/`"cancelled"` 两个值，还是复用 `"abandoned"`
   并加一个额外的 `fail_reason` 字段来区分——涉及看板 `GOAL_STATUS_COLUMNS` 是否要加列，
   建议 Track B 开工前先定。
2. Track G 依赖 agent 在回复里主动声明 `[ARTIFACTS] ...` 标记，需要确认现有 system prompt/
   工具描述里怎么引导模型稳定产出这个格式，或者退化成从 `tool_call` 记录里自动提取
   `write_file`/`patch_file` 类工具的路径参数（更可靠，建议优先走这条路而不是指望模型自觉）。
3. Track J 依赖 `LLMClientPool` 是否已支持"按 initiator/场景选择模型档位"，需要先读
   `config/models.py` 确认，标注为该 Track 的前置调研任务。
4. Track H 的"主题"关联字段目前 `GoalNode`/`activity_digest` 记录里的粒度是否够用，
   需要先读一遍 `soft_goal_deriver.py` 完整的 derive 三路来源（capability_map/work_index/
   lesson_review）分别用什么 ID 标识"同一主题"，确认能否统一映射。
