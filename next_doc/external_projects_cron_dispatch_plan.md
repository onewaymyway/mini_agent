# 外部项目 entrypoint 接入 daemon 常驻调度方案（External Projects Cron Dispatch）

- **版本**：v1.0（已实现并通过测试，见第 6 节）
- **实现摘要**（相对 v0.3 设计方案的取舍，详见第 6 节"实现记录"）：
  - 3.2 节设计的 `_fire()` 新分支 + `set_external_entrypoint_handler()`
    **未实现**——读码确认既有的 `job_runner` 分支（`_fire()` 里
    `if self._job_runner is not None: return self._job_runner.submit(job)`）
    对任意非 `goal_cycle`、未注册 local_handler 的 job 本来就会命中，
    `run_mode="external_entrypoint"` 的 job 不需要专门加一条对称分支
    就能走到 `CronJobRunner.submit()`——同一份并发/仲裁资源，代码量
    更小。`CronJob` 仍按设计新增了 `external_project`/
    `external_entrypoint` 字段。
  - 3.2 节"`CronJobRunner.submit()` 按 `run_mode` 分派 worker"按设计
    实现，落在 `_run_job_thread()` 里（拆成 `_run_message_job()` /
    `_run_external_entrypoint_job()` 两个 worker）。
  - 待确认问题 1（是否允许编辑 `ext:*` job 的 schedule）：确认当前看板
    根本没有"编辑已存在 job 的 schedule"这个入口（只有创建新 job 时
    选 schedule），所以不存在"允许/禁止编辑"的选择——按 3.4 节的精神
    在详情弹窗顶部加了一行说明，指向对应项目的 `project.yaml`。
  - 待确认问题 2（项目开关默认状态）：**已确认按"默认关闭（opt-in）"
    实现**——`RegisteredProject.enabled`/`register()` 默认值从 `True`
    改为 `False`，新注册项目需要用户在看板上手动打开"自动调度"开关
    （或 `mini-agent projects enable <name>`）后，daemon 才会开始按
    `project.yaml` 的 schedule 自动触发。
- **背景**：`external_projects/scheduler.py::run_due_entrypoints()` 从写出来
  那一刻起就没有被任何地方真正调用过——`project.yaml` 里声明的
  `schedule: "cron: ..."` 只是"声明"，daemon 常驻运行期间没有任何东西
  在驱动它。已注册的外部项目（比如 `stock_watch`）在看板上完全可见、
  健康检查/执行记录都正常，但其定时 entrypoint 从未被自动触发过；用户
  只能靠 OS 级 cron 或手动触发兜底（`stock_watch/PROJECT.md` 335 行已经
  写明了这一点）。
- **诱因排查**：用户反馈"stock_watch 看板对应的状态看板是空的，但是有
  执行记录"，排查后发现是两条独立链路（执行账本 vs `kanban_view.
  data_file`）没有联动导致的，属于另一个问题；顺带牵出了"cron 任务并没
  有执行"这个更根本的问题，即本文档要解决的。
- **定位**：这是 `external_projects_workspace_plan.md` 里"如何接入
  daemon"一节遗留的最后一块拼图，把 `scheduler.py` 已经写好的执行逻辑
  真正接进 daemon 的常驻调度体系（`evolution/cron_scheduler.py` +
  `evolution/cron_job_runner.py`），不改动 `_run_entrypoint()`/
  `EntrypointRunResult`/账本写入等既有执行细节。

---

## 1. 核心诉求（讨论过程中逐步收敛）

1. 外部项目声明的定时 entrypoint 要能被 daemon 自动触发，不再只是
   摆设的声明。
2. 触发/关闭要按**项目**为粒度提供开关（不是笼统一个全局总闸，也不是
   要做到逐 entrypoint 精细开关——项目粒度足够）：
   - 看板上每个外部项目卡片要有一个开关，控制"这个项目的定时
     entrypoint 要不要参与 daemon 自动调度"。
3. **关键澄清（本方案的核心取舍）**：外部项目里允许存在需要 agent（会
   触发 LLM 调用）的 entrypoint——外部项目设计之初就支持项目私有的
   `skills/`、`workflows/` 目录（`external_projects_workspace_plan.md`
   已落地），entrypoint 的 `cmd` 完全可以是
   `mini-agent workflow run <name> --project <source_dir>` 这样一条会
   触发 LLM 调用的命令。
   - 这类 entrypoint 的调度**必须和普通 cron job 共享同一份并发/仲裁
     资源**——原因不是"怕它跑太久"，而是 `CronJobRunner`/
     `ResourceArbiter` 那套并发闸门存在的目的本来就是"防止同一时间
     发起太多 LLM 调用、互相打架导致频繁失败"，外部项目里会调 LLM 的
     entrypoint 如果绕开这套机制单独跑，等于让这个防护出现一个缺口。
   - **为降低复杂度，本方案不做"区分 LLM/非 LLM entrypoint 走不同
     调度通道"这件事**（曾经讨论过 Lane A/Lane B 两条通道的方案，
     已放弃）——所有声明了 `schedule` 的 entrypoint，不管内部是否
     调用 LLM，统一走同一条路径、共享同一份并发资源。跑得快、不
     调 LLM 的 entrypoint 多占一个槽位排队等一下，代价可以接受，
     换来的是机制只有一套，不需要在 `project.yaml` 里新增字段区分
     "这条 entrypoint 是不是 agent 驱动的"，心智负担更低。
4. 项目开关关闭时，对应的调度记录要**真正删除**（不是仅仅
   disable），重新打开时按当前 `project.yaml` 内容重新生成——避免
   `project.yaml` 改了之后调度侧留着对不上的残影。

---

## 2. 现状代码结构（问题所在）

```
external_projects/scheduler.py
  ├── cron_matches() / _cron_field_matches() / _cron_weekday_matches()
  │     独立实现的一套 cron 表达式匹配逻辑（5 字段，支持 */,/-）
  ├── run_due_entrypoints(registry, now=None)
  │     扫描所有 enabled_only 项目 → 逐个 entrypoint 判断 cron_matches
  │     → 命中就同步调用 _run_entrypoint()（阻塞直到子进程跑完）
  │     从未被任何调用方触碰过 ← 问题根源
  └── _run_entrypoint(manifest, entrypoint, trigger, params)
        subprocess.run(shell=True, timeout=entrypoint.timeout_sec)
        跑完写账本（ledger.record_run），返回 EntrypointRunResult
        （这部分实现良好，本方案完整复用，不改动）
```

同时 `evolution/cron_scheduler.py` 已经有一套独立、更完善的 cron 调度
体系：`CronJob`（`schedule` 字段原生支持 `"cron:<5 字段表达式>"` /
`"interval:<秒数>"` 两种格式，和 `project.yaml` 里的写法直接兼容）+
`CronScheduler.tick()`（到期判断/`next_run_at` 计算）+
`CronJobRunner`（后台线程执行 + `effective_max_concurrent()` 并发槽位
+ `ResourceArbiter.gating_state()` 仲裁降级 + `reap_stale_jobs()`
watchdog 卡死回收）。

**结论**：`scheduler.py` 里 `cron_matches()`/`run_due_entrypoints()`
这一整套是重复造轮子，而且造出来之后从未接线。本方案不修这套轮子，
而是弃用它，把外部项目 entrypoint 的调度整体挪到
`evolution/cron_scheduler.py` 体系里，作为一种新的 `CronJob`
`run_mode`。

---

## 3. 方案设计

### 3.1 每个到期 entrypoint = 一条真实的、持久化的 CronJob

daemon 启动时（以及项目开关重新打开时，见 3.3），为项目 manifest 里
**每一个声明了 `schedule` 的 entrypoint**，注册一条：

```python
CronJob(
    id=f"ext:{project_name}:{entrypoint_key}",   # 命名空间前缀，跟 sys:* 平级
    name=f"[外部项目] {project_name}/{entrypoint_key}",
    schedule=entrypoint.schedule,                 # 原样取 project.yaml 的声明
    run_mode="external_entrypoint",               # 新增第三种 run_mode
    external_project=project_name,                # 新增字段
    external_entrypoint=entrypoint_key,            # 新增字段
    task_template="",                              # 不使用
    tags=["external_project", project_name],
    enabled=True,
)
```

`schedule` 格式直接复用——`project.yaml` 写的 `"cron: 45 15 * * 1-5"`
和 `cron_scheduler.py::compute_next_run()` 已经支持的
`"cron:<expr>"` 格式完全兼容（只是中间有无空格的区别，
`_next_cron()` 内部会 `strip()`，不需要额外转换）。

这样带来的好处：**不需要在 `scheduler.py` 里维护一份独立的 cron 表达式
解析/到期扫描逻辑**——到期判断、`next_run_at` 计算、`tick()` 主循环
全部交给 `CronScheduler` 原有实现，一行都不用重新发明；
`run_due_entrypoints()`/`cron_matches()` 这套可以整体废弃。

### 3.2 `_fire()` 新增第三条分支，`CronJobRunner` 解耦"并发保护"与"执行动作"

`CronScheduler._fire()` 目前是三选一的优先级链
（`goal_cycle` 分支 → `_local_handlers` → `job_runner` → 旧的
`submit_fn`），新增一条与 `goal_cycle` 完全对称的分支：

```python
if job.run_mode == "external_entrypoint":
    if self._external_entrypoint_fn is None:
        return False
    return self._external_entrypoint_fn(job)
```

`set_external_entrypoint_handler()`（对齐现有 `set_goal_cycle_handler()`
写法）在 daemon 启动时注册一次。

**关键点在 `_external_entrypoint_fn` 内部要做什么**——不能自己另开一条
线程/信号量，必须真正复用 `CronJobRunner` 已有的并发保护
（`effective_max_concurrent()` / `ResourceArbiter.gating_state()` /
`reap_stale_jobs()`），否则"和普通 cron 共享调度资源"就是一句空话。
需要给 `CronJobRunner.submit()` 做一次小幅泛化：目前它内部硬编码
"槽位到手后 → `executor.run_job(job, ...)`（当一轮 agent 对话处理）"，
改成按 `job.run_mode` 分派 worker：

- `run_mode="message"` → worker 仍是现有的 `executor.run_job(...)`
  （行为完全不变）
- `run_mode="external_entrypoint"` → worker 是
  `external_projects.scheduler._run_entrypoint(manifest, entrypoint,
  trigger="daemon")`（现成实现，直接复用，不改动其内部逻辑）

两种 `run_mode` 的 job 共用同一份槽位计数、同一次
`ResourceArbiter.gating_state()` 检查、同一套 watchdog 卡死回收——不是
"神似"，是字面意义上同一个信号量。

另外，`CronJob.is_system`（判断依据是 `id.startswith("sys:")`）对
`ext:*` 前缀天然是 `False`——`CronJobRunner.submit()` 里"仅对非
`sys:` job 做仲裁检查"这条既有规则不需要改动，`ext:*` job 自动就会
被纳入仲裁检查范围，符合"这些 entrypoint 可能调 LLM，要被同样限流"
的诉求。

### 3.3 项目粒度开关：真删而不是 disable

沿用上一轮已确认的机制（`RegisteredProject.enabled` 字段 + 看板
toggle + `PATCH /external_projects/{name}/enabled` 路由），这次补充
开关背后要联动的 cron job 生命周期：

- **关闭**（`enabled=False`）：遍历该项目当前 manifest 里所有带
  `schedule` 的 entrypoint，把对应的 `ext:{name}:{entrypoint_key}`
  从 `cron_jobs.json` 里逐条**删除**（`CronScheduler.delete_job()`），
  不是打上 `enabled=False` 标记留着。
- **打开**（`enabled=True`，含项目首次注册）：按当前 manifest 内容
  重新生成这些 job。
- 两处（daemon 启动时的初始化、开关切换时）共用同一个函数
  `ensure_external_project_cron_jobs(project_name)`，做成**全量对齐**
  而不是"缺失才创建"：
  - manifest 里新增的 entrypoint → 补注册
  - manifest 里已删除/取消 schedule 的 entrypoint → 删除对应 job
  - schedule 声明有变化的 → 更新 job 的 `schedule` 字段
  - 避免 `project.yaml` 改了之后 `cron_jobs.json` 里留着对不上的
    旧残影，这一点在项目作者迭代 `project.yaml` 时尤其重要（比如
    `stock_watch` 这种还在持续加 entrypoint 的项目）。
- 项目被整体从 registry 移除（如果未来有"注销外部项目"功能）时，
  同样要清理掉它名下所有 `ext:{name}:*` job——目前 registry 没有
  "移除"功能，先记录这个联动点，等该功能出现时一并处理。

### 3.4 看板展示

`ext:*` 前缀的 job 会自然出现在现有"⏰ Cron 任务" tab（复用现有渲染，
不需要新开发 UI），但有一处需要特殊处理：**这类 job 的 `schedule` 应
以 `project.yaml` 为准，不允许在通用 cron 编辑表单里直接改**（改了
也会在下次全量对齐时被覆盖回去，体验上会很困惑）。看板渲染时按
`id.startswith("ext:")` 识别，把"编辑 schedule"这个操作换成一行提示
"该任务的调度由项目自己的 project.yaml 定义，请前往对应项目目录
修改"，其余字段（`enabled`/查看执行记录/最近触发情况）不受影响，仍
可正常查看。

---

## 4. 改动清单（预告，供实现阶段核对，尚未动手）

| 层 | 改动 |
|---|---|
| `evolution/cron_scheduler.py` | `CronJob` 新增 `external_project`/`external_entrypoint` 字段；`run_mode` 新增 `"external_entrypoint"`；`_fire()` 新增对称分支；新增 `set_external_entrypoint_handler()` |
| `evolution/cron_job_runner.py` | `submit()`/`_run_job_thread()` 按 `run_mode` 分派 worker，`external_entrypoint` 分支调用 `_run_entrypoint()` 而不是 `executor.run_job()`，其余槽位/仲裁/watchdog 逻辑不变 |
| `external_projects/scheduler.py` | 废弃 `run_due_entrypoints()`/`cron_matches()`/`_cron_field_matches()`/`_cron_weekday_matches()`（保留 `_run_entrypoint()`/`trigger_run()` 不变）；新增 `ensure_external_project_cron_jobs(project_name, registry, cron_scheduler)` 全量对齐函数 |
| `external_projects/registry.py` | `set_enabled()` 调用方（API 路由）联动调用上面的对齐函数 |
| `api/routes.py` | 新增 `PATCH /v1/external_projects/{name}/enabled`，内部调用 `registry.set_enabled()` + `ensure_external_project_cron_jobs()`（关闭时走"删除"分支） |
| `api/server.py` | daemon 启动流程里对所有已注册项目调用一次 `ensure_external_project_cron_jobs()`（同构其它 `ensure_xxx_job()` 的注册模式） |
| `apps/mini_agent_kanban/client.py` | 新增 `set_external_project_enabled(name, enabled)` |
| `apps/mini_agent_kanban/app.py` | 项目卡片加 toggle；"⏰ Cron 任务" tab 对 `ext:*` job 隐藏/替换"编辑 schedule"操作 |

---

## 5. 待确认问题

1. **看板改 `ext:*` job 的 schedule 时的处理方式**：倾向禁止编辑 +
   提示跳去 `project.yaml`（3.4 节），而不是允许改（会被下次对齐
   覆盖，体验差）。
2. **项目开关默认状态**：新注册/已注册项目默认 `enabled=True`（这次
   改完 `stock_watch` 会立刻按 cron 开始自动跑），还是默认
   `enabled=False`（opt-in，需要用户去看板手动打开）？

以上两点不影响核心架构，实现前请明确告知倾向，我会据此实现，不再
额外确认。

---

## 6. 实现记录（v1.0）

### 6.1 改动清单（对照第 4 节预告逐项核对）

| 层 | 实际改动 |
|---|---|
| `evolution/cron_scheduler.py` | `CronJob` 新增 `external_project`/`external_entrypoint` 字段（`to_dict`/`from_dict` 同步）；新增 `upsert_external_entrypoint_job(job_id, name, schedule, external_project, external_entrypoint, tags)`——存在则原地更新 schedule/name（重算 `next_run_at`，不动 `enabled`/`run_count` 等运行时状态），不存在则新建，`enabled=True`。**未新增** `run_mode="external_entrypoint"` 专属 `_fire()` 分支/`set_external_entrypoint_handler()`（见上方"实现摘要"，既有 `job_runner` 分支已覆盖） |
| `evolution/cron_job_runner.py` | `_run_job_thread()` 拆成分派入口：`run_mode="external_entrypoint"` → `_run_external_entrypoint_job()`（加载 manifest/entrypoint，调用 `external_projects.scheduler._run_entrypoint()`，`trigger="daemon"`；项目被禁用/manifest 解析失败/entrypoint 已被删除时静默跳过本次触发，不抛异常影响其它 job）；`run_mode="message"`（默认）→ `_run_message_job()`（原有逻辑原样平移，行为不变）。槽位获取/释放/token 记账/watchdog 判活在分派之外统一处理，两条 worker 共用 |
| `external_projects/scheduler.py` | 新增 `ensure_external_project_cron_jobs(project_name, registry, cron_scheduler)`：全量对齐（新增/更新 schedule/删除失效 job；项目 `enabled=False` 或注册表查无此项目时清空该项目名下所有 `ext:*` job；manifest 解析失败时保留现状，等下次对齐）。`run_due_entrypoints()`/`cron_matches()`/`_cron_field_matches()`/`_cron_weekday_matches()`/`_run_entrypoint()`/`trigger_run()` 全部保留未删除（前四个标注 DEPRECATED，供旧测试/脚本兼容，新代码不应依赖） |
| `external_projects/registry.py` | `RegisteredProject.enabled` 默认值、`register()` 的 `enabled` 参数默认值均由 `True` 改为 `False`（opt-in，见待确认问题 2） |
| `api/server.py` | daemon 启动流程（`HttpServer._build_autonomous_loop`，紧跟 `cron_scheduler` 构造之后）对 `ExternalProjectRegistry().list()` 里每个已注册项目调用一次 `ensure_external_project_cron_jobs()`；单个项目对齐失败不影响其它项目/daemon 其余启动步骤 |
| `api/routes.py` | 新增 `PATCH /v1/external_projects/{name}/enabled`（body `{"enabled": bool}`）：`registry.set_enabled()` 落盘后，若当前进程有活跃的 `cron_scheduler`（从 `request.app.state.http_server` 拿），联动调用 `ensure_external_project_cron_jobs()` 即时对齐；daemon 未运行时静默跳过，注册表状态已经生效，下次 daemon 启动时的批量对齐会自然补上 |
| `apps/mini_agent_kanban/client.py` | 新增 `set_external_project_enabled(name, enabled)`，`_patch()` 到上面的新路由 |
| `apps/mini_agent_kanban/app.py` | 外部项目卡片顶部原来的"已启用/已停用"纯展示 caption 换成一个可点的 `st.checkbox`（"自动调度"），勾选状态变化时即时调用新客户端方法并 `st.rerun()`；注册新项目成功提示里补充"默认未开启自动调度"的说明；「⏰ Cron 任务」tab 的精简卡片给 `ext:*` job 加"🗂️ 外部项目"徽标，详情弹窗顶部给 `ext:*` job 加一行 `st.info()` 提示调度权威来源是 `project.yaml`（看板本身没有"编辑已存在 job 的 schedule"入口，不存在需要额外禁用的编辑控件） |

### 6.2 测试

`tests/test_external_projects.py` 新增 3 个用例覆盖
`ensure_external_project_cron_jobs()`：注册到期 entrypoint、项目禁用时
真删、`project.yaml` 改动后的全量对齐（新增/更新 schedule/删除）；同时
修正了 2 个因 `enabled` 默认值变化而需要更新的既有断言。

验证范围：`tests/test_external_projects.py`（37 通过）+
`tests/test_cron_job_runner.py` / `test_cron_job_runner_resource_arbiter.py`
/ `test_cron_scheduler_local_handler.py` / `test_cron_scheduler_priority.py`
/ `test_cron_scheduler_reap_stale_jobs.py` / `test_cron_schedule_validation.py`
/ `test_goal_cron_bridge.py`（合计 120 通过），确认本次改动未影响既有
cron/goal-cron 调度行为。

### 6.3 已知遗留 / 未覆盖场景

- CLI `mini-agent projects enable|disable` 未联动 `ensure_external_project_cron_jobs()`
  ——CLI 是无状态的一次性进程，不知道该对齐哪个 daemon 实例持有的
  `cron_jobs.json`（`cron_jobs.json` 挂在某个 agent 的 `workdir_dir`
  下，是 daemon 侧概念，注册表本身是全局的），强行对齐还有和正在运行的
  daemon 并发写同一份 `cron_jobs.json` 的竞态风险。当前行为：CLI
  切换开关后注册表状态立即生效，`ext:*` job 的对齐推迟到 daemon 下次
  启动（或用户在看板上再切一次开关触发即时对齐）时发生。CLI 输出文案
  未来可以补一句提示，本次未改。
- 3.3 节提到的"项目从 registry 整体移除时清理 `ext:*` job"：
  `ExternalProjectRegistry` 目前没有"注销"功能（`unregister()` 只是
  从注册表删记录，不感知 cron 侧），这次也未新增该联动——维持原文档
  "先记录这个联动点，等该功能出现时一并处理"的结论。
- 未新增看板端"手动触发一次全量对齐"的按钮（比如用户怀疑 `ext:*`
  job 和 `project.yaml` 不同步时）；当前对齐时机是"daemon 启动时"和
  "开关切换时"，覆盖了文档列出的主要触发点，暂不需要额外入口。
- 2026-08-30（补记，非本文档主题范围内的关联修正）：`stock_watch` 新增
  的 `stock_analysis_ai` entrypoint（个股 AI 综合研判，会触发 LLM 调用）
  未带 `schedule`，本次不涉及本文档描述的定时调度接线；仅按本文档 §1.3
  的既有取舍——凡会调 LLM 的外部项目 entrypoint，一旦未来声明
  `schedule` 接入自动调度，天然与普通 cron job 共享同一份
  `CronJobRunner` 并发闸门，不需要为此新增分支。详见
  `next_doc/external_projects_agent_skill_workflow_integration_plan.md`
  第 2 节。
