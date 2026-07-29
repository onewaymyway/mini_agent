# Cron 任务专属执行机制 实施记录

> 对应方案：`next_doc/cron_dedicated_execution_improvement_plan.md`
> 当前状态：Track A-L 全部完成（三轮提交），核心执行链路、REST API、看板
> tab、正式配置字段、`config.json` 缺省字段实时合并、schedule 格式前置
> 校验、单元测试均已落地并通过验证。

## 第一轮：核心执行链路 + REST/看板集成

### 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/evolution/cron_job_workspace.py` | 每 job 一个文件夹（`.agent/cron_jobs/<id>/`）：`prompt.md`/`config.json`/`state.json`/`runs/*.jsonl`，负责进度持久化、prompt 渲染（`{{progress}}`/`{{#progress}}...{{/progress}}`）、执行记录读写 |
| `src/mini_agent/evolution/cron_job_executor.py` | 单次执行调度循环：墙钟超时（默认 20 分钟）+ 最大步数（默认 60）双重兜底，接入 `StuckDetector` 卡死检测 |
| `src/mini_agent/evolution/cron_agent_bridge.py` | `build_cron_agent()`：全量继承主 Agent 工具集的专用 Agent 构造；`make_submit_step_fn()`：`[CRON_DONE]`/`[CRON_CONTINUE]` 标记 + `_last_turn_hit_max_turns` 兜底的完成判定 |
| `src/mini_agent/evolution/cron_job_runner.py` | 后台线程调度器：把 cron job 执行挪出 `AgentRunner` 主线程，`threading.Semaphore` 控制并发上限，同 job 去重 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/cron_scheduler.py` | `__init__`/`_fire()`/`load_cron_scheduler()` 新增 `job_runner` 参数；注入后优先走新通道，未注入回退旧 `submit_fn` 路径；新增 `is_job_running()` |
| `src/mini_agent/api/server.py` | `_build_autonomous_loop` 构造 `CronJobRunner` 并注入 `load_cron_scheduler()`；执行完成回调里调用 `bridge.emit_cron_job_finished()` |
| `src/mini_agent/api/models.py` | 新增 `EventType.CRON_JOB_FINISHED` |
| `src/mini_agent/api/bridge.py` | 新增 `AgentBridge.emit_cron_job_finished()` |
| `src/mini_agent/api/routes.py` | 新增 5 个 REST 端点：`GET/PUT .../prompt`、`GET .../workspace`、`GET .../runs/{run_id}`、`POST .../reset` |
| `apps/mini_agent_kanban/client.py` | 新增 6 个对应客户端方法 |
| `apps/mini_agent_kanban/app.py` | 新增 "⏰ Cron 任务" tab：状态徽标、进度摘要展开、最近执行记录回放、prompt 在线编辑、`needs_human_review` 一键重置 |
| `src/mini_agent/agent/turn_loop.py` | **顺带修复**（独立 bug，非本方案范围）：`skill_activate`/`skill_deactivate` 等工具执行后让 `_cached_system` 失效，避免同一 turn 内后续 LLM 调用读到激活/卸载之前的旧 system prompt |

### 验证

- 全量 `python3 -m py_compile src/mini_agent` 通过
- 手写冒烟测试验证：正常两步完成 → 状态归 `idle`、进度清空；强制输出重复
  → `StuckDetector` 正确判定 `needs_human_review`，`progress_summary`
  保留最后一步输出

## 第二轮：正式配置字段 + 单元测试

### 新增文件

| 文件 | 作用 |
|---|---|
| `tests/test_cron_job_workspace_and_executor.py` | 23 个单元测试（见下），覆盖 workspace 和 executor 的所有关键路径 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/config/models.py` | 新增 `CronConfig` 数据类（`max_concurrent_jobs`/`default_timeout_seconds`/`default_max_steps`/`inner_max_turns`）+ `AppConfig.cron` 字段 |
| `src/mini_agent/config/loader.py` | `load_config()` 解析 `agent_config.json` 里的 `"cron": {...}` 块 |
| `src/mini_agent/evolution/cron_job_workspace.py` | `ensure()` 新增 `default_config` 参数，支持全局配置注入**首次创建**的 `config.json`（已存在的不覆盖） |
| `src/mini_agent/evolution/cron_job_executor.py` | `run_job()` 新增 `default_config` 参数并透传给 `ensure()` |
| `src/mini_agent/evolution/cron_agent_bridge.py` | `build_cron_agent()` 的 `inner_max_turns` 不传时回退到 `cfg.cron.inner_max_turns` |
| `src/mini_agent/evolution/cron_job_runner.py` | 根据 `base_cfg.cron` 构造 `default_config` 传给 executor |
| `src/mini_agent/api/server.py` | 简化为直接读取 `cfg.cron.max_concurrent_jobs`（不再需要 `getattr` 兜底） |

`agent_config.json` 可选配置示例：

```json
{
  "cron": {
    "max_concurrent_jobs": 3,
    "default_timeout_seconds": 1200,
    "default_max_steps": 60,
    "inner_max_turns": 15
  }
}
```

### 单元测试清单（`tests/test_cron_job_workspace_and_executor.py`，23 项全过）

**`TestCronJobWorkspace`（11 项）**
- `test_ensure_creates_default_files` — 首次 `ensure()` 生成三个默认文件
- `test_job_id_with_colon_maps_to_safe_dirname` — `sys:daily_digest` → 目录名 `sys_daily_digest`
- `test_ensure_does_not_overwrite_existing_files` — 用户编辑过的 prompt 不被二次 `ensure()` 覆盖
- `test_ensure_with_default_config_only_applies_on_first_creation` — `default_config` 只在首次生效
- `test_write_and_read_state_roundtrip` — state 读写往返一致
- `test_render_prompt_without_progress_strips_block` — 无进度时 `{{#progress}}` 块整体去掉
- `test_render_prompt_with_progress_keeps_block` — 有进度时正确拼接
- `test_run_events_append_and_read` — 事件流追加/读取，自动打时间戳
- `test_recent_runs_ordered_and_limited` — 按 mtime 倒序、`limit` 生效
- `test_list_all_workspaces` — 枚举所有 job 文件夹
- `test_list_all_workspaces_empty_when_no_dir` — 目录不存在时返回空列表

**`TestCronJobExecutor`（12 项）**
- `test_normal_completion_clears_progress` — 正常完成后进度清空、失败计数归零
- `test_single_step_first_call_receives_rendered_prompt` — 首步拿到渲染后的完整 prompt
- `test_continuation_receives_simple_continue_marker` — 续步拿到简短的"继续"
- `test_stuck_detector_triggers_needs_human_review` — 连续雷同输出 → `needs_human_review`
- `test_single_step_error_marks_needs_human_review` — 单步抛异常不崩溃、正确降级
- `test_result_error_marks_needs_human_review` — `StepResult.error` 字段生效
- `test_max_steps_reached_marks_timed_out_and_keeps_progress` — 触达步数上限 → `timed_out`，保留进度
- `test_timeout_deadline_stops_loop` — 墙钟超时（0 秒极端用例）在第一次检查就生效，一步都不执行
- `test_progress_resumes_across_separate_run_job_calls` — **端到端验证跨次触发续接**：第一次超时后，`render_prompt()` 能读到遗留进度
- `test_consecutive_failures_reset_after_success` — 失败后再次成功，失败计数清零
- `test_stale_running_status_increments_failure_and_still_executes` — 僵尸 `running` 状态不阻止本次继续执行
- `test_run_events_written_for_full_lifecycle` — 完整生命周期的事件类型齐全（`run_started`/`step`/`run_finished`）

### 回归验证

- `python3 -m py_compile` 全量 `src/mini_agent` 通过
- `pytest tests/test_cron_job_workspace_and_executor.py` — 23/23 通过
- 手动验证 `load_config()` 正确解析默认值与 `agent_config.json` 覆盖
- `pytest tests/ --collect-only` 确认没有引入新的模块导入错误（补齐沙箱
  缺失的 `uvicorn`/`pydantic`/`json_repair`/`fastapi`/`python-multipart`
  等依赖后，2473 个测试用例全部可正常收集）
- 抽查部分预先失败的用例（`test_skill_manager.py` 等），确认失败原因是
  `skills/__init__.py` 里 `_auto_activate_blocked` 属性名不一致的**既有
  bug**，与本次改动无关（未触碰该文件），不是回归

## 第三轮：config.json 缺省字段实时回退 + 看板 schedule 格式前置校验

对应第二轮遗留的"剩余工作 #2"和"剩余工作 #4"（原始条目见本节末尾的历史
记录）。

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/cron_job_workspace.py` | `CronJobConfig.from_dict()` 新增 `default` 参数：字段缺省时回退到传入的 `default`（不传则回退硬编码默认值，向后兼容）；`CronJobWorkspace.read_config()` 新增同名 `default` 参数并透传，JSON 文件缺失/损坏时也回退到 `default` |
| `src/mini_agent/evolution/cron_job_executor.py` | `run_job()` 里 `ws.read_config()` 改为 `ws.read_config(default=default_config)`——`default_config`（调用方根据全局 `AppConfig.cron` 构造）现在既用于 `ensure()` 首次创建，也用于每次读取时的缺省字段合并 |
| `apps/mini_agent_kanban/app.py` | 两处"新建 cron job"表单（`render_cron_jobs_tab` 和目标看板顶部的精简版）提交前调用新增的 `validate_schedule()` 做格式校验，不合法直接在表单内提示，不再发起后端请求 |

### 新增文件

| 文件 | 作用 |
|---|---|
| `tests/test_cron_schedule_validation.py` | `validate_schedule()` 的 19 项单元测试：`interval:`/`cron:` 各自的合法/非法格式、空字符串、未知前缀、首尾空白容错 |

### 新增函数

`src/mini_agent/evolution/cron_scheduler.py::validate_schedule(schedule: str) -> Optional[str]`：
纯格式校验（不校验取值范围是否有意义，比如 `cron:99 * * * *` 这种"字段
永远不命中"的情况不拦截，运行时会自然退化成"这个 job 实际跑不起来"，
留给用户在看板上观察 `next_run_at`）。合法返回 `None`，非法返回中文
错误提示字符串。`CronScheduler.compute_next_run()` 本身对不合法输入
仍保留原有的静默退化兜底（`interval:abc` → 默认 3600 秒；`cron:` 字段数
不对 → 1 小时后），不因为校验函数的存在而改变运行时容错策略——两者
分工："提交前"用 `validate_schedule()` 拦截明显错误，"运行时"继续用
原有兜底保证不崩溃。

### 验证

- `python3 -m py_compile` 全量 `src/mini_agent` + `apps/mini_agent_kanban/app.py` 通过
- `pytest tests/test_cron_job_workspace_and_executor.py tests/test_cron_schedule_validation.py` — 42/42 通过（原 23 项 + `read_config` 缺省合并新增 3 项 + `validate_schedule` 新增 19 项）
- 手动核对：`CronJobConfig(**asdict(default))` 构造新实例避免修改调用方传入的
  `default` 对象；`read_config()` 不传 `default` 时的返回值与改动前完全一致
  （回归安全）

## 剩余工作 / 后续可选项

以下均为可选的进一步优化，不影响当前功能闭环：

1. **结构化 checkpoint**：目前跨次续接靠 `progress_summary` 一段文本摘要
   （截断到 2000 字）。如果未来出现"需要精细结构化状态"的 cron 任务
   （比如"已处理到第 N 条记录"这种需要精确恢复点的场景），可以在
   `state.json` 里加一个自由格式的 `checkpoint_data: dict` 字段，由任务
   自己的 prompt 约定写入/读取格式，本轮的 `CronJobState` 数据类预留了
   扩展空间（加字段即可，向后兼容）。
2. ~~**`config.json` 热更新到已存在的 job**~~ — 第三轮已解决，见上方
   「config.json 缺省字段实时回退」。
3. **`CronJobRunner`/`cron_agent_bridge` 的集成测试**：当前单元测试覆盖
   了 `cron_job_workspace`/`cron_job_executor` 两个不依赖真实 LLM 的纯
   Python 模块；`cron_agent_bridge.build_cron_agent()`（依赖真实 Agent/
   LLM client 构造）和 `cron_job_runner.CronJobRunner`（依赖真实线程调度）
   目前只做过人工冒烟验证，建议后续用 mock LLM client 补一版集成测试，
   或者先用 `interval:60` 这类短周期 job 在真实 daemon 里跑一轮观察
   `.agent/cron_jobs/<id>/state.json` 和 `runs/*.jsonl` 是否符合预期。
4. ~~**看板"新建 cron job"表单的 schedule 格式校验**~~ — 第三轮已解决，
   见上方「看板 schedule 格式前置校验」。
