# 三项优先改进：实现方案

> 对应上一轮分析中的优先级排序：
> 1. 补齐 AffordanceMap 本地路径接入
> 2. 打通具身自我感知与用户行为感知两层
> 3. 给自我进化补上"用户真实反馈"闭环指标
>
> 本文档是**设计/实现方案**，不是最终用户文档；落地后应分别同步更新
> `docs/embodied-agent-guide.md`、`docs/behavior-perception-guide.md`、
> `docs/self-evolution-stage2-guide.md` / `evolution.md` 相关章节，并在
> `docs/commands-and-tools-reference.md` 补充新增命令/参数。

---

## 方案一：补齐 AffordanceMap 本地路径接入

### 1.1 问题现状

`AffordanceMap`（`perception/affordance_analyzer.py`）目前只在多用户 daemon 路径生效：

- 调用点：`api/session_pool.py::SessionAgentPool._create_entry()` → `_inject_affordance_map(agent, session_cfg)`
- 本地单 Agent 路径（`cli/app.py::_main_inner()` 里 `agent = Agent(cfg=cfg, ...)` 之后、`run_repl(agent, skill_loader)` 之前）**没有任何等价调用**。

`_inject_affordance_map` 现有实现依赖三样东西：
1. `agent._memory`（已构造好的 MemoryStore 实例，避免重复构造并发写同一文件）
2. `AgentPaths(session_cfg.project_root)` + `load_open_threads(paths)`
3. `affordance_cfg = session_cfg.affordance`（`AffordanceConfig`，默认 `enabled=True`）

三者在本地路径下全部具备（`agent.py::Agent.__init__` 里 `self._memory` 的构造顺序不依赖 daemon），所以移植成本很低，核心工作是**抽取共享函数**而不是重新实现。

### 1.2 设计方案

**抽取共享函数**，从 `api/session_pool.py` 私有静态方法提升为 `perception/affordance_analyzer.py` 里的公共入口，daemon 路径和本地路径都调用它，避免逻辑分叉：

```python
# src/mini_agent/perception/affordance_analyzer.py（新增函数，与现有 AffordanceAnalyzer 类同文件）

def inject_affordance_map(agent: "Agent", cfg: "AppConfig", *, log=None) -> None:
    """
    构建一次 AffordanceMap 并拼进 agent.cfg.system_extra。

    daemon 多用户路径与本地单 Agent 路径共用同一实现：
      - daemon 路径：api/session_pool.py::SessionAgentPool._create_entry() 调用
      - 本地路径：cli/app.py::_main_inner() 在 Agent() 构造完成后调用

    调用时机要求：必须在 Agent() 构造之后（复用 agent._memory，
    避免重复构造 MemoryStore 实例指向同一文件）。

    失败静默降级：感知层失败不应阻断 Agent 启动。
    """
    affordance_cfg = getattr(cfg, "affordance", None)
    if affordance_cfg is None or not getattr(affordance_cfg, "enabled", True):
        return
    try:
        from mini_agent.perception.workdir_knowledge import load_open_threads
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(cfg.project_root)
        open_threads = load_open_threads(paths)

        memory_backend = getattr(agent, "_memory", None)
        lesson_entries, capability_entries = [], []
        if memory_backend is not None and hasattr(memory_backend, "all_entries"):
            all_entries = memory_backend.all_entries()
            lesson_entries = [e for e in all_entries if getattr(e, "entry_type", "") == "lesson"]
            if getattr(affordance_cfg, "use_capability_map", True):
                try:
                    from mini_agent.evolution.phase_g import build_capability_map
                    capability_entries = build_capability_map(paths, None)
                except Exception:
                    capability_entries = []

        affordance_map = AffordanceAnalyzer().analyze(
            open_threads=open_threads,
            lesson_entries=lesson_entries,
            capability_entries=capability_entries,
        )
        fragment = affordance_map.to_system_prompt_fragment()
        if not fragment:
            return
        if getattr(affordance_cfg, "verbose", False) and log is not None:
            log.info("[AffordanceMap] %s", affordance_map.to_dict())

        target_cfg = getattr(agent, "cfg", None) or cfg
        existing = getattr(target_cfg, "system_extra", "") or ""
        target_cfg.system_extra = (existing + "\n\n" + fragment).strip()
    except Exception:
        import logging
        logging.getLogger(__name__).debug("[AffordanceMap] injection failed", exc_info=True)
```

**改造 `api/session_pool.py`**：`_inject_affordance_map` 改为薄包装，直接转调新函数（保留旧方法名以免破坏调用点，内部委托）：

```python
@staticmethod
def _inject_affordance_map(agent: Any, session_cfg: "AppConfig") -> None:
    from mini_agent.perception.affordance_analyzer import inject_affordance_map
    inject_affordance_map(agent, session_cfg, log=log)
```

**改造 `cli/app.py`**：在 `agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)` 之后、`run_repl` 之前插入：

```python
agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

# [具身改进 B4 本地路径接入] 与 daemon 多用户路径共用同一实现，
# 消除 "只在 SessionAgentPool 生效" 的已知不对称（见 docs/embodied-agent-guide.md §8）。
from mini_agent.perception.affordance_analyzer import inject_affordance_map
inject_affordance_map(agent, cfg)
```

插入点需要放在**热重载器 / skill_loader 初始化完成之后**（如果 AffordanceAnalyzer 未来要读取 skill 相关的 capability 信息），当前实现不依赖 skill_loader，所以紧跟 `Agent()` 构造之后即可，不需要等 REPL 启动。

同时需要覆盖 `--resume <session_id>` 场景：resume 时 `agent.load_session()` 会重建 history，但 `system_extra` 属于 `cfg` 层，不随 session 历史重置，走同一条注入路径即可，无需特殊处理。

### 1.3 影响范围与风险

- **不新增开关**：复用现有 `--memory`/`cfg.affordance.enabled` 逻辑（默认 `enabled=True`），本地路径行为从"从不注入"变为"和 daemon 路径行为一致"，这是修复不对称而非新增功能，预期不会有人依赖"本地模式没有 AffordanceMap"这件事。
- **性能**：`AffordanceAnalyzer().analyze()` 是纯只读分析、不调用 LLM，一次性发生在 session 构建阶段，本地路径新增的开销可忽略。
- **测试**：
  - 复用/扩展 `tests/test_affordance_analyzer.py`，新增一个用例验证 `cli/app.py` 路径下 `agent.cfg.system_extra` 包含 affordance fragment（可 mock `AgentPaths`/`load_open_threads` 直接调用 `inject_affordance_map`）。
  - 回归验证 daemon 路径行为不变（`_inject_affordance_map` 委托后行为应与改造前逐字节一致）。

### 1.4 文档同步清单

- `docs/embodied-agent-guide.md` §8：删除"本地单 Agent 路径尚未接入，是已知的不对称，留作后续"这句话，改为说明两条路径共用 `perception/affordance_analyzer.py::inject_affordance_map()`。
- `docs/commands-and-tools-reference.md`：无需新增命令（本身没有新 CLI flag）。

---

## 方案二：打通具身自我感知层与用户行为感知层

### 2.1 问题现状

两套系统数据模型完全独立：

| | 具身自我感知（`embodied-agent-guide.md`） | 用户行为感知（`behavior-perception-guide.md`） |
|---|---|---|
| 目标 | Agent 感知**自身**在数字工作环境中的状态 | 感知**用户**在桌面/浏览器/移动端的实时活动 |
| 核心模块 | `perception/affordance_analyzer.py`、`self_model.py`、`proprioception.py` | `perception/behavior/manager.py`（`BehaviorPerceptionManager`）、`events.py`（`BehaviorEventStore`）、`collectors/` |
| 数据来源 | `open_threads.json`、`capability_map`（Phase G）、lesson memory | 前台窗口/Git/终端/浏览器/移动端采集事件 |
| 触发方式 | session 开始时构建一次（慢变量） | 持续后台采集，`BehaviorEventStore.query()` 按需查询 |
| 开关 | `AffordanceConfig.enabled`（默认开） | `BehaviorConfig` 总开关默认关，各子采集器默认关 |

AffordanceAnalyzer 目前只交叉分析 `open_threads`/`capability_map`/`lesson memory`，完全不读取 `BehaviorEventStore` 里的实时用户状态，导致"环境对 Agent 呈现的行动可能性"这一具身核心概念，缺了"用户当前状态"这一维度——例如：用户刚在另一个终端手动改过同一个文件、用户当前处于"专注编码"状态还是"来回切换应用心不在焉"，这些信息如果存在，AffordanceMap 完全感知不到。

### 2.2 设计方案：单向只读桥接，不合并两套系统

**原则**：不把两套系统合并成一套（职责边界仍然清晰：一个是 Agent 自感知，一个是用户行为采集），而是让 AffordanceAnalyzer **可选地**读取 `BehaviorEventStore` 的**只读快照**，作为第四路输入。全程遵循两个既有设计约束：

1. **默认关闭**：behavior perception 总开关默认关闭是刻意的隐私边界，跨层读取必须同样默认关闭，不能因为打通就变相绕开这层保护。
2. **失败静默降级**：behavior perception 未启用/不可用时，AffordanceAnalyzer 分析路径必须完全不受影响（等同于该输入源为空）。

**新增配置字段**（`config/models.py::AffordanceConfig`）：

```python
@dataclass
class AffordanceConfig:
    enabled: bool = True
    use_capability_map: bool = True
    verbose: bool = False
    # [新增] 是否交叉分析用户行为感知层的实时状态（默认关闭，双重开关）：
    #   仅当 BehaviorConfig.enabled 与本字段同时为 True 时才生效。
    use_behavior_context: bool = False
```

**新增桥接函数**（`perception/affordance_analyzer.py`，或拆到新文件 `perception/affordance_behavior_bridge.py` 保持单一职责）：

```python
def _load_behavior_context(cfg: "AppConfig", *, window_minutes: int = 30) -> Optional["BehaviorContext"]:
    """
    只读查询 BehaviorEventStore 最近 window_minutes 分钟内的活动，
    压缩为一个轻量摘要供 AffordanceAnalyzer 使用。

    双重开关：behavior_cfg.enabled 与 affordance_cfg.use_behavior_context
    必须同时为 True，任一为 False 直接返回 None（等同于该输入源缺失）。
    """
    behavior_cfg = getattr(cfg, "behavior", None)
    affordance_cfg = getattr(cfg, "affordance", None)
    if not (getattr(behavior_cfg, "enabled", False) and getattr(affordance_cfg, "use_behavior_context", False)):
        return None
    try:
        from mini_agent.perception.behavior.manager import BehaviorPerceptionManager
        mgr = BehaviorPerceptionManager.get_instance(cfg)  # 复用已有单例，不新建采集线程
        events = mgr.query(since_minutes=window_minutes)
        return _summarize_behavior_events(events)
    except Exception:
        return None


@dataclass
class BehaviorContext:
    """交叉分析用的最小摘要，只保留"是否与当前工作有潜在冲突/呼应"相关的字段。"""
    recent_git_touched_paths: list[str] = field(default_factory=list)   # 用户近期在其他终端 commit/checkout 触碰的路径
    recent_terminal_commands: list[str] = field(default_factory=list)   # 近期 shell 命令（用于判断用户是否已手动做过类似操作）
    is_actively_engaged: bool = False   # 前台窗口/idle 信号推导的"用户当前是否专注在相关工作"
    context_switch_count: int = 0       # window_minutes 内应用切换次数（心不在焉程度的粗略代理）
```

**接入 `AffordanceAnalyzer.analyze()`**：新增一个可选参数 `behavior_context: Optional[BehaviorContext] = None`，在生成 `to_system_prompt_fragment()` 时，若存在且 `recent_git_touched_paths` 与当前 `open_threads` 涉及路径有重叠，追加一句"用户最近在其他地方改过 X，建议先确认最新状态再继续"；若 `context_switch_count` 很高，追加"用户当前可能不在专注状态，非紧急事项可降低打扰优先级"这类提示。**只做加法**（追加新的行动建议/提示句），不改变现有三路（open_threads/capability_map/lesson）的既有输出结构，向后兼容。

**调用方改造**（`inject_affordance_map`，即方案一里抽出的共享函数）：

```python
behavior_context = _load_behavior_context(cfg)
affordance_map = AffordanceAnalyzer().analyze(
    open_threads=open_threads,
    lesson_entries=lesson_entries,
    capability_entries=capability_entries,
    behavior_context=behavior_context,   # 新增，可能为 None
)
```

### 2.3 为什么不做成"实时联合决策"

分析中提到的"Agent 感知到用户当前在物理/数字世界的状态 + 自身能力边界后联合决策"，如果做成每轮 turn 实时查询行为事件流，会有两个问题：

1. AffordanceMap 现有设计是**慢变量**（session 开始构建一次），而行为事件是持续变化的**快变量**，强行做成逐 turn 联合决策需要打破现有慢/快变量分层，改动面过大、且和 `self_model.py` 里"慢变量只在 session 开始时构建"的既定设计哲学冲突。
2. 高频读取 `BehaviorEventStore` 会增加隐私敏感数据被 system prompt 携带的暴露窗口。

因此本方案采用**折中**：session 开始时做**一次性**的只读交叉分析（与 AffordanceMap 本身的更新粒度对齐），不追求逐 turn 联合决策。如果未来确有需求，可以复用 `PrivacyGuard` 的占位符机制，对行为摘要中的敏感字段（如具体命令行参数）做脱敏后再拼进 prompt。

### 2.4 测试与文档同步清单

- 新增 `tests/test_affordance_behavior_bridge.py`：
  - `behavior.enabled=False` 时 `_load_behavior_context` 必须返回 `None`（不触发任何 `BehaviorPerceptionManager` 调用）。
  - `affordance.use_behavior_context=False`（默认）时同样必须返回 `None`，即使 behavior 总开关已开。
  - 双开关都为 `True` 时验证摘要字段正确性与 fragment 追加逻辑。
- `docs/embodied-agent-guide.md` §8：补充"可选交叉分析用户行为感知层"小节，明确默认关闭 + 双开关设计。
- `docs/behavior-perception-guide.md`：补充一节说明 `BehaviorEventStore` 现在多了一个只读消费方（AffordanceAnalyzer），澄清"读取方增加不代表采集范围扩大，采集开关完全独立不受影响"。
- `docs/commands-and-tools-reference.md`：`/behavior` 命令表格新增 `use_behavior_context` 相关的启动参数说明（若新增 CLI flag，如 `--affordance-use-behavior`）。

---

## 方案三：给自我进化补上"用户真实反馈"闭环指标

### 3.1 问题现状

现有验证链路（`evolution/validators.py` T0~T3、`evolution/eval_runner.py`）比较的都是**过程指标**：schema 校验、lint/类型检查、单测、eval 场景对比（tool 失败率/turns/token）。这些指标衡量的是"这次自我修改有没有引入明显的技术性回归"，但没有任何指标衡量"这次修改是否真的解决了它声称要解决的问题"——即触发这次 skill_propose/self-evolution 的那个 lesson，在修改落地之后，是否真的不再高频出现。

`perception/lesson_rules.py::LessonRuleEngine.observe()` 已经在持续记录"人类反馈纠正检测"事件；`evolution/state_repo.py::StateRepo.log()` 已经能拿到每次 self-evolution commit 的元信息（commit_id、risk_tier、时间戳）。两者目前没有关联起来。

### 3.2 设计方案：迟滞窗口回填 + revert 建议

**核心思路**：不改变现有 T0~T3 验证流水线（那是 merge 前的门槛，应保持不变），而是在 commit **落地之后**新增一个独立的、异步的"效果回填"机制。

#### 3.2.1 数据模型扩展

在 `StateRepo` 现有的 commit 元信息基础上，新增一个平行的追踪文件 `.agent/evolution/outcome_tracking.json`（避免直接改动 git commit 元信息结构，保持 T3 治理红线不受影响——回填数据本身属于 T0 级只读统计，不需要走受保护路径判定）：

```json
{
  "tracked_commits": [
    {
      "commit_id": "abc1234",
      "trigger_lesson_group_id": "lg_xxx",
      "committed_at": 1720000000,
      "baseline_trigger_count": 5,
      "baseline_window_days": 14,
      "observation_window_days": 14,
      "observation_deadline": 1721210000,
      "status": "observing",
      "post_trigger_count": null,
      "verdict": null
    }
  ]
}
```

字段说明：
- `baseline_trigger_count` / `baseline_window_days`：commit 之前 N 天内，该 lesson group 的触发次数（作为基线，来自现有 `lesson_review.py` 已有的 LessonGroup 统计能力，直接复用不重新实现）。
- `observation_window_days`：commit 之后观察多少天（默认与 baseline 对齐，取 14 天，可配置）。
- `status`：`observing` → `resolved`（观察期已结束，已生成 verdict）。
- `verdict`：`improved`（触发次数显著下降，阈值可配，如下降 ≥ 50%）/ `no_change` / `worsened`（触发次数不降反升）。

#### 3.2.2 新增模块 `evolution/outcome_tracker.py`

```python
"""
evolution/outcome_tracker.py — 自我进化"用户真实反馈"闭环指标

职责：
  1. record_commit_baseline(commit_id, lesson_group_id)
     —— 在一次 self-evolution commit 完成后调用，记录基线数据。
  2. tick()
     —— 由 Phase G 或 AutonomousLoop 的周期性维护调用，检查所有
        status=="observing" 且已到 observation_deadline 的记录，
        重新查询该 lesson_group 当前触发计数，计算 verdict。
  3. get_revert_candidates()
     —— 返回 verdict=="worsened" 的记录列表，供 /evolution 命令展示
        为"建议 revert"提示（不自动执行 revert，只提示，最终决策权
        留给用户 —— 与现有 /evolution revert 是显式手动命令的设计一致）。
"""
```

**与既有模块的接口关系**：
- 触发次数统计：直接调用 `perception/lesson_review.py` 里现有的 LessonGroup 聚合逻辑（`total_occurrence` 字段），不重新实现一套计数器。
- commit 完成回调：在 `evolution/eval_runner.py` 或 `state_repo.py::commit()` 成功返回后，如果这次 commit 的来源是某个 lesson group（`skill_propose`/`evolution-agent` 触发路径已经知道 `lesson_group_id`），调用 `record_commit_baseline()`。
- 周期性检查：挂到 `evolution/phase_g.py` 已有的周期性维护 tick 里新增一步 `outcome_tracker.tick()`，复用现有的节奏治理（`phase_g_rhythm.json`）机制，不新增独立的调度器。

#### 3.2.3 用户可见的变化

**`/digest` 输出新增一类事件**（复用 `evolution/autonomous_loop.py::_record_digest()` 已有的 digest 记录机制）：

```
💡 效果回填：commit abc1234（修复"忘记先 git status 就直接 commit"）
   观察期（14天）已结束：该问题触发次数从 5 次/14天 降至 1 次/14天，判定为 improved。
```

```
⚠️ 效果回填：commit def5678（修复"文件路径拼接错误"）
   观察期已结束：该问题触发次数从 3 次/14天 升至 6 次/14天，判定为 worsened，
   建议复核：/evolution show def5678 | /evolution revert def5678
```

**`/evolution` 命令新增子命令** `/evolution outcomes`：

```
| 命令 | 说明 |
|------|------|
| `/evolution outcomes` | 列出所有效果回填记录（observing / improved / no_change / worsened） |
| `/evolution outcomes --worsened` | 只看判定为 worsened、建议复核 revert 的记录 |
```

`worsened` 判定只产生**建议**，不自动触发 revert——这与项目里 SoftGoalDeriver 的"derive 出的 Goal 需要 `/goals accept`/`reject` 显式处理"是同一套设计哲学：自动化到"提出建议"为止，最终决策权留给人。

#### 3.2.4 边界情况与降级策略

- **该 lesson group 在观察期内被用户手动通过 `/evolution revert` 撤销**：`outcome_tracker` 应监听（或在 `tick()` 时顺带检查）commit 是否已被 revert，若是则将 `status` 直接置为 `resolved`、`verdict` 置为 `reverted_by_user`，不再等待观察期结束（因为观察已经没有意义）。
- **观察期内 lesson group 本身因为 30 天无触发被判定过时**（复用现有 `lesson_review.py` 的过时标记机制）：`post_trigger_count` 记为 0，按"improved"处理（触发次数降为 0 本身就是最强的正面信号）。
- **基线数据不足**（比如该 lesson group 触发次数本身就很少，baseline 只有 1-2 次，样本太小不足以做统计判断）：低于某个最小基线阈值（如 `MIN_BASELINE_COUNT = 3`）时，`verdict` 直接标记为 `insufficient_data`，不参与 revert 建议展示，避免小样本噪声误导用户。
- **失败静默降级**：`outcome_tracker.tick()` 内部任何异常都不应阻断 Phase G 主流程，与项目里其它感知层模块（AffordanceAnalyzer 等）保持一致的"失败不阻断"原则。

### 3.3 测试与文档同步清单

- 新增 `tests/test_outcome_tracker.py`：覆盖 baseline 记录、tick 判定三种 verdict、reverted_by_user 提前终止观察、insufficient_data 边界。
- `docs/self-evolution-stage2-guide.md` 或新建 `docs/self-evolution-outcome-tracking-guide.md`：说明 `outcome_tracking.json` 数据结构、`/evolution outcomes` 命令、与现有 T0~T3 验证流水线的关系（"T0~T3 是 merge 前门槛，outcome tracking 是 merge 后的迟滞效果回填，两者互补不冲突"）。
- `docs/commands-and-tools-reference.md`：`/evolution` 命令表格补充 `outcomes` 子命令。
- `README.md` 文档索引：视新建文档情况补一条索引项。

---

## 三项方案的依赖关系与建议实施顺序

1. **方案一（AffordanceMap 本地接入）无依赖**，改动范围最小（一个新函数 + 两处调用点改造），建议最先做，也是验证"共享函数抽取"这个重构方式是否顺手的试金石。
2. **方案二（打通两层感知）依赖方案一**：因为方案二是在方案一抽出的 `inject_affordance_map()` 基础上加第四路输入，如果先做方案二、后做方案一，会导致新逻辑要同时写两份（daemon 路径 + 本地路径），返工成本更高。
3. **方案三（效果回填）相对独立**，不依赖前两者，可以并行开发，但建议排在最后落地，因为它涉及新的持久化数据结构和周期性调度改造，风险面比前两者大，适合在前两者验证过"改动节奏"之后再动。
