# 目标树系统改进 — 阶段二（自动分解）实施记录

> 对应 `next_doc/goal_tree_system_plan.md` §五 分阶段实施规划 第 2 项。
> 依赖阶段一的数据模型，见
> `next_doc/goal_tree_system_phase1_implementation_record.md`。

## 一、改动范围

- 新增 `src/mini_agent/perception/goal_tree_decomposer.py`（`GoalTreeDecomposer`
  类 + 两个独立的巡检检测函数）。
- `src/mini_agent/perception/goal_backlog.py` 新增
  `append_decompose_candidates()`/`accept_candidate()`/`reject_candidate()`/
  `get_tree()` 四个方法，均只操作 `decompose_candidates` 字段和树结构，
  不触碰任何执行链路。
- `src/mini_agent/cli/commands/goals.py` 新增三个子命令：`tree`/
  `decompose`/`candidates`，用于按方案 §五"先不做看板 UI，用 CLI 验证
  生成质量和触发逻辑"跑通全流程。
- 新增测试 `tests/test_goal_tree_phase2.py`（37 个用例）。

没有接入任何 cron job，没有改动 `set_status()`/`GoalRunner`/
`ObjectiveExecutor`/`goal_cron_bridge`。

## 二、具体改动

### 2.1 `GoalTreeDecomposer`（§4.2）

- **输入拼装**（`build_prompt()`）：节点自身 title/description/level、
  祖先链（`_ancestor_chain()`，从根到该节点，环路防御性截断）、已有直接
  子节点（含状态）、30 天内被 reject 过的同节点候选主题——四项均按方案
  原文要求拼进 prompt。
- **调用方式**：`llm_helper.ask(prompt)`，`llm_helper` 未传时用
  `LLMHelper.from_config(cfg)` 兜底构造（`cfg` 也未传则 `load_config()`），
  跟 `ensemble/judge.py`/`goal_execution_spec.py` 等既有调用点同一种
  `helper or LLMHelper.from_config(cfg)` 约定。输出格式约定为"每行一个
  候选，`标题｜一句话描述｜层级`，全角竖线分隔"（同时兼容半角 `|`），
  比原方案"每行一个候选标题+一句话描述"多加了"层级"这一列——因为方案
  §4.1 明确要求"产出的子节点 level 由 LLM 按父节点 level 的下一层给出
  建议，程序侧做合法性兜底"，需要 LLM 给出显式建议才有兜底的对象。
- **合法性兜底**（`_parse_candidates()`）：LLM 给出的 level 经
  `validate_node_hierarchy()` 校验，不合法（包括 LLM 没给、给了空
  字符串）时拉回 `_next_default_level(parent.level)`（父节点在
  `LEVEL_ORDER` 里的下一层）。
- **去重**：候选标题跟"已有直接子节点标题"、"30 天内被 reject 过的同
  节点候选"、"本次生成结果内部"三处去重，复用
  `evolution/objective_outcome_tracker.normalize_title_key()`（与
  `soft_goal_deriver.py` 同一份归一化实现）。单次调用最多产出
  `MAX_CANDIDATES_PER_CALL=5` 个候选。
- **节奏治理**（`should_decompose()`）：
  1. 节点已有未处理候选（`decompose_candidates` 非空）→ 跳过；
  2. 距上次触发（`goal_tree_decompose_state.json` 记录，跟
     `soft_goal_deriver.py` 的 `.agent/consolidation_rhythm.json`
     是同一种"独立 JSON 状态文件、只记时间戳"模式）不足
     `MIN_DECOMPOSE_INTERVAL_SECONDS`（暂定 3 天）→ 跳过。
  `decompose(..., force=True)` 可以跳过这两条检查（供 CLI 手动强制触发）。
- **reject 去重**（`record_rejected_topic()`/`_load_rejected_keys()`）：
  跟 `soft_goal_deriver.py` 的 `_load_rejected_keys()`/`record_rejected()`
  是同一种实现模式（独立 JSON 文件 `goal_tree_decompose_rejected.json`，
  key 为 `"{node_id}:{归一化标题}"`，30 天 TTL），只是 key 里多了
  `node_id` 前缀——因为分解候选是"针对某个具体节点"的，不像
  `soft_goal_deriver` 是全局顶层 Goal 去重，同一个标题在节点 A 下被拒绝
  不该影响节点 B 下的生成。

### 2.2 三种触发时机的落地程度

- **触发时机 3（手动触发）**：完整实现，`decompose()` 可以被任何调用方
  直接调用，CLI `/agent goals decompose <id>` 是这条路径。
- **触发时机 1（停滞巡检）**：只实现了**检测函数**
  `find_stale_nodes_for_scan(backlog, stale_days=14)`——纯规则、只读、
  不加锁（同 `goals_missing_objective()` 的取舍：调用方紧接着要对命中
  节点逐个跑可能较慢的 LLM 调用，不该在锁内做）。**没有**接入
  cron，即"每 24 小时自动跑一次"这部分留给阶段三——阶段三会新增
  `sys:goal_tree_decompose_scan` cron job，内部调用
  `find_stale_nodes_for_scan()` + `GoalTreeDecomposer.decompose()`，这里
  已经把两者都写成了阶段三可以直接复用的独立函数/方法。
- **触发时机 2（完成态联动）**：同样只实现了**检测函数**
  `find_parent_needing_decompose_after_completion(backlog,
  completed_node_id)`——给定一个刚被标记 `completed` 的节点，判断它的
  父节点是否因此"没有其它 active 子节点了"。**没有**把它接进
  `GoalBacklog.set_status()`/`update_fields()` 的写入路径——原方案原文
  说"一个 goal/stage 节点被标记 completed 时，检查其父节点……触发一次
  分解建议"，但真正让一次状态写入同步触发一次 LLM 调用，会把
  `set_status()` 从"毫秒级临界区写入"变成"挂着不确定时长的 LLM 请求"，
  超出现状"轻量写入"的语义；这个"到底走同步内联还是走 cron 下一拍
  捕获"的取舍，方案 §六本身也列为"待实施阶段确认的细节"，因此本阶段
  只交付检测函数本身（已经过完整测试），接线方式留到阶段三跟 cron
  巡检一起决定。

### 2.3 `GoalBacklog` 新增的四个方法

- `append_decompose_candidates(node_id, candidates)`：`_locked()` 内原子
  追加，不去重不校验（生成阶段已经做过）。
- `accept_candidate(node_id, candidate_id, overrides=None)`：**没有**
  复用 `add_node()`——`_locked()` 底层是进程间文件锁（`fcntl.flock`），
  同一进程内嵌套获取会死锁，所以在同一个 `_locked()` 块内内联完成
  "校验层级 → 创建节点 → 挂 children_ids → 移除候选"，逻辑等价于
  `add_node()` 但避免了重入。`overrides` 支持覆盖
  `title`/`description`/`level`，对应 §4.4 的"✏️ 编辑后采纳"。
- `reject_candidate(node_id, candidate_id)`：只做移除，返回被移除的
  候选 dict（供调用方取 title 去记 30 天去重）；30 天去重记录本身不在
  这里写（`GoalBacklog` 不该知道 `GoalTreeDecomposer` 的状态文件），由
  `GoalTreeDecomposer.reject_candidate()`（组合两步）来做，CLI/未来看板
  都应该走后者。
- `get_tree(root_id=None)`：只读、不加锁，返回
  `{"node": GoalNode, "children": [...]}` 嵌套结构，`root_id` 省略时用
  全局根节点。原方案把它归在 §4.5（不分阶段），但因为 CLI `tree` 命令
  和阶段二的验证流程（"生成候选后要能看到它挂在哪个节点下"）都需要它，
  提前在本阶段一并实现，不等阶段三/四。

### 2.4 CLI 三个新子命令

- `/agent goals tree [root_id]`：文本树形打印，level 图标
  🌍/🧭/📅/🎯/📌，`current_focus_ids` 非空时标 ⭐（阶段三才会真正计算
  这个字段，目前恒为空，图标逻辑先写好），待确认候选以缩进的
  "┊ 待确认候选：" 前缀列在对应父节点下。
- `/agent goals decompose <id> [--force]`：手动触发一次分解，打印生成的
  候选和后续 accept/reject 命令提示；被节奏治理拦截时给出原因和
  `--force` 提示。
- `/agent goals candidates <id> accept|reject <candidate_id>`：
  accept 调 `GoalBacklog.accept_candidate()`，reject 调
  `GoalTreeDecomposer.reject_candidate()`（带去重记录）。

## 三、验证

新增 `tests/test_goal_tree_phase2.py`（37 个用例），覆盖：

- `GoalBacklog` 四个新方法的正常路径、异常路径（候选不存在/节点不存在/
  层级不合法）、`overrides` 覆盖；
- `GoalTreeDecomposer.build_prompt()` 包含祖先链/已有子节点；
- `_parse_candidates()`：基础解析、全角/半角竖线兼容、非法 level 兜底、
  三种去重（已有子节点/已拒绝主题/本次内部重复）、数量上限；
- `should_decompose()` 的两条节奏治理规则、`force=True` 绕过；
- `decompose()` 的完整流程（含 mock LLM helper）：正常生成、节点不存在、
  LLM 异常、空输出、节奏治理拦截 + `force` 绕过；
- `reject_candidate()`（decomposer 版）的移除 + 去重记录联动，及去重
  记录对后续 `_parse_candidates()` 的实际影响；
- `find_stale_nodes_for_scan()`：命中停滞节点、排除有 active 子节点的
  节点、排除刚 touch 过的节点、排除 `objective` 层级；
- `find_parent_needing_decompose_after_completion()`：最后一个 active
  子节点完成时返回父节点、还有其它 active 兄弟时不返回、无父节点/
  节点不存在时返回 `None`。

执行结果：

```
python -m pytest tests/test_goal_tree_phase1.py tests/test_goal_tree_phase2.py tests/test_goal_backlog.py -q
67 passed
```

另外做了一次手动端到端冒烟（mock `llm_helper`）：`add_node` 建根/domain
→ `decompose()` 生成候选 → CLI `_cmd_tree` 打印 → `accept_candidate()`
采纳 → 再次打印确认新节点挂载正确，全部符合预期。

CLI 模块 `import` 校验：本沙盒环境原本缺 `fastapi`/`rich` 两个第三方包
（与本次改动无关，是环境问题），补装后 `mini_agent.cli.commands.goals`
可以正常 import，新增的 `_cmd_tree`/`_cmd_decompose`/`_cmd_candidates`
三个函数存在且可调用。

## 四、与原方案的差异小结

| 方案原文 | 本阶段实际交付 | 原因 |
|---|---|---|
| 触发时机 1/2 直接生效 | 只交付检测函数，不接 cron/不接 `set_status()` | 方案 §五 本就把 cron 接入放在阶段三；触发时机 2 若同步接线会让状态写入意外挂上 LLM 调用，方案 §六 也列为待定细节 |
| 候选格式"标题+一句话描述" | 多加"层级"一列 | §4.1 要求 LLM 给出层级建议供程序侧兜底，没有这一列就没有兜底对象 |
| `get_tree()` 不分配具体阶段 | 阶段二提前实现 | CLI 验证流程和 `tree` 命令都需要，成本低 |

## 五、下一阶段

阶段三（现阶段焦点）需要：

1. `compute_current_focus(node, children, now)` 规则计算函数；
2. 两个新增内置 cron job：`sys:goal_tree_focus_recompute`（挂
   `compute_current_focus`）、`sys:goal_tree_decompose_scan`（挂本阶段
   已经写好的 `find_stale_nodes_for_scan()` + `GoalTreeDecomposer.
   decompose()`，以及决定 `find_parent_needing_decompose_after_
   completion()` 到底同步接线还是走 cron 下一拍捕获）；
3. `focus_pinned_ids` 的 pin/unpin 接口。

这些都可以直接在阶段一/二已有的数据结构和函数之上开工，不需要额外的
数据模型改动。
