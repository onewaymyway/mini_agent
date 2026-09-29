# 看板 Cron 详情弹窗内删除失效修复（kanban_cron_detail_dialog_delete_rerun_bugfix）

> 范围：仅 Streamlit 看板 `apps/mini_agent_kanban/app.py`（遵循 `CLAUDE.md`
> 约定，不改 `mini_agent_kanban_x`）。后端接口无改动。
> 与 [`kanban_cron_delete_consistency_bugfix.md`](kanban_cron_delete_consistency_bugfix.md)
> 是两个不同的问题：那份修的是后端 `/cron/jobs` 四个路由解析到不同
> `CronScheduler` 实例；本份修的是前端弹窗交互。

## 问题现象

「⏰ Cron 任务」Tab → 某个任务卡片点「📋 详情 / 操作」→ 在弹窗里点「🗑️ 删除」：

1. 详情弹窗直接消失，没有出现「⚠️ 确认删除」按钮；
2. 背后的 Cron 任务还在；
3. 再次点开详情，弹窗里显示的已经是「⚠️ 确认删除」（确认态残留）。

看起来像是"前一步把详情页面错误地关掉了"。

## 根因

`_show_cron_job_detail_dialog()` 用 `@st.dialog` 打开详情，`st.dialog` 的正文
本身是一个 `st.fragment`：弹窗内的按钮点击默认只会**局部重跑弹窗自己**，
弹窗保持打开。

但 `_render_cron_job_details()` 里的删除区块在点「🗑️ 删除」时写入
`cron_tab_confirm_delete_<job_id>` 标记后，调用的是不带参数的 `st.rerun()`。
`st.rerun()` 的默认 `scope` 是 `"app"`（整页重跑）。整页重跑时脚本从头执行，
而"打开弹窗"这件事只发生在"用户点了卡片上的详情按钮"那一轮
（`_render_cron_job_card` 里 `st.button(...)` 返回 True 时才调用
`_show_cron_job_detail_dialog`），这一轮没有点它，所以弹窗不会被重新调起，
直接消失。而确认态标记已经写进 `st.session_state` 并保留下来，于是下次打开
详情看到的就是确认态。

同一类问题还有两个衍生隐患，一并处理：

- **删除失败时错误提示丢失**：失败分支也是 `st.error(...)` 之后立刻
  `st.rerun()`，整页重跑把弹窗和错误提示一起清掉，用户看不到失败原因。
- **确认态残留**：用户在确认态下通过 ✕ / Esc / 点击弹窗外部关闭弹窗，这几种
  关闭方式不触发任何回调，标记会一直留在 `session_state`，下次打开就直接是
  「⚠️ 确认删除」，容易误触。

## 修复内容（`apps/mini_agent_kanban/app.py`）

| 位置 | 改动 |
|---|---|
| 新增 `_rerun_cron_dialog_only()` | 调用 `st.rerun(scope="fragment")`，只重跑弹窗自身；若抛 `StreamlitInvalidLayoutContextError`（弹窗刚打开的那轮全量重跑里 Streamlit 不允许 fragment scope），退回整页 `st.rerun()`，行为等价于修复前。写法与既有 `_async_fetch_or_retry` 一致。 |
| 新增 `_cron_delete_confirm_key(job_id)`、`_CRON_FLASH_KEY` | 统一确认态键名；一次性提示消息的 `session_state` 键。 |
| `_render_cron_job_details()` 删除区块 | 「🗑️ 删除」进入确认态、「取消」退出确认态：改用 `_rerun_cron_dialog_only()`，**弹窗保持打开**。确认态下新增一行 `st.warning` 写明任务名与"不可撤销"。 |
| 同上，「⚠️ 确认删除」 | **成功**：清确认态、把成功提示写入 `session_state[_CRON_FLASH_KEY]`，再整页 `st.rerun()`——需要刷新背后的任务列表，同时关闭弹窗（任务已不存在，符合预期）。**失败**：留在弹窗里用 `st.error` 展示原因，保留确认态便于直接重试，不再 rerun。 |
| `_render_cron_job_card()` | 点「📋 详情 / 操作」打开弹窗前先清掉该 job 的确认态，避免上一次遗留的确认态在下次打开时直接出现。 |
| `render_cron_jobs_tab()` | 顶部读取并清除 `_CRON_FLASH_KEY`，展示删除成功提示（直接 `st.success` 后立刻 `st.rerun()` 的话提示会一闪而过）。 |

修复后的交互流程：

```
点「📋 详情 / 操作」→ 弹窗打开（确认态已重置）
  └ 点「🗑️ 删除」→ 弹窗保持打开，显示警告 + 「⚠️ 确认删除」/「取消」
       ├ 「取消」→ 弹窗保持打开，回到「🗑️ 删除」
       └ 「⚠️ 确认删除」
            ├ 成功 → 弹窗关闭，列表刷新，顶部显示「已删除 cron job：xxx」
            └ 失败 → 弹窗保持打开，显示「删除失败：<原因>」，可再次点击重试
```

## 验证

1. **真实浏览器端到端**（Streamlit 1.64 + Chromium/Playwright；取自 `app.py`
   的真实卡片/弹窗/详情函数，配 FakeClient）：
   - 修复前：点「删除」→ 弹窗消失 → 任务仍在 → 再点开详情直接是「确认删除」
     ——与用户反馈完全一致，问题已复现。
   - 修复后：点「删除」弹窗保持打开并出现「确认删除」；「取消」回到「删除」；
     「确认删除」成功后弹窗关闭、该任务从列表消失、顶部出现成功提示；
     删除失败时弹窗保持、错误提示可见、可再次确认；确认态下按 Esc 关闭后
     重新打开，看到的是普通「删除」按钮（连续 3 次稳定通过）。
2. **单元测试** `tests/test_kanban_cron_detail_dialog_delete.py`（8 项）：
   `_rerun_cron_dialog_only()` 的正常/降级路径，以及删除区块的结构约束
   （进入确认/取消必须走局部重跑；整页 `st.rerun()` 只允许出现在"删除成功"
   分支；失败分支不得 rerun；打开弹窗前重置确认态；Tab 渲染一次性提示）。
   已做变异检查：把删除分支改回整页 `st.rerun()` 时测试会失败。
3. `python3 -m py_compile apps/mini_agent_kanban/app.py` 通过。

> 说明：`streamlit.testing.v1.AppTest` 对 `st.dialog` 内的按钮点击不会做
> fragment 级重跑，修复前后表现一致，没有鉴别力，因此弹窗交互不用它验证，
> 而是用真实浏览器 + 结构性单测组合覆盖。

## 未在本次改动范围内（已知同类模式）

- 「⏰ Cron 任务」详情弹窗里的其它操作（重置、提交意见、立即运行、启用/禁用、
  保存优先级、保存执行配置）同样调用整页 `st.rerun()`，操作后会关闭弹窗。
  这些操作会改变 `job` 的字段（`enabled`、`priority` 等），而弹窗闭包里捕获的
  `job` 字典是打开时的快照，若改成局部重跑会显示过期数据，需要先解决"弹窗内
  重新拉取最新 job"的问题，故保持现状（操作完成、弹窗关闭、列表刷新）。
- 「📌 目标看板」Goal 卡片详情弹窗（`_render_goal_card_details`）的
  「🗑️ 删除目标」使用相同的 `st.rerun()` 进入确认态，存在同样的
  "点删除弹窗关闭"现象。本次按需求只修 Cron，未连带修改；如需处理，可直接
  复用 `_rerun_cron_dialog_only()` 的思路（局部重跑进入/取消确认态，成功后
  整页重跑）。

## 涉及文件

- `apps/mini_agent_kanban/app.py`
- `tests/test_kanban_cron_detail_dialog_delete.py`（新增）
- `docs/kanban-dashboard-guide.md`（「⏰ Cron 任务 Tab」小节）
- `docs/cron-jobs-reference.md`（§5.1）
- `next_doc/kanban_cron_delete_consistency_bugfix.md`（增加指向本文的交叉引用）
- 本文档（新增）
