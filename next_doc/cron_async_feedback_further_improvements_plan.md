# Cron 异步用户反馈机制 —— 后续改进方向规划

> 状态：**方案已确认，阶段1/阶段2/阶段3已实施完成**
> 前置文档：`next_doc/cron_async_user_feedback_mechanism_plan.md`（原始设计）、
> `next_doc/cron_async_feedback_hardening_plan.md`（D1–D6 加固）、
> `next_doc/cron_async_feedback_lifecycle_and_usability_plan.md`（E1–E3
> 生命周期与看板可用性）
> 关联代码：`src/mini_agent/notification/questions_store.py`、
> `src/mini_agent/api/routes.py`、`apps/mini_agent_kanban/`、
> `src/mini_agent/evolution/cron_job_workspace.py`、
> `src/mini_agent/evolution/cron_scheduler.py`

## 0. 背景

前三轮方案已经把"问答通道机制本身"（原始设计）、"并发/时序/清理正确性"
（D1–D6）、"生命周期与看板可用性"（E1–E3）都打磨过了。这一轮是在现有
实现之上继续复查，找出还站得住脚的改进点，不是发现了此前遗漏的承诺项
（跟 `cron_async_user_feedback_mechanism_plan.md` §15 或
`cron_async_feedback_lifecycle_and_usability_plan.md` §8 那种"补做"性质
不同）。

## 1. 问题清单

| 编号 | 问题 | 影响 | 优先级 |
|---|---|---|---|
| F1 | 看板 tab 角标（E3 新增）用 `limit=200` 探测请求的 `len(questions)` 拼出来，只是"够用的近似值"：超过 200 条时角标固定显示"200+"不再变化，且这个探测请求本身要多拉 200 条记录的正文（question/hint/answer_history 等），比纯计数浪费 | 中——当前问题量级下几乎不会触发"超过 200 条"，但角标语义本身不精确，且 E1 的自动关闭阈值如果被用户调大，量级可能上升 | 高 |
| F2 | `dismiss_question()` 手动忽略时说不出"为什么忽略"，跟 growth_advisor 候选忽略（`_GROWTH_DISMISS_REASON_OPTIONS`）的既有设计对比明显缺一个维度；这个信号如果收集起来，将来 agent/growth 机制可以用来学习"什么样的问题容易被用户忽略、以后少问这类" | 中——不是马上就有消费方，但现在不采集，以后想补历史数据是采不回来的（`dismiss_question` 调用点已经散在多处：手动/自动过期/未来可能的批量） | 高 |
| F3 | `ask_user_async` 没有"紧急程度"概念，所有问题在看板里权重完全一样，只能靠"等待天数"这个纯时间维度排序；但显然有些问题是"不答就没法继续这个子任务"，有些是"答了更好但不影响主线" | 高——直接影响用户"该优先处理哪个"的判断质量，是这次复查里认为最值得做的可用性改进 | 高 |
| F4 | E1 的自动关闭阈值 `cron_question_stale_after_days` 是全局配置，没有按 job 覆盖的能力；不同频率的 job（每天巡检 vs 每周汇总）对"多久算没人理"的合理阈值天然不同 | 中——真实场景下用户可能确实只有一两个 job 触发这套机制，全局配置够用；但一旦 job 数量和调度频率差异变大，全局阈值会两头不讨好 | 中 |
| F5 | 忽略/回答动作没有语义级审计——`answer_history` 记录了改答案的轨迹，但"同一语义问题被反复问、反复忽略"这件事目前完全没有痕迹，用户在看板上看不出"这个问题我已经忽略过几次了" | 中——D4 的模糊去重能防止同一批未回答问题里的重复通知，但忽略之后 agent 换个说法重问、用户又忽略，这个"重复忽略"的模式目前不可见 | 中 |
| F6 | `{{unanswered_questions}}`/`{{dismissed_questions}}` 两个占位符渲染时没有做条数/字符数上限保护，长期堆积多条未回答问题时可能把 prompt 撑得很长 | 中——需要先确认现状是否已有隐含上限（`{{dismissed_questions}}` 文档里写的是"最近 20 条"，`{{unanswered_questions}}` 需要复查代码确认） | 中 |
| F7 | 看板三个子 tab 的 job_id 筛选下拉框（E3 新增）展示的是原始 `job_id`（如 `user:ab12cd34`），用户分不清对应哪个具体任务，需要切到别的 tab 查 | 低——锦上添花，等其它几项做完再看要不要做 | 低 |

## 2. 各项方案设计

### F1：精确计数，替代探测请求近似值

- 新增 `questions_store.count_questions(paths, *, status=None, job_id=None) -> int`：
  只统计数量，不拼列表——遍历 `_load_all()` 结果计数，不需要排序/切片，
  比 `list_pending_questions(limit=200)` 更轻。
- 新增 API `GET /v1/cron_questions/counts?job_id=`：一次性返回
  `{"pending": N, "answered": N, "dismissed": N}`，看板一次请求拿到三个
  tab 的角标数字，不用像 E3 那样分别发三次 `limit=200` 探测。
- 看板：tab 角标改用这个新接口的精确值；job_id 筛选下拉框的选项来源
  不变（仍从当前已加载的一批问题里取 `job_id` 去重，这个不需要精确
  计数）。
- 不改变 `list_pending_questions()` 等既有分页接口的行为契约。

### F2：忽略原因（可选）

- `dismiss_question(paths, question_id, reason=DISMISS_REASON_MANUAL, note=None)`
  新增可选 `note` 参数，落盘为 `dismiss_note` 字段（旧数据没有这个字段，
  `.get("dismiss_note")` 兜底为 `None`，不强制回填）。
- API `POST /v1/cron_questions/{id}/dismiss` body 新增可选
  `{"note": "..."}`。
- 看板"待处理"子 tab 的"🙈 忽略这个问题"按钮旁，参考 growth 面板的
  `_GROWTH_DISMISS_REASON_OPTIONS` 模式，加一个可选的原因下拉框（"不再
  需要"/"已通过其它方式解决"/"问题问得不清楚"/"不说明原因"），选择后
  随忽略请求一起提交；批量忽略（E3 新增）不支持逐条选原因，统一记
  "不说明原因"（跟 growth 面板拖拽式批量操作对"细化原因"的取舍一致，
  见 `growth_advisor` 相关代码里的注释）。
- 已忽略列表（"已忽略"子 tab）展示 `dismiss_note`（如果有）。
- 非目标：不建立"原因分类 → 自动分析/推荐"的下游消费逻辑，本轮只做
  采集和展示，留数据给未来判断用。

### F3：问题紧急程度（urgency）

- `ask_user_async` 工具新增可选入参 `urgency`（`"blocking"` | `"normal"`，
  默认 `"normal"`）：`"blocking"` 表示"不答这个问题，这个子任务确实没法
  继续"；`"normal"` 表示"答了更好，但不影响 agent 继续做其它可推进的
  部分"（跟原方案 §0 目标 1"标记为搁置、继续处理其它可推进的部分"的
  语义呼应——如果 agent 用了 `urgency=blocking`，意味着它判断这次触发
  没有其它可推进的部分了）。
- `questions_store.append_question()`/`find_or_create_question()` 落盘
  `urgency` 字段，旧数据没有这个字段按 `"normal"` 兜底。
- 看板"待处理"子 tab：`urgency="blocking"` 的问题用更醒目的标记
  （如红色"⛔ 阻塞"徽标）区分，排序时优先于 `urgency="normal"` 的问题
  （在等待时长排序的基础上，`blocking` 整体排在 `normal` 前面，同一组
  内部仍按等待时长升序）。
- `{{unanswered_questions}}` 占位符渲染时，`blocking` 的问题排在前面
  并加"（阻塞）"标记，帮助 agent/用户都能一眼看出哪些问题更紧急。
- 工具描述里明确告诉模型：`urgency` 是 agent 自己的判断，不需要每次都
  纠结要不要标，拿不准时用默认值 `"normal"` 即可，不强制要求。

### F4：按 job 覆盖自动关闭阈值

- cron job 的 `state.json`（或对应的 job 配置结构，实施时确认具体挂载
  位置）新增可选字段 `question_stale_after_days_override`：不设置时
  沿用全局 `cron_question_stale_after_days`。
- `expire_stale_pending_questions()` 调用时优先取该 job 的覆盖值，取不
  到再回退全局配置。
- 看板/API 暂不提供在线编辑这个覆盖值的入口（沿用项目里"部分配置只能
  直接改文件"的既有风格，比如 watchlist.yaml 也是只读展示），本轮只做
  读取生效，不做编辑 UI。

### F5：忽略语义审计（重复忽略提示）

- `expire_stale_pending_questions()`/`dismiss_question()` 在写入新的
  `dismissed` 记录前，用 D4 已有的模糊匹配逻辑，检查同一 `job_id` 下
  是否存在语义相似且状态也是 `dismissed` 的历史记录，若有则在新记录上
  额外记 `repeat_dismiss_count`（这一条是第几次语义重复被忽略）。
- 看板"已忽略"子 tab：`repeat_dismiss_count >= 2` 的记录加一条提示
  "这个问题已经被忽略过 N 次了，agent 可能一直在换着法子问同一件事，
  如果确实不需要，可以考虑在任务 prompt 里显式说明不需要这项信息"。
- 非目标：不自动做任何干预（比如自动升级为"强制不再问"），只做
  可见性提示，交给用户判断。

### F6：占位符长度保护

- 先复查 `cron_job_workspace.py` 现状：`{{dismissed_questions}}` 文档
  写的是"最近 20 条"，需要确认代码里是否真的有这个截断、`{{unanswered_
  questions}}` 是否也有类似保护。
- 如果确认没有保护，补上：`{{unanswered_questions}}` 限制最多渲染
  N 条（默认沿用 `{{dismissed_questions}}` 同款的 20 条），超出部分
  用一行"还有 M 条更早的未回答问题，见看板"收尾，不整段截断导致 markdown
  结构损坏。
- 如果复查后发现已有保护，本项降级为"确认现状符合预期，文档补充
  说明"，不算新增代码改动。

### F7：job 可读名称

- 待 F1–F6 完成后再评估是否要做，本轮不展开设计。

## 3. 非目标

- 不改变已有占位符 `{{pending_answers}}`/`{{unanswered_questions}}`/
  `{{dismissed_questions}}` 的名称和基本语义，F3/F6 都是在原有基础上
  增量（加字段、加排序、加截断），不破坏向后兼容。
- 不引入新的外部依赖，F1 计数、F2 原因采集、F3 紧急度、F5 重复计数
  全部用现有的 jsonl 存储 + `ExclusiveFileLock`/`atomic_write_jsonl`
  既有约定实现。
- F4/F7 本轮只做设计和明确排期，不承诺在同一批阶段内全部落地——按
  第 4 节的分阶段计划推进，允许分批交付。

## 4. 分阶段实施计划

每个阶段完成后：更新本文档"实施进度"勾选项 + 回填"阶段N实现记录" +
同步 `docs/cron-async-user-feedback-guide.md` 相关章节（若该阶段改变了
用户可观察行为）+ 打包该阶段新增/修改文件（保持仓库目录结构）。

1. **F1 精确计数**：`count_questions()` + `GET /cron_questions/counts` +
   看板角标改用精确值
2. **F2 忽略原因**：`dismiss_question()` 新增 `note` 参数 + API + 看板
   忽略原因下拉框 + 已忽略列表展示
3. **F3 问题紧急程度**：`ask_user_async` 新增 `urgency` 入参 + 存储 +
   看板徽标与排序 + `{{unanswered_questions}}` 联动
4. **F5 忽略语义审计**：`repeat_dismiss_count` 计算 + 看板提示
5. **F6 占位符长度保护**：复查现状 + 视情况补齐截断
6. **F4 按 job 覆盖阈值**：`state.json` 覆盖字段 + `expire_stale_pending_
   questions()` 联动
7. **收尾**：`docs/cron-async-user-feedback-guide.md` 全量同步、本文档
   状态更新为"已实施完成"、回归测试

F7（job 可读名称）不排进以上阶段，留待后续视需要再单独立项。

## 5. 实施进度

- [x] 阶段1：F1 精确计数
- [x] 阶段2：F2 忽略原因
- [x] 阶段3：F3 问题紧急程度
- [ ] 阶段4：F5 忽略语义审计
- [ ] 阶段5：F6 占位符长度保护
- [ ] 阶段6：F4 按 job 覆盖阈值
- [ ] 阶段7：收尾

## 6. 阶段1实现记录（F1 精确计数）

按第 2 节 F1 方案原样实施，没有出现需要偏离方案的情况。

- 新增 `questions_store.count_questions(paths, *, status=None, job_id=None)`：
  遍历 `_load_all()` 结果按 `status`/`job_id` 过滤后 `len()`，不排序不
  切片，语义上是 `list_pending_questions()` 等分页函数的"计数专用版"。
- 新增 API `GET /v1/cron_questions/counts?job_id=`，返回
  `{"pending": N, "answered": N, "dismissed": N}`，三次调用
  `count_questions()`（分别传 `STATUS_PENDING`/`STATUS_ANSWERED`/
  `STATUS_DISMISSED`），风格跟其余 `/cron_questions/*` 端点一致
  （`_require_owner()` 鉴权 + `project_root` 就绪性检查 + 延迟 import）。
- `client.py` 新增 `cron_questions_counts(job_id="")`，薄封装。
- 看板 `_render_cron_questions_panel`：tab 角标改为调用这个新接口一次，
  取代 E3 阶段"三个 `limit=200` 探测请求各自算 `len(questions)`"的做法；
  `limit=200` 请求仍然保留，但只用于给 job_id 筛选下拉框提供选项来源，
  不再兼职算角标。角标数字为 0 时不显示（跟 E3 阶段行为一致，避免
  "待处理 (0)"这种没有信息量的标签）。
- 失败降级：`counts_resp` 请求失败（`_error`）时三个角标数字都置为
  `None`，`_tab_count(None)` 返回空字符串，tab 标题退化为不带角标，
  不抛异常也不影响下面的问答功能本身。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（新增
  `count_questions`）
- 修改 `src/mini_agent/api/routes.py`（新增
  `GET /v1/cron_questions/counts`，路由索引注释同步更新）
- 修改 `apps/mini_agent_kanban/client.py`（新增 `cron_questions_counts`）
- 修改 `apps/mini_agent_kanban/app.py`（tab 角标改用精确计数接口）
- 修改 `tests/test_cron_questions_store.py`（新增 `TestCountQuestions`，
  3 条）
- 修改 `tests/test_cron_questions_api_routes.py`（新增
  `test_counts_endpoint_*`，2 条）
- 修改 `docs/cron-async-user-feedback-guide.md`（§6/§7/§10 同步新接口）

全量相关测试跑通：`test_cron_questions_store.py`（59）+
`test_cron_questions_api_routes.py`（13）+
`test_cron_async_user_feedback.py`/`test_cron_job_workspace_and_executor.py`
（79，未改动仍全部通过）。

## 7. 阶段2实现记录（F2 忽略原因）

按第 2 节 F2 方案原样实施，有一处小取舍跟原方案略有不同：原方案设想
"批量忽略不支持逐条选原因，统一记'不说明原因'"——实际实现时选择让
批量忽略**完全不传 note**（等价于不写入 `dismiss_note` 字段），而不是
显式写入"不说明原因"这个字符串。理由：`dismiss_note` 字段本来就是
"不存在 = 没说明原因"的语义（旧数据、自动过期、单条忽略选默认选项时
都是字段不存在），批量忽略再显式写一个"不说明原因"的字符串反而制造了
"该说的都说了但其实什么都没说"的冗余数据，不如保持字段不存在这一种
"没说明"的表示方式。

- `questions_store.dismiss_question()` 新增可选 `note` 参数，写入
  `dismiss_note` 字段；不传/传空串都不写入该字段（幂等重复调用也不会
  用后一次的空 note 覆盖已记录的 note，见
  `test_dismiss_note_not_overwritten_on_idempotent_repeat_call`）。
- API `POST /v1/cron_questions/{id}/dismiss` body 新增可选
  `{"note": "..."}`；不带 body（原有调用方式）行为完全不变。
- `client.py` 的 `dismiss_cron_question()` 新增可选 `note` 参数。
- 看板"待处理"子 tab：每条问题的"提交回答"和"忽略这个问题"之间新增
  一个"忽略原因（可选）"下拉框（`_CRON_QUESTION_DISMISS_REASON_OPTIONS`），
  默认"（不说明原因）"，选了别的选项才会带 note 一起提交；批量忽略
  按钮不带原因选择器，维持"逐条勾选 + 循环调用单条接口"不变，只是这次
  调用时不传 note。
- 看板"已忽略"子 tab：`dismiss_note` 存在时在问题下方多展示一行
  "忽略原因：xxx"。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（`dismiss_question`
  新增 `note` 参数）
- 修改 `src/mini_agent/api/routes.py`（`/dismiss` 端点解析可选 body）
- 修改 `apps/mini_agent_kanban/client.py`（`dismiss_cron_question` 新增
  `note` 参数）
- 修改 `apps/mini_agent_kanban/app.py`（新增
  `_CRON_QUESTION_DISMISS_REASON_OPTIONS`；"待处理"子 tab 忽略原因
  下拉框；"已忽略"子 tab 展示 `dismiss_note`）
- 修改 `tests/test_cron_questions_store.py`（新增 4 条 `note` 相关测试）
- 修改 `tests/test_cron_questions_api_routes.py`（新增 2 条 `note`
  相关测试）
- 修改 `docs/cron-async-user-feedback-guide.md`（§6/§7/§10 同步 note
  参数）

全量相关测试跑通：`test_cron_questions_store.py`（63）+
`test_cron_questions_api_routes.py`（15）+
`test_cron_async_user_feedback.py`/`test_cron_job_workspace_and_executor.py`
（79，未改动仍全部通过），共 157 条全部通过。

## 8. 阶段3实现记录（F3 问题紧急程度）

按第 2 节 F3 方案原样实施，一处小调整：`ask_user_async` 工具函数签名用
`urgency: str = ""`（空字符串默认值）而不是 `Optional[str] = None`——
跟同一工具里 `hint: str = ""` 的既有风格保持一致（这个工具的其它可选
字符串参数都是这个写法），传空串到 `questions_store.normalize_urgency()`
一样会兜底成 `"normal"`，语义没有差别。

- `questions_store.py` 新增 `URGENCY_BLOCKING`/`URGENCY_NORMAL` 常量和
  `normalize_urgency()`（做成公开函数，因为 `cron_job_workspace.py` 需要
  跨模块调用它判断某条记录是否 blocking，不适合用下划线私有名）。
- `append_question()`/`find_or_create_question()` 都新增可选 `urgency`
  参数，落盘为 `urgency` 字段（新记录一律经过 `normalize_urgency()` 归一，
  不存在"非法值污染存储"的情况）。**去重命中已存在记录时不会用这次调用
  的 urgency 覆盖已记录的值**——语义上去重命中意味着"这其实是同一个
  问题"，紧急程度应该以第一次提出时的判断为准。
- `tools/ask_user_async.py`：工具 schema 新增可选 `urgency` 参数
  （`"normal"` | `"blocking"`，枚举），description 里说明"拿不准用默认
  值就行，不强制要求"；新问题被判定为 `blocking` 时，kanban 通知标题
  额外加 `⛔` 前缀和"被阻塞"字样，跟 `normal` 问题的通知区分开。
- `questions_store.list_pending_question_texts_for_job()`（供
  `{{unanswered_questions}}` 占位符用）排序改为"blocking 组在前，组内
  仍按 created_at 正序"；`cron_job_workspace._format_unanswered_questions()`
  给 blocking 的问题加"（阻塞）"文本前缀。
- 看板"待处理"子 tab：排序在 E2 的"按等待时长升序"基础上叠一层
  "blocking 整体排在 normal 前面"；每条问题标题旁加一个红色"⛔ 阻塞"
  徽标（有别于"已等待≥7天"的红色"已等待 N 天"徽标，两者可以同时出现）。
- 看板"历史记录"/"已忽略"子 tab 未改动——`urgency` 是"这个问题还没被
  回答时有多紧急"的信号，问题一旦有了结果（回答/忽略），紧急程度已经
  不再是决策依据，不需要在这两个 tab 里展示。

新增/修改文件清单：
- 修改 `src/mini_agent/notification/questions_store.py`（`URGENCY_*`
  常量、`normalize_urgency()`、`append_question`/`find_or_create_question`
  新增 `urgency` 参数、`list_pending_question_texts_for_job()` 排序调整）
- 修改 `src/mini_agent/tools/ask_user_async.py`（工具 schema 新增
  `urgency` 参数、blocking 通知标题加前缀）
- 修改 `src/mini_agent/evolution/cron_job_workspace.py`
  （`_format_unanswered_questions()` 加"（阻塞）"前缀）
- 修改 `apps/mini_agent_kanban/app.py`（"待处理"子 tab 排序叠加
  blocking 优先、新增"⛔ 阻塞"徽标）
- 修改 `tests/test_cron_questions_store.py`（新增 `TestUrgency`，9 条）
- 修改 `tests/test_cron_async_user_feedback.py`（新增 2 条工具层测试 +
  1 条 `{{unanswered_questions}}` 排序/标记测试）
- 修改 `docs/cron-async-user-feedback-guide.md`（§4 工具参数、§6 看板
  说明、§10 文件清单同步 F3）

全量相关测试跑通：`test_cron_questions_store.py`（72）+
`test_cron_questions_api_routes.py`（15）+
`test_cron_async_user_feedback.py`（38）+
`test_cron_job_workspace_and_executor.py`（44）+
`test_cron_job_runner.py`/`test_cron_scheduler_reap_stale_jobs.py`
（19，未改动仍全部通过），共 188 条全部通过。
