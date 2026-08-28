# Cron 任务异步用户反馈 使用指南

- **设计文档**：`next_doc/cron_async_user_feedback_mechanism_plan.md`
- **前置依赖**：[cron-dedicated-execution-guide.md](cron-dedicated-execution-guide.md)（cron
  任务专属执行机制本体，本功能是它的收尾状态扩展，不重复实现执行线程/
  `CronJobWorkspace` 记录链路）
- **当前实施进度**：阶段1-5 已全部实施完成（数据层、执行链路、API + 通知
  联动、Streamlit 看板、本文档）。

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
- **同一 job 内自动去重**：同一个 job 对**完全相同**的问题文本（去除
  首尾空白后精确比较）重复调用，会直接复用已有的 `question_id`
  （`deduplicated: true`），不会在看板上刷出重复通知。这是**精确指纹
  去重**，不做语义判重——"这两个问题问的其实是一回事"这种情况不会被
  识别为重复。
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

`CronJobWorkspace.render_prompt()` 新增两个占位符，跟已有的
`{{progress}}`（续接进度）搭配使用：

| 占位符 | 内容 | 消费后的处理 |
|---|---|---|
| `{{pending_answers}}` | 该 job 下**已回答但还没喂给过 agent** 的问答对，格式化成"上次你问过「X」，用户回答：Y"的列表 | 渲染后立即标记为已消费（`consumed=True`），避免同一个答案被反复注入好几次触发 |
| `{{unanswered_questions}}` | 该 job 下**仍是 pending** 状态的问题列表 | 提醒 agent 不要针对同一个问题重复调用 `ask_user_async` |

两者都复用 `_render_condition_block()` 现有的 `{{#name}}...{{/name}}`
条件块机制——当前没有待消费答案/没有未答问题时，整段自动隐藏，不会在
prompt 里插入一个空标题。

**用户修改已回答的答案**（见下面第7节）会把该问答对的 `consumed`
重置为 `False`，让它在下一次该 job 触发时**再次**被注入——用户修改
答案通常意味着"上一版不对/不完整，请按新的来"，理应让 agent 重新看到。

**是否跳过本次触发**：当前默认"照常触发"——即便还有未回答的问题，
cron 到期时任务依然正常执行，由 agent 根据 `{{unanswered_questions}}`
自行判断去做其它可推进的部分。这是设计文档里已确认的默认行为。

## 6. 在看板上查看和回答问题

Streamlit 看板"🔔 关注与通知"tab 下新增"🙋 待我反馈"面板，分两个子 tab：

- **待处理**：逐条展示所属任务（`job_id`）、问题原文、`hint`；如果
  该问题带了 `options`，会额外展示一组单选按钮作为快捷参考——选中即
  自动填入文本框，仍可以在文本框里改成任意其它内容再提交，**不是
  强制枚举**。填好答案点"✅ 提交回答"即可。
- **历史记录**：展示已回答问题，每条展开可见当前答案 + **完整修改
  历史**（每次提交/修改的时间与内容，旧→新排列，改答案不会覆盖丢失
  旧版本）。展开后内置一个文本框，可以直接修改答案再提交——新答、
  改答走的是同一个入口，看板/API 都不区分"这是第一次回答"还是
  "这是第 N 次修改"。

两个子面板都用"⬇️ 加载更多"做增量分页，不会一次性把全部历史记录渲染
出来。

## 7. REST API

供看板前端调用，也可以直接用于自动化脚本或其它前端集成：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/v1/cron_questions/pending?limit=20&offset=0&job_id=` | 待回答问题列表，分页，`job_id` 留空返回全部 job 的 |
| `GET` | `/v1/cron_questions/history?limit=20&offset=0&job_id=` | 已回答问题历史（含完整 `answer_history`），分页，`job_id` 留空返回全部 |
| `POST` | `/v1/cron_questions/{question_id}/answer` | 提交/修改答案，body: `{"answer": "..."}`；新答、改答统一走这一个接口；答案为空返回 `400`，`question_id` 不存在返回 `404` |

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
`status`（`pending`/`answered`）/`created_at`/`updated_at`/`answer`/
`answer_history`/`consumed`（内部字段，只影响 prompt 注入去重，不影响
看板历史面板的展示——历史面板永远展示全部记录）。

## 9. 已知局限（本轮不做）

- **不做超时/自动作废**：问题会一直挂在"待回答"列表里，直到用户回答
  或在看板上手动关闭。当前版本还没有"关闭/忽略"这个动作，只有
  "回答"这一条路径。
- **不做语义判重**：只做同一 job 内的精确指纹去重（问题文本完全一致
  才会复用），不识别"这两个问题问的其实是一回事"这种情况。
- **未回答问题下次触发时不会自动跳过**：当前固定"照常触发"，agent 见
  机行事去做其它可推进的部分；如果希望"跳过直到有人回答"，需要后续
  单独实现为可配置项（当前未实现）。

## 10. 相关文件一览

| 文件 | 作用 |
|---|---|
| `src/mini_agent/notification/questions_store.py` | 问答记录存储：`append_question`/`find_pending_by_fingerprint`/`submit_answer`/`list_pending_questions`/`list_answered_questions`/`list_unconsumed_answers_for_job`/`mark_answers_consumed`/`list_pending_question_texts_for_job` |
| `src/mini_agent/tools/ask_user_async.py` | 异步提问工具本体，内部调用 `NotificationDispatcher.dispatch()`（`source="cron_question"`） |
| `src/mini_agent/evolution/cron_context.py` | thread-local `job_id` 透传，供 `ask_user_async` 在 cron 执行线程内取到当前 job_id |
| `src/mini_agent/evolution/cron_job_workspace.py` | `STATUS_WAITING_FEEDBACK` 状态、`{{pending_answers}}`/`{{unanswered_questions}}` 占位符渲染 |
| `src/mini_agent/evolution/cron_job_executor.py` | `run_job()` 设置/清空 thread-local job_id、收尾时的 `waiting_feedback` 判定 |
| `src/mini_agent/api/routes.py` | `/v1/cron_questions/{pending,history,{id}/answer}` 三个端点 |
| `apps/mini_agent_kanban/client.py` | `cron_questions_pending`/`cron_questions_history`/`answer_cron_question` 客户端方法 |
| `apps/mini_agent_kanban/app.py` | "🔔 关注与通知"tab 下"🙋 待我反馈"面板（`_render_cron_questions_panel`，独立 `@st.fragment`） |
| `.agent/notification/cron_questions.jsonl` | 问答记录落地文件（运行时生成） |
| `tests/test_cron_questions_store.py` | `questions_store.py` 单元测试 |
| `tests/test_cron_async_user_feedback.py` | 工具去重、状态机、占位符渲染等端到端覆盖 |
| `tests/test_cron_questions_api_routes.py` | API 端点测试（pending/history 分页过滤、答案提交与修改、错误处理） |
