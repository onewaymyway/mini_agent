# 用户画像"更新滞后 + 目标树信息缺失"改进方案

- **版本**: v1（草案，方向级 + 关键设计点级规划，尚未实施）
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

## 分期建议

1. **第一期（方向一 A+B+C，方向三）**：解决"更新滞后"这个可用性问题，
   改动集中在 `profile.py` / `agent/profile.py` / `cron_job_executor.py`
   / 看板展示，不涉及目标树模块，风险和工作量都可控。
2. **第二期（方向二）**：目标树接入画像，需要先确认目标树模块有没有
   现成的"轻量摘要"读取接口，没有的话先补这个接口，再接进
   `generate()`。

以上待确认后再展开到可直接实现的代码级设计。
