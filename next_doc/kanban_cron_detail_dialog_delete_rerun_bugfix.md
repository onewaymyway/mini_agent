# 看板 Cron 详情弹窗内删除失效修复（kanban_cron_detail_dialog_delete_rerun_bugfix）

> 范围：仅 Streamlit 看板 `apps/mini_agent_kanban/app.py`（遵循 `CLAUDE.md`
> 约定，不改 `mini_agent_kanban_x`）。后端接口无改动。
> 分两轮：**第一轮**修「Cron 详情弹窗内删除」；**第二轮**把同类问题一并修掉——
> Cron 弹窗内其它操作、目标看板 Goal 详情弹窗的删除（见文末「第二轮」）。
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

## 第二轮：同类问题一并修复

第一轮之后确认了两处同类模式，本轮一并处理。

### A. Cron 详情弹窗内的其它操作

重置（needs_human_review）、提交意见、立即运行、启用/禁用、保存优先级、
保存执行配置、恢复全局默认——这 7 个操作原先都是"提示 + 整页 `st.rerun()`"，
点完弹窗就被关掉，操作结果提示（`st.success`）也随之消失。

当时没有直接改成局部重跑，是因为弹窗闭包里的 `job` 字典是**打开时的快照**，
局部重跑后会显示过期数据（比如刚点了「⏸️ 禁用」，按钮仍显示「禁用」）。
本轮先解决数据新鲜度，再统一改成局部重跑：

| 改动 | 说明 |
|---|---|
| `_fetch_cron_job_fresh(client, job_id)` | 弹窗每次（含每次局部重跑）都从 `GET /cron/jobs` 取该 job 最新数据，返回 `(job, gone)`。找不到 -> `gone=True`，弹窗提示"任务已不存在"；请求失败/异常 -> 沿用打开时的快照，**不会**把一次网络抖动误判成"已被删除"。 |
| `_cron_dialog_done(job_id, kind, msg)` | 7 个操作完成后的统一收尾：把结果提示写入该 job 的弹窗提示位，再 `_rerun_dialog_only()`。 |
| `_render_cron_job_details` 顶部 | 读取并展示上一个操作留下的一次性提示（"已禁用。/已保存。/已触发…"），提示留在弹窗内可见。 |
| `st.dialog(..., on_dismiss="rerun")` | 弹窗内操作不再整页刷新，背后的卡片在弹窗开着时不会更新；用 ✕ / Esc / 点击外部关闭时补一次整页刷新，列表同步到最新（如 priority、启停状态）。 |

现在整个 `_render_cron_job_details` 里只剩**一处**整页 `st.rerun()`：删除成功。

### B. 目标看板 Goal 详情弹窗的「🗑️ 删除目标」

`_render_goal_card_details` 里的删除与 Cron 是完全相同的写法与问题（点「删除」
整页 rerun → 弹窗消失 → 再打开是确认态）。按同样方案修复：

- 进入确认态 / 取消：`_rerun_dialog_only()`，弹窗保持打开。
- 确认删除成功：清确认态，提示（含"同时清理 N 个关联 cron 任务"、清理失败警告）
  写入 `session_state[_GOAL_FLASH_KEY]`，整页 `st.rerun()` 刷新看板并关闭弹窗；
  `render_kanban_tab` 顶部展示提示。
- 确认删除失败：弹窗内显示错误、保留确认态可重试，不再 rerun。
- `_show_goal_detail_dialog` 打开弹窗前先清该 Goal 的确认态；加 `on_dismiss="rerun"`。

Goal 弹窗里的**其它**操作（状态切换、编辑、周期设置、提意见等，约 20 处
`st.rerun()`）本轮**没有**动：节点数据 `n` 同样是打开时的快照，且涉及的状态
更多，需要另行设计"弹窗内刷新节点数据"，且用户没有反馈这些操作有问题。

### C. 顺带发现并修复：提示消失导致折叠区收起

用真实浏览器验证时发现：Cron 弹窗执行「立即运行」后顶部出现一条提示，下一次
局部重跑提示消失，它下方所有元素位置上移一格，Streamlit 按位置识别控件，
「🔢 调整优先级」等 expander 被当成新元素重建，**展开状态丢失**。

修复：`_pop_and_render_flash(key)` 无论有无提示都固定占一个 `st.container()`，
后面元素的位置不受提示有无影响。Cron Tab 顶部与目标看板顶部的一次性提示也
统一使用该函数（支持单个 `(kind, msg)` 或列表）。

### 新增/调整的函数一览

- `_rerun_dialog_only()`：通用的"只重跑弹窗自身"（`_rerun_cron_dialog_only()` 保留为别名）。
- `_pop_and_render_flash(key)`：一次性提示（固定占位）。
- `_cron_dialog_done()` / `_cron_dialog_flash_key()` / `_fetch_cron_job_fresh()`。
- 常量 `_CRON_FLASH_KEY`、`_GOAL_FLASH_KEY`。

### 第二轮验证

- **真实浏览器端到端**（同上环境，harness 加载 `app.py` 全部函数定义，配有状态的
  FakeClient；Goal 弹窗用真实的 `_show_goal_detail_dialog`）：
  - Cron 其它操作：禁用后弹窗保持、按钮变为「启用」且显示「已禁用。」；再启用；
    立即运行提示；保存优先级后显示「已保存。」，Esc 关闭后背后卡片变为
    `priority: 7`。
  - Cron 删除回归：成功/取消/失败重试/Esc 后重开无残留确认态，全部通过。
  - Goal 删除：点删除弹窗保持且「删除目标」展开区未收起；取消；成功后弹窗关闭、
    卡片消失、显示"已删除目标「…」，同时清理了 1 个关联 cron 任务"；失败时
    弹窗保持并显示错误可重试；Esc 关闭后重开无残留确认态。
  - 折叠区回归：先「立即运行」再展开「调整优先级」并编辑，展开状态保持。
- **单元测试** `tests/test_kanban_cron_detail_dialog_delete.py` 扩到 21 项，新增
  `_fetch_cron_job_fresh` 三种结果、`_pop_and_render_flash` 固定占位与一次性、
  `_cron_dialog_done`、Cron 详情函数整页 rerun 只剩一处、Goal 删除块约束、
  两个弹窗的 `on_dismiss` 与确认态重置。对 Goal 取消分支、Cron 保存优先级分支
  各做一次变异（改回整页 rerun），测试均失败。
- 其它看板测试无新增失败（`test_kanban_growth_dragdrop.py` 里有 2 项在原始
  版本上就失败，与本改动无关）。

## 涉及文件（两轮合计）

- `apps/mini_agent_kanban/app.py`
- `tests/test_kanban_cron_detail_dialog_delete.py`（新增）
- `docs/kanban-dashboard-guide.md`（「⏰ Cron 任务 Tab」小节）
- `docs/cron-jobs-reference.md`（§5.1）
- `next_doc/kanban_cron_delete_consistency_bugfix.md`（增加指向本文的交叉引用）
- 本文档（新增）

> 第二轮追加改动同样只涉及上述文件，无新增文件。
