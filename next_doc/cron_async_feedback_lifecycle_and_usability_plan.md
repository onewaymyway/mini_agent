# Cron 异步用户反馈机制 —— 生命周期与看板可用性复查

> 状态：**方案已确认，E1/E2/E3 均已实施完成**（E3 原记录为"本轮不做"的
> 已知缺口，见 §8 补做记录）
> 关联现有方案：`next_doc/cron_async_user_feedback_mechanism_plan.md`（原始
> 设计）、`next_doc/cron_async_feedback_hardening_plan.md`（D1–D6 并发/时序/
> 忽略语义/模糊去重/归档/孤儿识别加固）
> 关联代码：`src/mini_agent/notification/questions_store.py`、
> `src/mini_agent/notification/config.py`、
> `src/mini_agent/evolution/autonomous_loop.py`、
> `src/mini_agent/evolution/cron_job_workspace.py`、
> `src/mini_agent/api/routes.py`、`apps/mini_agent_kanban/`

## 0. 这轮复查在做什么

前两轮方案（原始设计 + D1–D6 加固）已经把"问答通道本身"打磨得比较扎实：
不阻塞、去重、并发安全、消费时机正确、忽略后不会死循环、旧数据会归档。
这一轮不重复审视通道机制本身，而是换三个角度重新看：

1. **实际应用场景**：一个真实用户会怎么用这套机制、在哪个环节会卡住？
2. **整个流程周期**：从"提问"到"被彻底遗忘/清理"这条时间线上，有没有
   哪一段是设计里没想到、或者想到了但故意留白、现在看来代价超出预期的？
3. **看板可用性**：单纯从 UI 好不好用的角度看（不牵涉后端语义），有没有
   信息该露出来但没露出来、该好找但不好找的地方？

## 1. 问题清单

| 编号 | 角度 | 问题 | 影响 |
|---|---|---|---|
| E1 | 流程周期 | `pending` 状态的问题**永不自动过期**（`cron_async_user_feedback_mechanism_plan.md` §0 明确写的非目标），只能靠用户手动点"忽略"清理。真实场景里，cron 任务是无人值守跑的，用户可能几周甚至几个月都不会去点开"待我反馈"面板——问题会无限堆积，越堆越多，形成两个连锁反应：agent 每次触发都要在 `{{unanswered_questions}}` 里背着一份越来越长的"欠账清单"（可能挤占无关的、agent 本可以自主决断的判断空间）；用户某天终于打开看板，面对几十条不同时期堆积的旧问题，心理负担大到直接想全部忽略掉，而不是逐条甄别哪些还有意义 | 高——这正是你举的例子。原方案把"不做超时"当作对用户负责（"不替用户做主"），但实践下来"永不清理"制造的负担比"可能误关一个还想回答的问题"更大，且"误关"本身已经有低成本的补救路径（agent 可以换个方式重新问） |
| E2 | 看板可用性 | 忽略动作（含 E1 新增的自动关闭）落地后，看板完全不展示"已忽略"记录——`list_dismissed_questions()` 后端接口原方案§15 就写好了，但"看板当前未展示这个列表"。用户忽略一个问题后，这条记录就从视野里彻底消失，无法回头确认"我当初是不是手滑忽略错了""这个问题是不是已经被自动关闭了但我不知道" | 中——原方案已经预留了接口，只是没接 UI，属于遗漏而非重新设计；E1 一旦实装（问题会被系统自动关闭），这个缺口的代价会被放大：用户完全可能不知道某个问题已经"消失"了 |
| E2b | 看板可用性 | "待处理"列表按 `created_at` **倒序**（最新的排最前）展示——这对于"看最近发生了什么"是对的默认顺序，但对于"我该优先处理哪个"是反直觉的：真正需要用户注意力的往往是那些拖得最久、快被 E1 自动关闭的旧问题，它们被埋在列表最下面，翻页才能看到 | 中——纯 UI 排序问题，不涉及后端语义，但会直接影响 E1 上线后用户能不能"赶在自动关闭前处理该处理的问题" |
| E3（未做，记录为已知缺口） | 应用场景 + 看板可用性 | ① 没有批量操作（一次性忽略/回答多条同类问题）；② 没有跨 job 的问题数量提醒（比如 tab 标题上一个未读数角标，用户要点进"关注与通知"才知道有没有新问题，不像 reports 面板那样有更醒目的入口）；③ 没有按"等待时长"或"所属 job"筛选/排序的 UI 控件，`job_id` 参数虽然 API 层已支持过滤，看板 UI 没有暴露筛选框 | 低～中——真实场景下问题量通常不大（个位数到十几条），批量操作和角标提醒的边际收益没有 E1/E2 高；本轮明确**不实现**，留作后续如果问题量级上升再考虑，避免过度设计 |

## 2. E1 方案：长期无人回答的问题自动消耗

### 2.1 设计取舍

不是"删除"，是复用已有的 `dismiss_question()` 状态转换（`pending →
dismissed`），只是触发来源从"用户点按钮"变成"维护性 tick 判定超时"。
选择复用而不是新增一个 `expired` 状态，理由：

- `render_prompt()` 已有的 `{{dismissed_questions}}` 占位符语义天然就是
  "这个问题不会再被提醒去问了"，超时关闭和手动忽略在**这一点**上是
  一致的（都不该再出现在 `{{unanswered_questions}}` / 出现在
  `find_or_create_question()` 去重判定里），不需要在 render_prompt/
  去重逻辑里再加一条并行的状态分支。
- 但"agent 该用什么语气理解这条记录"是不一致的——手动忽略是"用户明确
  拒绝，绝对不要再问"；超时关闭是"用户可能只是没来得及看，不代表不
  需要这个答案"。所以在 `dismiss_question()` 内新增 `reason` 参数
  （`"manual"` | `"stale_timeout"`），落盘为 `dismiss_reason` 字段，
  `_format_dismissed_questions()` 据此给 agent 两种不同措辞的提示。

### 2.2 具体行为

- 新增 `questions_store.expire_stale_pending_questions(paths, *,
  stale_after_days=14, job_id=None)`：按 `created_at`（问题被提出的
  时间，不是 `updated_at`——命中去重时不会碰这个字段，只有
  `created_at` 能准确反映"挂了多久没人理"）判定，超过阈值的 `pending`
  记录转为 `dismissed` + `dismiss_reason="stale_timeout"`。
- 阈值可配置：`.agent/notification/config.yaml` 新增
  `cron_question_stale_after_days`（默认 14 天），`<=0` 时关闭该机制
  （等价于回到原始"永不过期"行为，供不需要这个功能的用户显式关掉）。
- 调用时机：复用 `AutonomousLoop._tick_maintenance()` 里
  `archive_old_records()` 已有的"24 小时最多跑一次"节流窗口，同一批
  维护性操作里顺带做，不新增一条独立的定时器。
- **不静默**：每关闭一条问题，补发一条 kanban 通知（`source=
  "cron_question_expired"`），标题点名是哪个任务、正文附问题原文和
  阈值天数——原始设计里"忽略"已经是低调的（用户主动点的，不需要额外
  通知自己），但"系统替用户做主"这件事必须让用户知道发生了，否则
  "问题凭空消失"本身就是一种新的困惑。
- `render_prompt()` 两处联动：`{{unanswered_questions}}` 每条问题
  附带"已等待 N 天"，让 agent（间接也通过摘要让用户）感知到时间压力；
  `{{dismissed_questions}}` 区分展示，超时关闭的条目提示 agent"如果
  仍然关键，可以换个更容易被顺手回答的方式重新问一次"，而不是像手动
  忽略那样说"不要再问"。

### 2.3 非目标（这轮仍然不做）

- 不做"用户来得及回答就撤销自动关闭"的特殊路径——`dismiss_question()`
  本身对已忽略问题是幂等的，用户如果真的想回答一个已经被自动关闭的
  问题，最自然的路径是等 agent 下次因为业务需要重新问一次（提示已经
  写好了引导 agent 这么做），不需要额外做"复活"功能徒增复杂度。
- 不做基于问题内容/重要性的差异化阈值（比如"选项类问题"和"自由文本
  问题"用不同的过期时间）——固定阈值 + 可配置已经能覆盖"用户觉得默认
  值不合适就自己改"的诉求，没必要引入分类判断。

## 3. E2 方案：看板可用性补齐

- 新增 `GET /v1/cron_questions/dismissed`（分页，可按 `job_id` 过滤，
  返回含 `dismiss_reason` 的完整记录），对齐 `pending`/`history` 两个
  已有端点的实现风格。
- "🙋 待我反馈"面板新增第三个子 tab"已忽略"，逐条展示所属任务、问题
  原文、关闭时间，并用不同的徽标区分"🙈 用户手动忽略" / "⏱️ 长期未
  回答，系统自动关闭"——回应 E2 指出的"忽略变成黑洞"问题。
- "待处理"子 tab：改为按等待时长**升序**重排（等得最久的排最前），
  并给每条问题加上"已等待 N 天"的徽标（≥7 天变红，提示接近默认的
  14 天自动关闭阈值），面板顶部说明文案同步提示存在自动关闭机制。

E2b 里提到的"角标/批量操作/筛选框"（E3 清单）本轮**不做**，见下节。

## 4. 明确列为本轮不做（E3）

> **[已过期]** 本节是当时的决定，后来在 §8 补做了下面列出的前两项，
> 详见 §8。保留本节原文不改，作为决策变化的记录。

对照第 1 节表格的 E3 行，这轮不实现：

- Tab 标题角标提醒（未读数）
- 批量忽略/批量回答
- 看板 UI 层面的 job_id 筛选框、等待时长排序控件（API 已支持
  `job_id` 过滤，只是 UI 没暴露）

原因：当前真实问题量级不大（个位数到十几条），这几项的边际收益暂时
低于实现和维护成本；E1 上线后如果发现即便有自动过期问题量依然持续
偏高，再回头做。这里显式记录而不是略过，是为了避免"以为顺手就一起做了
但其实漏了"——`cron_async_user_feedback_mechanism_plan.md` §15 就是
一次"设计里承诺了、后来复查才发现没落地"的教训，本文档不重复这个模式。

## 5. 分阶段实施计划

1. **E1 数据层 + 维护性 tick**：`expire_stale_pending_questions()` +
   `dismiss_question()` 的 `reason` 参数 + `config.yaml` 新增配置项 +
   `autonomous_loop.py` 接入 + kanban 通知
2. **E1 prompt 联动**：`{{unanswered_questions}}` 等待天数 +
   `{{dismissed_questions}}` 区分措辞
3. **E2 API + 看板**：`GET /cron_questions/dismissed` + "已忽略"子 tab +
   "待处理"排序/徽标调整
4. **收尾**：本文档状态更新、`docs/cron-async-user-feedback-guide.md`
   同步更新、回归测试

## 6. 实施进度

- [x] 阶段1：E1 数据层 + 维护性 tick
- [x] 阶段2：E1 prompt 联动
- [x] 阶段3：E2 API + 看板
- [x] 阶段4：收尾（本文档 + 用户向文档 + 回归测试）

## 7. 实现记录（与设计的差异说明）

按第 5 节设计原样实施，没有出现需要偏离方案的情况。补充两点实现细节：

- `expire_stale_pending_questions()` 的异常兜底、加锁范围跟
  `dismiss_question()`/`archive_old_records()` 保持同一套既有约定
  （`ExclusiveFileLock` 包裹读改写、`atomic_write_jsonl` 写文件、
  失败时 `log_exception` 后返回空结果，不向上抛异常）——维护性操作
  失败不能影响 cron 主流程或问答功能本身的可用性，这一约定从
  `cron_async_feedback_hardening_plan.md` D1/D5 延续下来，本轮未新增
  例外。
- `_format_unanswered_questions()` 计算等待天数用
  `max(0, int((now - created_at) // 86400))`，`created_at` 缺失（理论
  上不会发生，但防御性处理）时按"刚问的问题"展示，不抛异常也不显示
  负数天数。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（新增
  `expire_stale_pending_questions`、`DISMISS_REASON_MANUAL`/
  `DISMISS_REASON_STALE_TIMEOUT` 常量、`dismiss_question()` 新增
  `reason` 参数）
- 修改 `src/mini_agent/notification/config.py`（新增
  `cron_question_stale_after_days` 配置项，默认 14 天）
- 修改 `src/mini_agent/evolution/autonomous_loop.py`（`_tick_maintenance()`
  接入自动过期 + 逐条 kanban 通知，跟归档共用 24 小时节流窗口）
- 修改 `src/mini_agent/evolution/cron_job_workspace.py`
  （`{{unanswered_questions}}` 附带等待天数、`{{dismissed_questions}}`
  区分手动/超时两种措辞）
- 修改 `src/mini_agent/api/routes.py`（新增
  `GET /v1/cron_questions/dismissed`）
- 修改 `apps/mini_agent_kanban/client.py`（新增
  `cron_questions_dismissed`）
- 修改 `apps/mini_agent_kanban/app.py`（"待处理"子 tab 按等待时长排序 +
  年龄徽标 + 面板说明文案更新；新增"已忽略"子 tab）
- 修改 `tests/test_cron_questions_store.py`（新增
  `TestExpireStalePendingQuestions`、`TestDismissReason`，共 9 条）
- 修改 `tests/test_cron_questions_api_routes.py`（新增
  `test_dismissed_endpoint_*` 2 条）
- 修改 `tests/test_cron_async_user_feedback.py`（新增
  `test_stale_timeout_dismissed_question_gets_different_note_than_manual`、
  `TestUnansweredQuestionsAgeHint` 2 条）
- 修改 `docs/cron-async-user-feedback-guide.md`（同步 E1/E2 行为、
  更新"已知局限"一节、REST API 表新增 `dismissed` 端点）
- 修改本文档：状态改为"已实施完成"，进度全部勾选

全量相关测试跑通：`test_cron_questions_store.py`（56）+
`test_cron_async_user_feedback.py`（35）+
`test_cron_questions_api_routes.py`（11）+
`test_cron_job_workspace_and_executor.py`/`test_cron_job_executor_step_
detail.py`（现有回归覆盖，未改动仍全部通过）共计相关测试全部通过。

## 8. 补做记录：E3（原记录为"本轮不做"，现已实现）

第 4 节原文列出了三项明确不做的看板可用性项：tab 角标未读数、批量
忽略/批量回答、job_id 筛选/等待时长排序控件，理由是"当前问题量级不大，
边际收益低于实现成本"。这一轮复查后决定补上（属于按需推进，不是发现了
遗漏），只在**看板 UI 层**做，不改动数据层/API 语义，具体取舍：

- **tab 数量角标**：没有新增后端 count 端点——`questions_store` 目前
  没有 `count_*` 辅助函数，量级小（个位数到十几条）没必要为了一个角标
  专门加。复用了本来就要为"job_id 筛选下拉框"发的 `limit=200` 探测
  请求，用 `len(questions)` 拼角标，超过一页时显示"+"提示还有更多，
  不是精确总数（跟原方案里"待处理/历史/已忽略"三个列表接口本身就没有
  `total` 字段的既有设计一致，不引入新的精确计数语义）。
- **批量忽略**：没有新增批量后端端点，复用已有的单条
  `POST /v1/cron_questions/{id}/answer` 忽略接口，UI 层循环调用——跟
  `render_notification_tab()` 里"📋 待处理汇报"面板的"批量标记已读"是
  同一个模式（那边也是循环调用单条接口，不是后端批量语义）。只对
  "待处理"子 tab 做，没做"批量回答"——回答需要用户对每条问题分别输入
  答案文本，"批量"在这个场景下没有意义（不像"忽略"是无参数的单一
  动作）。
- **job_id 筛选**：三个子 tab 都补了一个下拉框，选项来自当前已加载的
  一批问题里的 `job_id` 去重排序（同样复用 `limit=200` 探测请求），
  API 层 `job_id` 参数本来就支持，只是接上 UI。
- **等待时长排序控件**：§3 已经把"待处理"改成默认按等待时长升序排列，
  这就是"排序"要解决的问题本身，没有再加一个独立的排序下拉框——如果
  以后有"按创建时间/按 job 排序"的多种排序需求再考虑加控件，当前只有
  一种排序方式时加选择器是过度设计。

新增/修改文件清单：
- 修改 `apps/mini_agent_kanban/app.py`（`_render_cron_questions_panel`：
  三个子 tab 标题数量角标、`job_id` 筛选下拉框、"待处理"子 tab 批量
  勾选 + "🙈 批量忽略（已选 N 条）"按钮）
- 修改 `docs/cron-async-user-feedback-guide.md`（§6 补充角标/筛选/批量
  说明、§9"已知局限"里 E3 一条改为"已解决"、§10 文件清单更新
  `app.py` 条目、开头元信息进度行更新）
- 修改本文档：状态改为"E1/E2/E3 均已实施完成"，新增本节（§8）
