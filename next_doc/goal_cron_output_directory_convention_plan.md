# Goal/Cron 周期性执行的产出目录规范设计方案

> 状态：**设计草案 / 待评审**（尚未实施——本文档先把方案定下来，评审通过后
> 再按 Track 拆解实施，遵循既有的"每个 Track 完成后更新文档 + 跑回归"节奏）
> 触发背景：用户反馈"cron 任务/goal 任务在工作目录的输出不够规范，尤其是
> 周期性执行的 goal，多次执行之间怎么传递进度信息"，希望先把目录结构和
> 传递机制想清楚，再动代码。

## 0. 现状盘点

先说清楚"现在已经有什么、还缺什么"，避免重复造轮子。

**已经有的（`.agent/` 内部状态，看板/调度器自己用，不是给用户看的交付物）**：

| 已有机制 | 位置 | 解决的问题 |
|---|---|---|
| `CronJobWorkspace`（`cron_job_workspace.py`） | `.agent/cron_jobs/<job_id>/` | 每个 cron job 的 prompt 模板、限制配置、状态机、逐步事件流（`runs/<ts>.jsonl`） |
| `CronJobState.progress_summary` | `.agent/cron_jobs/<job_id>/state.json` | 一段**自由文本**摘要，通过 `{{progress}}` 占位符注入下次 prompt，是目前唯一的"跨次执行传递信息"机制 |
| `ExecutionStep.artifacts` | `.agent/objective_executions.json`（内存态，落盘于此） | **同一个** Objective 执行内，后面的 step 能看到前面 step 声明/工具调用产生的文件路径（Track G，已完成） |
| `output_path_policy.md` | `.agent/policies/output_path_policy.md` | 一份**负面清单**（不许写 `src/`、`tests/`，skill 相关放 skill 目录），用户可编辑，prompt 层注入 |
| `GoalNode.cycle_count` | `goals.json` | 周期性 Goal 已经跑了第几轮（纯计数，不含"每一轮产出在哪"的信息） |

**缺的（也就是用户这次反馈的核心）**：

1. **没有为"deliverable"（任务实际产出的文件——报告/数据/生成的代码等）
   定义一个稳定、可预测的目录**。现在全凭 agent 自己临场判断放哪，同一个
   周期性 Goal 每一轮产出可能散落在完全不同的位置，人和下一轮的 agent
   都很难找。
2. **`progress_summary` 是自由文本**，只解决"上次做到哪了"，不解决
   "上次产出的文件具体在哪个目录、文件名是什么"——下一轮 agent 想接着
   用上一轮的产出（比如"基于上周的报告，写这周的对比"）时，只能指望
   自己在自由文本里描述清楚路径，没有结构化的可靠来源。
3. **`ExecutionStep.artifacts` 只在单次执行内有效**，跨"轮次"
   （下一次 cron 触发 / 下一个 recurring Goal cycle 是全新的 Objective）
   完全断开，即便本质上是同一个 job 反复执行。

## 1. 设计目标 / 非目标

**目标**：
1. 给每个 Goal（周期性）/CronJob 的**每一轮执行**分配一个可预测、按时间
   排序、人和 agent 都能一眼定位的产出目录。
2. 让"上一轮产出了什么、在哪"这件事从**自由文本回忆**升级成**结构化的、
   可程序化读取的清单**，下一轮启动时自动摘要注入 prompt（不需要 agent
   自己翻目录）。
3. 与现有机制**兼容而不是推倒重来**：复用已经在跑的 `progress_summary`
   占位符注入路径、已经在做的 artifacts 提取（Track G）、已经存在的
   `output_path_policy.md` 注入点，只补"目录在哪 + 怎么传递"这两块空白。
4. 跨平台安全：不用符号链接（用户环境是 Windows，`E:\codes\...`），
   "最新一轮"用一个小 JSON 指针文件表达，不用文件系统 symlink。

**非目标（本轮方案不覆盖）**：
- 不做产出文件的**强制路径拦截**（延续 `output_path_policy.md` 已有的
  "只做 prompt 注入，不做 hook 硬拦截"立场，原因同前）。
- 不做旧数据迁移——已经在跑的周期性 Goal/CronJob 历史产出散落在哪就留在
  哪，本方案只对"这次改造上线之后的新一轮执行"生效，不回溯改写用户已有
  文件的位置。
- 不做产出文件的自动清理/归档策略（磁盘占用治理）——先解决"找得到"，
  "清不清理"作为独立的后续 Track（可以参考 Track D 归档 `goals.json`
  条目的思路，但这里对象是文件而不是 JSON 记录，机制不同，不在本方案
  展开）。

## 2. 目录结构设计

```
<project_root>/.agent/daemon_run_outputs/
├── goals/
│   └── <goal_id>/
│       ├── latest.json                  # 指针文件，见 §2.3
│       ├── cycle_0001/
│       │   ├── manifest.json            # 本轮产出清单，见 §2.2
│       │   └── ...agent 实际写的文件（报告/数据/代码等）...
│       ├── cycle_0002/
│       │   ├── manifest.json
│       │   └── ...
│       └── ...
└── cron/
    └── <job_id>/                        # job_id 里的 ':' 换成 '_'，与
    │                                     # CronJobWorkspace 目录命名规则一致
        ├── latest.json
        ├── run_20260807-093000/
        │   ├── manifest.json
        │   └── ...
        └── ...
```

**为什么放在 `<project_root>/.agent/daemon_run_outputs/`，不是项目根下
`outputs/`**（[已根据评审反馈调整] 最初方案倾向于放项目根一级目录，
理由是"用户真正关心的任务产出不应该藏在点号开头的隐藏目录里"；但顶层
`outputs/` 容易和用户项目里已有的同名目录（很多项目本来就有构建产物/
导出目录叫 `outputs/`）冲突，评审后改为放进 `.agent/` 内部）：
`.agent/` 目录本身仍然是"agent 自己的内部状态"这层语义没变，
但 `daemon_run_outputs` 是其中命名明确、专门装"周期性任务实际产出"的
子目录，不会和 `cron_jobs/`/`policies/` 等纯调度态目录混在一起，也不会
和用户项目已有目录冲突；看板"📂 查看产出"折叠区把它暴露出来，用户不需要
自己记路径，代价是命令行下少一层可见性，可以接受。

**为什么"Goal 周期"用 `cycle_%04d`、"CronJob 单次触发"用
`run_<timestamp>`**：两者语义不同——recurring Goal 的每一轮是有序的、
论"第几轮"更自然（且已经有 `cycle_count` 字段可以直接复用编号）；普通
CronJob（不一定绑定 Goal）触发更像离散的一次次运行，用时间戳排序更直观，
也顺便避免和 `CronJobWorkspace.new_run_id()` 已有的 `runs/<ts>.jsonl`
命名脱节——两边用同一个 `run_id` 更好对照。

### 2.1 目录归属与生命周期

- **`goals/<goal_id>/`**：对应绑定了 `recurring=True` 的 Goal（参见
  `_render_goal_card()` 里的 `🔁 周期性` 徽标）。每次 `goal_cron_bridge.
  _fire_goal_cycle()` 成功触发新一轮（而不是被 `skip_next_cycle` 跳过）时，
  分配一个新的 `cycle_%04d` 目录，编号取
  `GoalNode.cycle_count + 1`（触发前的值 +1，与"这是第几轮"的直觉对齐）。
- **`cron/<job_id>/`**：对应**没有**绑定 recurring Goal 的普通 CronJob
  （`run_mode` 不是 `goal_cycle` 的情形）。目录名沿用 `CronJobWorkspace`
  已有的 job_id 安全转义规则。
- 两者互斥：一个 CronJob 一旦是 `run_mode=goal_cycle`（绑定了 recurring
  Goal），产出目录走 `goals/<goal_id>/`，不重复在 `cron/<job_id>/` 下
  再开一份——避免用户对着两个目录找不到东西。

### 2.2 `manifest.json`（每一轮产出清单）

```json
{
  "version": 1,
  "goal_id": "goal_abcd1234",
  "cycle": 3,
  "started_at": 1754567890.0,
  "finished_at": 1754568900.0,
  "status": "completed",
  "task_summary": "本轮任务的一句话描述（取自触发时的 task_description）",
  "artifacts": [
    {"path": "weekly_report.md", "description": "本周数据对比报告"},
    {"path": "raw_data.csv", "description": "本周原始导出数据"}
  ],
  "progress_note": "已完成 3/3 步骤；下一轮建议直接对比本轮 raw_data.csv",
  "previous_cycle_dir": ".agent/daemon_run_outputs/goals/goal_abcd1234/cycle_0002"
}
```

- `artifacts` **优先复用已经在跑的 Track G 提取结果**
  （`ExecutionStep.artifacts`，来自 `_extract_tool_artifacts`/
  `_parse_step_artifacts`）——execution 结束时把该 execution 所有 step 的
  `artifacts` 去重合并写进来，**不需要新增一套产出发现机制**，只是把
  已经提取到的信息从"内存态/`objective_executions.json`"多落一份到
  这个更贴近用户、按轮次组织的位置。`description` 字段留空或做一次轻量
  LLM 摘要都可以，作为后续可选优化，不阻塞本方案落地。
- `progress_note` 直接复用 `ObjectiveExecution.progress_notes` /
  `CronJobState.progress_summary` 的既有文本，不新造一套摘要逻辑。
- `previous_cycle_dir` 让"下一轮读上一轮产出"不需要额外查表，manifest
  自己就是一条链表。

### 2.3 `latest.json`（跨平台"最新一轮"指针）

```json
{"latest_dir": "cycle_0003", "updated_at": 1754568900.0}
```

每次一轮执行收尾（无论 completed/failed/cancelled）时更新。不用符号
链接（Windows 默认不支持无权限创建），下一轮渲染 prompt 时读这个文件
拿到"上一轮目录名"，比每次扫目录取 mtime 最大值更快也更明确（失败的
半成品目录不会被误判成"最新"，因为写入时机在收尾之后）。

## 3. 传递机制：下一轮启动时怎么"看到"上一轮

复用现有的 `{{progress}}` 占位符注入路径（`CronJobWorkspace.
render_prompt()`），新增一个并列占位符 `{{previous_output}}`：

```
{{#progress}}
--- 上次执行遗留的进度 ---
{{progress}}
{{/progress}}

{{#previous_output}}
--- 上一轮产出（{{previous_output_dir}}） ---
{{previous_output}}
{{/previous_output}}

{{output_policy}}

本轮产出请写入：{{output_dir}}
```

渲染时：
1. 读 `latest.json` 拿到上一轮目录（没有则整段 `{{#previous_output}}`
   连同标记一起去掉，语义与现有 `{{#progress}}` 空块处理完全一致）。
2. 读该目录下的 `manifest.json`，把 `artifacts` 列表格式化成几行
   `- weekly_report.md：本周数据对比报告` 注入 `{{previous_output}}`，
   `previous_cycle_dir` 的绝对路径注入 `{{previous_output_dir}}`。
3. `{{output_dir}}` 注入**本轮**分配到的产出目录绝对路径——这是本方案
   里最关键的一行：**agent 不用自己判断"这次东西该放哪"，prompt 里
   明确告诉它**，`output_path_policy.md` 里第 4 条"任务本身已经说明了
   工作目录的，产出放到该工作目录下"天然覆盖到这个新场景，不用改
   policy 文件本身的规则，只是这次"任务说明"由系统自动附加，不需要
   用户每次手写。

recurring Goal 一侧（`goal_cron_bridge._fire_goal_cycle()`）目前触发时
构造的是子 Objective 的 `task_description`，不经过 `CronJobWorkspace.
render_prompt()`（那是 dedicated-execution cron 专属路径）——需要在
`_fire_goal_cycle()` 里补一段等价的"读上一轮 manifest → 拼进子 Objective
描述末尾"逻辑，与 dedicated 模式共享同一份"读取/格式化 manifest"的工具
函数，避免两处实现分叉。

## 4. 落地涉及的模块（供后续拆 Track 参考，本文档暂不实施）

| 模块 | 改动内容 |
|---|---|
| 新增 `src/mini_agent/evolution/output_workspace.py` | 新模块：`allocate_cycle_dir()`/`allocate_run_dir()`、`write_manifest()`、`read_latest_manifest()`、`format_manifest_for_prompt()`——集中管理 §2 的目录分配和 §3 的读写，`cron_job_workspace.py`/`goal_cron_bridge.py`/`objective_executor.py` 都只调用这个模块，不各自实现一遍 |
| `cron_job_workspace.py` | `render_prompt()` 增加 `{{previous_output}}`/`{{previous_output_dir}}`/`{{output_dir}}` 三个占位符，复用上面新模块 |
| `goal_cron_bridge.py` | `_fire_goal_cycle()` 触发时调用 `allocate_cycle_dir()`，把 `{{output_dir}}` 等价内容拼进子 Objective 的 `task_description` |
| `objective_executor.py` | execution 收尾（`_finish`/`cancel`/`_on_objective_failed`）时调用 `write_manifest()`，复用已有的 `step.artifacts` 汇总逻辑 |
| `output_path_policy.py` | `DEFAULT_POLICY` 追加一条第 5 条规则，说明"如果 prompt 里出现了『本轮产出请写入：』这行，以该目录为准" |
| `apps/mini_agent_kanban/app.py` | Goal/CronJob 卡片新增一个"📂 查看产出"折叠区，读 `latest.json` + 最近几轮 `manifest.json`，列出文件名（不做文件预览/下载，避免看板膨胀成文件管理器——需要的话用户直接去 `.agent/daemon_run_outputs/` 目录看） |
| `docs/goal-cron-binding-guide.md` | 补一节说明这套目录规范，供用户查阅 |

## 5. 待评审的开放问题

在开始实施前，希望先确认这几点（直接影响 §2 的具体命名/结构，改起来
成本不同，值得先定下来）：

1. ~~`outputs/` 这个顶层目录名是否合适？~~ **[已定]** 改放
   `.agent/daemon_run_outputs/`，不占用项目根目录一级命名空间，避免和
   用户项目里已有的同名 `outputs/` 目录冲突；未做成可配置项，直接采用
   这个固定路径（见 §2 开头的说明）。
2. **`manifest.json` 里的 `artifacts` 要不要做路径存在性校验？**
   即 agent 在 `[ARTIFACTS]` 里声明的路径，如果实际上传目录不在
   `.agent/daemon_run_outputs/goals/<goal_id>/cycle_000N/` 下（比如手滑
   写到别处），要不要在 manifest 里标一个 `outside_output_dir: true` 的
   提示，还是干脆不管——本方案目前倾向于**不管**（不做强制校验，只做
   记录），但想确认这是否符合预期。
3. **非 recurring 的普通一次性 Goal 要不要也套这个目录规范？**
   本方案目前只覆盖"周期性执行"（recurring Goal + CronJob），因为
   "多次执行之间传递进度"这个问题只有周期性场景才存在；一次性 Goal
   要不要也统一放 `.agent/daemon_run_outputs/goals/<goal_id>/cycle_0001/`（哪怕只有一轮）
   以保持看板"📂 查看产出"入口逻辑一致，还是保持现状（一次性 Goal 不
   套用，agent 自己找地方放）——两种都可以，想听听你的倾向。

## 6. 实施记录

**已实施**（一次性完成，未按 Track 拆分——改动范围与本文档设计一致，无需
分阶段评审）：

- 新增 `src/mini_agent/evolution/output_workspace.py`：`allocate_cycle_dir()`/
  `allocate_run_dir()`、`write_manifest()`、`read_latest_manifest()`、
  `format_manifest_for_prompt()`，集中管理目录分配和 manifest 读写。
- `cron_job_workspace.py`：`render_prompt()` 新增 `run_id` 参数，支持
  `{{previous_output}}`/`{{previous_output_dir}}`/`{{output_dir}}` 三个
  占位符 + `{{#previous_output}}` 条件块；`DEFAULT_PROMPT_TEMPLATE` 已更新。
- `cron_job_executor.py`：`run_job()` 传入 `run_id` 渲染 prompt，收尾时调用
  新增的 `_write_output_manifest()` 落 manifest（`cron/<job_id>/run_<run_id>/`）。
- `goal_cron_bridge.py`：`_fire_goal_cycle()` 新增 `_append_output_workspace_context()`，
  分配 `goals/<goal_id>/cycle_%04d/` 目录并拼进子 Objective 的 `description`。
- `objective_executor.py`：新增 `_write_output_manifest()`，在
  `_on_objective_completed`/`_on_objective_failed`/`_on_objective_cancelled`
  三处收尾方法中调用，只对绑定了 `recurring=True` 父 Goal 的子 Objective
  生效（开放问题 3 采纳"一次性 Goal 不套用"的方案）；`artifacts` 复用
  `ExecutionStep.artifacts`（Track G）去重合并写入。
- `output_path_policy.py`：`DEFAULT_POLICY` 追加第 5 条规则。
- `apps/mini_agent_kanban/app.py`：新增 `_render_goal_output_manifests()`，
  周期性 Goal 卡片新增"📂 查看产出"折叠区，复用已有 `/fs/list`/`/fs/read`
  只读接口，不新增后端路由。
- `docs/goal-cron-binding-guide.md`：新增第 10 节说明这套目录规范，第 8 节
  产出路径规范示例同步更新第 5 条规则，第 9 节已知限制改编号为第 11 节
  并追加本方案的限制说明。
- 回归：`test_cron_job_workspace_and_executor.py`/`test_goal_cron_bridge.py`/
  `test_goal_cron_feedback_and_output_policy.py` 全部通过（`test_goal_cron_bridge.py`
  里断言子 Objective `description` 精确等于 `task_template` 的旧用例已更新为
  断言"以 task_template 开头 + 包含本轮产出目录提示"，因为这正是本方案的
  预期行为变化）。`test_objective_executor_*` 系列在补齐本地测试环境缺失的
  `pydantic`/`uvicorn`/`fastapi` 依赖后，未受本方案改动影响的用例继续通过。

**后续调整**：顶层目录从最初实施时的 `<project_root>/outputs/` 改为
`<project_root>/.agent/daemon_run_outputs/`（用户反馈原顶层目录名容易和
项目里已有的 `outputs/` 目录冲突）。改动点：`output_workspace.
outputs_root()`、`output_path_policy.py` 规则 5 的提示文案、
`apps/mini_agent_kanban/app.py` 里 `/fs/read` 的 `base` 路径、以及本文档
与 `docs/goal-cron-binding-guide.md` 里的目录示例，均已同步更新；`.agent/`
目录本身默认对用户可见（看板"📂 查看产出"折叠区 + `/fs/*` 只读接口），
不需要额外的可见性补偿。

**未覆盖（开放问题的暂定结论，供后续按需调整）**：
- 顶层目录名未做成可配置项，直接固定为 `.agent/daemon_run_outputs/`
  （评审后从最初的项目根 `outputs/` 调整而来，见开放问题 1 的结论）。
- `manifest.json` 里的 `artifacts` 不做路径存在性校验。
- 一次性 Goal 不套用本目录规范。
- 不做旧数据迁移、不做自动清理/归档策略（与"非目标"一节保持一致）。
