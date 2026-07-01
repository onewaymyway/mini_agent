# 具身智能改进计划 v3

> **基准版本**：当前代码库现状（多用户 Phase 1-4 已全部完成，Stage 9 daemon 化已落地）。
> **前身文档**：`embodied_agent_improvement_plan_v2.md`（基于旧代码，部分设计已被实现或架构已变）。
> **定位**：在现有架构的真实接缝上落地具身智能理念，不重复已实现的内容，不悬空设计。

> **实施进度更新（阶段 A 已完成）**：
> - **A1 Connected REPL 完整命令对等**：✅ 已实现。`cli/daemon.py` 新增
>   `DaemonClient.list_cron_jobs/run_cron_job/list_goals/get_autonomous_status/get_digest`
>   以及 `_handle_connected_cron/_handle_connected_goals/_handle_connected_digest`，
>   `run_connected_repl()` 现支持 `/cron`、`/cron run <job_id>`、`/goals`、`/digest`。
>   "digest"语义对接已有 `/v1/self/status`，未新增重复路由。测试见
>   `tests/test_daemon_connected_repl_commands.py`。
> - **A2 Lesson source 区分（human_feedback）**：核对代码库后发现**已经实现**
>   （`perception/correction_detector.py` + `agent.py::_detect_and_record_correction`/
>   `_on_edit_detected`，confidence=0.85，高于规则触发 lesson 的 0.6），本文档 §1.2
>   表格中"未实现"的描述已过时，不需要重复开发。
> - **A3 Reminder pre_tool 触发时机**：✅ 已实现。新增 `TRIGGER_PRE_TOOL` 触发类型
>   （`reminders/loader.py`/`matcher.py`/`manager.py`）、`ReminderConfig.pre_tool_enabled`
>   开关、`ToolExecutor` 在工具真正执行前（甚至权限检查/PreToolUse hook 之前）调用
>   `reminder_mgr.check_pre_tool()` 并通过 Agent 注入的回调写入历史，新增示例
>   reminder `prompts/reminders/large_file_read_warning.md`。测试见
>   `tests/test_reminder_pre_tool.py`。
> - **B1 ProprioceptionModule（本体感知模块）**：✅ 已实现。新增
>   `perception/proprioception.py`（`AgentInternalState` + `ProprioceptionModule`，
>   纯计算、不调用 LLM）、`ProprioceptionConfig`（默认开启），在
>   `agent.py::_agentic_loop()` 中每轮 LLM 调用前 `sense()` 一次（cognitive_load
>   复用已有的 token 预估 `_budget_pct`，risk_perception 基于最近工具名，
>   energy_budget_ratio 基于剩余 turn 预算），工具执行后 `record_tool_outcome()`
>   累积/衰减 frustration。frustration 超阈值且连续失败达标时注入一条元认知
>   提示（建议模型停下来汇报困境），**不强制中断循环**——前馈控制 + 保留人类
>   控制权。快照可选写入 `traces.jsonl`（`SessionTracer.record_internal_state`）。
>   测试见 `tests/test_proprioception.py`（21 个用例，纯单元测试）。
> - **B2 Lesson → Reminder 自动闭环**：✅ 已实现，但复用了比设计稿更成熟的
>   已有基础设施——`perception/lesson_review.py`（Stage 3.1 已经实现的
>   trigger 文本 Jaccard 相似度聚类 + T0/T1/T2/T3 门槛判定）此前只喂给
>   evolution-agent 提案流程（`/evolve review`），未连到 reminder。新增
>   `evolution/lesson_to_reminder.py::LessonToReminderBridge`，直接复用
>   `group_lessons()` 的聚类结果：含 human_feedback 来源的分组直接激活
>   （写入 reminder 目录，`pre_tool` 触发类型，依赖 A3），仅 self_reflection
>   来源但达到 T1 门槛的分组写成草稿（`drafts/` 子目录，`enabled: false`，
>   `ReminderLoader` 不递归子目录因此不会被自动加载，需要手动
>   `promote_draft()` 提升）。CLI 入口：`/evolution lessons-to-reminders`
>   （未接入 Phase G 自动周期扫描，作为手动触发的简化版本，后续可仿照
>   `/evolve phase-g` 的模式接入周期触发）。测试见
>   `tests/test_lesson_to_reminder.py`（12 个用例，含真实 `ReminderLoader`
>   端到端解析校验）。
> - **B3 Workflow 并发执行（depends_on 拓扑分析）**：✅ 已实现。新增
>   `WorkflowRunner._compute_parallel_batches()`（Kahn 拓扑分层算法，循环/缺失
>   依赖检测语义与原 `_topological_sort()` 一致），`run()` 改为按层执行，
>   同一层内默认用 `ThreadPoolExecutor` 并发跑。并发安全性来自既有架构：
>   `_execute_with_main_agent()` 本来就给每个步骤创建独立 `Agent` 实例（独立
>   history/PermissionGuard），步骤间不共享可变 Agent 状态；唯一的跨线程
>   共享状态 `step_results` dict 通过新增的 `results_lock` 保护读写。新增
>   `WorkflowStep.allow_parallel`（默认 True）允许单步骤强制串行（应对
>   depends_on 未声明的隐式副作用），新增 `WorkflowConfig.parallel_enabled`/
>   `max_parallel` 全局开关。测试见 `tests/test_workflow_parallel.py`（17 个
>   用例，含真实多线程并发数验证、gate-retry 在并发路径下的回归测试）。
>   测试过程中发现一个改动前就存在、与本次改动无关的语义细节：步骤被
>   condition 判定 SKIPPED 后，依赖它的下游步骤不会级联 SKIPPED（依赖检查
>   只把 FAILED/PENDING 视为"未完成"）——按"不在本次改动范围内调整语义"
>   的原则原样保留，仅在新增测试里补充注释说明。
> - **B4 AffordanceMap / 阶段 C（AgentSelfModel 等）**：B4 与 C1 已实现
>   （`perception/affordance_analyzer.py` + `perception/self_model.py`，
>   接入 `api/session_pool.py::_inject_affordance_map` 与 `agent.py`
>   构造流程），详见下方"实施进度更新（阶段 D 已完成）"。
> - **工具透明性（IntentActionMapper）/ C2 时间加权记忆激活 / C3 认知锚点
>   文件 / C4 自维护模块**：✅ 均已实现，详见下方"实施进度更新（阶段 D
>   已完成）"。

---

> **实施进度更新（阶段 D 已完成）**：
> - **工具透明性（IntentActionMapper，§ 2.3）**：✅ 已实现。新增
>   `perception/intent_action_mapper.py`（`ActionEvent` + `IntentActionMapper`），
>   纯规则匹配（不调用 LLM），按"工具名所属意图类别"做连续游程分组——
>   `exploration`/`code_edit`/`test_run`/`env_setup`/`vcs_op`/`research`/`other`，
>   `bash` 工具按命令内容关键词细分类别。接入 `agent.py` 主循环
>   `execute_tools` span：分组结果写入 `traces.jsonl` 的 `action_events`
>   字段（不改变 history 本身，只在可观测性侧补充语义标注），供 /diagnostics
>   和后续 Phase G 扫描读取。测试见 `tests/test_intent_action_mapper.py`
>   （17 个用例）。
> - **C2 时间加权记忆激活**：✅ 已实现，但实现方式与本文档原计划（"Phase G
>   tick 时批量预计算 temporal_weight 缓存字段"）不同——核对
>   `memory_store.py::_score_all()` 后发现时间衰减本来就是按 `entry.age_days`
>   实时计算（不是缓存字段，没有"缓存过期"问题），批量预计算反而要多维护
>   一份一致性。改为新增 `evolution/memory_aging.py::compute_decay_factor()`
>   纯函数，由 `_score_all()` 直接调用替换原有的全局 `self._decay_lambda`：
>   `entry_type=="lesson"` 的条目按 `source` 区分半衰期基准
>   （human_feedback=90d/experiment_confirmed=60d/self_reflection=30d/
>   revert_record=14d），并按 `occurrence_count` 做加成（封顶 4 倍）；非
>   lesson 条目（summary 等）沿用构造时传入的全局半衰期配置，行为不变。
>   测试见 `tests/test_memory_aging.py`（10 个用例，含 MemoryStore 端到端
>   排序验证）。
> - **C3 认知锚点文件**：✅ 已实现。新增 `AgentPaths.workdir_cognitive_anchor`
>   路径属性（`.agent/cognitive_anchor.md`）、`agent.py::_save_cognitive_anchor()`
>   （LLM 生成"思维状态重建指南"，固定四段式格式，新增
>   `prompts/system/cognitive_anchor.md` + `prompts/user/cognitive_anchor_request.md`）
>   与 `agent.py::_maybe_load_cognitive_anchor()`（session 启动时读取并注入
>   `system_extra`，读取后立即归档重命名为带时间戳文件，避免重复注入）。
>   触发点：`cli/repl.py::run_repl()` 的 `KeyboardInterrupt` 处理分支（用户
>   Ctrl-C 打断当前任务时调用），`_maybe_load_cognitive_anchor()` 接入
>   `agent.py::_init_session()`（与 `_maybe_ensure_project_meta()` 同批调用，
>   对本地/daemon 两条路径统一生效，不像 B4 AffordanceMap 那样只在
>   `SessionAgentPool` 多用户路径生效）。新增配置开关
>   `AppConfig.cognitive_anchor_enabled`（默认 True）。daemon connected REPL
>   模式（`cli/daemon.py`）的 Ctrl-C 暂未接入——客户端进程不直接持有
>   Agent 实例，需要额外的 API 触发路径，留作后续迭代。测试见
>   `tests/test_cognitive_anchor.py`（12 个用例，用 duck-typed fake object
>   以未绑定方法方式调用，避免构造完整 Agent 实例）。
> - **C4 自维护模块（SelfMaintenanceModule）**：✅ 已实现，但 `stale_tools`
>   检测方式与本文档原计划（"最近 N 天未被成功调用的工具"）不同——核对
>   代码库后发现不存在跨 session 持久化的"每个工具最后一次成功调用时间"，
>   改用已经持久化的信号：扫描最近 20 个 session 的 `traces.jsonl` 里
>   `phase="tool_call"` 记录，统计每个工具近期失败率（≥3 次调用且失败率
>   ≥60% 才判定为"可能失效"）。`stale_skills` 复用 `phase_g.py::prune_skills()`
>   同款 `skill_loader.tracker` 基础设施（角度不同：长期未用 vs 高成本未用）。
>   `conflicting_lessons` 复用 `lesson_review.py::group_lessons()` 聚类结果，
>   同一聚类内同时出现正面/负面关键词信号时标记"可能矛盾"（启发式，非
>   精确判断）。新增 `evolution/self_maintenance.py`，与 Phase G 同款
>   "时间门控"模式（独立状态文件 `self_maintenance_state.json`），接入
>   `agent.py::_maybe_run_self_maintenance()`（SessionEnd 时间门控，与
>   `_maybe_run_phase_g()` 并列调用）并新增内置 cron job `sys:self_maintain`
>   （`evolution/cron_scheduler.py`，interval:86400）。只产出报告和建议文本，
>   写入 `activity_digest.jsonl`（`type="health_report"`），不自动修复——
>   与 v3 §九"保留人类控制权"原则一致。测试见 `tests/test_self_maintenance.py`
>   （22 个用例）。
> - 阶段 D 完成后，本文档原计划中列出的全部改进项（A/B/C 三阶段 + P2 的
>   IntentActionMapper）均已落地，只剩"行为级测试框架"一项严格意义上未
>   按原计划新建独立的 `tests/behavior/` 目录——核对后认为现有测试文件
>   （`tests/test_*.py`）已经覆盖了对应行为（frustration 累积/衰减、
>   lesson source 区分、workflow 并行批次、本节新增的四项），新增一个平行
>   目录复制相同断言收益有限，故未单独建立 `tests/behavior/`，详见 §六
>   末尾说明。



## 一、现状盘点——哪些已经实现，哪些还是空白

### 1.1 已实现（不再列入改进计划）

| 功能 | 实现位置 | 完成状态 |
|------|---------|---------|
| daemon 化 + AutonomousLoop 三档位 | `evolution/autonomous_loop.py` | ✅ |
| GoalBacklog / ObjectiveExecutor / CronScheduler | `evolution/` | ✅ |
| 多用户认证（Phase 1）| `api/multi_auth.py` + `api/user_store.py` | ✅ |
| per-user 数据目录与画像注入（Phase 2）| `api/server.py` + `api/user_store.py` | ✅ |
| SessionAgentPool 独立 Agent 实例（Phase 3）| `api/session_pool.py` | ✅ |
| Self ↔ SessionAgent 消息总线（Phase 4）| `api/session_pool.py::SelfMessageBus` | ✅ |
| Phase G 扫描、capability_map、ExplorationSandbox | `evolution/phase_g.py` | ✅ |
| Lesson Memory + LessonRuleDetector | `perception/lesson_rules.py` | ✅ |
| Reminder 系统（tool_error / post_tool / pre_user） | `reminders/` | ✅ |
| Workflow（串行，含 evaluator gate）| `workflow/runner.py` | ✅ |
| observability traces.jsonl + /diagnostics | `perception/observability.py` | ✅ |
| ResourceArbiter + activity_digest.jsonl | `evolution/resource_arbiter.py` | ✅ |

### 1.2 计划中但尚未实现（本文档的改进对象）

| 功能 | v2 计划 § | 当前状态 | 优先级 |
|------|----------|---------|-------|
| 本体感知模块（ProprioceptionModule） | § 2.1 | ✅ 已实现（B1） | P1 |
| Connected REPL 完整命令支持 | 架构文档已知缺口 | ✅ 已实现（A1） | P1 |
| Lesson source 区分（human_feedback） | § 3.2 | ✅ 已实现（核对后发现是历史遗留，见上方说明） | P1 |
| Reminder pre_tool 触发时机 | § 4.1 | ✅ 已实现（A3） | P2 |
| Lesson → Reminder 自动闭环 | § 4.1 | ✅ 已实现（B2） | P2 |
| 工具透明性（IntentActionMapper） | § 2.3 | ✅ 已实现（阶段 D） | P2 |
| 余裕感知层（AffordanceMap） | § 3.1 | ✅ 已实现（B4） | P2 |
| Workflow 并发执行（depends_on 拓扑分析） | § 12.6 | ✅ 已实现（B3） | P2 |
| AgentSelfModel（命名澄清 + 聚合视图） | § 4.2 | ✅ 已实现（C1） | P3 |
| 认知锚点文件 | § 3.3 | ✅ 已实现（阶段 D） | P3 |
| 时间加权记忆激活 | § 1.1 | ✅ 已实现（阶段 D，实现方式见文首说明） | P3 |
| 自维护模块（SelfMaintenanceModule） | § 1.2 | ✅ 已实现（阶段 D，stale_tools 检测方式见文首说明） | P3 |
| 行为级测试框架 | § 4.3 | 未单独建立 tests/behavior/，见 §六末尾说明 | P3 |

### 1.3 架构已变、v2 计划需修订的部分

v2 计划中的某些设计基于旧的单 Agent 架构，现在多用户 Phase 3 已经是 per-session Agent，需要重新定位接入点：

- **ProprioceptionModule 的归属**：v2 说"每轮 run_turn 时调用"——现在每个 SessionEntry 有独立的 AgentRunner 线程，ProprioceptionModule 应该是每个 SessionAgent 实例的成员，而不是全局单例
- **AffordanceMap 的构建时机**：v2 说"session 开始时构建"——现在是 `SessionAgentPool._create_entry()` + `_build_session_cfg()` 时，可以更精确地定位
- **SelfMessageBus 已经实现**：v2 把 Self ↔ SessionAgent 通信归入"计划"，实际上已经通过 `session_pool.py::SelfMessageBus` 实现了基础设施，现在可以在这个基础上扩展更多消息类型

---

## 二、架构现状图（精准版）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  daemon 进程（常驻）                                                           │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  AgentRunner（Self 线程）                                             │   │
│  │    ├── agent.run_turn()  ← 用户通过 InputQueue 提交的消息            │   │
│  │    └── AutonomousLoop.tick()（内嵌，每次 idle 时触发）               │   │
│  │         ├── passive:  CronScheduler → Phase G                        │   │
│  │         ├── maintenance:  ObjectiveExecutor                          │   │
│  │         └── autonomous:  SoftGoalDeriver + ExplorationSandbox        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  SessionAgentPool（多用户 Phase 3）                                    │   │
│  │    session_id → SessionEntry{Agent, AgentRunner, AgentBridge}        │   │
│  │    idle 30min → 自动 suspend（保存到磁盘）                            │   │
│  │    max_sessions=20 并发上限                                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  SelfMessageBus（Phase 4）                                                    │
│    "self" ↔ "session:<id>"  消息类型：                                       │
│    session_summary / profile_update / approval_req / session_crashed         │
│    ← 已实现，但消费端只处理 session_crashed 和 session_summary               │
│                                                                              │
│  HttpServer（FastAPI + uvicorn）                                              │
│    MultiUserAuthMiddleware（Phase 1）                                         │
│    /v1/users /v1/chat /v1/stream /v1/goals /v1/cron/jobs 等                  │
└──────────────────────────────────────────────────────────────────────────────┘
                    │ HTTP
     ┌──────────────┼──────────────┐
     │              │              │
  CLI 连接模式    Web Demo      其他 HTTP 客户端
 (daemon.py::     (apps/)      (API 调用方)
  run_connected_repl)
  ⚠️ 不支持 /cron /goals /digest
```

**当前最大的架构缺口**：
1. Connected REPL（CLI 连接模式）与本地直跑模式命令不对等
2. Agent 对自身状态没有实时感知（无 ProprioceptionModule）
3. Lesson source 只有一种，无法区分人类反馈与自反思

---

## 三、改进计划（P1 优先级）

> 阶段 A：修复架构缺口，投入小、价值高、不依赖其他改进

### A1. Connected REPL 完整命令对等

**问题**：`run_connected_repl()` 目前只支持 `/session list|new|<id>` 和 `exit`，不支持 `/cron`、`/goals`、`/digest`、`/agent` 等命令，用户通过 CLI 连接到 daemon 时无法管理自主任务。

**已知缺口来源**：`daemon-multiuser-implementation-design.md §12.3`

**实现位置**：`src/mini_agent/cli/daemon.py::run_connected_repl()`

**实现方案**：在 `run_connected_repl()` 的内置命令分发里识别 `/cron`、`/goals`、`/digest`，通过 `DaemonClient` 的 HTTP 调用转发，然后格式化输出：

```python
# daemon.py 内新增 DaemonClient 方法
class DaemonClient:
    # 已有：respond_permission / list_pending_permissions / health_check
    
    def list_cron_jobs(self) -> dict | None:
        return self._get("/v1/cron/jobs")
    
    def run_cron_job(self, job_id: str) -> dict | None:
        return self._post(f"/v1/cron/jobs/{job_id}/run")
    
    def list_goals(self) -> dict | None:
        return self._get("/v1/goals")
    
    def get_autonomous_status(self) -> dict | None:
        return self._get("/v1/autonomous/status")
    
    def get_digest(self) -> dict | None:
        return self._get("/v1/digest")


# run_connected_repl() 命令分发（在 /session 命令分发之后追加）
elif user_input.startswith("/cron"):
    _handle_connected_cron(client, user_input, term)
elif user_input.startswith("/goals"):
    _handle_connected_goals(client, user_input, term)
elif user_input == "/digest":
    _handle_connected_digest(client, term)
```

**涉及文件**：`cli/daemon.py`

**验收标准**：
- `mini-agent`（connected 模式）下 `/cron list` 能正确展示 job 列表
- `/goals` 能展示当前 Goal/Objective 层级
- 输出格式与本地直跑模式视觉一致（复用 `_render_sse_event` 的 markup 风格）

---

### A2. Lesson source 区分——人类反馈特权信号

**问题**：`perception/lesson_rules.py` 生成的所有 lesson 的 `source` 字段都是 `"self_reflection"`，无法区分哪些来自用户的直接纠正，导致人类反馈与 Agent 自我猜测被平等对待。

**具身来源**：社会具身——人类反馈是最高质量的社会信号，一次明确的人类纠正价值上远高于多次自我猜测。

**实现位置**：`perception/lesson_rules.py` + `agent.py`

**实现方案**：

```python
# perception/lesson_rules.py

# 扩展 source 枚举
LESSON_SOURCES = {
    "human_feedback",      # 用户的直接纠正（新增）
    "self_reflection",     # Agent 自我反思（已有）
    "experiment_confirmed", # 实验验证（已有但未使用）
    "revert_record",       # 回退记录（已有但未使用）
}

# 新增：从用户消息中检测直接纠正信号
class HumanFeedbackDetector:
    """
    检测用户消息是否包含对 Agent 行为的直接纠正。
    触发条件：
      - 明确否定词 + 正确做法（"不对，应该用 patch_file"）
      - "下次记得 / 以后 / 记住" + 规则
      - 对刚执行的工具调用的直接反驳
    """
    
    CORRECTION_PATTERNS = [
        r"不对[，,]?\s*(?:应该|要|需要)",
        r"(?:下次|以后|记住|记得)\s*.+",
        r"不应该.+，?应该.+",
        r"(?:错了|不对|不是这样)[，,。]",
    ]
    
    def detect(self, user_msg: str, recent_tool_calls: list[str]) -> bool:
        """返回 True 表示这条消息包含直接纠正信号。"""
        for pattern in self.CORRECTION_PATTERNS:
            if re.search(pattern, user_msg):
                return True
        return False
    
    def extract_lesson_body(self, user_msg: str) -> str:
        """从纠正消息中提取 lesson 要点（直接用原文，不 LLM 推断）。"""
        return user_msg.strip()


# agent.py：在处理用户消息时调用 HumanFeedbackDetector
def _process_user_message(self, user_msg: str) -> None:
    if self._human_feedback_detector.detect(user_msg, self._recent_tool_calls):
        lesson = MemoryEntry(
            entry_type="lesson",
            body=self._human_feedback_detector.extract_lesson_body(user_msg),
            source="human_feedback",
            occurrence_count=1,
            # human_feedback 的 promote_threshold 明显低于 self_reflection
            # 一次人类纠正直接写入，不需要达到阈值才 promote
            promote_threshold=1,
        )
        self._memory_store.add(lesson)
```

**与 Phase G 的联动**：`phase_g.py` 的 `capability_map` 更新时，`source="human_feedback"` 的 lesson 权重高于 `"self_reflection"`，能更快速地影响能力评分。

**涉及文件**：`perception/lesson_rules.py`、`perception/memory_store.py`（确认 promote_threshold 字段存在）、`agent.py`

---

### A3. Reminder pre_tool 触发时机（前馈控制）

**问题**：当前 Reminder 只支持事后触发（`tool_error`、`post_tool`、`pre_user`、`post_assistant`），缺少**工具执行前**的前馈控制，无法在危险操作发生前注入警示。

**具身来源**：前馈控制（feedforward control）——成熟的运动系统不只靠错误反馈，还依赖预期动作的预先调节。

**实现位置**：`reminders/manager.py` + `tool_executor.py`

**实现方案**：

```python
# reminders/manager.py 新增方法
def on_pre_tool(self, tool_name: str, tool_args: dict) -> list[Reminder]:
    """
    工具执行前调用。匹配 trigger_event="pre_tool" 的 reminder。
    
    触发条件示例（YAML reminder 文件）：
      trigger_event: pre_tool
      condition:
        tool_name: read_file    ← 可匹配具体工具名
      inject_as: user
      body: "提示：读取文件前请确认大小，大文件会导致 context 溢出。"
    """
    return self._matcher.match_pre_tool(tool_name, tool_args)


# tool_executor.py：在调用工具前注入 pre_tool reminder
async def execute(self, tool_name: str, tool_args: dict) -> ToolResult:
    # 新增：pre_tool reminder 检查
    pre_reminders = self._reminder_manager.on_pre_tool(tool_name, tool_args)
    if pre_reminders:
        for r in pre_reminders:
            self._inject_reminder_to_history(r)
    
    # 原有：执行工具
    result = await self._call_tool(tool_name, tool_args)
    
    # 原有：post_tool / tool_error reminder
    ...
```

**YAML reminder 格式扩展**：

```yaml
# prompts/reminders/large_file_warning.yaml
name: large_file_read_warning
trigger_event: pre_tool          # 新增触发时机
condition:
  tool_name: read_file
inject_as: user
body: |
  提示：读取文件前建议先用 bash wc -l 确认行数。
  大文件（>1000行）直接读取会占满 context 窗口，
  建议只读取需要的部分。
deduplicate_in_turn: true        # 同一 turn 内不重复注入
```

**涉及文件**：`reminders/manager.py`、`reminders/matcher.py`、`tool_executor.py`、`prompts/reminders/`（新增示例 reminder）

---

## 四、改进计划（P2 优先级）

> 阶段 B：核心具身能力，需要较多新代码但有明确接入点

### B1. 本体感知模块（ProprioceptionModule）

**问题**：Agent 对自身状态的认知是外部的、被动的——token 超阈值才压缩，`max_turns` 到了才停，连续失败了没有内部信号。

**具身来源**：本体感知——不靠眼睛看，直接感受自身状态。

**实现位置**：新增 `perception/proprioception.py`，接入 `agent.py` 主循环

**架构归属**：每个 SessionAgent 实例持有一个 `ProprioceptionModule` 实例（非全局单例，因为多用户下每个 session 独立）。

```python
# perception/proprioception.py

@dataclass
class AgentInternalState:
    cognitive_load: float        # context 填充率 × 0.6 + 压缩次数 × 0.2 + 工具调用深度 × 0.2
    uncertainty: float           # 基于 LLM 输出语言特征（"我不确定"、"可能"等词频）
    risk_perception: float       # 涉及写操作 / 不可逆操作时升高，成功后下降
    energy_budget_ratio: float   # 剩余 token 预算 / 当前 turn 已消耗比率
    frustration: float           # 连续失败的指数衰减累积（成功后迅速降低）


class ProprioceptionModule:
    """
    Agent 的本体感知——每个 SessionAgent 实例持有，非全局单例。
    在 agent.py 的每个 turn 开始前调用 sense()，影响当轮行为决策。
    """
    
    def __init__(self) -> None:
        self._frustration_accumulator: float = 0.0
        self._consecutive_failures: int = 0
        self._recent_tool_outcomes: list[bool] = []   # 最近 10 次工具调用结果
    
    def sense(self, agent: "Agent") -> AgentInternalState:
        """快照当前状态，每 turn 调用一次。O(1) 操作，不做 LLM 调用。"""
        return AgentInternalState(
            cognitive_load=self._calc_cognitive_load(agent),
            uncertainty=self._calc_uncertainty(agent),
            risk_perception=self._calc_risk(agent),
            energy_budget_ratio=self._calc_budget_ratio(agent),
            frustration=self._frustration_accumulator,
        )
    
    def record_tool_outcome(self, success: bool) -> None:
        """工具执行后调用，更新内部状态。"""
        self._recent_tool_outcomes = (self._recent_tool_outcomes + [success])[-10:]
        if not success:
            self._consecutive_failures += 1
            # 指数积累：每次失败 +0.2，但不超过 1.0
            self._frustration_accumulator = min(
                1.0, self._frustration_accumulator + 0.2
            )
        else:
            self._consecutive_failures = 0
            # 成功后快速衰减：乘以 0.5
            self._frustration_accumulator *= 0.5
    
    def _calc_cognitive_load(self, agent) -> float:
        ctx = agent.context_builder
        # 复用 token_counter 的已有能力
        fill_ratio = ctx.estimated_fill_ratio() if hasattr(ctx, "estimated_fill_ratio") else 0.5
        compress_count = getattr(agent, "_compress_count", 0)
        tool_depth = len(getattr(agent, "_current_tool_stack", []))
        return min(1.0, fill_ratio * 0.6 + min(compress_count / 5, 1.0) * 0.2 + min(tool_depth / 10, 1.0) * 0.2)
    
    def _calc_uncertainty(self, agent) -> float:
        # 基于最近 LLM 输出中不确定词频（可从 agent._last_assistant_text 分析）
        uncertainty_words = ["不确定", "可能", "也许", "应该", "我猜", "unclear", "might", "maybe"]
        last_text = getattr(agent, "_last_assistant_text", "") or ""
        count = sum(1 for w in uncertainty_words if w in last_text)
        return min(1.0, count * 0.15)
    
    def _calc_risk(self, agent) -> float:
        # 基于最近工具调用是否涉及写操作
        risky_tools = {"write_file", "str_replace_editor", "bash", "patch_file", "delete_file"}
        recent_tools = getattr(agent, "_recent_tool_names", [])[-5:]
        risky_count = sum(1 for t in recent_tools if t in risky_tools)
        return min(1.0, risky_count * 0.25)
    
    def _calc_budget_ratio(self, agent) -> float:
        max_turns = getattr(agent.config, "max_turns", 50) or 50
        current_turn = getattr(agent, "_turn_count", 0)
        return max(0.0, 1.0 - current_turn / max_turns)
```

**接入 agent.py 主循环**：

```python
# agent.py
class Agent:
    def __init__(self, cfg, ...):
        ...
        self._proprioception = ProprioceptionModule()
    
    def run_turn(self, user_msg: str) -> TurnResult:
        # 每轮开始前感知内部状态
        state = self._proprioception.sense(self)
        
        # 行为调节：根据内部状态调整决策
        if state.uncertainty > 0.6:
            # 在 system prompt 补充元认知提示
            self._inject_metacog_hint("当前任务存在不确定性，优先确认意图再执行。")
        
        if state.frustration > 0.5 and self._proprioception._consecutive_failures >= 3:
            # 主动汇报困境，不盲目重试
            return self._request_user_guidance(state)
        
        if state.cognitive_load > 0.85:
            # 主动触发历史压缩
            self._history_manager.force_compress()
        
        # 原有：执行 LLM 调用 + 工具调用
        result = self._run_turn_inner(user_msg)
        
        # 更新本体感知
        self._proprioception.record_tool_outcome(result.success)
        
        # 写入 traces.jsonl（复用已有 observability 基础设施）
        self._tracer.record_internal_state(state)
        
        return result
```

**与 daemon 层的联动**：`traces.jsonl` 记录每轮的 `AgentInternalState`，Phase G 扫描时可以分析 `frustration` / `cognitive_load` 的历史趋势，识别"某类任务系统性地让 Agent 感到挫败"这类信号，反馈到 `capability_map`。

**涉及文件**：新增 `perception/proprioception.py`、修改 `agent.py`、修改 `perception/observability.py`（扩展 traces 字段）

---

### B2. Lesson → Reminder 自动闭环

**问题**：Lesson Memory 积累的经验只能影响未来 LLM 的"语言层面认知"，无法转化为"行动前的自动警示"。人类的经验不只改变知识，还会形成条件反射——下次遇到同类情况前，自动触发预防行为。

**具身来源**：前馈控制 + 经验内化——重复的经验不只是被记住，而是被内化为前馈模式。

**实现方案**：在 Phase G 扫描时，识别"高质量、高频触发"的 lesson，自动生成 reminder 草稿并写入 `activity_digest.jsonl` 待用户确认，或对 `human_feedback` 来源的 lesson 直接激活。

```python
# evolution/phase_g.py 或新增 evolution/lesson_to_reminder.py

class LessonToReminderBridge:
    """
    扫描 lesson memory，将达到阈值的 lesson 转化为 reminder。
    在 Phase G 扫描时触发（passive tick 周期）。
    """
    
    # 转化阈值
    HUMAN_FEEDBACK_THRESHOLD = 1    # human_feedback 来源：1 次即可生成
    SELF_REFLECTION_THRESHOLD = 3   # self_reflection 来源：出现 3 次才生成
    
    def scan_and_generate(
        self,
        memory_store: MemoryStore,
        reminder_dir: Path,
    ) -> list[GeneratedReminder]:
        """扫描 memory，生成 reminder YAML 草稿，返回生成列表。"""
        candidates = []
        for entry in memory_store.list(entry_type="lesson"):
            threshold = (
                self.HUMAN_FEEDBACK_THRESHOLD
                if entry.source == "human_feedback"
                else self.SELF_REFLECTION_THRESHOLD
            )
            if entry.occurrence_count >= threshold:
                # 检查是否已经有对应的 auto-generated reminder
                if not self._already_has_reminder(entry, reminder_dir):
                    candidates.append(self._generate_reminder_draft(entry))
        return candidates
    
    def _generate_reminder_draft(self, lesson: MemoryEntry) -> GeneratedReminder:
        """
        从 lesson 生成 reminder YAML 草稿。
        触发工具和触发时机由 lesson 的 trigger_category 推断：
          - tool_error 类 lesson → pre_tool reminder（执行前警示）
          - pattern 类 lesson → pre_user reminder（用户发消息时注入）
        """
        trigger_event = "pre_tool" if "tool_error" in (lesson.trigger_categories or []) else "pre_user"
        
        yaml_content = f"""# Auto-generated from lesson {lesson.entry_id}
# Source: {lesson.source}, occurrences: {lesson.occurrence_count}
name: auto_{lesson.entry_id[:8]}
trigger_event: {trigger_event}
inject_as: user
confidence: {min(1.0, lesson.occurrence_count * 0.2):.1f}
source_lesson_id: {lesson.entry_id}
body: |
  {lesson.body}
"""
        return GeneratedReminder(yaml_content=yaml_content, lesson=lesson)
    
    def activate(self, reminder: GeneratedReminder, reminder_dir: Path) -> None:
        """将 reminder 草稿写入 prompts/reminders/ 目录并激活。"""
        filename = f"auto_{reminder.lesson.entry_id[:8]}.yaml"
        (reminder_dir / filename).write_text(reminder.yaml_content, encoding="utf-8")
        # 触发 ReminderManager 热重载（已有 hot_reload 机制）
        get_reminder_manager(self._cfg).reload()
```

**激活策略**：
- `human_feedback` 来源的 lesson：达到阈值后自动激活（写文件 + 触发热重载）
- `self_reflection` 来源的 lesson：生成草稿，写入 `activity_digest.jsonl`，用户晨报中展示，手动确认后激活

**涉及文件**：新增 `evolution/lesson_to_reminder.py`、修改 `evolution/phase_g.py`（接入扫描流程）、`reminders/manager.py`（支持 pre_tool 触发——依赖 A3）

---

### B3. Workflow 并发执行（depends_on 拓扑分析）

**问题**：`workflow/runner.py` 虽然已有 `depends_on` 字段，但仍然是**严格串行**执行，没有利用 DAG 结构识别可并行的步骤。

**实现位置**：`workflow/runner.py`

**实现方案**：拓扑排序得到并行层（batches），每层内的步骤通过 `TaskManager` 并发执行：

```python
# workflow/runner.py

class WorkflowRunner:
    
    def _compute_parallel_batches(
        self, steps: list[WorkflowStep]
    ) -> list[list[WorkflowStep]]:
        """
        拓扑排序：将步骤分组为"并行层"（同一层内步骤互无依赖）。
        
        示例：
          step_a（无依赖）
          step_b（无依赖）     →  batch 0: [step_a, step_b]
          step_c（依赖 a, b）  →  batch 1: [step_c]
          step_d（依赖 c）     →  batch 2: [step_d]
        """
        step_map = {s.id: s for s in steps}
        in_degree = {s.id: len(s.depends_on) for s in steps}
        ready = [s for s in steps if in_degree[s.id] == 0]
        batches = []
        completed = set()
        
        while ready:
            batches.append(list(ready))
            next_ready = []
            for step in ready:
                completed.add(step.id)
                for other in steps:
                    if step.id in other.depends_on:
                        in_degree[other.id] -= 1
                        if in_degree[other.id] == 0 and other.id not in completed:
                            next_ready.append(other)
            ready = next_ready
        
        return batches
    
    def run(self, workflow: WorkflowDef) -> WorkflowResult:
        batches = self._compute_parallel_batches(workflow.steps)
        results = {}
        
        for batch in batches:
            if len(batch) == 1:
                # 单步骤：直接执行（保持现有逻辑，不引入线程开销）
                step = batch[0]
                results[step.id] = self._run_step_with_gate_retry(step, results)
            else:
                # 多步骤：通过 TaskManager 并发执行
                batch_results = self._run_batch_concurrent(batch, results)
                results.update(batch_results)
        
        return WorkflowResult(step_results=results)
    
    def _run_batch_concurrent(
        self, steps: list[WorkflowStep], prior_results: dict
    ) -> dict:
        """
        将一个批次的步骤提交给 TaskManager 并发执行。
        复用 TaskManager 已有的 SubAgent 并发基础设施。
        """
        from mini_agent.orchestrator.task_manager import TaskManager
        
        task_manager = TaskManager(self._agent, max_workers=len(steps))
        tasks = []
        for step in steps:
            task = task_manager.create_task(
                description=step.prompt,
                context=self._build_step_context(step, prior_results),
                agent_profile=step.agent_profile,
            )
            tasks.append((step.id, task))
        
        task_manager.run_all()
        
        return {
            step_id: task.result
            for step_id, task in tasks
        }
```

**注意事项**：
- `evaluator` 步骤仍然串行执行（evaluator 语义要求它在被评估步骤之后运行）
- 并发步骤共享 `prior_results` 但不互相写入，不存在数据竞争
- 并发上限受 `ResourceArbiter` 控制，不超过全局并发预算

**涉及文件**：`workflow/runner.py`、`workflow/schema.py`（确认并发相关字段）

---

### B4. 余裕感知层（AffordanceMap）✅ 已实现

**问题**：`ProjectScanner` 生成"这里有什么"的描述性快照，缺少"这里对我意味着哪些行动机会"的语义层。

**具身来源**：Gibson 的余裕理论——生物感知的不是客观属性，而是环境提供的行动可能性。

**实现位置**：新增 `perception/affordance_analyzer.py`，接入 session 启动流程

**架构归属**：session 开始时（`SessionAgentPool._create_entry()` 完成后），作为 `system_extra` 的一部分注入，不在每次 turn 时重新计算。

```python
# perception/affordance_analyzer.py

@dataclass
class AffordanceMap:
    """当前环境为 Agent 提供的行动可能性地图（session 开始时构建一次）"""
    
    # 立即可行的行动机会
    known_issues: list[str]          # open_threads 中的已知问题
    testable_modules: list[str]      # 有测试框架、可被测试的模块
    
    # 能力边界相关
    unexplored_areas: list[str]      # capability_map 低置信度区域
    high_risk_zones: list[str]       # 近期有失败历史的操作区域
    
    # 优先行动建议
    top_opportunities: list[str]     # 综合排序后的 top 3 行动机会
    
    def to_system_prompt_fragment(self) -> str:
        """格式化为注入 system prompt 的文本块。"""
        lines = ["## 当前环境行动可能性"]
        if self.known_issues:
            lines.append(f"- 已知待解决问题：{', '.join(self.known_issues[:3])}")
        if self.unexplored_areas:
            lines.append(f"- 能力盲区（建议谨慎）：{', '.join(self.unexplored_areas[:2])}")
        if self.high_risk_zones:
            lines.append(f"- 高风险区域（需额外确认）：{', '.join(self.high_risk_zones[:2])}")
        if self.top_opportunities:
            lines.append("- 当前最值得关注：")
            for opp in self.top_opportunities[:3]:
                lines.append(f"  · {opp}")
        return "\n".join(lines)


class AffordanceAnalyzer:
    """
    交叉分析：项目结构 + 历史经验 + open_threads + capability_map
    → 行动可能性地图
    """
    
    def analyze(
        self,
        project_snapshot: dict,
        lesson_memory: MemoryStore,
        workdir_knowledge,
        capability_map: dict,
    ) -> AffordanceMap:
        known_issues = self._extract_known_issues(workdir_knowledge)
        testable = self._find_testable_modules(project_snapshot)
        unexplored = self._find_unexplored(capability_map)
        risky = self._find_risky_zones(lesson_memory)
        
        # 综合打分，得出 top_opportunities
        opportunities = self._rank_opportunities(
            known_issues, testable, unexplored
        )
        
        return AffordanceMap(
            known_issues=known_issues,
            testable_modules=testable,
            unexplored_areas=unexplored,
            high_risk_zones=risky,
            top_opportunities=opportunities[:3],
        )
```

**接入点**：在 `api/server.py` 或 `SessionAgentPool._build_session_cfg()` 中，session 创建后调用 `AffordanceAnalyzer.analyze()`，将结果追加到 `session_cfg.system_extra`。

**涉及文件**：新增 `perception/affordance_analyzer.py`、修改 `api/session_pool.py`

---

## 五、改进计划（P3 优先级）

> 阶段 C：深层具身能力，需要较大的架构扩展，在 P1/P2 稳定后推进

### C1. AgentSelfModel——三个 Profile 概念的语义澄清与聚合 ✅ 已实现

**问题**：代码库中存在三个命名相近但职责完全不同的"profile"概念，导致可读性混乱：

```
UserProfile (profile.py)
  → 主语：用户，跨项目，LLM 自动生成技术栈/习惯画像
  → 路径：~/.agent/users/<user_id>/profile.json

RoleProfileManager (api/user_store.py)  
  → 主语：多用户角色，项目级，记录关系/信任等级/社交画像
  → 路径：<project>/.agent/users/<user_id>/profile.json

AgentProfile (orchestrator/agent_profiles.py)
  → 主语：SubAgent 角色定义，描述工具集/模型/系统提示
  → 与前两者完全不同，是"配置模板"而非"画像记录"
```

**实现方案**：不做破坏性重命名，而是引入 `AgentSelfModel` 作为**聚合视图**，明确语义分工：

```python
# perception/self_model.py（新增）

@dataclass  
class AgentSelfModel:
    """
    Agent 自身状态的聚合视图，供 context_builder 注入 system prompt。
    
    与其他三个 profile 的关系：
      - 不替代它们，而是在 session 开始时从它们读取数据并聚合
      - 生命周期：session 级别（不同于 UserProfile 的跨 session 持久化）
      - 更新频率：每次 turn 更新 internal_state，其余慢变量按 Phase G 周期更新
    """
    
    # 来自 Phase G 扫描（慢变量，daemon 层维护）
    strong_areas: list[str]      # capability_map 高置信度领域
    weak_areas: list[str]        # capability_map 低置信度领域
    recent_evolution: list[str]  # 最近 Phase G 发现的能力变化摘要
    
    # 来自 ProprioceptionModule（快变量，每 turn 更新）
    internal_state: AgentInternalState | None = None
    
    # 来自 session 初始化（session 级别）
    active_skill_names: list[str] = field(default_factory=list)
    
    def to_system_prompt_fragment(self) -> str:
        """格式化为注入 system prompt 的自我认知块。"""
        lines = ["## 我的能力认知"]
        if self.strong_areas:
            lines.append(f"- 擅长：{', '.join(self.strong_areas[:3])}")
        if self.weak_areas:
            lines.append(f"- 需谨慎的领域：{', '.join(self.weak_areas[:2])}")
        if self.recent_evolution:
            lines.append(f"- 最近发现：{self.recent_evolution[0]}")
        return "\n".join(lines)
```

**涉及文件**：新增 `perception/self_model.py`、修改 `context_builder.py`（注入 AgentSelfModel）

---

### C2. 时间加权记忆激活 ✅ 已实现（阶段 D，实现方式见文首说明）

**问题**：`memory.jsonl` 所有条目地位平等，高质量的旧经验和低质量的新经验被同等检索，没有反映"被反复印证的知识更稳固、环境相关知识衰退更快"这一规律。

**实现位置**：`evolution/phase_g.py` 或新增 `evolution/memory_aging.py`，在 `AutonomousLoop._tick_passive()` 定期触发

```python
# evolution/memory_aging.py

def compute_temporal_weight(entry: MemoryEntry, now: float) -> float:
    """计算记忆条目的时间权重，写入条目的缓存字段。"""
    age_days = (now - entry.created_at) / 86400
    
    # 半衰期：human_feedback 来源衰减最慢，revert_record 最快
    half_life_base = {
        "human_feedback": 90,      # 90 天
        "experiment_confirmed": 60,
        "self_reflection": 30,
        "revert_record": 14,
    }.get(entry.source, 30)
    
    # 被反复印证的知识衰减更慢
    half_life = half_life_base * (1 + entry.occurrence_count * 0.3)
    
    time_decay = 0.5 ** (age_days / half_life)
    
    # 环境相关知识额外加速衰减
    env_penalty = 0.7 if getattr(entry, "environment_tags", None) else 1.0
    
    return time_decay * env_penalty


def run_memory_aging(memory_store: MemoryStore) -> None:
    """在 Phase G tick 时批量更新所有条目的 temporal_weight。"""
    now = time.time()
    for entry in memory_store.list():
        weight = compute_temporal_weight(entry, now)
        memory_store.update_weight(entry.entry_id, weight)
```

`context_builder.py` 检索 memory 时，以 `temporal_weight` 作为排序权重之一，而不是纯语义相关度。

---

### C3. 认知锚点文件——思维状态重建指南 ✅ 已实现（阶段 D）

**问题**：任务中途被打断后，下次 session 恢复时 Agent 只能从对话历史重建状态，而历史记录是"做了什么"而非"在想什么"。

**具身来源**：环境中的身份留存——工人在工作台上留下便条，不是给别人看的，是给被打断后返回的自己看的。

**实现方案**：在任务被用户明确暂停（识别"先停一下"、`/stop`、`Ctrl-C` 等信号）时，主动生成认知锚点文件：

```python
# agent.py

def _save_cognitive_anchor(self, task_context: str) -> None:
    """
    在任务被打断时，生成给下次 session 恢复用的思维状态重建文档。
    内容由 LLM 生成，格式固定，不是给人类看的进展报告。
    """
    anchor_prompt = f"""
    根据当前对话历史，生成一个认知锚点文件，供下次 session 恢复时使用。
    
    格式（严格按此，不要改动标题）：
    
    ## 当时在想什么
    （描述当前正在推进的核心假设和关注点，不是已做的事）
    
    ## 为什么这么做
    （当前方向的来源：用户反馈、错误分析、还是主动探索）
    
    ## 下一步的直觉
    （不是计划列表，而是对下一步应该"感觉上"怎么走的判断）
    
    ## 未解决的疑问
    （当时没来得及验证的猜测，或者发现了但没有跟进的线索）
    
    注意：不要写"已完成的步骤列表"，那是 session 历史的职责。
    """
    
    anchor_content = self._llm.generate(anchor_prompt)
    
    # 写入 .agent/ 目录，不污染项目代码
    anchor_path = self._paths.cognitive_anchor(task_id=self._current_task_id)
    anchor_path.write_text(anchor_content, encoding="utf-8")
```

**AffordanceAnalyzer 联动**：Session 开始时，`AffordanceAnalyzer` 检查是否存在认知锚点文件，将其内容优先注入 session 的 context 作为"恢复记忆"。

---

### C4. 自维护模块（SelfMaintenanceModule）✅ 已实现（阶段 D，stale_tools 检测方式见文首说明）

**问题**：Agent 被动响应工具失效、skill 过时等问题，没有主动感知和修复自身健康的机制。

**具身来源**：Varela 的自创生——生物体能主动维持自身边界。

**实现位置**：新增 `evolution/self_maintenance.py`，接入 `AutonomousLoop._tick_passive()`

```python
# evolution/self_maintenance.py

class SelfMaintenanceModule:
    
    def health_check(self, paths, skill_loader, lesson_memory) -> HealthReport:
        return HealthReport(
            # 最近 N 天未被成功调用的工具（排查是否 API 变更）
            stale_tools=self._check_tool_health(paths),
            # skill 文件修改时间 vs 对应代码模块修改时间
            stale_skills=self._check_skill_freshness(skill_loader, paths),
            # memory.jsonl 中互相矛盾的条目（同一 trigger_category 结论相反）
            conflicting_lessons=self._check_memory_conflicts(lesson_memory),
        )
    
    def generate_repair_suggestions(self, report: HealthReport) -> list[str]:
        """生成修复建议文本（不自动执行，写入晨报待用户确认）"""
        suggestions = []
        for tool in report.stale_tools:
            suggestions.append(f"工具 `{tool}` 最近 30 天未成功调用，建议验证是否仍可用。")
        for skill in report.stale_skills:
            suggestions.append(f"Skill `{skill}` 可能已过时（对应代码模块有更新），建议审查。")
        for pair in report.conflicting_lessons:
            suggestions.append(f"发现矛盾经验：{pair[0]} vs {pair[1]}，建议人工判断保留哪条。")
        return suggestions
```

健康检查结果写入 `activity_digest.jsonl`（`type="health_report"`），用户下次连接时在晨报中看到，不自动修复——**维护主动感知，保留人类控制权**。

---

## 六、行为级测试框架

> 覆盖上述所有具身改进的验证方式——修改后的感知必须通过真实任务体验验证

**实现位置**：新增 `tests/behavior/` 目录

```python
# tests/behavior/test_proprioception.py

class TestProprioception(unittest.TestCase):
    
    def test_frustration_accumulates_on_failure(self):
        """连续工具调用失败，frustration 应该累积"""
        module = ProprioceptionModule()
        for _ in range(3):
            module.record_tool_outcome(success=False)
        state = module.sense(mock_agent())
        self.assertGreater(state.frustration, 0.4)
    
    def test_frustration_decays_on_success(self):
        """成功后 frustration 应该快速衰减"""
        module = ProprioceptionModule()
        for _ in range(3):
            module.record_tool_outcome(success=False)
        module.record_tool_outcome(success=True)
        state = module.sense(mock_agent())
        # 成功一次后应该降至 50% 以下
        self.assertLess(state.frustration, 0.4)


# tests/behavior/test_lesson_source.py

class TestLessonSource(unittest.TestCase):
    
    def test_human_feedback_detection(self):
        """识别用户消息中的直接纠正信号"""
        detector = HumanFeedbackDetector()
        self.assertTrue(detector.detect("不对，应该用 patch_file", []))
        self.assertTrue(detector.detect("下次记得先跑测试再提交", []))
        self.assertFalse(detector.detect("帮我写一个函数", []))
    
    def test_human_feedback_lesson_higher_priority(self):
        """human_feedback 来源的 lesson 应该有更低的 promote_threshold"""
        human_lesson = create_lesson(source="human_feedback")
        self_lesson = create_lesson(source="self_reflection")
        self.assertLessEqual(
            human_lesson.promote_threshold,
            self_lesson.promote_threshold,
        )


# tests/behavior/test_workflow_parallel.py

class TestWorkflowParallel(unittest.TestCase):
    
    def test_independent_steps_run_concurrently(self):
        """无依赖的步骤应该被识别为同一批次并并发执行"""
        workflow = WorkflowDef(steps=[
            WorkflowStep(id="a", depends_on=[]),
            WorkflowStep(id="b", depends_on=[]),
            WorkflowStep(id="c", depends_on=["a", "b"]),
        ])
        runner = WorkflowRunner(mock_agent())
        batches = runner._compute_parallel_batches(workflow.steps)
        
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)   # a, b 并行
        self.assertEqual(len(batches[1]), 1)   # c 串行
    
    def test_evaluator_steps_stay_serial(self):
        """evaluator 步骤不应该与其前置步骤并行"""
        # evaluator 总是依赖被评估步骤，所以自然在不同批次
        ...
```

> **实施说明（阶段 D 已完成时一并核对）**：未按上述伪代码单独建立
> `tests/behavior/` 目录。核对后发现现有测试文件已经在 `tests/` 顶层
> 覆盖了同等粒度的行为断言，与本节示例一一对应：
> - `test_frustration_accumulates_on_failure` / `_decays_on_success`
>   → `tests/test_proprioception.py`（B1，已实现）
> - `test_human_feedback_detection` / `_lesson_higher_priority`
>   → `tests/test_lesson_to_reminder.py` + A2 相关测试（human_feedback
>   分组"1 次即激活" vs self_reflection 需达门槛，语义等价于
>   "更低 promote_threshold"）
> - `test_independent_steps_run_concurrently` / `_evaluator_steps_stay_serial`
>   → `tests/test_workflow_parallel.py`（B3，已实现）
>
> 阶段 D 新增的四项也遵循同一模式——落在 `tests/` 顶层而非独立
> `tests/behavior/` 子目录：`tests/test_intent_action_mapper.py`（17 例）、
> `tests/test_memory_aging.py`（10 例，含 MemoryStore 端到端排序验证）、
> `tests/test_cognitive_anchor.py`（12 例）、`tests/test_self_maintenance.py`
> （22 例）。不新建平行目录的理由：项目现有测试组织方式本来就是"一个
> 模块一个 test_xxx.py"，额外建一个 `tests/behavior/` 目录收纳同粒度的
> 断言只是换了个位置放同样的东西，没有带来新的验证价值，反而增加了
> "同一个功能测试分散在两处"的维护成本。

---

## 七、实施路线图

> **状态**：阶段 A/B/C 全部已完成；C2/C3/C4 的实际接入点与下方路线图
> 描述有出入（详见文首"实施进度更新"），但立项依据（依赖关系）基本成立。

```
阶段 A（P1，当前可开始）  估计 1-2 周
│
├── A1. Connected REPL 完整命令对等
│     cli/daemon.py 扩展 DaemonClient + 命令分发
│     依赖：无（现有 HTTP API 已就绪）
│
├── A2. Lesson source 区分（human_feedback）
│     perception/lesson_rules.py + agent.py
│     依赖：无
│
└── A3. Reminder pre_tool 触发时机
      reminders/manager.py + tool_executor.py
      依赖：无

阶段 B（P2，A 阶段完成后）  估计 2-4 周
│
├── B1. ProprioceptionModule
│     新增 perception/proprioception.py
│     接入 agent.py 主循环 + traces.jsonl
│     依赖：A3（pre_tool 触发机制）
│
├── B2. Lesson → Reminder 自动闭环
│     新增 evolution/lesson_to_reminder.py
│     接入 phase_g.py 扫描流程
│     依赖：A2（human_feedback 来源字段）+ A3（pre_tool 触发）
│
├── B3. Workflow 并发执行
│     workflow/runner.py 拓扑分析 + TaskManager 桥接
│     依赖：无（独立改进）
│
└── B4. AffordanceMap
      新增 perception/affordance_analyzer.py
      接入 SessionAgentPool._create_entry()
      依赖：B1（内部状态，影响高风险区域判断）

阶段 C（P3，B 阶段稳定后）  估计 4-6 周
│
├── C1. AgentSelfModel 命名澄清 + 聚合视图
│     新增 perception/self_model.py
│     依赖：B1（ProprioceptionModule）+ B4（AffordanceMap）
│
├── C2. 时间加权记忆激活
│     新增 evolution/memory_aging.py
│     接入 AutonomousLoop._tick_passive()
│     依赖：A2（source 字段区分影响权重）
│
├── C3. 认知锚点文件
│     agent.py + storage/paths.py 新增路径
│     依赖：B4（AffordanceAnalyzer 联动）
│
└── C4. 自维护模块（SelfMaintenanceModule）
      新增 evolution/self_maintenance.py
      接入 AutonomousLoop._tick_passive()
      依赖：无

贯穿所有阶段
└── 行为级测试（tests/behavior/）
      每个阶段同步补充对应的行为测试用例
      验收标准：对应功能的核心行为有测试覆盖
```

---

## 八、与现有自主化档位的协同

具身改进不是独立于 Stage 9 之外的，而是为三个档位的**决策质量**提供基础：

| 自主化档位 | 具身改进提供的支撑 |
|-----------|-----------------|
| `passive`（已有）| A2 让 Phase G 扫描的 lesson 质量更高；A3 让 cron job 触发的警示更精准 |
| `maintenance`（目标档）| B1 让 ObjectiveExecutor 的步骤推进有实时质量感知；B2 让历史经验自动转化为前馈保护 |
| `autonomous`（长期目标）| B4 给软目标 derive 提供行动可能性地图；C1 给自主决策提供准确的自我能力认知 |

**核心逻辑不变**：在感知能力不足时提升自主化档位，等于"失去触觉的义肢"——能动但无法感受后果。阶段 A/B 完成前，`autonomous` 档位保持关闭。

---

## 九、关键设计原则（与 v2 的差异点）

1. **接入点精确化**：每个改进都明确到具体文件和函数，不再泛泛说"接入 `agent.py` 主循环"
2. **多用户架构适配**：ProprioceptionModule 是 per-SessionAgent 实例，不是全局单例；AffordanceMap 在 `SessionAgentPool._create_entry()` 时构建
3. **SelfMessageBus 已就绪**：v2 把 Self ↔ Session 通信列为计划项，实际上 Phase 4 已实现基础设施，新的消息类型（如 `profile_update` 消费端、`approval_req` 传递）可以直接扩展
4. **保留人类控制权**：自维护模块生成**建议**而不是自动执行；Lesson → Reminder 的 `self_reflection` 来源需要用户确认激活；认知锚点文件由 LLM 生成但写入用户可见目录
5. **不重新发明**：时间加权接入已有 traces.jsonl；Workflow 并发桥接已有 TaskManager；Reminder 自动生成复用热重载机制

---

*本文档基于当前代码库（多用户 Phase 1-4 全部完成，Stage 9 daemon 化已落地）的真实状态编写，每个改进方向均对应到具体代码文件和接入点。*

*前身文档：`embodied_agent_design.md`（理论框架）、`embodied_agent_improvement_plan_v2.md`（旧版计划，部分已实现或架构已变）。*
