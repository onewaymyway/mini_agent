# Cron 任务异步用户反馈机制设计方案

> 状态：**方案已确认（照常触发 + 自由文本+提示），已实施完成**
> 关联现有代码：`src/mini_agent/tools/user_input.py`、`src/mini_agent/interaction.py`、
> `src/mini_agent/notification/dispatcher.py`、`src/mini_agent/notification/channels/kanban.py`、
> `src/mini_agent/notification/reports_store.py`、`src/mini_agent/evolution/cron_job_executor.py`、
> `src/mini_agent/evolution/cron_job_workspace.py`、`src/mini_agent/evolution/cron_scheduler.py`、
> `apps/mini_agent_kanban/`
> 预计新增文件：`src/mini_agent/notification/questions_store.py`、
> `src/mini_agent/tools/ask_user_async.py`（或并入 `user_input.py`）、
> `src/mini_agent/api/routes_cron_questions.py`（或并入现有 routes.py）、
> `apps/mini_agent_kanban/` 新增"待我反馈"面板代码

## 0. 目标与非目标

**目标**：
1. Cron 任务在执行过程中遇到需要用户决策/补充信息的节点时，可以**不阻塞、不中断**当前
   cron 触发，发布一条问题后转去做其它可推进的工作（或本次触发直接收尾）。
2. 问题以类似通知的形式出现在 Streamlit 看板上，用户可以异步查看并作答，不需要"实时守着"。
3. 看板上能看到全部问答的**历史记录**（含已回答的），并且用户可以**修改**之前已经提交
   过的答案（改答案会保留修改轨迹，不是覆盖丢失）。
4. 用户回答之后，下一次该 cron job 被调度触发时，能自动把回答内容带入 prompt，让 agent
   "接着之前搁置的工作继续"，而不需要用户手动把答案粘贴回任务描述里。

**非目标（本轮不做）**：
- 不改变 `ask_user`/`ask_user_confirm`/`ask_user_choice` 现有的同步阻塞语义——那三个
  工具服务于"交互式对话"场景（用户就在旁边），本方案是**新增**一条独立的异步问答通道，
  服务于"cron 后台无人值守"场景，两者并存、互不影响。
- 不做"用户一直不回答"的强制超时/自动作废机制——问题会一直挂在"待回答"列表里，直到
  用户回答或用户在看板上手动忽略/关闭（关闭动作本轮实现，但不做自动过期）。
- 不做问题内容的语义判重/合并（比如"这两个问题问的其实是一回事"），只做同一 job 内
  按问题文本做**精确指纹去重**，避免同一个未回答问题被重复创建。

## 1. 现状回顾（问题清单）

| 编号 | 问题 |
|---|---|
| P1 | `ask_user`/`ask_user_confirm`/`ask_user_choice` 都基于 `interaction.ask()` **同步阻塞**等待回答；cron job 由 `AutonomousLoop._tick_passive()` 后台触发，没有人实时连着回答，用在 cron 场景里会一直卡到 `interaction.ask` 的超时，白白占用这次 cron 触发的执行时间，且不会真正等到答案 |
| P2 | `notification/` 现有的 kanban 通知（`reports_store.py`）是**单向**的：agent → 用户，只有 `acknowledged` 一个布尔"已读"字段，没有"用户填写回答内容"的语义，也没有历史版本 |
| P3 | `CronJobState.status` 目前只有 `idle/running/needs_human_review/timed_out` 四种，没有"问了个问题、其它部分还能继续跑，不算失败"这种状态；如果直接借用 `needs_human_review`，会被计入 `consecutive_failures`，语义不对（那是"卡死判定放弃"，不是"等一个具体问题的答案"） |
| P4 | `CronJobWorkspace.render_prompt()` 已有 `{{progress}}` 占位符续接进度，但没有承载"上次搁置的问题 + 用户的回答"这种结构化问答对的占位符 |
| P5 | 没有机制阻止同一个 job 每次触发都对同一个未决问题重复调用一次"提问"，如果不做去重，看板会被同一个问题的重复通知刷屏 |

## 2. 整体流程

```
cron 触发 → agent 跑到需要用户输入的节点
    → 调用 ask_user_async(question, hint, options?)
    → 写入 questions_store（status=pending）+ 走 NotificationDispatcher 发 kanban 通知
    → 工具立刻返回 {"status": "pending", "question_id": ...}，不阻塞
    → agent 据此把这一小节工作标记为搁置，继续处理其它可推进的部分（或本步收尾）
    → CronJobExecutor 记录本次收尾时关联的 pending question_id 到 state.json
    → run_job() 正常返回（不算失败，不计入 consecutive_failures）

用户在看板"待我反馈"面板看到问题 → 填写/提交答案
    → questions_store 把该问题 status 改为 answered，answer + 追加 answer_history

下一次 cron 到期被调度 → render_prompt() 检查该 job 关联的 pending question_id：
    - 已回答的 → 通过 {{pending_answers}} 占位符注入"上次你问过 X，用户回答：Y"
    - 仍未回答的 → 在 prompt 里列出"以下问题仍未回复，请先处理其它可推进的部分，
      不要重复调用 ask_user_async 问同一个问题"
    → agent 接着之前的进度继续
```

## 3. 数据结构

### 3.1 新增 `notification/questions_store.py`（与 `reports_store.py` 刻意同构）

存储文件：`.agent/notification/cron_questions.jsonl`（独立于 `reports.jsonl`，语义不同：
reports 是只读汇报 + 已读标记，questions 是双向问答 + 可修改答案）。

单条记录 schema：
```jsonc
{
  "question_id": "cq:<job_id>:<uuid>",
  "job_id": "user:ab12cd34",
  "question": "……",
  "hint": "……",              // 可选
  "options": ["A", "B"],       // 可选，选择题时使用
  "status": "pending",         // "pending" | "answered"
  "created_at": 1730000000.0,
  "updated_at": 1730000500.0,
  "answer": "……",             // 当前最新答案，status=pending 时为空
  "answer_history": [          // 每次提交/修改都追加一条，不覆盖丢失
    {"text": "……", "at": 1730000500.0}
  ]
}
```

提供的函数（对齐 `reports_store.py` 的函数命名习惯）：
- `append_question(paths, record)` — 新建一条问题
- `find_pending_by_fingerprint(paths, job_id, question_text)` — 去重用：同一 job 下是否
  已有相同问题文本且仍是 pending 状态的记录，有则复用其 `question_id`，不新建
- `list_pending_questions(paths, job_id=None, limit=None, offset=0)`
- `list_answered_questions(paths, job_id=None, limit=None, offset=0)` — 历史记录面板用
- `get_question(paths, question_id)`
- `submit_answer(paths, question_id, answer_text)` — 新答/改答统一入口，内部：
  `status="answered"`，`answer=answer_text`，`answer_history` 追加一条，`updated_at` 刷新。
  修改已回答的问题时同样调用这个函数，不区分"首次回答"和"修改回答"。
- `list_unconsumed_answers_for_job(paths, job_id)` — 供 `render_prompt()` 调用：取出
  "已回答但还没被下一次 prompt 消费过"的问答对（见 3.3 `consumed` 字段）
- `mark_answers_consumed(paths, question_ids)` — `render_prompt()` 把答案注入 prompt 后
  调用，避免同一个答案在后续多次触发里被反复注入

记录额外带一个内部字段 `consumed: bool`（默认 `False`），语义类似
`reports_store.py` 里 `acknowledged` 的镜像但用途不同：控制"这条已回答的问答对是否已经
被喂给过 agent 一次"，不影响看板历史展示（历史面板不看这个字段，永远展示全部）。

### 3.2 `CronJobState` 新增字段（`evolution/cron_job_workspace.py`）

```python
STATUS_WAITING_FEEDBACK = "waiting_feedback"   # 新增状态：不算失败，不计入 consecutive_failures

# CronJobState 新增字段：
pending_question_ids: list[str] = field(default_factory=list)
```

`run_job()` 收尾时若本步 `StepResult` 携带了新建的 `question_id`，追加进
`pending_question_ids`（去重追加），并把 `status` 置为 `STATUS_WAITING_FEEDBACK`。

### 3.3 `render_prompt()` 新增占位符

- `{{pending_answers}}`：调用 `list_unconsumed_answers_for_job()`，格式化成
  "上次你问过「X」，用户回答：Y" 的列表；渲染完成后调用 `mark_answers_consumed()`。
- `{{unanswered_questions}}`：仍是 `pending` 状态的问题列表，提醒 agent 不要重复提问。
- 两者复用 `_render_condition_block()` 现有的 `{{#name}}...{{/name}}` 条件块机制，
  为空时整段隐藏，不强行插入空标题。

## 4. 新增工具 `ask_user_async`

新增独立工具（不改动 `ask_user` 三兄弟），放在 `tools/user_input.py` 同文件或新文件
`tools/ask_user_async.py`。

- 入参：`question`（必填）、`hint`（可选）、`options`（可选，选择题）。
- 行为：
  1. 从当前执行上下文取 `job_id`（cron 执行时由 `cron_agent_bridge.make_submit_step_fn()`
     注入到工具调用上下文，具体取值方式沿用现有"cron 执行专用上下文"的注入模式）。
  2. 调 `find_pending_by_fingerprint()` 查重，有则直接复用已有 `question_id`。
  3. 无则 `append_question()` 新建一条，并通过 `NotificationDispatcher.dispatch()`
     发一条 `source="cron_question"` 的通知（kanban 恒真兜底渠道，看板必现）。
  4. **立刻返回** `{"status": "pending", "question_id": ...}`，不调用 `interaction.ask()`，
     不阻塞。
- 工具描述里明确告诉模型：这个调用不会等到答案，应把相关子任务标记为搁置、去做其它
  可推进的工作；下次任务触发时如果已有回复会通过 prompt 提供，届时再继续。
- 非 cron 执行上下文（比如普通交互式对话里）调用这个工具时的降级行为：仍然可以正常
  创建问题+发通知，只是没有"下次 cron 触发自动续接"这一环（`job_id` 缺省时用一个固定的
  `"adhoc"` 分组，历史记录里仍可见，只是不会被任何 `render_prompt()` 自动消费）。

## 5. `NotificationDispatcher` 联动

`ask_user_async` 内部直接调用已有的 `NotificationDispatcher.dispatch()`，`source` 取值
`"cron_question"`，`title`/`body` 里带问题原文和 `job_id`；不修改 `dispatcher.py`/
`channels/kanban.py` 本身逻辑，只是新增一个调用方。是否需要把 `cron_question` 加入
`reports_store.py` 的 `_SOURCE_CATEGORY_MAP` 分类表，待实施阶段确认（**倾向不加**——
这类通知不走 `reports_store`，是走新的 `questions_store`，看板需要专门的"待我反馈"
面板而不是塞进现有"待处理汇报"面板，避免用户把"需要作答的问题"和"只读汇报"混淆）。

## 6. API 层

新增路由（并入 `api/routes.py` 或新文件 `api/routes_cron_questions.py`，视实施时
`routes.py` 体量决定）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v1/cron_questions/pending` | 待回答问题列表（分页，可按 job_id 过滤） |
| GET | `/v1/cron_questions/history` | 已回答问题历史（分页，可按 job_id 过滤） |
| POST | `/v1/cron_questions/{id}/answer` | 提交/修改答案，body: `{"answer": "..."}`；
新答、改答统一走这一个接口（`questions_store.submit_answer()` 本身就不区分） |

这几个接口都是纯本地文件读写，耗时可忽略，**不需要**走
`kanban_async_job_mechanism_plan.md` 里那套 LLM 调用专用的异步任务机制（那套机制是为了
应对分钟级不可预测耗时的 LLM/Agent 调用，这里的读写是毫秒级的）。

## 7. Streamlit 看板

在 `apps/mini_agent_kanban/`"关注与通知"tab 下新增一个子面板"待我反馈"：

- **待处理**：列出 `pending` 问题（job 名称、问题原文、hint、选项按钮/文本框），提交后
  调 `POST /v1/cron_questions/{id}/answer`。
- **历史记录**：列出 `answered` 问题，每条展开可见 `answer_history` 全部版本（时间 +
  内容），提供"修改答案"按钮——点开后复用同一个提交表单，再次调用同一个
  `POST /v1/cron_questions/{id}/answer` 接口（覆盖式提交，历史里保留旧版本）。

## 8. 分阶段实施计划

每个阶段完成后：更新本文档的"状态"栏 + 相关 `docs/` 用户向文档，并打包该阶段新增/
修改的文件（保持仓库内目录结构，便于用户直接覆盖解压）。

1. **数据层**：`questions_store.py` + 单元测试
2. **执行链路**：`ask_user_async` 工具 + `CronJobState.STATUS_WAITING_FEEDBACK` +
   `CronJobExecutor` 收尾逻辑 + `render_prompt()` 两个新占位符 + 去重逻辑
3. **API + 通知联动**：路由 + `NotificationDispatcher` 调用点
4. **看板**：Streamlit"待我反馈"面板（待处理 + 历史 + 修改答案）
5. **收尾**：`docs/` 用户向文档、`next_doc` 本文档状态更新为"已实施完成"、端到端测试

## 9. 待确认的开放问题

- 仍有未回答问题时，下次 cron 到期是否应该**照常触发**（默认方案，agent 见机行事去做
  其它部分）还是**跳过本次触发**？本方案默认"照常触发"，如果你更倾向"跳过直到有人
  回答"，实施阶段可以改成可配置项。
- ~~`ask_user_async` 是否需要支持 `ask_user_choice` 那样的严格选项校验~~ ——
  **已确认**：自由文本 + `options` 仅做展示提示，不做强校验。
- ~~仍有未回答问题时下次 cron 到期是否照常触发~~ —— **已确认**：照常触发，
  agent 见机行事去做其它可推进的部分。

## 10. 实施进度

- [x] 阶段1：数据层 `questions_store.py` + 单元测试
- [x] 阶段2：`ask_user_async` 工具 + 执行链路集成
- [x] 阶段3：API + 通知联动
- [x] 阶段4：Streamlit 看板
- [x] 阶段5：文档收尾

## 11. 阶段2实现记录（与设计的差异说明）

实施时做了一处简化，比原方案更干净，记录在这里：

- **没有给 `CronJobState` 新增 `pending_question_ids` 字段。** 原方案设想
  在 `state.json` 里额外记一份"这个 job 关联哪些 question_id"，但
  `questions_store` 里每条问题记录本身已经带 `job_id` 字段——
  `render_prompt()` 和 `CronJobExecutor` 直接按 `job_id` 查询
  `questions_store`（`list_unconsumed_answers_for_job`/
  `list_pending_question_texts_for_job`）就够用，不需要在两处重复记账，
  也不会有"两份记录不同步"的风险。

`STATUS_WAITING_FEEDBACK` 判定时机：`CronJobExecutor.run_job()` 收尾时，
如果本次触发的执行结果原本是"正常完成"（`STATUS_IDLE`），但该 job 通过
`questions_store` 查到仍有未回答的问题，就把最终状态改记成
`STATUS_WAITING_FEEDBACK`（不计入 `consecutive_failures`，`progress_summary`
保留最后一步输出，供下次触发续接）。`STATUS_TIMED_OUT`/
`STATUS_NEEDS_REVIEW` 语义上更紧急，不会被这个判定覆盖。

新增/修改文件清单：
- 新增 `src/mini_agent/evolution/cron_context.py`（thread-local job_id 透传）
- 新增 `src/mini_agent/tools/ask_user_async.py`（异步提问工具）
- 修改 `src/mini_agent/evolution/cron_job_workspace.py`（新增
  `STATUS_WAITING_FEEDBACK`、`{{pending_answers}}`/`{{unanswered_questions}}`
  占位符及其格式化辅助方法、`DEFAULT_PROMPT_TEMPLATE` 和 `ensure()` 最小
  默认模板同步更新）
- 修改 `src/mini_agent/evolution/cron_job_executor.py`（`run_job()` 设置/
  清空 thread-local job_id、收尾时的 waiting_feedback 判定）
- 修改 `src/mini_agent/agent/core.py`（为 `ask_user_async` 注册
  project_root provider）
- 修改 `src/mini_agent/cli/app.py`（注册 `ask_user_async` 工具 import）
- 新增 `tests/test_cron_async_user_feedback.py`（23 条测试，覆盖
  cron_context/工具去重/状态机/占位符渲染）

## 12. 阶段3实现记录（API + 通知联动）

`ask_user_async` 工具在阶段2实施时就已经直接调用了
`NotificationDispatcher.dispatch()`（`source="cron_question"`），第5节"通知
联动"在阶段2就已完成，阶段3不需要再补——这里只补 API 层（第6节）。

按第6节设计原样实施，路由并入现有 `src/mini_agent/api/routes.py`（跟在
`/v1/notifications/pending` 系列端点后面，风格保持一致：`_require_owner()`
鉴权 + `project_root` 就绪性检查 + 延迟 import 存储模块）：

- `GET /v1/cron_questions/pending` — 分页，支持 `job_id` 过滤；用"多取一条
  探测下一页是否存在"的方式给 `has_more`，没有在 `questions_store` 里加
  `count_*` 辅助函数（量级小，没必要）。
- `GET /v1/cron_questions/history` — 同上分页方式，返回完整
  `answer_history`。
- `POST /v1/cron_questions/{id}/answer` — 直接转发到
  `questions_store.submit_answer()`；空答案拒绝（400），
  未知 `question_id` 返回 404。新答/改答复用同一个接口，跟设计文档一致。

未对 `reports_store.py` 的 `_SOURCE_CATEGORY_MAP` 做任何改动（第5节里"倾向
不加"的开放问题，阶段3确认维持不加——`cron_question` 通知走独立的看板
"待我反馈"面板，不复用"待处理汇报"分类体系）。

新增/修改文件清单：
- 修改 `src/mini_agent/api/routes.py`（新增 3 个 `/v1/cron_questions/*`
  路由）
- 新增 `tests/test_cron_questions_api_routes.py`（5 条测试，覆盖
  pending/history 过滤与分页、答案提交与修改、空答案/未知 ID 的错误处理）

## 13. 阶段4实现记录（Streamlit 看板）

按第7节设计原样实施，在 `apps/mini_agent_kanban/`"🔔 关注与通知"tab 下新增
"🙋 待我反馈"子面板，独立成 `@st.fragment`（`_render_cron_questions_panel`），
跟已有的"📋 待处理汇报"面板（`_render_pending_reports_panel`）风格保持一致：
分页用同一套 `_load_more_control()`，翻页/提交只重跑面板本身，不带动整个
`render_notification_tab()`（关注对象列表、tier 配置、通知发送记录）一起
重新请求。

- **待处理**子 tab：逐条展示 `job_id`/问题原文/`hint`；若带 `options`，用
  单选按钮做"参考选项 + 自己输入"二选一的快捷填充（不做强校验，选完仍是
  普通文本框可编辑，跟 §4"自由文本、`options` 仅展示提示"的设计一致）；
  提交调 `POST /v1/cron_questions/{id}/answer`。
- **历史记录**子 tab：每条用 `st.expander` 折叠展示问题/当前答案/完整
  `answer_history`（时间+内容，旧→新排列）；展开后内置一个"修改答案"
  文本框+提交按钮，复用同一个 `answer_cron_question()` 接口——跟设计文档
  一致，不区分首次回答和修改回答的入口。
- `client.py` 新增三个方法：`cron_questions_pending()`/
  `cron_questions_history()`/`answer_cron_question()`，均为对
  `/v1/cron_questions/*` 端点的薄封装，风格与 `notification_pending_reports()`
  系列一致。

未新增看板专用测试——项目里 Streamlit UI 代码（`apps/mini_agent_kanban/app.py`/
`client.py`）一贯不做单元测试，只做 `py_compile` 语法检查，实际交互靠 API
层的 `test_cron_questions_api_routes.py` 覆盖后端逻辑正确性。

新增/修改文件清单：
- 修改 `apps/mini_agent_kanban/client.py`（新增
  `cron_questions_pending`/`cron_questions_history`/`answer_cron_question`
  三个方法）
- 修改 `apps/mini_agent_kanban/app.py`（新增 `_render_cron_questions_panel`
  fragment，接入 `render_notification_tab()`）

## 14. 阶段5实现记录（文档收尾 + 端到端测试）

新增用户向文档 `docs/cron-async-user-feedback-guide.md`，覆盖：机制解决
的问题、`ask_user_async` 工具用法与去重语义、`waiting_feedback` 状态机、
两个 prompt 占位符、看板操作方式、REST API、数据存储结构、已知局限
（不做超时/自动作废、不做语义判重、未回答问题不自动跳过触发）。单篇
文档，未在 `docs/README.md` 索引里单列（该索引只给超过 5 篇同前缀文档群
加导读，零散单篇按文件名查找即可，符合 `documentation-guidelines.md` §2
第 4 条）。

在既有的 [cron-dedicated-execution-guide.md](../docs/cron-dedicated-execution-guide.md)
里做了两处最小交叉引用同步（该文档§6.1/§6.3 列出的占位符/状态清单是
"权威汇总"，新增的东西必须同步进去，否则会造成信息漂移）：
- §6.1 占位符表格补充 `{{pending_answers}}`/`{{unanswered_questions}}`
  两行，并链接到新文档 §5。
- §6.3 `state.json` 状态表补充 `waiting_feedback` 一行，并链接到新文档
  §4。

端到端测试：在 `tests/test_cron_async_user_feedback.py` 新增
`TestEndToEndAcrossApiAndPromptLayers`，贯穿此前分阶段各自测试过的三层
（工具 → API → prompt 渲染），验证拼在一起确实能跑通完整用户故事，
而不只是各层单元测试各自通过：
- `test_full_round_trip_tool_then_api_answer_then_prompt_injection`：
  `ask_user_async` 提问 → `render_prompt()` 能看到未答问题 →
  真实 FastAPI `TestClient` 调 `POST /v1/cron_questions/{id}/answer`
  提交答案 → pending/history 列表各自变化正确 → `render_prompt()` 自动
  注入答案且只注入一次（`consumed` 生效）→ 通过 API 修改答案后答案
  重新出现在下一次渲染里 → `answer_history` 完整保留两版。
- `test_deduplicated_question_across_multiple_triggers_only_notified_once`：
  同一 job 连续两次提出完全相同的问题，API 层 pending 列表只应该有
  一条记录。

全量相关测试跑通：`test_cron_async_user_feedback.py`（25，含新增 2 条
端到端）+ `test_cron_questions_store.py` + `test_cron_questions_api_routes.py`
+ `test_cron_job_workspace_and_executor.py` + `test_cron_job_executor_step_detail.py`
共 106 条全部通过。

新增/修改文件清单：
- 新增 `docs/cron-async-user-feedback-guide.md`（用户向使用指南）
- 修改 `docs/cron-dedicated-execution-guide.md`（§6.1 占位符表、§6.3
  状态表两处交叉引用同步）
- 修改 `tests/test_cron_async_user_feedback.py`（新增
  `TestEndToEndAcrossApiAndPromptLayers`，2 条端到端测试）
- 修改本文档：状态改为"已实施完成"，进度全部勾选
