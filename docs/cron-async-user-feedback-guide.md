# Cron 任务异步用户反馈 使用指南

- **设计文档**：`next_doc/cron_async_user_feedback_mechanism_plan.md`（原始
  设计）、`next_doc/cron_async_feedback_hardening_plan.md`（D1–D6 加固）、
  `next_doc/cron_async_feedback_lifecycle_and_usability_plan.md`（E1/E2
  长期未回答自动关闭 + 看板可用性补齐）
- **前置依赖**：[cron-dedicated-execution-guide.md](cron-dedicated-execution-guide.md)（cron
  任务专属执行机制本体，本功能是它的收尾状态扩展，不重复实现执行线程/
  `CronJobWorkspace` 记录链路）
- **当前实施进度**：阶段1-5（原始设计）+ D1-D6（加固）+ E1-E2（生命周期
  与看板可用性）已全部实施完成。

---

## 1. 这篇文档管什么、不管什么

**管**：`ask_user_async` 工具的使用方式、cron 任务收到答案后如何自动
续接、用户在看板上如何查看/回答/修改问题。

**不管**：`ask_user`/`ask_user_confirm`/`ask_user_choice` 那三个**同步
阻塞**的交互式提问工具——它们服务于"用户就在旁边"的场景，跟本功能是
两条完全独立的通道，互不影响。这三个工具本身的用法参见
`src/mini_agent/tools/user_input.py` 内的工具描述，本文档不重复介绍。

## 2. 这套机制解决什么问题

Cron 任务由 `AutonomousLoop._tick_passive()` 在后台无人值守触发。如果
任务执行到一半需要用户做个决策或补充点信息，用同步阻塞的 `ask_user`
会一直卡到超时，白白占用这次触发的执行时间，也不会真正等到答案——因为
根本没有人在电脑前实时回答。

`ask_user_async` 反过来：**问完立刻返回，不等任何人**。agent 据此把
这部分工作标记为搁置，转去做其它还能推进的部分（或者这次触发就直接
收尾）。问题会出现在看板"待我反馈"面板，用户可以在方便的时候慢慢看、
慢慢答；一旦回答，**下次这个 cron job 再被调度触发时，回答内容会自动
出现在 prompt 里**，agent 借此接着搁置的工作继续，不需要用户把答案
手动粘贴回任务描述。

## 3. 在 cron 任务里怎么用：`ask_user_async` 工具

agent（模型自己）在 cron 任务执行过程中，遇到需要用户输入的节点时会
自主决定调用这个工具，用户通常不需要手动触发。工具签名：

| 参数 | 必填 | 说明 |
|---|---|---|
| `question` | 是 | 问题原文，会展示在看板上 |
| `hint` | 否 | 附加提示/上下文，展示在问题下方 |
| `options` | 否 | 参考选项列表，**仅做展示提示，不做强校验**——用户仍可以输入不在列表里的任意文本 |

调用后立刻返回：

```json
{
  "status": "pending",
  "question_id": "cq:user:ab12cd34:9f1a2b3c4d5e",
  "deduplicated": false,
  "note": "This question has been posted asynchronously. Do not wait for an answer — ..."
}
```

- **不阻塞、不超时**：内部不调用 `interaction.ask()`，工具本身是一次
  纯本地文件写入 + 一次通知分发，毫秒级返回。
- **同一 job 内自动去重（精确匹配 + 模糊兜底）**：同一个 job 对**完全
  相同**的问题文本（去除首尾空白后精确比较）重复调用，会直接复用已有
  的 `question_id`（`deduplicated: true`）。**[加固后]** 精确匹配之外
  新增一层模糊匹配兜底：规范化文本（去标点/空白/大小写）后用
  `difflib.SequenceMatcher` 算相似度，达到阈值（默认 0.82）也判定为
  重复——LLM 每次生成的问题措辞几乎不可能逐字相同，纯精确匹配在真实
  场景下容易被换一种说法绕过，导致同语义问题反复刷屏，详见
  `next_doc/cron_async_feedback_hardening_plan.md` D4。这仍然**不是
  语义判重**，只是缓解常见的措辞微调场景，明显不同表述的问题不会被
  误合并。
- **非 cron 场景下的降级**：如果在普通交互式对话里直接调用这个工具
  （而不是在 cron 任务执行上下文里），问题仍然会被正常创建并通知到
  看板，只是会归到固定分组 `"adhoc"`，**不会**被任何 cron job 的
  `render_prompt()` 自动续接消费——因为它压根不属于哪个 job。

## 4. 任务状态如何体现："等反馈中"不算失败

`CronJobExecutor.run_job()` 收尾时，如果本次触发本来是"正常完成"，但
该 job 通过 `questions_store` 查到还有未回答的问题，就会把最终状态
改记成新增的状态值 `waiting_feedback`：

- **不计入** `consecutive_failures`——这不是"卡死判定放弃"，只是"正在
  等一个具体问题的答案"，语义跟真正执行失败完全不同。
- `progress_summary` 会保留最后一步的输出，供下次触发时 prompt 拼接
  续接依据。
- 如果本次触发本身超时（`timed_out`）或触发了需要人工介入的严重问题
  （`needs_human_review`），这两种更紧急的状态优先，不会被
  `waiting_feedback` 覆盖。

## 5. 已回答的答案怎么自动喂回去：两个 prompt 占位符

`CronJobWorkspace.render_prompt()` 新增三个占位符，跟已有的
`{{progress}}`（续接进度）搭配使用：

| 占位符 | 内容 | 消费后的处理 |
|---|---|---|
| `{{pending_answers}}` | 该 job 下**已回答但还没喂给过 agent** 的问答对，格式化成"上次你问过「X」，用户回答：Y"的列表 | **[加固后]** 渲染时只记录本次带出的 question_id，不立即标记消费；等 `CronJobExecutor` 确认这一步 `submit_step_fn()` 真正提交成功（未抛异常、`result.error` 为空）后才调用 `consume_last_rendered_answers()` 正式标记 `consumed=True`——如果这一步本身失败了，答案会保留"未消费"，下次触发能再次注入，不会静默丢失（详见 `next_doc/cron_async_feedback_hardening_plan.md` D2） |
| `{{unanswered_questions}}` | 该 job 下**仍是 pending** 状态的问题列表，**[E1 新增]** 每条附带"已等待 N 天" | 提醒 agent 不要针对同一个问题重复调用 `ask_user_async`；等待天数越久越应该优先考虑自己拿主意或换个方式绕过去，因为快接近自动关闭阈值了 |
| `{{dismissed_questions}}` | 该 job 下最近 20 条已忽略/已自动关闭的问题原文 | **[E1 更新]** 区分两种措辞：用户手动忽略的提示"不要再问"；**[E1 新增]** 因长期无人回答被系统自动关闭的提示"如果仍然关键，可以换个更容易顺手回答的方式重新问一次，不要用原话重复问"——两者语义不同，不能混为一谈 |

三者都复用 `_render_condition_block()` 现有的 `{{#name}}...{{/name}}`
条件块机制——当前没有待消费答案/没有未答问题/没有已忽略问题时，整段
自动隐藏，不会在 prompt 里插入一个空标题。

**用户修改已回答的答案**（见下面第7节）会把该问答对的 `consumed`
重置为 `False`，让它在下一次该 job 触发时**再次**被注入——用户修改
答案通常意味着"上一版不对/不完整，请按新的来"，理应让 agent 重新看到。

**是否跳过本次触发**：当前默认"照常触发"——即便还有未回答的问题，
cron 到期时任务依然正常执行，由 agent 根据 `{{unanswered_questions}}`
自行判断去做其它可推进的部分。这是设计文档里已确认的默认行为。

## 6. 在看板上查看和回答问题

Streamlit 看板"🔔 关注与通知"tab 下新增"🙋 待我反馈"面板，分三个子 tab：

- **待处理**：逐条展示所属任务（`job_id`）、问题原文、`hint`；**[E2 新增]**
  按等待时长**升序**排列（等得最久的排最前）并附带"已等待 N 天"徽标
  （≥7 天变红），帮助优先处理接近自动关闭阈值的旧问题。如果该问题带了
  `options`，会额外展示一组单选按钮作为快捷参考——选中即自动填入文本框，
  仍可以在文本框里改成任意其它内容再提交，**不是强制枚举**。填好答案点
  "✅ 提交回答"即可。如果这个问题已经不需要回答了（比如任务本身已经
  过时，或者你已经通过其它方式处理了），可以点旁边的"🙈 忽略这个问题"
  ——忽略后它从"待处理"列表消失，**不会**被当作答案注入下次触发的
  prompt，agent 就当这个问题从未存在过；已经回答过的问题不能再被忽略
  （应该用下面的"修改答案"）。
- **历史记录**：展示已回答问题，每条展开可见当前答案 + **完整修改
  历史**（每次提交/修改的时间与内容，旧→新排列，改答案不会覆盖丢失
  旧版本）。展开后内置一个文本框，可以直接修改答案再提交——新答、
  改答走的是同一个入口，看板/API 都不区分"这是第一次回答"还是
  "这是第 N 次修改"。
- **已忽略**（**[E2 新增]**）：展示所有已忽略/已自动关闭的问题，仅供
  查阅，不会再提醒你、也不会被当作答案带入下次任务触发。每条会标明
  是"🙈 用户手动忽略"还是"⏱️ 长期未回答，系统自动关闭"——原方案（见
  §15）虽然实现了忽略功能，但一直没有把 `list_dismissed_questions()`
  接到 UI，忽略动作等于一个查无对证的黑洞；这里补上，尤其是"系统自动
  关闭"这种用户可能完全没意识到发生过的情况，需要一个能回头确认的地方。

三个子面板都用"⬇️ 加载更多"做增量分页，不会一次性把全部历史记录渲染
出来。

## 7. REST API

供看板前端调用，也可以直接用于自动化脚本或其它前端集成：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/v1/cron_questions/pending?limit=20&offset=0&job_id=` | 待回答问题列表，分页，`job_id` 留空返回全部 job 的 |
| `GET` | `/v1/cron_questions/history?limit=20&offset=0&job_id=` | 已回答问题历史（含完整 `answer_history`），分页，`job_id` 留空返回全部 |
| `POST` | `/v1/cron_questions/{question_id}/answer` | 提交/修改答案，body: `{"answer": "..."}`；新答、改答统一走这一个接口；答案为空返回 `400`，`question_id` 不存在返回 `404` |
| `POST` | `/v1/cron_questions/{question_id}/dismiss` | 忽略/关闭一条仍待回答的问题；问题不存在或已被回答返回 `404`；重复忽略同一条是幂等的 |
| `GET` | `/v1/cron_questions/dismissed?limit=20&offset=0&job_id=` | **[E2 新增]** 已忽略/已自动关闭问题列表（含 `dismiss_reason`），分页，`job_id` 留空返回全部 |

这几个接口都是纯本地文件读写，耗时可忽略，不走
`kanban_async_job_mechanism_plan.md` 里那套面向分钟级不可预测耗时的
LLM/Agent 调用专用异步任务机制。

## 8. 数据存储

问答记录独立存储在 `.agent/notification/cron_questions.jsonl`，跟
watchlist 分级汇报用的 `.agent/notification/reports.jsonl` 是两份彻底
分开的文件：

- `reports.jsonl` —— 只读汇报，`acknowledged` 布尔已读标记，用户不能
  修改内容。
- `cron_questions.jsonl` —— 双向问答，`answer` 可被用户反复修改，且
  每次修改都会在 `answer_history` 里留痕，不会覆盖丢失。

单条记录字段：`question_id`/`job_id`/`question`/`hint`/`options`/
`status`（`pending`/`answered`/`dismissed`）/`created_at`/`updated_at`/
`answer`/`answer_history`/`consumed`（内部字段，只影响 prompt 注入去重，
不影响看板历史面板的展示——历史面板永远展示全部记录）/`run_id`（**[加固后]
新增**，见下方"孤儿线程识别"）/`dismiss_reason`（**[E1 新增]**，
`"manual"` | `"stale_timeout"`，只在 `status=dismissed` 时有意义）。

**[加固后] 并发安全**：所有"读全部→改→整体覆盖写"的复合操作
（`submit_answer`/`dismiss_question`/`mark_answers_consumed`/新建问题）
都通过 `<file>.lock` 哨兵文件加独占锁，写文件走 tmp+replace 原子写入，
详见 `next_doc/cron_async_feedback_hardening_plan.md` D1。

**[加固后] 数据归档**：`AutonomousLoop._tick_maintenance()` 每 24 小时
最多触发一次 `archive_old_records()`，把超过保留期（默认 90 天）的
`answered`/`dismissed` 记录挪到同目录的 `cron_questions.archive.jsonl`
（只追加、供审计查阅，不参与日常读取）；`pending` 状态的记录不论多老
都不会被归档。job 被删除时（`CronScheduler.remove_job()`）会顺带清掉
其名下所有问答记录（不分状态），避免永久遗留的孤儿数据。详见 D5。

**[E1 新增] 长期未回答自动关闭**：同一个 24 小时维护窗口里，还会调用
`expire_stale_pending_questions()`，把超过 `cron_question_stale_after_
days`（默认 14 天，配置项见下方）的 `pending` 记录转为 `dismissed`
（`dismiss_reason="stale_timeout"`），并逐条补发 kanban 通知。这填补了
原设计"pending 永不过期"的空白——见
`next_doc/cron_async_feedback_lifecycle_and_usability_plan.md` E1 的
背景说明。配置示例（`.agent/notification/config.yaml`）：

```yaml
cron_question_stale_after_days: 14   # 默认值；改成 0 或负数关闭该机制
```

**[加固后] 孤儿线程识别**：`CronJobExecutor.run_job()` 生成 `run_id` 后
连同 `job_id` 一起写进 thread-local，`ask_user_async` 写入问题时把当次
`run_id` 一并记录。watchdog（`reap_stale_jobs()`）判定某次 run 卡死并
代替它释放并发槽位后，卡死的旧线程本身杀不掉，可能成为孤儿线程事后才
执行到 `ask_user_async`——`questions_store.list_orphaned_pending_
questions()` 通过比较问题记录的 `run_id` 跟对应 job 当前
`CronJobWorkspace.read_state().last_run_id` 是否一致来识别这类"迟到"
写入。这一版只做**事后可识别**（供审计/看板展示用），不做写入时拦截
——拦截需要反查 `CronJobRunner` 当前合法 run 状态，跨模块耦合成本高，
留作后续如有需要再做。看板 UI 上的置灰/过滤展示本轮**未实现**，只有
后端识别能力，见"已知局限"一节。详见 D6。

## 9. 已知局限（本轮不做）

- **[E1 已解决，不再是局限]** 原来这里写的是"pending 永不自动过期，
  只能靠用户手动忽略"——现在 `AutonomousLoop._tick_maintenance()` 每
  24 小时会检查一次，超过 `cron_question_stale_after_days`（默认 14 天，
  可在 `.agent/notification/config.yaml` 调整，`<=0` 关闭该机制）没人
  回答的问题会被自动关闭（`dismiss_reason="stale_timeout"`），并补发
  一条 kanban 通知告知用户。详见
  `next_doc/cron_async_feedback_lifecycle_and_usability_plan.md` E1。
- **不做严格语义判重**：精确匹配 + 相似度兜底（见上方 §3），能覆盖
  常见的措辞微调，但不识别真正意义上"这两个问题问的其实是一回事但
  表述完全不同"的情况。
- **忽略后仍会通过 `{{dismissed_questions}}` 提醒 agent**：忽略不是对
  agent 完全不可见的黑洞——下次 `render_prompt()` 会把最近忽略的问题
  列出来提醒 agent 不要再问（手动忽略）/可以换种方式重新问（自动
  关闭），缓解"忽略→agent 换个说法重问→用户再忽略"的死循环，详见 D3
  和 E1。但 agent 仍有可能在措辞差异较大、绕开模糊去重的情况下重新
  触发相似问题，这不是 100% 保证。
- **未回答问题下次触发时不会自动跳过**：当前固定"照常触发"，agent 见
  机行事去做其它可推进的部分；如果希望"跳过直到有人回答"，需要后续
  单独实现为可配置项（当前未实现）。
- **孤儿线程识别只有后端能力，看板 UI 未接入**：`list_orphaned_pending_
  questions()` 能识别出"迟到"写入的问题，但 Streamlit 看板"🙋 待我
  反馈"面板目前不区分展示，用户看不出某条问题是不是孤儿线程迟到问的。
  也不做写入时拦截（不会阻止孤儿线程把问题写进去），只做事后可查询，
  详见 D6。
- **[E3，明确不做]** 没有 tab 角标未读数提醒、没有批量忽略/批量回答、
  看板 UI 没有暴露按 job_id 筛选或按等待时长排序的控件（API 层已支持
  `job_id` 过滤，只是没做成 UI 输入框）——当前问题量级下这几项的
  边际收益低于实现成本，留作后续按需再做，详见
  `next_doc/cron_async_feedback_lifecycle_and_usability_plan.md` §4。

## 10. 相关文件一览

| 文件 | 作用 |
|---|---|
| `src/mini_agent/notification/questions_store.py` | 问答记录存储：`append_question`/`find_or_create_question`（加锁原子操作，含模糊去重）/`find_pending_by_fingerprint`/`submit_answer`/`dismiss_question`（**[E1 新增]** `reason` 参数）/`expire_stale_pending_questions`（**[E1 新增]**）/`list_pending_questions`/`list_answered_questions`/`list_dismissed_questions`/`list_unconsumed_answers_for_job`/`mark_answers_consumed`/`list_pending_question_texts_for_job`/`archive_old_records`/`purge_questions_for_job`/`list_orphaned_pending_questions` |
| `src/mini_agent/notification/config.py` | **[E1 新增]** `cron_question_stale_after_days` 配置项加载 |
| `src/mini_agent/tools/ask_user_async.py` | 异步提问工具本体，内部调用 `NotificationDispatcher.dispatch()`（`source="cron_question"`） |
| `src/mini_agent/evolution/cron_context.py` | thread-local `job_id`/`run_id` 透传，供 `ask_user_async` 在 cron 执行线程内取到当前 job_id/run_id |
| `src/mini_agent/evolution/cron_job_workspace.py` | `STATUS_WAITING_FEEDBACK` 状态、`{{pending_answers}}`/`{{unanswered_questions}}`（**[E1]** 附带等待天数）/`{{dismissed_questions}}`（**[E1]** 区分手动/超时措辞）占位符渲染、`consume_last_rendered_answers()` |
| `src/mini_agent/evolution/cron_job_executor.py` | `run_job()` 设置/清空 thread-local job_id/run_id、确认第一步成功后才消费答案、收尾时的 `waiting_feedback` 判定 |
| `src/mini_agent/evolution/cron_scheduler.py` | `remove_job()` 顺带清理该 job 名下问答记录 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_maintenance()` 每 24 小时触发一次问答记录归档，**[E1 新增]** 同窗口触发长期未回答问题自动关闭 + 逐条通知 |
| `src/mini_agent/api/routes.py` | `/v1/cron_questions/{pending,history,dismissed,{id}/answer,{id}/dismiss}` 五个端点（**[E2 新增]** `dismissed`） |
| `apps/mini_agent_kanban/client.py` | `cron_questions_pending`/`cron_questions_history`/`answer_cron_question`/`dismiss_cron_question`/`cron_questions_dismissed`（**[E2 新增]**）客户端方法 |
| `apps/mini_agent_kanban/app.py` | "🔔 关注与通知"tab 下"🙋 待我反馈"面板（`_render_cron_questions_panel`，独立 `@st.fragment`），**[E2]** 待处理按等待时长排序+徽标、新增"已忽略"子 tab |
| `.agent/notification/cron_questions.jsonl` | 问答记录落地文件（运行时生成） |
| `.agent/notification/cron_questions.archive.jsonl` | **[加固后新增]** 归档文件（运行时生成，超过保留期的 answered/dismissed 记录） |
| `tests/test_cron_questions_store.py` | `questions_store.py` 单元测试（含并发/消费时机/归档/清理/孤儿识别/**[E1 新增]** 自动过期回归测试） |
| `tests/test_cron_async_user_feedback.py` | 工具去重、状态机、占位符渲染（**[E1 新增]** 等待天数/超时措辞区分）、忽略提醒、模糊去重、run_id 传播等端到端覆盖 |
| `tests/test_cron_questions_api_routes.py` | API 端点测试（pending/history/**[E2 新增]** dismissed 分页过滤、答案提交与修改、错误处理） |
| `tests/test_cron_job_workspace_and_executor.py` | 含答案消费时机（D2）回归测试 |
| `tests/test_cron_scheduler_reap_stale_jobs.py` | 含 `remove_job()` 清理问答记录（D5）回归测试 |
| `next_doc/cron_async_feedback_hardening_plan.md` | **[加固后新增]** D1–D6 六个缺陷的方案设计 + 各阶段实现记录 |
| `next_doc/cron_async_feedback_lifecycle_and_usability_plan.md` | **[本轮新增]** E1（长期未回答自动关闭）/E2（看板可用性补齐）方案设计 + 实现记录 |
