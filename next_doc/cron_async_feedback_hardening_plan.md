# Cron 异步用户反馈机制加固方案

> 状态：**已实施完成**（D1–D6 全部 5 个阶段均已落地，回归测试全量通过）
> 前置文档：`next_doc/cron_async_user_feedback_mechanism_plan.md`（已实施完成，
> 本文档针对该机制在真实运行场景下暴露出的问题做加固，不改变其对外行为契约，
> 只修正实现层面的正确性/健壮性缺陷）
> 关联现有代码：`src/mini_agent/notification/questions_store.py`、
> `src/mini_agent/evolution/cron_job_workspace.py`、
> `src/mini_agent/evolution/cron_job_executor.py`、
> `src/mini_agent/evolution/cron_context.py`、
> `src/mini_agent/tools/ask_user_async.py`、
> `src/mini_agent/external_input/filelock.py`（复用其 `ExclusiveFileLock`）

## 0. 背景

对已实施完成的 `cron_async_user_feedback_mechanism_plan.md` 做复查（结合
`cron_job_runner.py` 的真并发调度模型一起看），发现 6 个实际场景下会触发的
缺陷。本文档逐一定方案，按阶段实施，每阶段完成后回填"实现记录"并更新
状态。

## 1. 问题清单与对应方案

| 编号 | 问题 | 触发场景 | 方案 |
|---|---|---|---|
| D1 | `questions_store.py` 读-改-写无锁、非原子 | `cron_job_runner.py` 支持 `max_concurrent_jobs>1`，多个 job 线程 + API 请求线程并发操作同一份 `cron_questions.jsonl`，"读全部→改→整体覆盖写"会互相踩踏丢更新 | 复用 `external_input/filelock.py::ExclusiveFileLock`（项目里已有的同款场景先例：`pending_hits.jsonl`），把 `submit_answer`/`dismiss_question`/`mark_answers_consumed`/`append_question` 的读改写整体包进锁；写文件改用 `atomic_write_jsonl` |
| D2 | 答案的 `consumed` 标记在 `render_prompt()` 渲染时就打上，早于 `submit_step_fn()` 实际调用/成功 | 该次触发第一步 LLM 调用异常（网络错误等）导致 `final_status=STATUS_NEEDS_REVIEW`，用户的回答已被标记消费但 agent 从未真正看到 prompt 内容 | 把"标记 consumed"的时机从 `render_prompt()` 内部挪到 `CronJobExecutor.run_job()` 里——`render_prompt()` 只返回本次要注入的 question_ids（不再在渲染时刻打标记），`run_job()` 确认第一步 `submit_step_fn()` 成功返回（未抛异常、`result.error` 为空）之后才调用 `mark_answers_consumed()` |
| D3 | 忽略（dismiss）后问题对 agent 完全不可见，无法区分"从未问过"与"用户明确不想回答"，导致忽略-重问死循环 | 用户忽略问题 X 后，agent 下次触发到同一节点措辞变体后重新问出 X | 新增 `{{dismissed_questions}}` 占位符，`render_prompt()` 渲染最近一批（默认 20 条，可配置）已忽略问题原文，提示 agent "用户已明确表示不需要回答，不要再问" |
| D4 | 去重是问题文本精确匹配，LLM 复述措辞天然多变，去重基本失效 | 同一语义问题每次触发都换个说法问出来，看板被同语义问题刷屏 | `find_pending_by_fingerprint` 增加一层轻量模糊匹配兜底：规范化（去标点/空白/大小写）后用 `difflib.SequenceMatcher` 算相似度，达到阈值（默认 0.82，可配置）也判定为重复；精确匹配优先，模糊匹配只做兜底，避免语义完全不同但巧合相似的文本被误合并（保留原精确匹配路径不变，模糊匹配是新增的第二道判定） |
| D5 | `cron_questions.jsonl` 只增不减，`_load_all()` 全量线性扫描，且 job 被删除后遗留问题永久孤儿 | 长期运行后文件线性增长，每次工具调用开销上升；用户删除 cron job 后其问题记录无人清理 | 新增 `archive_old_records()`：把超过保留期（默认 90 天，可配置）的 `answered`/`dismissed` 记录归档到 `cron_questions.archive.jsonl`（同目录，供审计但不参与日常读取），从主文件移除；`CronJobExecutor`/cron job 删除入口调用处新增按 `job_id` 清理挂钩 `purge_questions_for_job()` |
| D6 | watchdog 判定卡死并回收槽位后，孤儿线程可能在"该次 run 已被判定结束"之后才执行到 `ask_user_async`，产生时序上让人困惑的"迟到"问题 | `cron_job_runner.py` 的 `reap_stale_jobs()` 场景 | `cron_context.py` 增加"运行代次令牌"（generation token）：`run_job()` 开始时连同 `job_id` 一起设置一个唯一 token；`ask_user_async` 写入问题时把当时读到的 token 一并存入记录的 `run_token` 字段（仅用于审计标记，不做写入拦截——拦截需要 `ask_user_async` 反查 runner 当前合法 token，跨模块耦合成本高，本轮先做"事后可识别"，不做"事前拦截"）；`append_question` 时若能判定 `job_id` 对应的 job 已不存在于当前 `CronScheduler` 配置中，则仍正常写入但打上 `orphaned=true`，看板过滤/置灰展示，不再作为常规通知渠道打扰用户（复用 D5 的清理机制最终归档掉） |

## 2. 非目标

- 不改变 `ask_user_async`/`{{pending_answers}}`/`{{unanswered_questions}}` 等已有对外行为契约，D2/D3/D4 都是在原有占位符体系上增量，不删除/不改名已有占位符。
- D6 只做到"事后可识别 + 归档"，不做跨线程强制拦截（收益/复杂度不成比例，且 `cron_job_runner.py` 本身已经说明"Python 无法强制杀死线程"，这是既有约束）。
- 不引入外部依赖（不用 `filelock` PyPI 包），沿用项目已有的 `ExclusiveFileLock`/`atomic_write` 自研方案，保持风格一致。

## 3. 分阶段实施计划

每阶段完成后：更新本文档"实施进度"勾选项 + 回填"阶段N实现记录" + 同步
`docs/cron-async-user-feedback-guide.md` 相关章节（若该阶段改变了用户可观察
行为）+ 打包本阶段新增/修改文件（保持仓库目录结构）。

1. **D1 并发安全**：`ExclusiveFileLock` 包裹读改写 + `atomic_write_jsonl`
2. **D2 消费时机修正**：`render_prompt()` 返回 question_ids、`run_job()` 确认成功后再消费
3. **D3 忽略语义 + D4 模糊去重**：`{{dismissed_questions}}` 占位符 + 相似度兜底去重
4. **D5 数据清理**：归档 + 按 job 清理挂钩
5. **D6 孤儿线程可识别** + 收尾：`run_token` 审计字段 + 看板置灰展示 + 文档收尾 + 端到端回归测试

## 4. 实施进度

- [x] 阶段1：D1 并发安全
- [x] 阶段2：D2 消费时机修正
- [x] 阶段3：D3 忽略语义 + D4 模糊去重
- [x] 阶段4：D5 数据清理
- [x] 阶段5：D6 孤儿线程可识别 + 收尾

## 5. 阶段1实现记录（D1 并发安全）

- `questions_store.py` 的 `_write_all()` 改用
  `utils/atomic_write.py::atomic_write_jsonl()`（tmp+replace，避免读端看到
  半截内容），不再手写 `p.write_text()`。
- `submit_answer`/`dismiss_question`/`mark_answers_consumed`/
  `append_question` 全部把"读全部→改→整体覆盖写"包进
  `external_input/filelock.py::ExclusiveFileLock`（复用项目里
  `pending_hits.jsonl` 的既有同款先例，未新增依赖）。
- 新增 `find_or_create_question()`：把 `ask_user_async` 原先"先查重、再
  建新"两次独立调用（中间有未加锁的时间窗口，两个并发线程可能都判断
  "无重复"各自建一条）合并成一次加锁内完成的原子操作。`tools/
  ask_user_async.py` 改为调用这个新函数（`fuzzy_threshold=None`，本阶段
  只修并发安全，模糊去重开关留给阶段3打开）。原有的
  `find_pending_by_fingerprint()` 保留不变（供旧调用方/测试兼容），仅
  新函数内部另有一份不导出的 `_find_pending_by_fingerprint_locked()` 在
  已持锁状态下复用。
- 新增回归测试 `TestFindOrCreateQuestionConcurrency`
  （`tests/test_cron_questions_store.py`）：8 线程并发对同一问题调用
  `find_or_create_question` 只产生 1 条记录；20 条问题各自并发
  提交答案/忽略，文件不损坏、计数不丢不重。
- 相关测试全量跑通：`test_cron_questions_store.py` +
  `test_cron_async_user_feedback.py` + `test_cron_questions_api_routes.py`
  共 73 条全部通过。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（加锁 + 原子写 +
  新增 `find_or_create_question`）
- 修改 `src/mini_agent/tools/ask_user_async.py`（改用
  `find_or_create_question`）
- 修改 `tests/test_cron_questions_store.py`（新增
  `TestFindOrCreateQuestionConcurrency`，2 条）

## 6. 阶段2实现记录（D2 消费时机修正）

- `CronJobWorkspace._format_pending_answers()` 不再在渲染时刻调用
  `mark_answers_consumed()`，改为把本次渲染取到的 `question_id` 列表存进
  `self._last_rendered_answer_ids`（`__init__` 里初始化为空列表）。
- 新增公开方法 `consume_last_rendered_answers()`：把上一次
  `render_prompt()` 渲染出的答案正式标记为已消费；没有渲染过或没有待
  消费答案时是安全空操作（返回 0）。
- `CronJobExecutor.run_job()` 在第一步（`step_index == 0`，携带
  `{{pending_answers}}` 的那次完整 prompt）`submit_step_fn()` **调用成功
  且 `result.error` 为空**之后才调用 `ws.consume_last_rendered_answers()`；
  第一步抛异常或 `result.error` 非空时都不调用，让答案保留"未消费"，
  下次触发能再次被注入，不会因为这一步没跑起来就静默丢失。
  （对不具备这个新方法的测试替身用 `hasattr()` 兜底，避免旧的假
  workspace 测试因为多出这一次方法调用而报错。）
- 更新 `tests/test_cron_async_user_feedback.py` 两处直接调用
  `render_prompt()` 断言"消费"的测试，改为显式调用
  `consume_last_rendered_answers()` 模拟"这一步成功提交"，并新增
  `test_pending_answers_not_consumed_if_never_explicitly_confirmed`
  验证"渲染了但没确认消费"时答案能在下次渲染里再次出现。
- 新增 `tests/test_cron_job_workspace_and_executor.py::
  TestCronJobExecutorAnswerConsumptionTiming`（3 条，覆盖 D2 场景本身）：
  第一步抛异常不消费、第一步 `result.error` 非空不消费、第一步成功正常
  消费。
- 全量相关测试跑通：125 条全部通过。

新增/修改文件清单：
- 修改 `src/mini_agent/evolution/cron_job_workspace.py`（`__init__` 新增
  `_last_rendered_answer_ids`；`_format_pending_answers()` 不再自动消费；
  新增 `consume_last_rendered_answers()`）
- 修改 `src/mini_agent/evolution/cron_job_executor.py`（第一步成功后才
  调用 `consume_last_rendered_answers()`）
- 修改 `tests/test_cron_async_user_feedback.py`（2 处更新 + 新增 1 条）
- 修改 `tests/test_cron_job_workspace_and_executor.py`（新增
  `TestCronJobExecutorAnswerConsumptionTiming`，3 条）

## 7. 阶段3实现记录（D3 忽略语义 + D4 模糊去重）

**D3**：
- `DEFAULT_PROMPT_TEMPLATE` 与 `ensure()` 里的最小默认模板都新增
  `{{#dismissed_questions}}...{{/dismissed_questions}}` 条件块。
- 新增 `CronJobWorkspace._format_dismissed_questions(limit=20)`：调用已有
  的 `questions_store.list_dismissed_questions(job_id=..., limit=20)`
  （按 `updated_at` 倒序），格式化成"「X」（用户已忽略，不要再问）"列表；
  异常兜底返回空字符串，不影响 `render_prompt()` 本身。
- `render_prompt()` 接入新占位符的渲染 + 条件块隐藏逻辑，写法与
  `pending_answers`/`unanswered_questions` 一致。
- 更新 `docs/cron-async-user-feedback-guide.md` 占位符表格 + "已知限制"
  章节，说明忽略不再是完全不可见的黑洞。

**D4**：
- `tools/ask_user_async.py` 调用 `find_or_create_question()` 时去掉阶段1
  临时传入的 `fuzzy_threshold=None`，改用函数默认值（0.82），默认开启
  模糊去重。
- 更新 `docs/cron-async-user-feedback-guide.md` 相应段落，说明精确匹配
  优先、模糊匹配兜底的行为，以及仍然不是语义判重。

新增回归测试（`tests/test_cron_async_user_feedback.py`）：
- `TestDismissedQuestionsPlaceholder`（3 条）：忽略问题出现在占位符里、
  没有忽略记录时条件块隐藏、忽略后的问题不会同时出现在
  `unanswered_questions`/`pending_answers` 里。
- `TestFuzzyDeduplication`（2 条）：措辞相近的复述被合并去重
  （`"你希望预算提高到多少？"` vs `"你希望把预算提高到多少呢？"`，相似度
  ~0.91）；语义明显不同的两个问题不会被误合并。

全量相关测试跑通：130 条全部通过。

新增/修改文件清单：
- 修改 `src/mini_agent/evolution/cron_job_workspace.py`（新增
  `{{dismissed_questions}}` 占位符渲染 + `_format_dismissed_questions()`）
- 修改 `src/mini_agent/tools/ask_user_async.py`（开启默认模糊去重）
- 修改 `docs/cron-async-user-feedback-guide.md`（占位符表格 + 去重/忽略
  相关章节）
- 修改 `tests/test_cron_async_user_feedback.py`（新增
  `TestDismissedQuestionsPlaceholder` + `TestFuzzyDeduplication`，共 5 条）

## 8. 阶段4实现记录（D5 数据清理）

- 新增 `questions_store.archive_old_records(paths, retention_days=90)`：
  加锁读改写，把 `updated_at`（回退到 `created_at`）超过保留期且状态为
  `answered`/`dismissed` 的记录挪到 `<原文件名>.archive.jsonl`（同目录，
  追加写），主文件写回剩余记录。`pending` 状态永不归档，不管多老——
  只要还没被回答/忽略就还是"活"的。异常兜底返回 0，归档失败不影响
  问答功能主链路。
- 新增 `questions_store.purge_questions_for_job(paths, job_id)`：加锁
  删掉某个 job 名下的全部问答记录（不分状态）。异常兜底返回 0，清理
  失败不阻断更重要的"job 已被删除"这个事实。
- `CronScheduler.remove_job()` 在成功删除 job 之后调用
  `purge_questions_for_job()`，避免用户删除 cron job 后其问答记录变成
  永久孤儿数据（原来 §5 复查里指出的 D5 遗留场景之一）。
- `AutonomousLoop.__init__` 新增 `_last_questions_archive_at` 节流字段
  （初始为 0.0）；`_tick_maintenance()` 在既有 `reap_stale_jobs()` 调用
  后面新增一段：每 24 小时最多触发一次 `archive_old_records()`。放在
  `_tick_maintenance()` 而不是 `_tick_passive()` 是跟同文件里
  `reap_stale_jobs()`/`cycle_patrol` 等既有维护性调用同一个档位边界，
  失败静默降级，不影响本次 tick 其余步骤。
- 更新 `docs/cron-async-user-feedback-guide.md`：§8 数据存储新增"并发
  安全"/"数据归档"两段说明；§9 已知局限里"不做超时/自动作废"一条
  补充说明"这里指 pending 状态，answered/dismissed 会被归档"。

新增回归测试：
- `tests/test_cron_questions_store.py::TestArchiveOldRecords`（3 条）：
  超过保留期的 answered/dismissed 被归档到 archive 文件且主文件删除
  对应记录；未超保留期的不归档；pending 状态无论多老都不归档。
- `tests/test_cron_questions_store.py::TestPurgeQuestionsForJob`（2 条）：
  清除某 job 的全部记录且不影响其它 job；清理不存在的 job 是安全空
  操作。
- `tests/test_cron_scheduler_reap_stale_jobs.py::
  TestRemoveJobPurgesQuestions`（1 条）：`CronScheduler.remove_job()`
  端到端验证会顺带清掉该 job 的问答记录。

全量相关测试跑通：140 条全部通过（含相邻的
`test_cron_scheduler_priority.py`/`test_cron_scheduler_local_handler.py`
无回归）。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（新增
  `archive_old_records`/`purge_questions_for_job`/`_archive_path`）
- 修改 `src/mini_agent/evolution/cron_scheduler.py`（`remove_job()` 接入
  `purge_questions_for_job`）
- 修改 `src/mini_agent/evolution/autonomous_loop.py`（`__init__` 新增节流
  字段；`_tick_maintenance()` 接入 `archive_old_records`）
- 修改 `docs/cron-async-user-feedback-guide.md`（§8/§9 相关说明）
- 修改 `tests/test_cron_questions_store.py`（新增
  `TestArchiveOldRecords` + `TestPurgeQuestionsForJob`，共 5 条）
- 修改 `tests/test_cron_scheduler_reap_stale_jobs.py`（新增
  `TestRemoveJobPurgesQuestions`，1 条）

## 9. 阶段5实现记录（D6 孤儿线程可识别 + 收尾）

- `cron_context.py` 新增 `set_current_cron_run_id()`/`get_current_cron_run_id()`，
  跟已有的 `job_id` thread-local 同一套模式；`clear_current_cron_job_id()`
  一并清空 `run_id`，避免残留到线程之后的非 cron 调用。
- `CronJobExecutor.run_job()` 在 `ws.new_run_id()` 生成 `run_id` 之后立刻
  调用 `set_current_cron_run_id(run_id)`——覆盖本次 run 内任意一步调用到
  的 `ask_user_async`。
- `questions_store.find_or_create_question()`/新建记录时新增 `run_id`
  字段（`ask_user_async.py` 透传 `get_current_cron_run_id()` 的返回值）。
- 新增 `questions_store.list_orphaned_pending_questions()`：遍历所有
  pending 问题，逐个跟对应 job 的 `CronJobWorkspace.read_state().
  last_run_id` 比较 `run_id`，不一致的判定为"疑似孤儿线程迟到写入"。
  `run_id`/`job_id` 为空、或 `job_id == "adhoc"` 的记录跳过（非 cron
  场景不参与判定）。只做识别，不做自动处理，异常兜底返回空列表。
- **范围收敛说明**（对照 §1 表格里 D6 原方案）：原方案提到"看板过滤/
  置灰展示"，本轮**只实现了后端识别能力**（`list_orphaned_pending_
  questions()`），Streamlit 看板 UI 未接入展示——已如实更新进
  `docs/cron-async-user-feedback-guide.md` §9"已知局限"，留作后续
  按需跟进，不在本次加固范围内谎报为"已完成"。
- 更新 `docs/cron-async-user-feedback-guide.md`：§8 数据存储新增
  `run_id` 字段说明 + "孤儿线程识别"段落；§9 已知局限新增一条说明 UI
  未接入；§10 文件一览表格全量刷新（新增/变更的函数、文件都补全）。

新增回归测试：
- `tests/test_cron_questions_store.py::TestOrphanedPendingQuestions`
  （3 条）：`run_id` 与当前 state 一致时不判定为孤儿；不一致时判定为
  孤儿；`adhoc`/无 `run_id` 的记录永不判定为孤儿。
- `tests/test_cron_async_user_feedback.py::
  TestRunIdPropagationForOrphanDetection`（1 条）：`CronJobExecutor.
  run_job()` 执行期间调用 `ask_user_async`，验证问题记录的 `run_id`
  正确等于本次 run 的 `run_id`（跟 `CronJobWorkspace.read_state().
  last_run_id` 一致），且不会被误判为孤儿。

全量相关测试跑通：162 条全部通过（含全部前四阶段 + 本阶段新增测试，
以及相邻的 `test_cron_scheduler_priority.py`/
`test_cron_scheduler_local_handler.py` 无回归）。

新增/修改文件清单：
- 修改 `src/mini_agent/evolution/cron_context.py`（新增
  `set_current_cron_run_id`/`get_current_cron_run_id`）
- 修改 `src/mini_agent/evolution/cron_job_executor.py`（生成 run_id 后
  写入 thread-local）
- 修改 `src/mini_agent/tools/ask_user_async.py`（透传 run_id）
- 修改 `src/mini_agent/notification/questions_store.py`（新增 `run_id`
  字段 + `list_orphaned_pending_questions()`）
- 修改 `docs/cron-async-user-feedback-guide.md`（§8/§9/§10）
- 修改 `tests/test_cron_questions_store.py`（新增
  `TestOrphanedPendingQuestions`，3 条）
- 修改 `tests/test_cron_async_user_feedback.py`（新增
  `TestRunIdPropagationForOrphanDetection`，1 条）

## 10. 收尾总结

D1–D6 六个真实场景缺陷全部完成加固，未改变 `cron_async_user_feedback_
mechanism_plan.md` 原有的对外行为契约（占位符不删不改名，API/UI 现有
交互不变）。唯一主动收窄范围的是 D6 的看板 UI 展示（说明见上方阶段5
记录），其余 D1–D5 均按 §1 表格方案原样落地。全流程回归测试
（`test_cron_async_user_feedback.py` + `test_cron_questions_store.py` +
`test_cron_questions_api_routes.py` + `test_cron_job_workspace_and_
executor.py` + `test_cron_job_executor_step_detail.py` +
`test_cron_scheduler_reap_stale_jobs.py` + 相邻两个 scheduler 测试文件）
共 162 条全部通过，无一失败。
