# Workflow 机制改进计划（P7）：看板集成 + 可控性护栏 + 可定制 / 可扩展补齐

> 状态：**设计稿，未开始任何代码修改**，需要确认后再动手。
> 这是对现有 `workflow机制改进计划.md`（P1-P6，已实现：断点续跑、暂停/取消、
> 人工审批门、Step 类型化、模板库）的延续，编号延续为 **P7**。
> 范围收敛为四项，按"投入产出比 + 风险"排序，**建议按顺序做，可以只做前 N 项**：
> 1. 看板集成（REST API + Streamlit Tab）——纯组装，不动执行引擎，风险最低
> 2. 可控性护栏（预算上限 / dry-run 预览 / 单步编辑续跑）
> 3. 可定制性（workflow 级默认配置 / 可复用 step 片段）
> 4. 可扩展性（Step Executor 插件化）——长期投资，短期不迫切
>
> 文档风格延续 `next_doc/four_priority_improvements_design.md`：每项定位到
> 具体文件/函数，说明与现有基础设施的复用关系，不悬空设计。

---

## 总体依赖关系

```
①看板集成 REST API ──┬──→ ①看板集成 Streamlit Tab（依赖 REST API 先落地）
                     │
②可控性护栏（预算/dry-run/单步编辑）── 独立，可与①并行
                     │        └─ 单步编辑续跑 需要①的 Tab 作为交互入口，
                     │           其余两项（预算护栏、dry-run）与①无关，可先做
③可定制性（defaults块/可复用片段）── 独立，纯 schema.py + store.py 改动
④可扩展性（Step Executor 插件化）── 独立，纯 executors.py 改动
```

**建议实施顺序：①REST API → ①Tab → ②预算护栏/dry-run → ②单步编辑续跑 → ③ → ④**

---

## 一、看板集成：REST API

### 1.1 问题现状

`workflow/tools.py` 里已经有一整套成熟的工作流控制逻辑（`run_workflow` /
`get_workflow_run_status` / `pause_workflow_run` / `cancel_workflow_run` /
`approve_workflow_step` / `reject_workflow_step` / `resume_workflow_run` /
`list_workflow_runs`），但这些都是**注册给主 Agent 的对话内工具**（`@tool`
装饰器，`register_workflow_tools(cfg)`），只能通过 LLM 在聊天里调用。

看板（`apps/mini_agent_kanban/`）走的是完全独立的一套 HTTP API
（`src/mini_agent/api/routes.py` + `apps/mini_agent_kanban/client.py`），
现状是 `client.py` 里没有任何 `workflow` 相关方法，`routes.py` 里也没有
`/v1/workflow*` 路由——两边完全没打通。

好消息是：`workflow/tools.py` 里的逻辑本身就是"薄封装"（真正的状态都在
`WorkflowSession`、`workflow/registry.py` 的进程内 `ControlState`、
`WorkflowStore` 里），REST 端点只需要重新封装一层，**不用碰
`runner.py` / `executors.py` 的任何执行逻辑**。

### 1.2 新增路由（`src/mini_agent/api/routes.py`）

比照现有 `/v1/cron/jobs` 这组路由的写法（鉴权用 `_require_owner(request)`，
`http_server = getattr(request.app.state, "http_server", None)` 取 bridge）：

```
GET    /v1/workflows                     列出已保存的工作流
                                          → WorkflowStore(project_root).list_all()
GET    /v1/workflows/{name}               查看 YAML 定义
                                          → WorkflowStore.export_yaml(name)
POST   /v1/workflows/{name}/run           启动一次执行
                                          Body: {"inputs": {...}, "background": true}
                                          → 直接复用 workflow/tools.py::run_workflow()
                                            里的分支逻辑（前台/后台/强制后台判断），
                                            但不经过 @tool 包装，直接调用
                                            WorkflowRunner(cfg).run(...)
GET    /v1/workflow_runs                  列出所有执行记录
                                          → AgentPaths.list_workflow_session_ids()
                                            + WorkflowSession.load() 逐个汇总
                                            （比照 list_workflow_runs 工具的实现）
GET    /v1/workflow_runs/{id}             单次执行详情
                                          → WorkflowSession.load(paths, id).to_dict()
                                            补充 output_dir（P6 已加的
                                            workflow_session_output_dir()）
GET    /v1/workflow_runs/{id}/events      events.jsonl 增量拉取
                                          Query: ?since_line=N（避免每次全量读取，
                                          前端轮询用行号做增量，类似 /v1/events 的
                                          since_id 设计）
POST   /v1/workflow_runs/{id}/pause       → workflow.registry.get(id).request_pause()
POST   /v1/workflow_runs/{id}/cancel      → ...request_cancel()
POST   /v1/workflow_runs/{id}/resume      → resume_workflow_run() 同款逻辑
                                            （断点续跑，非 pause 的反向操作）
POST   /v1/workflow_runs/{id}/approve     Body: {} （用 control.pending_approval_step）
POST   /v1/workflow_runs/{id}/reject      Body: {"reason": str}
POST   /v1/workflow_runs/{id}/input       Body: {"text": str}
                                          （human_input 类型 step 送入文本，
                                          对应 provide_workflow_step_input）
```

**实现要点**：
- 这些路由应该是 `workflow/tools.py` 里对应函数体的"提取复用"，而不是重新
  写一遍逻辑——建议先把 `run_workflow` / `resume_workflow_run` 等函数体里
  "真正做事"的部分抽成 `workflow/api_helpers.py` 里的纯函数（不依赖
  `@tool` 装饰器、不依赖"返回给 LLM 看的字符串"），`tools.py` 和
  `routes.py` 两边都调用这批纯函数，各自包装成不同的返回格式（一个返回
  Markdown 字符串给 LLM，一个返回 JSON 给前端）。避免逻辑两处维护、后续
  行为不一致。
- `POST /v1/workflows/{name}/run` 的 background 语义与 `run_workflow` 工具
  一致：含 `require_approval` 步骤的工作流强制 `background=True`
  （否则前台阻塞等审批会超时）。
- 鉴权沿用现有 `_require_owner` 还是普通用户也能跑，需要产品侧确认——
  建议默认 owner-only（跟 cron jobs 一致），有明确多用户协作需求时再放开。

### 1.3 `apps/mini_agent_kanban/client.py` 新增方法

对称补一组 `AgentClient` 方法，命名和现有 `cron_jobs()` / `add_cron_job()`
风格一致：

```python
def workflows(self): ...
def workflow_yaml(self, name): ...
def run_workflow(self, name, inputs=None, background=True): ...
def workflow_runs(self): ...
def workflow_run_detail(self, run_id): ...
def workflow_run_events(self, run_id, since_line=0): ...
def pause_workflow_run(self, run_id): ...
def cancel_workflow_run(self, run_id): ...
def resume_workflow_run(self, run_id): ...
def approve_workflow_step(self, run_id): ...
def reject_workflow_step(self, run_id, reason=""): ...
def provide_workflow_input(self, run_id, text): ...
```

### 1.4 风险点

- `workflow/registry.py` 的 `ControlState` 是**进程内内存态**——如果 kanban
  和 agent 主进程本来就是同一个 daemon 进程（现状应该是这样，看板通过
  HTTP 打到同一个 `HttpServer`），这点没问题；如果未来拆成独立进程，
  pause/cancel/approve 这几个端点就需要改成读写 `WorkflowSession` 磁盘上
  的 `control_flags` 字段并轮询响应（`session.py` 里已经预留了这个字段
  和注释，说明设计时就考虑到了这个降级路径）。

---

## 二、看板集成：Streamlit "🔄 工作流" Tab

### 2.1 位置与整体结构

新增 `render_workflow_tab(client)`，参照 `render_kanban_tab()`
（`apps/mini_agent_kanban/app.py:1064`）的写法风格，在 `main()`
（约 1371 行）里加一个新 tab 页签。

### 2.2 三个区块

**① 运行面板（顶部）**
- 下拉选择已保存工作流（`client.workflows()`）
- 选中后调用 `client.workflow_yaml(name)`，用正则扫描 `{xxx}` 占位符
  （复用 `schema.py::validate()` 里 `check_placeholders` 那段
  `re.finditer(r'\{([^}]+)\}', ...)` 的思路，只取不含 `.` 的
  `{param}` 形式——那些才是需要用户填的 `inputs`），动态生成
  `st.text_input` 表单
- 「运行」按钮 → `client.run_workflow(name, inputs, background=True)`，
  拿到 `workflow_session_id` 后 `st.session_state` 记住，自动跳到下面
  的执行详情区

**② 看板视图（列 = StepStatus）**

复用 `StepStatus` 枚举（`schema.py`）本身的取值做列头，不用新造分类：
`pending / running / done / gate_failed / awaiting_approval / failed /
skipped / cancelled`（一行太多列可以合并展示为
"未开始 / 进行中 / 已完成 / 需要关注(gate_failed+failed) / 等待审批"
5 栏，具体归并规则由 UI 走查决定）。

数据来源：`client.workflow_run_detail(run_id)` → `step_results` 字典，
每个 `StepResult` 一张卡片，展示 `step_id`、耗时、评分（有的话）、
输出前 200 字预览（复用 `runner.py::to_summary()` 里现成的截断逻辑）。

`awaiting_approval` 状态的卡片额外挂两个按钮：
「✅ 批准」→ `client.approve_workflow_step(run_id)`，
「❌ 拒绝」→ `client.reject_workflow_step(run_id, reason)`（reason 用一个
内联 text_input）。

顶部工具条：⏸️ 暂停 / 🛑 取消 / ▶️ 续跑（`pause/cancel/resume_workflow_run`），
状态展示 `s.status.value`（running/paused/awaiting_approval/done/failed/
partial/cancelled）。

**轮询机制**：跟现有 `_stream_turn_into_placeholder`（app.py:638）走 SSE
不同，workflow 执行状态更适合走**定长轮询**（Streamlit 本身不擅长长连接），
建议 `st.rerun()` + `time.sleep(2)` 的简单轮询，只在 `status == "running"`
时才轮询，跑完自动停止，避免空转耗资源。

**③ 历史执行列表（折叠区）**

仿照 `render_sessions_tab()`（app.py:1023）的表格样式，
`client.workflow_runs()` 列出所有历史，点击某一行展开看 ②的详情视图
（复用同一套渲染函数，传入不同的 `run_id`）。

### 2.3 交付顺序建议

先做②（看板视图，只读，纯展示 + 轮询），验证 REST API 打通、数据展示
没问题；再做①（运行面板，会真正触发执行，风险高一点，建议先在测试
workflow 上验证背景执行 + 审批门流程完整走通）；③最后做（纯只读列表，
风险最低但优先级也最低）。

---

## 三、可控性护栏

### 3.1 预算上限（token / 成本）

**现状**：`WorkflowDef.max_total_duration` + `WorkflowWatchdog._check_resource_guard()`
（`watchdog.py:115`）只护栏"累计运行时长"，没有 token/成本维度。长
workflow 里某个 step 陷入低效重试循环（比如 `retry_on_error` 配置过大、
LLM 一直答不到点子上），只能靠时间超时兜底，但费用可能已经超支。

**设计**：
- `WorkflowDef` 新增 `max_total_tokens: Optional[int]`（沿用
  `max_total_duration` 的写法：字段 + `to_dict`/`from_dict` 序列化 +
  `cfg.workflow.max_total_tokens` 全局兜底配置）。
- `WorkflowWatchdog` 新增 `register_step_tokens(step_id, tokens_used)`，
  由 `_execute_step()`（`runner.py`）在每个 step 的 Agent 跑完后回填
  （Agent 本身应该已经有 token 用量统计，具体挂载点需要看
  `agent/lifecycle.py` 或 usage 追踪模块是否已暴露这个数字，若没有则是
  这一项的前置小任务）。
- `_check_resource_guard()` 增加 token 累计检查，超限时跟现有超时逻辑
  一样 `request_cancel()` + 记一条 `max_total_tokens_exceeded` 事件。

### 3.2 Dry-run / 执行计划预览

**设计**：`workflow/tools.py` 新增 `preview_workflow(name, inputs)` 工具
（同时也作为 REST 端点 `POST /v1/workflows/{name}/preview` 暴露给看板
"运行面板"，跑之前先点"预览"看一眼）：
- 复用 `WorkflowRunner._compute_parallel_batches(wf)` 做拓扑分层，展示
  "第几批并发跑哪些 step"
- 复用 `_resolve_prompt()` 把 `{param}` 占位符替换成用户传入的 `inputs`
  （`{step_id.output}` 这类运行时占位符替换不了，原样展示 + 标注
  "运行时决定"）
- `condition` 表达式：能静态求值的求值展示（比如纯 `inputs` 相关的
  condition），涉及 `{step_id.score}` 这类依赖运行结果的，标注
  "运行时决定，无法预览"
- 不实际调用任何 Agent/工具，只是纯计算 + 字符串拼装，零成本

### 3.3 单步编辑续跑

**现状**：`resume_workflow_run` 只能"跳过已 DONE 的 step，重跑未完成
部分"，不能"人工改一下某个已完成 step 的输出，然后让后续 step 用
改过的结果继续跑"。结合看板 Tab 看的话，这个交互（在卡片上直接编辑
文本，点"以此结果继续"）对人工把关中间产出很有价值。

**设计**：
- `WorkflowSession` 新增方法 `override_step_output(step_id, new_output)`：
  把 `step_results[step_id].output` 替换成人工编辑的文本，
  `status` 保持 `DONE`，落盘（`session.save(paths)`）
- `resume_workflow_run` 支持一个新参数 `force_rerun_from: Optional[str]`
  ——重跑时，从这个 step_id 开始之后的 step 视为"未完成"（即使原来是
  DONE），只有这个 step 自己的 output 沿用人工编辑后的版本，不重新调用
  Agent；其后续依赖它的 step 才重新执行。这样"改一步、后面全部按新
  结果重跑"的语义就完整了。
- REST 端点：`POST /v1/workflow_runs/{id}/steps/{step_id}/override`
  Body: `{"output": str, "rerun_downstream": true}`

---

## 四、可定制性

### 4.1 workflow 级默认配置

**现状**：`model` / `timeout` / `retry_on_error` / `max_turns` /
`allow_parallel` 都是 `WorkflowStep` 级别字段，每个 step 想统一改个
`max_turns` 要逐条改。

**设计**：`WorkflowDef` 新增 `defaults: dict`（`from_dict`/`to_dict`
按现有可选字段的写法处理），`WorkflowStep` 的这几个字段改为
`Optional`（去掉默认值），运行时由 `runner.py::_execute_step()`
组装 step_cfg 前做一次"继承合并"：`step.model or wf.defaults.get("model")
or self._cfg.model`，跟现有 `model=step.model or self._cfg.model`
（`runner.py:1047`）是同一个 pattern，只是中间插一层 workflow 级默认值。
**完全向后兼容**：没写 `defaults` 的旧 YAML 行为不变。

### 4.2 可复用 step 片段

**现状**：目录化 workflow（`resource_bundle.py`）已经支持本地
`agents/`、`skills/` 资源包，但没有"可复用 step 序列"的概念——比如
"质检 + 打分"这个 2-step pattern 在很多 workflow 里重复出现，只能复制
粘贴整段 YAML。

**设计**：`WorkflowStore`（`store.py`）新增 `snippets_dir`
（`.agent/workflow_snippets/<name>.yaml`，格式是一段 `steps:` 列表片段），
`WorkflowDef.from_dict` 支持步骤里出现 `include: <snippet_name>`
时，加载阶段（`store.py::load()`）展开替换为片段里的实际 steps
（注意 `depends_on`/`id` 需要做一次前缀命名空间化，避免多次
`include` 同一片段导致 `id` 冲突——比如 `snippet_id` 统一加
`{step_id}__` 前缀）。这是纯加载期的文本展开，不涉及 `runner.py`
执行逻辑改动。

---

## 五、可扩展性：Step Executor 插件化

### 5.1 现状

`executors.py:307` 的 `_EXECUTORS` 是模块级 dict，7 种类型
（`agent/role_agent/sub_workflow/tool_call/human_input/script/skill_agent`）
全部硬编码在核心包内。新增一种类型（比如"调用外部 HTTP API 的 step"、
"发飞书/邮件通知的 step"）现在只能改 `src/mini_agent/workflow/executors.py`
源码。

### 5.2 设计

- `StepExecutor` 从"模块内约定"升格为公开抽象基类
  （`from mini_agent.workflow.executors import StepExecutor`），
  文档化 `execute(step, context) -> StepResult` 接口。
- 新增公开 API `register_step_executor(type_name: str, executor: StepExecutor)`
  （类似 `tools.py` 里 `@tool` 装饰器的地位），供外部插件调用。
- `myplugins/` 约定一个 `register()` 钩子（若项目已有类似"启动时扫描
  `myplugins/` 目录并调用各模块 `register()` 函数"的机制，直接复用；
  若没有，需要先补一个轻量插件发现机制，扫描 `myplugins/*.py`，
  import 后调用模块级 `register(cfg)` 函数——与工具注册
  `register_workflow_tools(cfg)` 的调用时机保持一致，在 app 启动阶段
  统一调用）。
- `schema.py::STEP_TYPES` 元组、`validate()` 里针对每种类型的必填字段
  校验，改为允许 executor 自带 `validate_step(step) -> list[str]`，
  核心校验逻辑只跑内置 7 种类型的规则，外部类型的校验委托给对应
  executor（避免 `schema.py` 因为插件类型不断膨胀 if/else）。

### 5.3 风险点

这一项改动面最广（涉及 `schema.py` 序列化白名单、`store.py` 保存前
校验、`runner.py::_dispatch_step`），且短期没有明确的"要新增哪种 step
类型"的具体需求，建议放在最后，等前三项落地后视实际需要的自定义类型
再启动。

---

## 实施检查清单

- [x] ①-1 `routes.py` 新增 workflow REST 端点（含鉴权、错误处理）——
      实际新增 14 个端点：`GET /workflows`、`GET /workflows/{name}`、
      `POST /workflows/{name}/preview`、`POST /workflows/{name}/run`、
      `GET /workflow_runs`、`GET /workflow_runs/{id}`、
      `GET /workflow_runs/{id}/events`、`POST .../pause|cancel|resume|
      approve|reject|input`、`POST .../steps/{step_id}/override`。
      鉴权沿用 `_require_owner`（owner-only，与 cron jobs 一致）。
- [x] ①-2 抽取 `workflow/tools.py` 共用逻辑到 `workflow/api_helpers.py`——
      `run_workflow`/`resume_workflow_run`/`list_workflow_runs`/
      `pause`/`cancel`/`approve`/`reject`/`provide_input` 等工具已改为
      调用 `api_helpers` 里的纯函数，`routes.py` 调用同一批函数；
      `get_workflow_run_status` 工具因输出格式（含图标/评分展示）与 REST
      JSON 差异较大，暂未合并，保留原实现（`api_helpers.get_workflow_run_detail`
      仍提供等价的结构化数据供 REST 使用）。
- [x] ①-3 `client.py` 新增对称方法（`workflows`/`workflow_yaml`/
      `preview_workflow`/`run_workflow`/`workflow_runs`/
      `workflow_run_detail`/`workflow_run_events`/`pause_workflow_run`/
      `cancel_workflow_run`/`resume_workflow_run`/`approve_workflow_step`/
      `reject_workflow_step`/`provide_workflow_input`/
      `override_workflow_step_output`）
- [x] ①-4 `app.py` 新增 `render_workflow_tab()` + main() 挂载新 tab
      （"🔄 工作流"，含运行面板/看板视图/历史执行列表三区块）
- [x] ②-1 `WorkflowDef.max_total_tokens` + watchdog token 护栏——已实现：
      `WorkflowConfig.max_total_tokens`/`WorkflowDef.max_total_tokens` 双层
      配置（wf 优先，cfg 兜底）；`WorkflowWatchdog.register_step_tokens()`
      线程安全累加，`_check_resource_guard()` 超限时与 `max_total_duration`
      一样 `request_cancel()` + 记 `max_total_tokens_exceeded` 事件；
      `WorkflowRunner._execute_with_main_agent()` 跑完后用
      `agent.stats.input_tokens/output_tokens` 回填（`_report_step_tokens()`）。
      **已知范围限制**：只统计 `agent`/`skill_agent` 类型 step（能拿到独立
      `Agent.stats` 的类型），`role_agent`/`sub_workflow`/`tool_call` 等
      类型暂不计入，见 runner.py 对应注释。
- [x] ②-2 `preview_workflow` 工具 + REST 端点（`api_helpers.preview_workflow`，
      复用 `WorkflowRunner._compute_parallel_batches` 做并发分批展示，
      `{param}` 占位符按 inputs 静态替换，`{step_id.output}` 等运行时占位符
      原样保留并标注；condition 表达式对纯 inputs 依赖的做沙箱 `eval` 求值，
      含运行期依赖的标注"运行时决定"）
- [x] ②-3 `override_step_output` + `resume_workflow_run(force_rerun_from=)`——
      `api_helpers.override_step_output` 编辑已完成 step 的输出并落盘；
      `api_helpers.resume_workflow_run(force_rerun_from=...)` 通过反向依赖图
      计算下游 step 集合，从 session 里摘掉对应 `step_results` 使其被
      runner 当作未完成重新执行；Streamlit Tab 里对应"✏️ 编辑此步骤输出
      并续跑"交互已接入。
- [x] ③-1 `WorkflowDef.defaults` + `_execute_step` 继承合并——已实现：
      `WorkflowStep.max_turns/allow_parallel/retry_on_error` 改为
      `Optional`（`None`=未显式设置），新增 `WorkflowDef.defaults: dict`；
      `WorkflowRunner._effective_step_field(step, field, hardcoded_default)`
      做"step 显式值 → wf.defaults → 硬编码兜底"三层查找，接入并发分批、
      普通异常重试、`_execute_with_main_agent`（model/max_turns/timeout）、
      `_execute_step_bounded` 硬超时、看护线程心跳注册、`skill_agent`
      Executor 共 7 处调用点。`to_dict`/`from_dict` 同步调整：`None` 不再
      写入 YAML（代表"继承"），显式写的值（即使等于硬编码默认值）会被
      保留，与"未设置"区分开——这是一处行为变化，`test_workflow_parallel.py`
      里两个断言旧语义的用例已同步更新。
- [x] ③-2 `workflow_snippets` + `include:` 展开——已实现：
      `WorkflowStore.SNIPPETS_DIR`（`.agent/workflow_snippets/<n>.yaml`）+
      `list_snippets()`/`load_snippet()`/`save_snippet()`/`delete_snippet()`；
      `_expand_includes()` 在 `_load_path()` 内、`WorkflowDef.from_dict()`
      之前对原始 dict 做纯文本级展开：片段内每个 step 的 `id` 加
      `"{include_step_id}__"` 前缀，片段内部 `depends_on` 与 prompt 占位符
      引用同步改写为加前缀后的 id；片段"入口" step 自动接上 include 条目
      自己声明的外部 `depends_on`；外部其它 step 对 include 条目 id 的引用
      （`depends_on` + prompt 占位符）改写为指向片段展开后的最后一个 step。
      `WorkflowStep.include` 字段 + `to_dict`/`from_dict` 序列化；
      `schema.py::validate()` 对 `include` 非空的 step 豁免"prompt 为空"
      校验（真正的 prompt 校验发生在展开之后）。
- [x] ④-1 `StepExecutor` 公开化 + `register_step_executor`——已实现：
      `StepExecutor` 新增 `validate_step(step) -> list[str]` 钩子（默认空
      列表）；`executors.py` 新增 `register_step_executor(type_name, executor)`
      和 `get_registered_types()`；`schema.py::validate()` 的类型合法性检查
      从写死的 `STEP_TYPES` 改为懒加载查询 `get_registered_types()`
      （容错：import 失败退回 `STEP_TYPES`），自定义类型的必填字段校验
      委托给对应 Executor 的 `validate_step()`。
- [x] ④-2 `myplugins/` 插件发现机制——已实现：新增
      `src/mini_agent/plugins.py::discover_and_register_plugins(cfg)`，
      扫描 `<project_root>/myplugins/*.py`（跳过 `_` 开头文件），逐个
      `importlib` 动态加载并调用模块级 `register(cfg)`（若存在）；单个
      插件加载/调用失败只记警告，不影响其余插件与主程序启动。接入点：
      `cli/app.py` 在 `register_workflow_tools(cfg)` 之后调用。新增示例
      插件 `myplugins/example_http_step.py` 演示
      `register_step_executor()` 用法（新增 `type: http` step 类型）。

> 本轮（2026-07，第二次迭代）完成了②可控性护栏剩余的 token 预算护栏
> （②-1），以及③可定制性、④可扩展性两大项的全部四个子项
> （③-1/③-2/④-1/④-2）。至此本文档四大项（①②③④）的全部子项均已
> 完成实现并通过 `tests/` 中 workflow 相关用例（86 个）。
