# 用户画像"更新滞后 + 目标树信息缺失"改进方案

- **版本**: v1——**第一期（方向一 A/B/C + 方向三）、第二期（方向二：
  目标树接入画像）均已实施**，详见文末"实施记录"一节。
- **前置文档**:
  - `next_doc/memory_backfill_and_profile_update_plan.md`（M1/M2 已完成——
    存量 session 回填；M3 已完成——cron 成功运行回填记忆，见下方"现状核实"）
  - `next_doc/growth_advisor_improvement_plan_v4.md`（方向一：cron/daemon
    记忆覆盖率盲区，本文档是它遗留的一个子问题的后续深挖）
  - `src/mini_agent/profile.py` / `src/mini_agent/agent/profile.py` /
    `apps/mini_agent_kanban/app.py::_render_growth_profile_and_keywords`
- **触发背景**: 用户反馈看板"🧠 Agent 对你的了解"板块 20 天未更新，且
  完全没有反映目标树（goal tree）里的长期任务信息。经代码核实，这是两个
  独立的结构性缺口，不是偶发 bug。

---

## 现状核实

### 缺口一：画像刷新的触发链路仍然漏掉"失败/未完结"的 cron 运行

`_maybe_refresh_profile()`（`agent/profile.py:53`）只在
`trigger_summary_and_profile()` 里被调用，即交互式会话收尾或 `/memory`
命令。它判断是否需要刷新调用 `UserProfileManager.should_refresh()`
（`profile.py:241`），条件纯粹是**全局记忆条目数的增量**
（`profile_refresh_interval_entries`，默认 3 条），**不含时间维度**。

M3（`cron_job_executor.py::_maybe_backfill_memory`，见
`growth_advisor_improvement_plan_v4.md` 方向一）已经实现了"cron 任务
收尾时顺带写一条记忆"，但有两处限制直接导致本次反馈的现象：

1. **只在 `final_status == STATUS_IDLE`（正常收尾）且 `last_text`
   非空时才回填**（`cron_job_executor.py` 300 行附近的
   `if final_status == STATUS_IDLE and last_text.strip()`）。
   `timed_out` / `needs_human_review` / `waiting_feedback` 等一切"未
   完全正常收尾"的运行——也就是用户描述的"工具调用失败或异常导致结果
   作废"——**不产出任何记忆**，因此完全不参与 `should_refresh()` 的
   计数。
2. **`backfill_cron_run()`（`memory_backfill.py:234`）只负责把记忆写进
   `memory_backend`，全程没有调用 `_maybe_refresh_profile()`**。也就是
   说即使 cron 任务成功回填了记忆、账面上的 entry count 涨了，画像也
   不会自动跟着刷新——要等到用户下一次跑一个交互式会话并触发
   `trigger_summary_and_profile()` 时，才会顺带把攒下的 delta 一次性
   消化掉。如果用户近期主要靠 daemon/cron 自主运行、极少开交互式会话，
   这个"顺带"的触发点长期缺席，画像就会停在很久以前的状态。

两者叠加：失败的 cron 运行不计数，成功的 cron 运行计数但不触发刷新，
唯一能触发刷新的交互式会话又恰好被用户搁置了——这就是"20 天没更新"的
完整成因链，不是单一原因。

### 缺口二：画像生成的输入源里没有目标树

`UserProfileManager.generate()`（`profile.py:229`）的输入只有
`MemoryEntry.summary/tags`（`memory_text` 变量），prompt 模板
`prompts/user/profile_update_request.md` 同样只渲染 `memory_text` +
上一版画像三个字段（`summary`/`tech_stack`/`habits`）。目标树相关的
一整套子系统（`goal_tree_decomposer.py`、`goal_tree_report.py`、
`goal_backlog.py` 等）与画像生成之间没有任何数据通路——目标树里"用户
主导两项长期自主任务"这类信息，只有在某条 memory entry 的摘要文本里
恰好提到时才会间接进入画像，命中与否完全靠运气，是结构性缺失而非
"LLM 没写好"。

---

## 改进方向

### 方向一：画像刷新加时间兜底 + 让"未完结"的 cron 运行也能被感知

**A. `should_refresh()` 增加时间维度的强制刷新条件**

```python
def should_refresh(self, current_entry_count: int, cfg) -> bool:
    if current_entry_count < cfg.profile_min_entries:
        return False
    profile = self.load()
    if profile.is_new:
        return True
    last_count = profile.derived.get("source_entry_count", 0)
    if (current_entry_count - last_count) >= cfg.profile_refresh_interval_entries:
        return True
    # 新增：即使 entry 增量不够，距上次刷新超过 N 天也强制刷新一次
    # （哪怕 delta 很小，也让 LLM 看一眼有没有该标 stale 的条目）
    last_updated = profile.derived.get("updated_at", 0)
    stale_days = getattr(cfg.profile, "force_refresh_after_days", 14)
    if last_updated and (time.time() - last_updated) >= stale_days * 86400:
        return current_entry_count > last_count  # 至少要有 1 条新东西才值得跑一次
    return False
```

配套在 `ProfileConfig` 加一个 `force_refresh_after_days: int = 14`。
这条不解决"完全没有新记忆"的极端情况（那种情况下刷新了也没有新信息可
更新），但能解决"记忆在缓慢累积、但增量门槛一直没跨过"的常见情况。

**B. 画像刷新的触发点从"仅交互式会话收尾"解耦出来，独立挂周期检查**

不改变现有 `trigger_summary_and_profile` 里的调用，而是新增一个独立
入口（复用现有的 daemon 自评 cron 机制，或单独注册一个低频系统 cron
job，如 `sys:profile_refresh_scan`），周期性地：

```python
profile_mgr = UserProfileManager(paths)
entries = [...]  # 同 _maybe_refresh_profile 里的合并逻辑
if profile_mgr.should_refresh(len(entries), cfg):
    profile_mgr.generate(llm_client, entries, ...)
```

这样即使用户长期不开交互式会话，只要 daemon/cron 那条线在正常写记忆，
画像也能自己追上。**这是让 A 真正生效的前提**——否则时间兜底判断得再对，
也要等到下次交互式会话才会被执行到。

**C. `_maybe_backfill_memory` 的回填条件补一档"降级记忆"**

`timed_out` / `needs_human_review` 等未正常收尾的运行，不应该完全静默。
建议在这些分支也写一条内容更简短、明确标注"未完成"的降级 memory
entry（例如 `"[cron:{job.id}] 本轮运行因 {final_status} 未正常完成，
最后进展：{last_text[:200]}"`），不需要走完整的摘要生成 LLM 调用，纯拼
字符串即可。这样至少能让 `should_refresh()` 的计数器和成长顾问的诊断
面板感知到"这里发生过一次失败"，而不是这条运行在系统里完全不留痕迹。
需要评估这类降级记忆会不会干扰 growth_advisor 现有的"从记忆里找信号"
逻辑（如果会，可以给这类 entry 打一个专门的 tag，如 `cron_incomplete`，
供下游按需过滤）。

### 方向二：目标树摘要作为独立输入接入画像生成

不把目标树数据混进 `memory_text`（会稀释/污染现有的"近期记忆增量"语义），
而是作为 **并列的独立字段** 传给 `generate()` 和 prompt：

1. 新增一个轻量读取函数（放在 `evolution/` 或 `perception/` 下，具体
   位置视目标树模块现有的对外接口而定），从 `goal_tree_report.py` /
   `goal_backlog.py` 拉一份"当前活跃目标 + 最近进展"的结构化摘要
   （目标名称、状态、最近一次推进的时间点/一句话进展即可，不需要完整
   执行细节）。
2. `UserProfileManager.generate()` 增加一个可选入参
   `goal_tree_snapshot: Optional[str]`，连同 `memory_text` 一起传给
   `pm.render("user/profile_update_request", ...)`；prompt 模板里新增
   一段"当前用户主导的长期目标"区块，明确告诉 LLM 这部分是独立于近期
   记忆的背景信息,用于让 `summary` 里能稳定提到"用户在推进哪些长期
   任务"，即使近期没有相关的新 memory entry。
3. 生成结果里目标树相关的内容仍然只落在 `summary`/`tech_stack`/
   `habits` 这三个既有字段里，**不新增独立的 derived key**——目标树本身
   已经有自己的持久化和展示（看板应该已有目标树专属板块），画像这里
   只是"引用"而非"复制"，避免出现两份可能不同步的目标状态。

这一步的改动量比方向一大，且依赖目标树模块暴露一个"轻量摘要"接口
（如果目前没有,需要先补）。建议方向一先做（成本低、直接解决"更新滞后"
的可用性问题），方向二作为第二期。

### 方向三：看板显式提示"画像可能已过时"

`_render_growth_profile_and_keywords`（`apps/mini_agent_kanban/app.py`
6853 行起）已经展示 `updated_at`，但只是一个时间戳，需要用户自己心算
"这算不算太久"。建议加一行醒目提示：

```python
if user_profile.get("updated_at"):
    age_days = (time.time() - user_profile["updated_at"]) / 86400
    st.caption(f"更新时间：{...}")
    if age_days > cfg.profile.force_refresh_after_days:
        st.warning(f"⚠️ 已 {age_days:.0f} 天未更新，可能滞后于近期进展")
```

这条改动量最小，可以独立于方向一/二先做，先解决"用户不知道自己该不该
怀疑这份画像"的问题。

---

## 分期建议（均已实施，见下方"实施记录"）

1. **第一期（方向一 A+B+C，方向三）**：解决"更新滞后"这个可用性问题，
   改动集中在 `profile.py` / `agent/profile.py` / `cron_job_executor.py`
   / 看板展示，不涉及目标树模块，风险和工作量都可控。
2. **第二期（方向二）**：目标树接入画像，需要先确认目标树模块有没有
   现成的"轻量摘要"读取接口，没有的话先补这个接口，再接进
   `generate()`。

---

## 实施记录（第一期：方向一 A/B/C + 方向三）

### 方向一 A：`should_refresh()` 时间兜底

- `src/mini_agent/config/models.py`：`ProfileConfig` 新增
  `force_refresh_after_days: int = 14`，并新增
  `AppConfig.profile_force_refresh_after_days` 属性。
- `src/mini_agent/config/loader.py`：`profile_cfg` 的 flat/nested 兼容
  映射补上 `force_refresh_after_days` ↔ `profile_force_refresh_after_days`。
- `src/mini_agent/profile.py`：`UserProfileManager.should_refresh()`
  在原有"增量条目数达标"判断之外，新增"距上次刷新超过
  `force_refresh_after_days` 天，且期间至少有 1 条新记忆"的强制刷新
  分支；完全没有新记忆时不受影响（刷新了也没有新信息可用）。
- 测试：`tests/test_profile.py::TestShouldRefreshTimeFallback`（4 个
  用例：无新记忆不强制刷新 / 有新记忆且超时强制刷新 / 未超时不刷新 /
  原有增量门槛判断不受影响）。

### 方向一 B：刷新触发点从"仅交互式会话收尾"解耦

- `src/mini_agent/cli/repl.py`：新增 `/profile scan` 子命令，调用
  `agent._maybe_refresh_profile(force=False, rebuild=False)`——是否真的
  刷新完全由 `should_refresh()` 判断，跟无参数的 `/profile`
  （`force=True`，无条件刷新）区分开。
- `src/mini_agent/evolution/cron_scheduler.py`：新增系统 cron job
  `sys:profile_refresh_scan`（`interval:21600`，每 6 小时），
  `task_template` 调用 `/profile scan`。默认 `enabled=True`，跟
  `ProfileConfig.enabled` 的默认值一致（opt-out）。
- 效果：即使用户长期不开交互式会话，只要 `memory_backfill` /
  cron 记忆回填那条线在正常写记忆，画像也能在下一次
  `sys:profile_refresh_scan` 运行时被动追上，不再完全依赖交互式会话
  收尾这一条触发链路。

### 方向一 C：未正常收尾的 cron 运行补一条降级记忆

- `src/mini_agent/evolution/memory_backfill.py`：新增
  `backfill_incomplete_cron_run()`——不调用 LLM，纯字符串拼接生成一条
  `summary`（含 `final_status` 和最后 200 字进展），打
  `["cron_incomplete", final_status]` 两个 tag，供下游（如
  growth_advisor 信号扫描）按需过滤。
- `src/mini_agent/evolution/cron_job_executor.py`：`run_job()` 收尾时，
  `final_status != STATUS_IDLE` 且 `last_text` 非空的分支新增调用
  `_maybe_backfill_incomplete_memory()`（新方法，依赖检查与现有
  `_maybe_backfill_memory()` 一致，但不需要 `llm_client`）。
- 测试：`tests/test_memory_backfill.py::TestBackfillIncompleteCronRun`
  （2 个用例：写入降级记忆且 tag/内容正确 / 空文本不写入）。

### 方向三：看板"距今 N 天未更新"提示

- `src/mini_agent/evolution/growth_advisor.py`：
  `user_profile_snapshot` 新增 `force_refresh_after_days` 字段（同
  `stale_after_days` 的透出方式，取不到时退回 dataclass 默认值 14）。
- `apps/mini_agent_kanban/app.py`：`_render_growth_profile_and_keywords`
  在展示 `updated_at` 之后，若 `age_days > force_refresh_after_days`
  则用 `st.warning()` 显式提示"已 N 天未更新，可能滞后于近期进展"，
  阈值跟 `should_refresh()` 的时间兜底判断用同一个配置项。

### 验证

`tests/test_profile.py`、`tests/test_memory_backfill.py`、
`tests/test_cron_job_workspace_and_executor.py`、
`tests/test_cron_job_executor_step_detail.py`、
`tests/test_cron_scheduler_priority.py`、
`tests/test_flat_nested_config_compat.py`、
`tests/test_growth_diagnostics_and_lang_fix.py`、
`tests/test_growth_diagnostics_backfill_count_cache.py` 全部通过
（含新增用例）。

### 遗留/待观察

- `sys:profile_refresh_scan` 和 `sys:memory_backfill_scan` 目前都是
  `interval:21600`（6 小时），二者本质上是"先回填记忆、再检查是否该
  刷新画像"的前后关系，若后续发现两个 job 调度时机对不上（回填还没跑
  完刷新就先跑了），可以考虑把画像刷新检查也挂在
  `sys:memory_backfill_scan` 跑完之后触发，而不是各自独立定时——本期
  先用最简单的"各自独立定时"验证效果，非必要不引入两个 job 之间的
  依赖耦合。
- `cron_incomplete` 记忆是否会干扰 growth_advisor 现有的信号扫描/
  主题地图，本期未评估（growth_advisor 相关测试全部通过，说明至少
  没有破坏现有逻辑，但"降级记忆的语义是否应该参与成长信号计算"是一个
  产品判断，留待观察实际效果后再决定要不要在 growth_advisor 里显式
  排除 `cron_incomplete` tag）。

## 实施记录（第二期：方向二——目标树接入画像）

- **接口确认**：`perception/goal_tree_report.py::build_goal_tree_report()`
  已经是一个零成本、不引入 LLM、任一子数据源异常都能优雅降级的"轻量
  聚合报告"接口（`root_id=None` 时聚合全局森林），完全满足方向二设计
  时提出的"轻量摘要读取接口"要求，不需要新建一整套接口，只需要在它
  之上加一层"挑画像用得上的字段、格式化成文本"的薄封装。
- `src/mini_agent/perception/goal_tree_report.py`：新增
  `build_goal_tree_profile_snapshot(paths, max_active_goals=8)`——加载
  `GoalBacklog`（用 `load_goal_backlog()`，会从磁盘读取持久化数据；
  直接 `GoalBacklog(paths)` 不调用 `.load()` 拿到的是空壳，踩过这个坑，
  已在测试里覆盖），调用 `build_goal_tree_report(root_id=None)`，只挑
  "状态为 active 的 Goal 标题 + 最近一条产出摘要"格式化成几行文本；
  空森林/任一环节异常都返回空串，不影响调用方主流程。
- `src/mini_agent/profile.py`：`generate()` 在渲染 prompt 前调用
  `build_goal_tree_profile_snapshot(self._paths)`，结果作为独立的
  `goal_tree_block` 变量传给模板，跟"上一版画像"文本块（`previous_profile_block`）
  并列但不混在一起——目标树快照是"每次都重新拉取的当前状态"，不是
  "上一版画像"的一部分。异常兜底为空串，不引入新的失败点。
- `src/mini_agent/prompts/user/profile_update_request.md`：新增
  `{{goal_tree_block}}` 变量，紧跟在 `{{previous_profile_block}}` 之后、
  `Session summaries:` 之前。
- `src/mini_agent/prompts/system/profile_summarizer.md`：新增一段
  指引，明确告诉模型"目标树背景信息可以用来丰富 summary，但不能单独
  凭一个目标标题就编造 tech_stack/habits 条目"——避免目标树信息喧宾
  夺主，稀释 tech_stack/habits 原本"必须有 memory 证据支撑"的约束。
- **设计取舍**：目标树摘要不落成 `derived` 里的独立 key（如
  `derived.active_goals_snapshot`），只作为 prompt 输入影响
  `summary` 的措辞——目标树本身已经有自己的持久化和看板展示
  （`GoalTreeReport`/树级报告页面），画像这里只是"引用参考"，避免
  出现两份可能不同步的目标状态数据。

### 验证

新增 `tests/test_goal_tree_report.py::TestBuildGoalTreeProfileSnapshot`
（4 个用例：空森林返回空串 / 活跃目标标题正确出现 / draft 状态目标被
排除 / `max_active_goals` 生效截断）。连同第一期的全部测试，累计
133 个用例全部通过：`test_profile.py`、`test_goal_tree_report.py`、
`test_memory_backfill.py`、`test_cron_job_workspace_and_executor.py`、
`test_cron_job_executor_step_detail.py`、`test_cron_scheduler_priority.py`、
`test_flat_nested_config_compat.py`、`test_growth_diagnostics_and_lang_fix.py`。

### 遗留/待观察（第二期）

- 目前只在生成画像时（`generate()` 被调用那一刻）拉取目标树快照，
  快照本身不缓存、不落盘——如果后续发现 `GoalBacklog(paths).load()`
  在目标节点很多时有明显的 IO/解析开销，可以考虑跟
  `should_refresh()` 的触发频率对齐做一层缓存，本期数据量下未观察到
  这个问题，暂不引入额外复杂度。
- prompt 里只给了模型"活跃目标标题 + 最近一条产出摘要"，没有给目标的
  长期方向分类、优先级等更细的字段——如果后续发现画像里对目标的描述
  过于笼统，可以再从 `GoalTreeReport` 里挑更多字段加进
  `build_goal_tree_profile_snapshot()`，接口本身已经预留了扩展空间
  （`max_active_goals` 参数、独立的文本拼接逻辑）。
