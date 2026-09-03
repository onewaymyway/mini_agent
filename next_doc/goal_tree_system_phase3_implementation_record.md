# 目标树系统改进 — 阶段三（现阶段焦点）实施记录

> 对应 `next_doc/goal_tree_system_plan.md` §五 分阶段实施规划第 3 项。
> 依赖阶段一（数据模型）、阶段二（自动分解）的落地成果，见
> `next_doc/goal_tree_system_phase1_implementation_record.md`、
> `next_doc/goal_tree_system_phase2_implementation_record.md`。

## 一、改动范围

- `src/mini_agent/perception/goal_backlog.py`：
  - 新增 `DEFAULT_FOCUS_TOP_N` 常量、`compute_current_focus()` 纯规则函数；
  - `GoalBacklog` 新增 `set_focus_pin()`/`recompute_current_focus_tree()`
    两个方法；
  - 新增 `JOB_ID_FOCUS_RECOMPUTE` + `ensure_goal_tree_focus_recompute_job()`
    （`sys:goal_tree_focus_recompute`）。
- `src/mini_agent/perception/goal_tree_decomposer.py`：
  - 新增 `DecomposeScanSummary`/`run_decompose_scan_cycle()`，把阶段二已经
    写好的 `find_stale_nodes_for_scan()`/
    `find_parent_needing_decompose_after_completion()` 接成一次完整巡检；
  - 新增 `JOB_ID_DECOMPOSE_SCAN` + `ensure_goal_tree_decompose_scan_job()`
    （`sys:goal_tree_decompose_scan`）。
- `src/mini_agent/api/server.py`：daemon 启动时补注册上述两个新 cron job
  （`_build_autonomous_loop()` 里跟其它 `ensure_*_job` 调用点相同写法）。
- `src/mini_agent/cli/commands/goals.py`：新增 `/agent goals focus <id>` /
  `/agent goals focus pin|unpin <node_id> <child_id>` 两个子命令。
- 新增测试 `tests/test_goal_tree_phase3.py`（27 个用例）。

没有改动 `GoalRunner`/`ObjectiveExecutor`/`goal_cron_bridge`/`set_status()`
本体（`set_status()` 完全没有改动，见下文"与原方案的差异"关于触发时机 2 的
取舍说明），没有涉及 Streamlit 看板（阶段四的范围）。

## 二、具体改动

### 2.1 `compute_current_focus()`（§4.3）

纯规则、同步、不调用 LLM。计算顺序：

1. 先并入 `node.focus_pinned_ids` 里仍然是 `children` 之一的 id——**不要求
   其状态是 active**：pin 是"持续生效直到用户显式取消"的承诺，如果因为
   状态变化被静默替换掉，等于变相撤销了这个承诺，用户应该能在
   `current_focus_ids` 里直接看到"我 pin 的这个已经完成/放弃了"这个事实
   本身（配合 CLI `focus` 子命令的 📌 标记一起看），而不是被换成别的子
   节点却毫无提示。这是方案原文没有显式写清楚、本阶段做出的一个补充
   设计决定。
2. 从"不在 pinned 集合里、且 `status == "active"`"的子节点里，按
   `priority + compute_aging_boost()`（复用 P3 阶段既有的老化加成，同一套
   停滞天数口径）降序排序，取 `top_n - len(pinned)` 个补足。
3. `pinned + picked` 去重合并（`dict.fromkeys` 保序），pinned 在前。

没有子节点，或全部 `completed`/`abandoned`（且没有 pin，或 pin 的也已进入
终态）时返回空列表——这正是 `GoalNode.current_focus_ids` 字段注释"空列表
表示子节点已全部进入终态，该节点该被停滞巡检捕获"的计算侧落地，跟阶段二
`find_stale_nodes_for_scan()`"没有 active 子节点"的判定口径完全对齐（都是
看 `status not in ("completed", "abandoned")`）。

`top_n` 默认取 `DEFAULT_FOCUS_TOP_N = 3`，跟方案 §六"N 默认 1~3"的量级
参考一致，调用方可覆盖。

### 2.2 `GoalBacklog.set_focus_pin()` / `recompute_current_focus_tree()`（§4.3/§4.5）

- `set_focus_pin(node_id, child_id, pinned)`：`_locked()` 内原子完成
  "改 `focus_pinned_ids` → 立即用 `compute_current_focus()` 重算**该节点
  自身**的 `current_focus_ids`"，不用等下一次 `sys:goal_tree_focus_recompute`
  才生效（跟 CLI"立即重算"的提示语对应）。**不递归**影响祖先——pin 只
  改变"这个节点该关注哪个子节点"，不改变子节点自身在祖父节点排序里的
  `priority`/老化加成，祖先的 `current_focus_ids` 交给下一次巡检自然覆盖，
  避免每次 pin/unpin 都要做一次全树重算。`node_id` 不存在、或 `child_id`
  不是它的直接子节点时返回 `False`，不做任何修改。
- `recompute_current_focus_tree(root_id=None, top_n=3)`：`sys:
  goal_tree_focus_recompute` 的核心逻辑。`_locked()` 内先做一次后序遍历
  （子节点先算好，结果再影响父节点排序——对应方案 §4.3"子节点的完成状态
  变化要先反映到自己身上，再影响父节点的排序"），只对
  `level in ("ultimate", "domain", "stage")` 三层节点写回结果，跟
  `GoalNode.current_focus_ids` 字段注释"仅这三层非叶子节点使用"严格对齐
  ——`goal`/`objective` 两层（即使出现"goal 挂 goal"这种非叶子 goal）本次
  不计算，继续只走 `GoalRunner`/fairness 排序，不引入第二套排序语义。
  返回实际发生变化的节点数，`root_id` 不存在（含压根没有全局根节点）时
  返回 0。

### 2.3 `sys:goal_tree_focus_recompute`

零 LLM 成本、纯规则、本地回调（`register_local_handler`），跟
`failure_pattern_store.ensure_failure_pattern_aggregation_job()` 同构。
`schedule="interval:3600"`（每小时一次），比 `sys:goal_tree_decompose_scan`
的 24 小时短很多，对应方案原文"这个只是纯规则计算、成本很低，可以比 LLM
驱动的巡检跑得更勤"。handler 直接调用
`backlog.recompute_current_focus_tree()`（默认全局根节点起点，`top_n` 走
默认值），恒返回 `True`（纯规则计算不会失败）。

### 2.4 `run_decompose_scan_cycle()` 与 §六 遗留问题的结论

方案 §六"待实施阶段确认的细节"遗留了两个问题，本阶段给出结论：

1. **触发时机 2（完成态联动）走同步内联还是 cron 下一拍捕获**——选择
   **cron 下一拍捕获**：`run_decompose_scan_cycle()` 每次运行时，除了
   `find_stale_nodes_for_scan()` 命中的停滞节点，还会额外扫一遍"最近
   `COMPLETION_LINK_LOOKBACK_SECONDS_DEFAULT`（25 小时，略大于巡检间隔
   本身的 24 小时，避免"节点恰好在两次巡检边界完成"被漏判）内被标记
   `completed` 的非 `objective` 节点"，对每个这样的节点调用阶段二已经
   写好的 `find_parent_needing_decompose_after_completion()`，命中的父
   节点与停滞节点集合按 id 去重合并（同一个节点同时命中两路时只处理一
   次，不重复调用 `decompose()`）后一起触发。`GoalBacklog.set_status()`
   本身**完全没有改动**，继续保持"毫秒级临界区写入"的语义；代价是完成态
   联动最多要等一个巡检周期（默认 24 小时）才会生效，而不是立即触发——
   方案原文"不断更新现阶段目标"本来就是持续巡检的效果，不是强实时通知，
   这个延迟可以接受。
2. **两个新 job 是"轻量规则内部执行"还是"提交 task_template 走完整 Agent
   轮次"**——两个都选择**本地回调**（`register_local_handler`）：
   - `sys:goal_tree_focus_recompute` 本身零 LLM、纯规则，天然适合本地回调；
   - `sys:goal_tree_decompose_scan` 虽然会调 LLM（经
     `GoalTreeDecomposer.decompose()` → `llm_helper.ask()`），但这次调用
     本身只是"落一份候选文本"，不需要工具调用/多轮 Agent 决策，跟
     `sys:wiki_quarantine_repair` 的取舍完全一致——走完整 Agent 轮次是
     过度设计，还会占用主 Agent 的 `InputQueue` 轮次预算。

`run_decompose_scan_cycle(paths, backlog, *, llm_helper=None, stale_days=14,
completion_lookback_seconds=90000)` 逐节点调用 `decomposer.decompose(node.id,
llm_helper=llm_helper)`，**不传 `force=True`**——巡检场景应该尊重
`should_decompose()` 的节奏治理（间隔/已有未处理候选），不该绕过；已经在
测试里验证"停滞命中但节点已有未处理候选时本函数不会重复生成"。返回
`DecomposeScanSummary`（停滞命中数/完成态联动命中数/实际扫描数/新增候选
总数/命中节点 id 列表），供 cron handler 判定 `ok`、也方便测试断言。

### 2.5 `sys:goal_tree_decompose_scan`

`schedule="interval:86400"`（24 小时一次，方案原文"参考 `sys:goal_review`
的量级"）。`llm_helper_provider` 可选（opt-in，默认 `None`），与
`wiki_quarantine_repair.ensure_wiki_quarantine_repair_job()` 完全同一种
约定：不传，或调用返回 `None`，job 触发时直接跳过（返回 `True`，不算
失败）——分解建议是主动巡检的锦上添花能力，不该在没有配置可用 LLM 的
环境下产生噪音日志或报错。`api/server.py` 接线时传入
`lambda: getattr(agent, "llm_helper", None)`，跟现有 `agent.llm_helper`
在其它 cron job 接线点的用法一致（没有额外配置开关——`sys:
wiki_quarantine_repair` 有独立配置开关是因为那是"要不要让 LLM 去改写
wiki 页面"这种有一定风险的动作，分解建议只是落一份待用户确认的候选，
风险量级不同，本阶段决定不新增专门的开关，后续如果观察到噪音问题
再补）。

### 2.6 CLI 新增 `focus` 子命令（§4.5）

- `/agent goals focus <id>`：打印该节点当前的 `current_focus_ids`（附
  直接子节点标题，避免用户拿到一串 id 还要再手动查一遍 `tree`），
  `current_focus_ids` 里被 pin 的项标 📌；节点不是 `ultimate`/`domain`/
  `stage` 时给出"不参与本字段计算"的提示，但仍然继续展示（可能是遗留
  数据或用户手误，不阻断查询）。
- `/agent goals focus pin|unpin <node_id> <child_id>`：调用
  `GoalBacklog.set_focus_pin()`，成功后提示"已立即重算"。

## 三、验证

新增 `tests/test_goal_tree_phase3.py`（27 个用例），覆盖：

- `compute_current_focus()`：空子节点/全终态返回空列表、按 priority 取
  top-N、pin 不论状态都优先并入、pin 的 id 若已不是子节点则被丢弃、
  `DEFAULT_FOCUS_TOP_N == 3`、老化加成足以反超 priority 改变排序结果；
- `GoalBacklog.set_focus_pin()`：pin 添加并立即重算、unpin 移除、节点
  不存在/child 不是直接子节点两种拒绝路径；
- `GoalBacklog.recompute_current_focus_tree()`：无根节点返回 0、只对
  `ultimate`/`domain`/`stage` 三层写回（`goal`/`objective` 恒为空）、
  连续两次调用在无变化时第二次返回 0（幂等）、子节点 `completed` 后
  父节点的 `current_focus_ids` 自动变空（自底向上生效）；
- `ensure_goal_tree_focus_recompute_job()`：注册 job + handler 真的触发
  重算、第二次调用不重复添加；
- `run_decompose_scan_cycle()`：停滞节点触发分解、完成态联动命中父节点
  触发分解、超出回看窗口的旧完成事件被忽略、同一节点被两路同时命中时
  去重只处理一次、没有任何命中时返回空 summary 且不调用 LLM、命中但节点
  已有未处理候选时节奏治理依然拦截（不调用 LLM）；
- `ensure_goal_tree_decompose_scan_job()`：注册 job、没有
  `llm_helper_provider`/provider 返回 `None` 两种场景都静默跳过（`ok=True`
  且不调用 LLM）、配置了 helper 时真正跑完一轮扫描并落盘候选。

执行结果：

```
python -m pytest tests/test_goal_tree_phase1.py tests/test_goal_tree_phase2.py \
    tests/test_goal_tree_phase3.py tests/test_goal_backlog.py \
    tests/test_goal_execution_fairness.py -q
104 passed
```

另外做了三次手动冒烟（跟阶段二记录同样的"本沙盒环境原本缺
`fastapi`/`rich`，补装后验证"）：

1. `import` 校验：`mini_agent.perception.goal_backlog`/
   `mini_agent.perception.goal_tree_decomposer`/
   `mini_agent.cli.commands.goals` 均可正常 import，本阶段新增的全部
   函数/方法存在且可调用；`mini_agent.api.server` 语法校验通过（未整体
   `import`，该模块依赖较重，改动点已通过前两者的组合场景间接验证）。
2. 端到端场景：建根 → 建 `domain` → 建两个 `priority` 不同的 `goal` →
   `recompute_current_focus_tree()` 返回 2（root + domain 各更新一次）→
   `domain.current_focus_ids` 正确包含两个 goal（因为 `top_n=3` 默认值
   足够容纳）。
3. `set_focus_pin()` 直接调用：pin 低优先级的 goal 后，
   `current_focus_ids` 第一位变成被 pin 的节点，第二位仍然是高优先级的
   另一个 goal（`top_n=3` 够放下两个，未触发截断场景，但顺序验证了
   "pinned 在前"）。

## 四、与原方案的差异小结

| 方案原文 | 本阶段实际交付 | 原因 |
|---|---|---|
| 触发时机 2（完成态联动）"检查其父节点……触发一次分解建议" | 走 cron 下一拍捕获，不同步接入 `set_status()` | 方案 §六本身列为待定细节；同步接线会让 `set_status()` 从毫秒级写入变成挂着不确定时长的 LLM 请求，超出"轻量写入"语义 |
| `focus_pinned_ids`"持续生效直到用户取消" | `compute_current_focus()` 明确不要求 pinned 子节点是 active 状态才计入 | 方案原文没有显式说清楚 pin 遇到子节点终态时该怎么办；选择"继续显示但配合状态/📌 标记让用户自己看到"，不做静默替换，更贴合"pin 是持续承诺"这句话 |
| `sys:goal_tree_decompose_scan` 走"轻量规则内部执行"还是"task_template" | 本地回调，同 `sys:wiki_quarantine_repair` | 方案 §六本身列为待定细节；分解建议只是一次 `llm_helper.ask()`，不需要工具调用/多轮决策 |

## 五、下一阶段

阶段四（看板树形 UI）需要：

1. `apps/mini_agent_kanban` 新增"🌳 目标树"子页，用本阶段/阶段一二已有的
   `GoalBacklog.get_tree()`/`current_focus_ids`/`decompose_candidates`
   渲染树形结构 + 高亮焦点节点；
2. 候选展示 + "✅ 采纳"/"✖️ 忽略"/"✏️ 编辑后采纳"交互，复用
   `wiki_tab_async_changes` 的 `start_async_job`/`run_async_job` 异步模式
   （分解候选生成含 LLM 调用，不该走固定超时同步等待）；
3. 手动管理：新建节点/编辑/改 `parent_id`/pin 焦点/手动触发"帮我拆解"，
   全部是对本阶段和阶段一二已有方法（`add_node`/`update_fields`/
   `set_focus_pin`/`GoalTreeDecomposer.decompose`）的 UI 包装，不需要
   新的后端能力。

这些都可以直接在阶段一/二/三已有的数据结构和函数之上开工，不需要额外的
数据模型或 cron 接线改动。
