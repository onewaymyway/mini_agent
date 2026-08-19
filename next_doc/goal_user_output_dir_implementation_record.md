# Goal 用户自定义产出目录 — 实施记录

> 前置阅读：[Goal 产出目录规范](../docs/goal-output-directory-guide.md)
> （§11 已同步更新，这里是设计动机 + 实施细节的存档，日常查阅优先看
> docs/ 下的用户指南）

## 1. 问题

`output_workspace.py` 引入固定四目录模型（`output/notes/spec/scratch`）
之后，`output/` 的物理位置被写死为
`.agent/daemon_run_outputs/goals/<goal_id>/output/`。但用户创建 Goal 时
经常会在 description 里明确指定一个业务路径，例如：

> 持续关注中国 A 股信息……生成相关的股票分析报告，放到
> `research/stock_analyse` 目录下。

旧实现下，`detect_user_specified_output_hint()` 只能从 description 里
正则抓出"疑似路径片段"，塞进 prompt 做一段**软性文字提醒**——真正落盘
位置仍然是系统默认的 `output/`，用户写的路径既不生效也不会被真正采纳，
agent 只能"尽量按提示理解"，可靠性完全依赖 agent 当轮对这段提示的理解
是否到位。

## 2. 方案取舍

评估过三种方案：

1. **完全语义化**：靠 LLM 在创建 Goal 时理解 description、直接决定
   `output/` 位置。放弃——不确定性太高，一旦理解错，后续每一轮都会
   持续写错地方，且难以事后纠正（每轮 prompt 都要重新"猜"一次）。
2. **正则检测直接生效**：`detect_user_specified_output_hint()` 检测到
   就直接写入 `user_output_dir`。放弃——正则本身承认自己"存在漏检/
   误检都是预期内的"，直接生效风险太高（例如一句不相关的话里恰好出现
   路径样式的片段）。
3. **检测出建议 + 人工确认后才生效**（采用）：检测结果只作为
   `user_output_dir_suggested` 存档，看板据此预填一个可编辑的输入框，
   用户确认或改写后才真正写入 `user_output_dir` 并从下一轮触发开始生效。
   兼顾"不用每次都手动从零打字"和"最终决定权在人不在正则"。

`notes/`/`spec/`/`scratch/` 三个目录**不**跟随 `user_output_dir` 迁移，
理由：用户表达"产出放哪"这个诉求，语境永远是"最终交付物"，从没有 Goal
描述里出现过"把执行规范/试验田也放到 xxx"这类表达；这三个目录是 agent
自己的过程记账，跟随迁移反而会让用户在自己指定的业务目录里看到一堆
不相关的内部状态文件（`SPEC.md`、`cycle_0003.md`……），违背用户本来的
诉求。

## 3. 改动点

| 文件 | 改动 |
|---|---|
| `perception/goal_backlog.py` | `GoalNode` 新增 `user_output_dir`/`user_output_dir_suggested` 两个字段；`add_goal()` 创建时跑一遍检测，命中则写入 `user_output_dir_suggested`（异常静默跳过，不影响创建主流程） |
| `evolution/output_workspace.py` | `goal_output_dir()` 新增 `user_output_dir` 可选参数，设置时解析到 `<project_root>/<user_output_dir>`（支持绝对路径原样使用）；`ensure_output_skeleton()`/`scan_output_structure()`/`render_output_readme()`/`build_legacy_migration_directive()`/`check_scripts_requirements_consistency()`/`detect_experiments_promotion_candidates()`/`detect_accretive_duplicate_candidates()` 均透传同一参数 |
| `evolution/goal_cron_bridge.py` | `_append_output_workspace_context()` 读 `goal.user_output_dir` 传给上述函数；已确认时 prompt 直接写明"本 Goal 已由用户明确指定产出目录为……"，不再依赖旧的软性提醒；未确认但检测到候选时，提醒文案改为指向看板确认入口；`_build_tidy_problem_checklist()` 同步新增 `user_output_dir` 参数并透传给内部三个启发式检查函数 |
| `api/routes.py` | `PATCH /v1/goals/{goal_id}` 支持 `user_output_dir` 字段（空字符串 = 清除，改回默认路径） |
| `apps/mini_agent_kanban/app.py` | Goal 编辑表单新增"产出目录"输入框，默认值优先 `user_output_dir`，否则回填 `user_output_dir_suggested` 并给出提示文案 |

`mini_agent_kanban_x`（React/TS 版看板）对应的 Goal 编辑表单**尚未同步
这个字段**——目前主看板仍是 Streamlit 版，React 版还在 P8/P9 阶段，留作
后续一项待办，接口层（`PATCH /v1/goals/{goal_id}`）已经支持，纯前端
补一个输入框即可。

以下三个 tidy 阶段的启发式辅助函数已同步支持 `user_output_dir` 透传：
`detect_accretive_duplicate_candidates()`、
`check_scripts_requirements_consistency()`、
`detect_experiments_promotion_candidates()`——用法与 `goal_output_dir()`
一致，`goal_cron_bridge._build_tidy_problem_checklist()` 已相应更新，
tidy 阶段的问题清单在用户自定义产出目录下同样能正确核查。

## 4. 兼容性

`user_output_dir`/`user_output_dir_suggested` 均默认 `None`，未设置时
`goal_output_dir()` 行为与改动前完全一致——所有已存在的 Goal、已跑过的
历史轮次不受影响，不需要任何数据迁移脚本。

`GoalNode.to_dict()`/`from_dict()` 已同步补上这两个新字段的读写（早期
版本漏掉了这一步，字段虽然在 dataclass 上但从不落盘/加载，属于内部
修正，未对外发布过，这里直接记录不单独作为一条改动列出）。

## 5. 存量 Goal 的自动补齐检测

这个功能上线前创建的 Goal（`goals.json` 里没有 `user_output_dir`/
`user_output_dir_suggested` 这两个键）**不需要跑任何迁移脚本/命令**。

`GoalNode.from_dict()` 对缺失的键统一 `.get(key)` 兜底为 `None`，`None`
在这里被赋予明确语义：**从未跑过检测**（区别于"检测过、没找到"的空
字符串 `""`——`add_goal()` 现在无论有没有命中都会写非 `None` 值，保证
"新建的 Goal"和"没来得及检测的历史 Goal"可以被精确区分）。

`GoalBacklog.load()` 新增 `_backfill_user_output_dir_suggestions()`：
每次加载后，扫一遍所有 `level == "goal"` 且字段仍是 `None` 的节点，对其
`description` 补跑一次 `detect_user_specified_output_hint()`，结果写回
**内存**（命中→路径片段，未命中→`""`，两者都会让状态从"未检测"变成
"已检测"）。

**特意不在这一步落盘**：`GoalBacklog.load()` 有多个只读查询路径（如
`goals_missing_objective()`）明确注释过"只读查询，不加锁"，如果补齐时
顺手写盘，等于绕开了 `_locked()` 的加锁写保护，可能跟其他进程在锁保护
下的并发写产生"后写覆盖先写"的问题。不落盘不影响可见性：真正暴露给
用户的入口（`GET /v1/goals`）每次请求都会重新 `load_goal_backlog()`，
检测本身是纯函数、开销可忽略，等价于"每次读都顺手算一遍"；需要真正
落盘时，自然会经过某次 `_locked()` 写操作（`load()` → 调用方改字段 →
`save()`），这次内存里算出来的补齐结果会随之一起落盘，不需要额外处理。

效果：**用户打开一次看板，旧 Goal 就会自动带上建议值**（如果描述里确实
有路径提示的话），跟新建 Goal 的体验完全一致。这个自动补齐只影响建议
字段本身，不会替用户自动设置 `user_output_dir`（真正生效仍然需要用户
在看板上确认一次），保持"检测归检测、生效归人工确认"这条设计主线不变。

已手工验证端到端行为：构造一份缺失新字段的旧格式 `goals.json` 加载 →
`user_output_dir_suggested` 正确从 description 检测出路径、Objective
节点不受影响（只对 `level == "goal"` 生效）→ 磁盘上暂时不变 → 触发一次
`update_fields()` 后磁盘上正确带上补齐结果。

## 6. 使用方式速查

- 创建 Goal 时描述里带路径提示 → 系统自动检测存为建议，不生效；
- 存量 Goal（这个功能上线前创建的）→ 打开一次看板即自动补齐建议，不需要
  手动迁移；
- 看板 → 对应 Goal 卡片 → "✏️ 编辑标题/描述/优先级" → "产出目录"
  输入框（若有建议会自动预填）→ 确认/改写 → 保存；
- 保存后立即生效于**下一次触发**（自然到期，或手动 `/cron run
  <job_id>` 立即触发一轮）；
- 想改回默认路径：把"产出目录"输入框清空并保存。
