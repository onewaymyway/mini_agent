# world_simulator — 通用事物发展模拟引擎：决策推演分析 + 持续性模拟

> 本项目遵循 `next_doc/external_projects_workspace_plan.md` 确立的外部
> 项目标准结构：完全自包含、可独立于 mini_agent daemon 运行。可以整体
> 移动到任意路径、放进独立 git 仓库，只要在 daemon 侧重新
> `mini-agent projects register <新路径>` 一下即可继续被"大管家"看见。
>
> **面向使用者的说明文档在 [`docs/`](./docs/README.md)**：项目是什么/
> 怎么用见 `docs/overview.md`，如何测试/验证功能正常见
> `docs/testing_guide.md`。本文件（`PROJECT.md`）偏工程/设计视角，
> 记录目标、已知限制和分阶段交付历史，两者不重复。

## 目标

一个能模拟"任意事物发展"的通用引擎，服务两类场景：决策推演分析
（给定处境+候选选择，模拟各自可能未来）、持续性模拟/游戏（可推进/
暂停/恢复/存档分叉的模拟实例）。完整方案见
`next_doc/world_simulator_external_project_plan.md`。

阶段一（脚手架 + 核心闭环，已完成）交付的功能点：

1. `create_simulation`：一句话意图 → 生成模拟提案草稿（走
   `generate_scenario` workflow）→ 落盘为 step 0 初始状态。
2. `advance_simulation`：推进模拟实例一步（走 `advance_step`
   workflow），支持传入候选选项 id，也支持不传（由引擎给默认走向）。
3. `list_simulations`：列出所有实例及当前状态摘要。
4. `health`：健康检查（不依赖 LLM/网络，只验证存储层可读）。

阶段一只落地"人生模拟"（`life_sim`）一个模板，`--template` 参数已
预留但暂不接受其它取值；分支/对比（阶段三）、自动挡（阶段四）、
`batch_advance_daily` 调度（阶段五）尚未实现，见下方"变更记录"与主
文档第 7 节的阶段划分。

阶段二（独立看板 MVP，已完成）交付 `app.py`（Streamlit，`streamlit
run app.py`启动）：

1. **模拟列表**：实例卡片（标题/状态/当前步数/推进模式），支持打开
   详情、新建入口。
2. **创建向导**：意图 → 调用 `spec_generator.generate_scenario()` →
   展示可编辑草稿（标题/摘要/变量 JSON/初始候选方向）→
   `engine.materialize_simulation()` 落盘。
3. **实例详情/推进面板**：当前状态 + 变量、时间线（"章节卡片"倒序
   展示）、候选分支渲染成可点击选择卡片、暂停/恢复/标记结束。

视觉主题"夜航日志"（深靛蓝底 + 灯笼金点缀），与 stock_watch 的数据
看板风区分开，见方案第 5 节 + `frontend-design` skill 的设计取向指南。
对比视图（页面 4）、存档管理（页面 5）、游戏化视图（页面 6）留给阶段
三及之后实现。

阶段三（分支/对比，已完成）交付：

1. `world_simulator/branch_manager.py`：`list_branches`（枚举实例下已
   存在的分支）、`fork_branch`（在某个历史节点开一条新分支，原时间线
   原样保留，不销毁重写；默认切换到新分支，也支持 `switch=False` 仅
   留档）、`switch_branch`（切换"当前活跃分支"）、`compare_timelines`
   （取齐若干条时间线的数据，可以跨实例）。
2. `app.py` 新增：详情页"分支"区块（分支列表、切换、从任意历史节点
   分叉、加入对比）；独立的"对比视图"页面（选两条时间线并排展示时间线
   + 按 step 对齐的关键变量表格）。

"回滚重新选"在本项目里被设计成"在历史节点开一条新分支"而不是原地
覆写——这个取舍从阶段一 `state_history.jsonl` 只追加不删除的落盘方式
就已经确定，阶段三只是把它暴露成一个正式的分支管理接口。

阶段四（自动挡/代理执行，已完成）交付：

1. `world_simulator/autopilot.py`：`_build_decision_context()` 把
   `manifest.autopilot`（`principles`/`risk_preference`）渲染成自然
   语言"决策者画像"；`run_autopilot_step()` 让单个实例的代理推进一步
   （`review_mode=pause_on_major_decision` 且这一步被判定为重大决策时
   自动把实例状态设为 `paused`）；`run_batch_autopilot()` 批量推进所有
   "开启自动挡且进行中"的实例，单实例失败不中断整批。
2. `engine.py::advance()` 扩展：新增 `current_options_json` 输入（把
   当前候选分支喂给 skill），自动挡场景下从 `advance_step` 结果里读回
   `chosen_option_id`/`chosen_reason` 并校验其确实在候选列表里（拒绝
   LLM 编造的 id）；`SimState` 新增 `major_decision` 字段。新增
   `set_pilot_config()` 供看板更新 `pilot_mode`/`autopilot`。
3. `advance_step.yaml` + `life-sim-template` SKILL.md 补充"自动挡代选"
   规则（decision_context 非空且未显式指定选择时，skill 需要从候选里
   自己选一个并给理由）。
4. `entrypoints/advance_simulation.py --all-autopilot --steps N` 落地
   （原来是"阶段四实现前先报错"的占位），`project.yaml` 补上
   `batch_advance_daily`（`cron: 0 6 * * *`）。
5. `app.py` 详情页新增"推进模式"区块：查看/配置自动挡（原则、风险
   偏好、review_mode）、手动触发一次代理推进（用于测试）；时间线里
   自动挡选择的步骤会带上"理由"展示。

代理决策不是新的推理组件，复用的仍是 `advance_step` 这一次 LLM 调用，
只是多喂了一段角色设定——这个取舍在方案第 4.1 节就已经定好，阶段四
只是把它接了起来。

阶段五（batch_advance 调度 + 注册可见性，已完成）交付：

1. `project.yaml` 的 `batch_advance_daily`（`cron: 0 6 * * *`，阶段四
   已加入）经 `mini-agent projects register external_projects/
   world_simulator` 注册验证：`mini-agent projects status
   world_simulator` 能正确列出全部 5 个 entrypoint（含
   `batch_advance_daily` 及其 cron 声明）、`health: healthy`（走
   `health_check.cmd`）。
2. 验证了方案要求的两条路径都通：
   - **不依赖 daemon 也能单独跑**：`python entrypoints/health.py`、
     `python entrypoints/list_simulations.py` 等在没有任何注册表记录
     的情况下直接执行成功（阶段一起就是这样，阶段五只是正式复核）。
   - **daemon 在场时能看到健康状态**：注册后 `mini-agent projects
     status` 能看到实时健康状态与账本（`last_run`），符合
     `external_projects_workspace_plan.md` 的既有约定。
3. 新注册的项目 `enabled` 默认为 `False`（opt-in，daemon 侧既有约定），
   即"注册"本身不会让 `batch_advance_daily` 立刻开始按 cron 自动跑——
   需要用户在看板上或 `mini-agent projects enable world_simulator`
   显式打开，才会真正参与 daemon 的定时调度；验证完毕后已
   `unregister`，不在这个开发环境里留下自动调度的痕迹。

阶段五本身不新增业务代码——它验证的是"这个外部项目符合
`external_projects_workspace_plan.md` 的接入契约"这件事，`project.yaml`
在阶段四就已经写对了。

阶段六（多模板扩展，已完成）交付：

1. 新增 `skills/group-evolution-template/SKILL.md`（第二个场景模板：
   群体演化——公司/社群/文明等群体整体的结构性发展，区别于
   `life-sim-template` 的"单个人的人生轨迹"）。
2. **零引擎代码改动**：`engine.py`/`spec_generator.py`/两个 workflow
   yaml 一行都没改，`_skill_name_for_template()` 的既有命名约定
   （`<template 下划线转连字符>-template`）直接就能把
   `template="group_evolution"` 解析到新 skill；
   `tests/test_multi_template.py`（2 个用例）专门断言这一点，验证了
   方案 2.4 节"新增模拟类型=新增一个 skill，引擎本身不用改"这条设计
   假设成立。
3. `app.py` 创建向导的模板下拉框、`create_simulation.py --template`
   帮助文本同步补充第二个选项。

新模板落地过程中唯一"改"的地方，是 UI 层的下拉框选项和 CLI 帮助文本
——这恰好印证了这条假设：扩展成本被完全限制在"UI 暴露入口"和"新增
skill 内容"这两处，没有渗透进核心引擎。

阶段七（游戏化视图深化，已完成）交付：

1. `world_simulator/engine.py::delete_simulation()`：删除一个实例的
   `data/<sim_id>/` 整个目录（含所有分支）。不做软删除/回收站——分叉/
   回滚已经覆盖"不想要这条时间线了"的场景（见阶段三），真正点删除通常
   是想彻底清掉不再需要的实例；不可逆风险由 `app.py` 在 UI 层用「二次
   勾选确认」弥补。
2. `world_simulator/achievements.py`（新文件）：`compute_achievements()`
   纯函数，只依据既有字段（`step`/`chosen_by`/`major_decision`/
   `manifest.status`）计算 6 个成就徽章（启程/崭露头绪/长篇在望/命运
   转折/放手托管/落幕）是否解锁；`achievement_progress()` 汇总解锁
   进度。不引入任何新的持久化结构，是纯展示层的派生计算，因此单独
   成一个不依赖 Streamlit 的模块，便于单测（`tests/
   test_delete_and_achievements.py`，10 个用例）。
3. `app.py` 新增两个页面，补齐方案第 5 节规划的全部 6 个页面：
   - **存档管理**（页面 5）：全部实例总览卡片（标题/状态/步数/分支数/
     创建时间），每张卡片提供「打开」与「🗑 删除」（`st.popover` 内
     二次勾选确认才能点「确认删除」）。
   - **游戏化视图**（页面 6）：从实例详情页新增的「📖 游戏化视图」按钮
     进入；顶部展示成就徽章墙（解锁/未解锁两种视觉状态 + 总体进度条）；
     下方把 `state_history` 按章节重渲染，支持"上一章/下一章/跳章"
     翻页浏览，不提供推进入口——推进仍然只在「推进面板」发生，游戏化
     视图是纯只读的故事回顾呈现层。
4. 侧边栏导航新增「🗄 存档管理」入口。

游戏化视图刻意不做成"新的推进入口"（比如在这个页面上也放"选择继续"
按钮）：方案第 5 节页面 6 的定位是"纯前端呈现层，不需要新的数据结构"，
如果在这里也能推进，就需要同步维护两处选择卡片的状态与交互逻辑，
收益（少点一次「回到推进面板」）远小于维护两套交互入口的复杂度。

阶段八（回填：分支信息增强/自定义选项/多策略对比实验 + 自动时间
粒度，已完成，未在本文件正式编号前即已落地）交付：

1. `branch_manager.list_branches_detailed()`：在原有 `list_branches`
   基础上补齐创建时间/来源分支/当前进度等信息，供「分支」区块展示更
   丰富的列表，而不只是分支名。
2. `engine.advance()` 新增 `custom_option` 参数：用户/自动挡可以跳出
   当前候选列表、提出一个不在 `options` 里的新方向（手动挡/自动挡
   两条路径共用同一套落盘逻辑，`chosen_by` 区分来源），对应方案里
   "系统给的候选终究只是建议"这条设计取向。
3. `autopilot.run_comparison_experiment()`：给定 N 个命名策略 profile，
   各自独立跑一次相同步数，返回可比较的结果列表——"多方案横向对比"
   的第一版，比较对象是**不同的**决策原则/风险偏好，不是同一方案的
   分布（这个缺口正是本文档 4.2 节要补的）。
4. `state_model.SimManifest.settings.time_granularity_mode`
   （`fixed`/`auto`/`guided`）+ `SimState.time_granularity`/
   `granularity_changed`/`granularity_reason`：把"每一步的时间跨度"
   从"全实例固定一个值"升级为"默认延续上一步、情境需要时可以切换，
   切换需要给理由"，`engine.advance()` 负责比较前后粒度算出
   `granularity_changed`（不直接信任 skill 自报），时间线在切换处
   高亮展示。

这两轮迭代完成之后、`next_doc/
world_simulator_universal_world_model_upgrade_plan.md`（简称"演进
计划"）启动之前，`PROJECT.md` 一直没有为它们分配正式阶段编号——本条
目就是那次编号回填，不代表这两轮功能是本次才新增的。

阶段九（资源类字段代码层校验，已完成，见演进计划 4.1 节）交付：

1. `state_model.SimManifest.settings.resource_fields`：可选字段，
   声明 `vars` 里哪些字段是"资源类数值字段"（如 `cash`，或
   `resources.amount` 这种一层嵌套路径），每项可以只给字段名（下限
   默认 0）或 `{"field": ..., "min": ...}` 自定义下限；留空（默认）
   表示不做任何校验，行为与引入这个功能之前完全一致。
   `SimState.resource_violations`：新增字段，记录某一步落盘时被纠正
   过的越界项（`{field, llm_value, clamped_value}`），默认空列表。
2. `engine.py` 新增 `_normalize_resource_fields()`/`_get_nested()`/
   `_set_nested()`/`_apply_resource_guard()`：`advance()` 落盘
   `next_vars` 前对声明的每个字段做一次下限检查——**不拒绝这次
   推进**，只是把越界值原地夹到下限，越界详情记入
   `resource_violations`，保持"系统纠正了一处不合理数值"的透明可见，
   而不是静默篡改或让整次推进失败。
3. `spec_generator.ScenarioDraft.resource_fields`：`generate_scenario`
   阶段 skill 可选输出的建议值（`generate_scenario.yaml` 提示词 +
   两个 `SKILL.md` 都已补充这段可选输出的说明），创建向导展示为一个
   可编辑的逗号分隔字段列表，不强制每次都填。
4. `app.py`：创建向导新增"资源类字段"编辑框（草稿阶段）；实例详情页
   "模拟设置"区块新增同款编辑框（随时增删，下一步推进开始生效）；
   时间线卡片（含独立时间线视图与游戏化章节视图）新增
   `resource_violations` 非空时的高亮提示行，展示字段名 + 原始值 +
   纠正后的值。
5. `tests/test_spec_and_engine.py` 新增两个用例：字段越界时被正确夹到
   下限并记录、字段在范围内时不产生任何记录。

范围严格克制在"数值下限"这一种最简单的校验（演进计划 4.1 节"范围
克制"一节已说明原因）：不引入通用规则引擎，不做资源之间的转移/生产
关系建模，`resource_fields` 未声明时的行为与阶段九之前完全一致。

阶段十（对比实验升级：重复采样 + 结果聚合，已完成，见演进计划 4.2
节；敏感性分析部分见下方"实施记录"）交付：

1. `autopilot.py`：`ExperimentBranchResult` 新增 `final_vars` 字段
   （分支跑完之后当前状态的 `vars`，`run_comparison_experiment()` 里
   也一并回填，两种实验模式的结果结构保持一致）；新增
   `run_repeated_experiment()`：给定**同一份**策略画像 + 起点分支 +
   推进步数 + 重复次数 `n_repeats`，从同一节点 fork 出 `n_repeats`
   条分支各自独立推进（差异只来自 LLM 输出本身的随机性）——实现上
   直接把"同一份 profile 复制 n_repeats 次、各自编号"委托给既有的
   `run_comparison_experiment()`，不重复一套"fork→切换→跑步数→切回"
   的分支管理逻辑。
2. `world_simulator/analysis.py`（新文件）：`aggregate_field_stats()`
   纯函数，输入一组 `vars` + 关注的字段路径列表，按路径分别计算——
   数值型给均值/最小/最大/标准差（`statistics` 标准库，不引入
   numpy/pandas），枚举型给值→次数的分布（降序）；字段完全取不到值
   时返回 `kind="missing"` 的占位结果而不是静默跳过，调用方不用额外
   判断"是不是被漏算了"。不落盘任何新数据结构，纯计算。
3. `app.py`「对比实验」页面新增"🔁 重复模式"开关：关闭时是原有的
   "N 个不同策略各跑一次"；打开时变成"1 个策略 × N 次重复"，额外提供
   一个"关注字段"输入框，跑完后调用 `aggregate_field_stats()` 展示
   均值/极差/标准差（或分布）摘要，而不再是简单列出每条分支的完成
   情况。
4. 新增 `tests/test_analysis.py`（6 个用例，覆盖数值/枚举/嵌套路径/
   缺失字段/顺序保持）；`tests/test_autopilot.py` 新增
   `test_run_repeated_experiment_forks_n_branches_with_same_profile`，
   验证重复实验确实各自独立、`final_vars` 可以直接喂给
   `aggregate_field_stats()` 算出有意义的摘要。

**实施记录（敏感性分析部分）**：演进计划 4.2 节把"单变量扰动"列为
`run_repeated_experiment()` 的直接复用——把"随机性"换成"人为设定的
初始值差异"，不需要新机制。这次没有另外包一层"敏感性分析"专用函数/
UI：`run_repeated_experiment()` 的 `profile` 参数本身就可以在调用方
（未来的看板交互或脚本）那一侧按"同一份策略、不同初始 `vars`"的方式
反复调用，`aggregate_field_stats()` 同样能拿来对比几组结果的差异
幅度；如果之后发现这个用法足够高频，值得专门包一层向导 UI，再单独
排期，不在阶段十范围内强行加一层还没有真实用例验证过的封装。

阶段十一（关键变量可信度标注，已完成，见演进计划 4.3 节）交付：

1. `state_model.SimState.uncertain_fields`：新增字段，列表，每项
   `{"field": ..., "confidence": "high"|"medium"|"low", "note": "..."}`，
   由 skill 在 `generate_scenario`/`advance_step` 两个阶段按需可选
   输出，标注"本质是主观估计、置信度不高"的字段（比如"创业成功率"），
   不要求覆盖全量字段；默认空列表，不影响旧数据/未标注字段。
2. `spec_generator.ScenarioDraft.uncertain_fields`：承接
   `generate_scenario` 阶段 skill 的输出，直接落到 `state0`（不像
   `resource_fields` 那样需要先经过创建向导的编辑确认——置信度标注是
   描述性的，不是需要用户配置的项）。`engine.py` 的
   `materialize_simulation()`/`create_simulation()`/`advance()` 三处
   都已打通传递与解析。
3. `workflows/generate_scenario.yaml`、`workflows/advance_step.yaml`
   与两个模板 `SKILL.md` 都补充了 `uncertain_fields` 的可选输出说明
   （`confidence` 只分三档，明确要求不要给出精确概率制造"伪精确"）。
4. `app.py`：新增 `_uncertain_fields_html()`，在实例详情页与游戏化
   视图的"关键变量"展开区里、`st.json` 原始展示之上，新增置信度徽章
   + 一句话说明的列表渲染，不改动 `st.json` 本身的展示方式。
5. `tests/test_state_and_store.py`/`tests/test_spec_and_engine.py`
   新增四个用例，覆盖 `SimState`/`ScenarioDraft` 的序列化往返、
   `materialize_simulation()` 落盘到 `state0`、`advance()` 从 LLM
   输出解析到 `next_state` 这几条链路，未接入真实 LLM 手动验证。

范围严格克制在"按需可选标注"（演进计划 4.3 节已说明）：不对全量字段
做置信度评估，不引入精确概率数值，`uncertain_fields` 未输出时行为与
引入这个功能之前完全一致。

阶段十二（Problem Compiler 雏形，已完成，见演进计划 4.4 节；范围较
原方案草案收窄）交付：

1. `spec_generator.ScenarioDraft.objectives`：`generate_scenario` 阶段
   skill 可选输出的建议值（字符串列表，如 `["资产净值", "工作满意度"]`，
   不要求是 `vars` 里的精确字段路径）；`generate_scenario.yaml` 提示词
   + 两个 `SKILL.md` 都已补充这段可选输出的说明。
2. `state_model.SimManifest.settings.objectives`：新增设置项，用途与
   `resource_fields` 一致——创建向导展示建议值、允许编辑，最终结果
   落进 `manifest.settings`，实例详情页"模拟设置"区块可随时增删；
   引擎本身不因这个字段的声明与否改变任何推进/校验逻辑，纯粹是记录。
3. `app.py`：创建向导新增"关注指标"编辑框（草稿阶段）；实例详情页
   "模拟设置"区块新增同款编辑框；「对比实验」页面的"关注哪些变量字段
   做统计摘要"输入框会用 `manifest.settings.objectives` 作为默认值
   （仍可修改），把 4.2 节的统计聚合能力和这里的"关注指标"声明串起来。
4. `tests/test_spec_and_engine.py` 新增两个用例：`ScenarioDraft.
   objectives` 解析、`materialize_simulation()` 落盘到
   `manifest.settings.objectives`。

**范围说明**：原方案草案（4.4 节"方案草案"一段）设想的"自动判断哪个
结果算更好"/自动排序**本次未实现**——只做"记录 + UI 默认值参考"这一层
最小闭环，`objectives` 声明之后是否要触发自动排序/推荐留给收集到真实
使用反馈后再决定，避免在没有验证过"用户真的想要系统替他判断好坏"这个
假设之前就把判断权交给系统。

阶段十三（最小版因果摘要，已完成，见演进计划 4.5 节）交付：

1. `state_model.SimState.key_drivers`：新增字段，字符串列表（默认
   空），由 `advance_step` 阶段 skill 按需可选给出，1~3 条短语概括
   这一步变化的关键驱动因素（不是完整的可点击因果调试器）。
2. `workflows/advance_step.yaml` 与两个模板 `SKILL.md` 都补充了
   "1~3 条短语、变化平淡就不用输出、不要凑数"的可选输出说明；
   `engine.py::advance()` 从输出里解析 `key_drivers` 落到
   `next_state`。
3. `app.py`：新增 `_key_drivers_html()`，在时间线卡片（含独立时间线
   视图与游戏化章节视图）叙事文本之前，以"🔑 短语"标签样式展示。
4. `tests/test_state_and_store.py`/`tests/test_spec_and_engine.py`
   新增两个用例（序列化往返、从 LLM 输出解析到 `next_state`）。

这一节收益本身不确定（取决于用户是否真觉得"叙事读不过来"），实现
范围严格限制在"划重点标签"，不做结构化因果链/可点击调试器（演进
计划第 6 节已说明原因）；至此 `next_doc/
world_simulator_universal_world_model_upgrade_plan.md` 4.1~4.5 节
全部落地。

阶段十四（Problem Compiler 进阶：目标驱动的自动排序，已完成，见演进
计划 4.6 节）交付：

1. `spec_generator.ScenarioDraft.objectives` 类型从 `List[str]` 放宽
   为 `List[Any]`：纯字符串写法（阶段十二行为）保持不变，新增支持
   结构化字典 `{"label": ..., "field": ..., "direction": "max"|"min"}`，
   `from_dict()` 对字典项原样保留、不强制转字符串。
2. `world_simulator/analysis.py` 新增 `normalize_objectives()`（把两种
   写法统一成 `Objective` dataclass）与 `rank_by_objectives()`（对声明
   了 `field` 的目标按"逐项胜负计数"排序，刻意不做加权求和——见该
   函数 docstring 的取舍说明；没有任何一条声明 `field` 时返回空列表）。
3. `app.py`：创建向导"关注指标"编辑框下新增可折叠的"高级：声明可
   排序字段"小节（JSON 数组输入，纯文本写法完全不受影响）；实例详情
   页"模拟设置"区块同款折叠区；「对比实验」页面"重复模式"结果区新增
   "按关注指标排序"展示（表格式列出每条分支的目标字段取值 + 胜出
   项数，明确标注"仅供参考，不代表系统认定的最优解"），只在
   `objectives` 里存在声明了 `field` 的条目时才出现。
4. `tests/test_analysis.py` 新增 8 个用例覆盖
   `normalize_objectives()`/`rank_by_objectives()`（纯字符串/结构化/
   混合写法、max/min 方向、缺失值不计分、自定义标签、空结果）；
   `tests/test_spec_and_engine.py` 新增一个用例覆盖
   `ScenarioDraft.from_dict()` 对结构化 `objectives` 的解析。

**范围说明**：严格按 4.6 节方案实现，排序用最朴素的"逐项胜负计数"，
不引入权重配置；纯字符串写法（阶段十二行为）与不声明 `objectives`
时的行为完全不受影响，向后兼容。未接入真实 LLM 手动验证（`generate_
scenario` 阶段 skill 仍只输出纯字符串建议值，结构化写法目前只能由
用户在"高级"折叠区手动声明，skill 端是否要主动建议可排序字段留给
后续按使用反馈决定）。

阶段十五（Evidence Chain 进阶：结构化因果链，已完成，见演进计划
4.7 节）交付：

1. `state_model.SimState.causal_links`：新增字段，字典列表（默认
   空），每项 `{"driver": ..., "affected_fields": [...], "effect":
   ...}`，是 `key_drivers` 的可选进阶信息，二者可以同时输出也可以
   只输出前者，不要求一一对应。
2. `workflows/advance_step.yaml` 与两个模板 `SKILL.md` 都补充了
   `causal_links` 的可选输出说明；`engine.py::advance()` 从输出里
   解析并跳过非字典项，落到 `next_state.causal_links`。
3. `app.py`：`_key_drivers_html()` 升级为按 `driver` 文本匹配
   `causal_links`，有对应说明的标签用原生 `<details>/<summary>`
   渲染成可点击展开（展开显示"受影响字段"/"具体后果"），没有对应
   说明时退化为阶段十三的纯标签展示，新增配套 CSS（`ws-key-driver-
   details` 等）。
4. `tests/test_state_and_store.py`/`tests/test_spec_and_engine.py`
   各新增一个用例（序列化往返含非字典项过滤、从 LLM 输出解析到
   `next_state`）。

**范围说明**：仍然是自由文本 + 字段名列表，不是可执行的因果图/完整
可点击因果调试器（演进计划第 6 节已说明投入产出比偏低的原因）；
`causal_links` 完全可选，不给这个字段时行为与阶段十三完全一致，
向后兼容。未接入真实 LLM 手动验证。

阶段十六（资源转移关系建模——通用规则引擎的最小可行版本，已完成，见
演进计划 4.8 节）交付：

1. `state_model.SimManifest.settings.resource_relations`：可选字段，
   声明 `vars` 里资源字段之间的"转移"关系，每项
   `{"type": "transfer", "from": "cash", "to": "inventory.value",
   "tolerance": 0.1}`（`tolerance` 是允许的相对误差比例，默认 0.1，
   即允许 10% 的"汇率损耗/交易成本"之类的合理偏差）。**只做
   `transfer` 这一种关系类型**，`production`（生产/持续产出）暂不
   支持。`SimState.relation_violations`：新增字段，记录某一步落盘时
   发现的转移不一致项（`{from, to, delta_from, delta_to}`），默认
   空列表。
2. `engine.py` 新增 `_normalize_resource_relations()`/
   `_check_resource_relations()`：`advance()` 落盘 `next_vars` 前对
   声明的每条 `transfer` 关系计算两个字段各自的变化量，超出容差记为
   一条不一致——**不拒绝这次推进、不修改任何数值**（和阶段九的下限
   校验不同，转移关系没有"应该是多少"的唯一正确答案，没法像下限那样
   直接夹值），只做留痕，供时间线展示"这一步的资源转移不太守恒"的
   不一致提示。检查用的是**夹值之后**的 `next_vars`（已经过阶段九
   下限校验修正）与 `current.vars` 对比，看的是"最终真实落盘的
   变化"。
3. `spec_generator.ScenarioDraft.resource_relations`：`generate_
   scenario` 阶段 skill 可选输出的建议值（`generate_scenario.yaml`
   提示词 + 两个 `SKILL.md` 都已补充这段可选输出的说明），写法与
   `resource_fields` 一致。
4. `app.py`：创建向导与实例详情"模拟设置"区块都新增"高级：声明资源
   转移关系"折叠区（JSON 数组输入，参考阶段十四"高级：声明可排序
   字段"的交互模式）；时间线卡片（含独立时间线视图与游戏化章节视图）
   新增 `relation_violations` 非空时的提示行（新增 CSS
   `ws-chapter-relation-violation`，措辞明确是"不一致提示"而不是
   "已自动纠正"，与 `resource_violations` 的提示样式/措辞都做了区分）。
5. `tests/test_spec_and_engine.py` 新增三个用例（转移超出容差被正确
   记录、容差内不产生记录、`ScenarioDraft.resource_relations` 解析）；
   `tests/test_state_and_store.py` 新增一个序列化往返用例（含非字典
   项过滤）。

**踩坑记录**：`generate_scenario.yaml` 提示词最初的举例用了带"."的
字段路径（`"inventory.value"`）和小数（`0.1`）拼进大括号示例，被
`tests/test_workflow_prompt_placeholders.py`（阶段八之后新增的回归
测试，防止 prompt 里的举例大括号被误判为 `{step_id.field}` 占位符，
见该文件顶部的事故复盘）判定为潜在风险——改用不含"."的占位字段名
（`"字段A"`/`"字段B"`）和文字描述（"默认十分之一"）规避，真正的
数值默认值 0.1 只保留在代码（`_normalize_resource_relations()`）里。

**范围严格克制**在"transfer 一种关系类型 + 事后一致性检查"（演进
计划 4.8 节已说明原因）：不引入通用规则引擎 DSL，不做 `production`
（生产/持续产出，需要引入"速率"和"时间粒度换算"，复杂度明显更高），
`resource_relations` 未声明时的行为与阶段十六之前完全一致，向后
兼容。未接入真实 LLM 手动验证。

阶段十七（Belief 与 State 分离 + Entity/Relationship 图结构最小可行
版本，已完成，见演进计划 4.9 节）交付：

**⚠ 特殊说明：本阶段是在触发条件未满足的情况下提前实施的**。演进
计划 4.9 节明确把这一节列为"条件触发"项目——要求先出现真实的"多主体
信息不对称"使用场景才启动，理由是没有真实场景验证时很容易把
`entities`/`shared_vars` 的字段粒度设计错。用户在没有具体场景的前提
下明确要求"先把设计草案落地成代码架子"；执行前已提醒过这个风险，并
建议改为更保守的"只做最小验证性骨架、不碰核心文件"的方案，用户仍
坚持按 4.9 节方案草案完整实现，故按此执行。这不是对"条件触发"原则
的推翻，只是这一次的例外，记录在案供以后复盘参考。

1. `state_model.SimManifest.settings.multi_entity_mode`：新增可选
   布尔字段（默认 `False`）。为 `True` 时，`vars` 顶层按约定组织成
   `entities: Dict[str, Dict[str, Any]]`（每个主体一个 id，值是这个
   主体自己的私有信息）+ `shared_vars: Dict[str, Any]`（所有主体共享
   的公开信息）。**这个约定只存在于文档和消费方（skill/`app.py`）
   里，`engine.py`/`spec_generator.py` 对 `vars` 的处理逻辑没有任何
   改动**——`vars` 对引擎而言始终是不透明的自由 JSON，这也是本阶段
   没有像阶段十六那样新增引擎层校验函数的原因。
2. 新增第三个场景模板 `skills/negotiation-template/SKILL.md`（服务
   "多方谈判/博弈"场景），而不是改造现有两个模板——完整定义了
   `entities`/`shared_vars` 的组织约定、"私有信息不能互相泄露"的
   硬性规则、`generate_scenario`/`advance_step` 两阶段的完整输出
   契约。`spec_generator._skill_name_for_template()`/
   `engine._skill_name_for_template()` 的"模板名下划线转连字符 +
   `-template` 后缀"约定天然支持新模板，两处都不需要改代码（延续
   阶段六验证过的设计假设）。
3. `spec_generator.resolve_hints()` 新增 `multi_entity_mode_hint`：
   根据 `settings.multi_entity_mode` 生成"已启用/未启用"两种提示
   文案，开启时明确点出 `entities`/`shared_vars` 两个约定字段名和
   "私有信息不能泄露"的要求；`generate_scenario.yaml`/
   `advance_step.yaml` 都新增了 `{multi_entity_mode_hint}` 占位符。
   `advance_step` 仍然只是一次 LLM 调用（不会为每个主体单独调用一次
   LLM），只是要求单次输出里区分"谁知道什么"，符合方案草案"仍然一次
   调用"的设计，避免成倍增加调用成本。
4. `app.py` 新增 `_render_vars_display()`：`multi_entity_mode` 为真
   且 `vars.entities` 是非空字典时，按主体分 tab 展示各自的私有信息
   （+ 一个"共享信息"tab 展示 `shared_vars`）；否则安全退化为原来的
   `st.json(vars)` 原样展示（包括 `multi_entity_mode` 为真但 `vars`
   没有按约定给出 `entities` 的情况，比如旧数据或 skill 没遵守约定，
   不会报错）。详情页"关键变量"区块和游戏化章节视图的"这一章的关键
   变量"区块都接入了这个函数。创建向导新增"多方谈判（多主体，阶段
   十七）"模板选项（选中后创建时自动带上 `multi_entity_mode: True`），
   详情页"模拟设置"区块新增对应的手动勾选开关（供其它模板需要时也能
   手动开启，或谈判模板需要时手动关闭）。
5. `tests/test_multi_template.py` 新增端到端用例
   `test_negotiation_template_binds_correct_skill_and_preserves_
   entities_structure`：验证 `entities.甲方`（含 `budget`）与
   `entities.乙方`（含 `walk_away_price`）互不可见（`"乙方" not in
   vars["entities"]["甲方"]`）、`推进后私有信息仍然互不泄露、
   `skill_name` 按模板动态推导为 `negotiation-template`；
   `tests/test_spec_and_engine.py` 新增两个 `resolve_hints` 用例
   覆盖开关两种文案。累计 101 个测试全部通过。

**范围说明（已知限制，均如实记录，不回避）**：只做了最小化的
"Entity + 私有信息"结构，**不是**完整的关系图数据库——不单独建模
`Relationship`，主体之间的关系仍靠 `narrative` 自由文本表达（符合
演进计划 4.9 节方案草案的范围声明）。**未接入真实 LLM 手动验证，
也未经过任何真实多主体场景的使用反馈**——`entities`/`shared_vars`
的字段粒度、UI 分 tab 的展示方式都只是这次实现时的一次性设计判断，
风险高于本项目此前所有阶段（此前每一阶段都是"先有真实使用反馈或
明确设计意图，再落地"，这一阶段是例外）。一旦出现真实谈判/竞争场景
的使用反馈，应该优先按反馈调整现有设计，不应假定现在的实现就是
最终正确的版本；`multi_entity_mode` 未声明或为 `False` 时，行为与
引入这个功能之前完全一致，向后兼容，不影响 `life_sim`/
`group_evolution` 两个既有模板。

阶段十八（Reality Sync 轻量版：外部数据手动校准输入，已完成，见演进
计划 4.11 节）交付：

**⚠ 同样是在触发条件未满足的情况下提前实施**——与阶段十七一起，
应用户明确要求"把剩下的条件触发项也提前实施成代码"而完成，完整背景
见演进计划 4.11 节"实施记录"。这是本轮三个"提前实施"项目里改动范围
最小、风险最低的一个：纯粹的"原样透传一段文本"，没有新增任何数值
校验/覆盖逻辑。

`state_model.SimManifest.settings.calibration_notes`：新增可选字符串
字段（默认空字符串），用户手动填入的"真实世界参考信息"；
`spec_generator.generate_scenario()`/`engine.advance()` 都把这个字段
原样传入对应 workflow 的 `calibration_notes` prompt 输入
（`generate_scenario.yaml`/`advance_step.yaml` 新增
`{calibration_notes}` 占位符，为空时提示语写清楚"没有需要参考的真实
数据"，不是突兀的空白）；`app.py` 创建向导与详情页"模拟设置"区块都
新增"填入真实世界参考信息"折叠区。**没有做任何自动数据抓取/更新**，
是否采信、怎么融入完全由 LLM 判断，与方案原文一致。

`tests/test_spec_and_engine.py` 新增用例
`test_advance_passes_calibration_notes_to_prompt_inputs`，
monkeypatch 打桩验证文本原样出现在 `inputs` 字典里；`generate_scenario`
一侧传递逻辑与之完全对称，未单独重复打桩。**未接入真实 LLM 验证"是否
真的被采信"**——按方案原文说明，这属于 prompt 质量调优范畴，不是这次
的验收内容。`calibration_notes` 未声明或为空时，行为与引入这个功能
之前完全一致，向后兼容。

阶段十九（Hierarchical Agent / Dynamic Cognition Router 设计草案
第一步，已完成，见演进计划 4.10 节）交付：

**⚠ 同样是在触发条件未满足的情况下提前实施**——项目里目前唯一用到
`multi_entity_mode` 的场景是阶段十七的"多方谈判"，主体数量最多两三
个，远没到"数量级明显超过个位数、验证了阶段十七的 `entities` 方案
撑不住"的触发门槛。演进计划里这一节此前是"不排期，无阶段编号"，
应用户明确要求提前实施后，才补记为正式的阶段编号，完整背景见演进
计划 4.10 节"实施记录"。这是本轮三个"提前实施"项目里风险最高的一个
——不仅未经真实场景验证，连"设计草案本身有没有真实价值"都没有验证
过。

1. `state_model.SimManifest.settings.hierarchical_agent_mode`（默认
   `False`）+ `background_entities`（背景角色名字列表，需要与
   `vars.entities` 的键一致，只有 `multi_entity_mode` 同时启用时才
   有意义）。`SimState.background_entities_applied`：新增字段，记录
   每一步实际生效外推的主体名字，默认空列表。
2. `engine.py` 新增 `_apply_background_entity_extrapolation()`：
   `advance()` 落盘前对声明的每个背景角色，用它"上一步到这一步"每个
   数值字段的变化量按相同量再外推一步，**强制覆盖** LLM 这一步给这些
   主体的输出（不管 LLM 实际给了什么值）；没有"上一步"可参考（比如
   第一次推进）或字段非数值时，外推量按 0 处理，即原样保留当前值。
   **只实现了"设计草案第一步"（分层本身），没有实现"调度框架"/
   "认知路由器"**——没有把背景角色从 prompt 输入/输出里物理剔除，
   `advance_step` 仍然是一次包含全部主体的 LLM 调用，理论上仍会在
   背景角色身上浪费一点推理 token（只是不采纳其结果），真正的成本
   节省需要动态拼装 prompt，这次没有做。
3. `spec_generator.resolve_hints()` 新增 `background_entities_hint`：
   区分"未启用"/"启用但没列名字"/"启用且列出名字"三种文案，提示
   skill 这些主体"不用深入推理，反正不会被采纳"，减少不必要的推理
   消耗（但不改变"仍是一次调用"的事实）；`generate_scenario.yaml`/
   `advance_step.yaml` 都新增了 `{background_entities_hint}` 占位符。
4. `app.py` 创建向导与详情页"模拟设置"区块都新增"声明背景角色，
   简化其推理"折叠区；时间线（含游戏化视图）新增"🧩 背景角色由规则
   自动外推"的信息性提示（新增 CSS `ws-chapter-background-entity-
   note`，措辞刻意区别于两种资源相关提示——这不是"问题"，是设计
   使然）。
5. `tests/test_spec_and_engine.py` 新增 4 个用例：第一次推进无"上一
   步"时外推量为 0（强制覆盖 LLM 的编造值）；有真实历史趋势时按线性
   关系正确外推（手动用 `store.append_state()` 构造跨两步的真实数值
   变化，绕开"每步都被强制覆盖导致永远冻结在初始值"这个设计本身的
   已知局限来验证外推逻辑本身是对的）；`hierarchical_agent_mode`
   关闭时完全不介入（向后兼容）；`resolve_hints` 三种提示文案。累计
   106 个测试全部通过。

**范围说明（已知限制，均如实记录，不回避）**：只是"分层"的第一步，
不是完整的"认知路由器"——没有"路由策略"、没有按场景动态决定哪些
主体该升级成"关键角色"，也没有真正做到"背景角色不消耗 LLM 推理
成本"。**未经过任何真实的"大量主体"场景验证**，线性趋势外推是否是
合适的简化模型、`background_entities` 该怎么声明才顺手，都只是这次
的一次性设计判断。还有一个设计本身的局限（不是实现 bug，值得记录）：
因为每一步都会把背景角色强制覆盖成"上一步 + 上一步的变化量"，如果
连续两步的变化量恰好相等（比如初始值从未变化过），这个主体的数值会
永远冻结在初始值上，不会产生任何新的趋势——这是"用简单规则替代真实
推理"必然要付出的代价，真实场景里是否可接受，还没有验证过。
`hierarchical_agent_mode` 未声明或为 `False` 时，行为与引入这个
功能之前完全一致，不影响其它任何模板/场景。

## 数据源与依赖策略

不依赖任何外部数据源，核心依赖是 mini_agent 框架自身的能力：

- `Workspace`：路径/skill 分层隔离（本项目未来若脱离 mini_agent 环境
  独立运行，路径规则不受影响）。
- `LLMHelper`（经由 `WorkflowRunner`/`skill_agent` 间接使用）：模拟
  推进每一步的核心是一次结构化 LLM 调用，不自己另起一套调用逻辑。
- `workflow` 引擎：`generate_scenario`/`advance_step` 两个 workflow
  定义负责"LLM 输出→结构化校验→重试"，不自己重写这套轮子。
- `mini_agent.utils.atomic_write`：状态快照/历史落盘复用现成的原子
  写工具；未装 mini_agent 时 `world_simulator/store.py` 会降级为普通
  文件写（换取"可独立运行"这条硬约束，见 2.6 节）。
- `mini_agent.config.load_config()`（经由 `world_simulator/config.py::
  load_llm_cfg()` 统一入口调用）：provider/api_key/重试/fallback
  chain 全部由宿主实现，本项目不重新写一套配置/调用逻辑；
  `load_llm_cfg()` 只多做一步"原地布局自动探测"（本项目仍挂在某个
  mini_agent 主仓库的 `external_projects/` 下、且未注册/未显式设置
  `MINI_AGENT_MAIN_PROJECT_ROOT` 时，自动把该环境变量设置好），目的是
  让本地开发/未注册进 daemon 时也能直接继承主项目已经配好的
  `agent_config.json`/`providers.json`，不需要为本项目单独再配一份，
  详见 `docs/overview.md`"依赖与配置"一节与
  `docs/testing_guide.md`"常见报错排查"一节。

`requirements.txt` 目前为空——阶段一未引入任何本项目私有的第三方
依赖，所有依赖都来自 mini_agent 主库。

## 已知限制 / 待验证事项

- **`spec_generator`/`engine` 的 LLM 调用路径尚未跑过真实模型**：单测
  用 monkeypatch 打桩了 `WorkflowRunner`/`WorkflowStore`，验证的是
  "engine 传给 workflow 引擎的 skill_name/inputs 是否符合约定"以及
  "workflow 结果如何解析回 SimState 并落盘"，没有验证
  `life-sim-template` skill 在真实 LLM 下产出的 JSON 质量/稳定性——
  首次真实调用前建议先用一个简单意图手动跑一遍
  `create_simulation`/`advance_simulation` 做人工检查。
- **`state_history.jsonl` 用整体重写实现追加**：见 `store.py::
  SimStore.append_state` 的注释，阶段一实例数据量小时可接受，实例
  历史变得很长后需要换成真正的追加写（不影响调用方签名）。
- **暂不支持并发推进同一实例**：没有加锁，如果同一 `sim_id` 被两个
  进程同时 `advance`，历史/当前状态可能互相覆盖；阶段一场景（CLI
  手动触发）不会遇到，独立看板（阶段二）落地后需要评估是否要加锁。
- **`--template` 目前只有 `life_sim` 一个合法取值**：传其它值会在
  `generate_scenario`/`advance_step` workflow 触发时因找不到对应
  skill（`<template>-template`）而报错，属于预期行为，不是 bug。
- **`review_mode: notify_each_step` 目前只是配置项，没有接实际的通知
  渠道**：`autopilot.py` 只实现了 `silent`（不额外处理）和
  `pause_on_major_decision`（暂停）两种取值的真实行为；选
  `notify_each_step` 目前效果等同于 `silent`，后续需要时再接入具体的
  通知方式（比如看板内的提醒列表/`daemon` 的通知渠道）。
- **`batch_advance_daily` 的 cron 调度是否真正被执行取决于宿主 daemon
  环境**：`project.yaml` 里声明了 `schedule: "cron: 0 6 * * *"`，但
  这只是声明，本项目自身不负责起定时任务；本地/独立跑 `app.py`/CLI
  时不会自动触发，需要手动跑 `python entrypoints/advance_simulation.py
  --all-autopilot --steps 1` 或依赖 mini_agent daemon 的调度接线。
- **`autopilot.principles` 不做内容校验**：原样拼进 prompt，恶意或
  荒谬的原则设定不会被拦截，只会体现在推演结果的荒谬程度上——阶段四
  范围内认为这是用户自己配置自己实例的合理边界，不引入额外审核。
- **`delete_simulation` 不是软删除，也没有加锁**：与"暂不支持并发推进
  同一实例"是同一类风险——如果删除操作和另一个进程的 `advance` 同时
  发生，行为未定义；单用户本地跑看板的场景不会遇到，多人共用同一份
  `data/` 目录时需要评估加锁。删除前没有自动导出快照，`app.py` 只在
  UI 层做二次勾选确认，误删后无法恢复。
- **成就徽章（`achievements.py`）是固定的 6 个，不支持自定义/按模板
  区分**：`major_decision`/`autopilot` 这两个徽章的判断逻辑目前对
  `life_sim`/`group_evolution` 两个模板通用，还没有"某个模板特有成就"
  的扩展点；如果后续模板需要模板专属徽章，需要在 `compute_achievements`
  之外再加一层"模板徽章插件"机制，阶段七范围内认为通用 6 个已经够验证
  "游戏化反馈"这个方向的价值。

## 目录结构

```
world_simulator/
├── project.yaml            # daemon 与本项目之间的契约
├── PROJECT.md               # 本文件
├── docs/                      # 面向使用者的说明文档（见文件头链接）
├── requirements.txt          # 独立依赖环境
├── entrypoints/              # headless 单次执行入口
├── world_simulator/         # 业务代码（含 achievements.py：游戏化
│                             #   视图的成就徽章纯函数计算，阶段七）
├── config/                    # 配置文件
├── data/                      # 项目状态数据（账本类）
├── reports/                   # 面向人的产出物
├── tests/                     # 单元测试
└── .agent/
    ├── run_status.jsonl        # 执行状态账本（框架自动读写，见 ledger.py）
    └── improvement_backlog.jsonl  # 改进积压账本（可选，见 backlog.py）
```

## 运行与维护

- 本地单独跑一个 entrypoint：`python entrypoints/<key>.py`（不依赖
  daemon 是否在运行）。
- 注册到 daemon：`mini-agent projects register <本项目路径>`。
- 查看状态：`mini-agent projects status world_simulator`。
- 查看执行历史：`mini-agent projects ledger world_simulator [limit]`。
- 手动触发某个 entrypoint：`mini-agent projects run world_simulator <entrypoint>`。
- 查看/追加改进积压：`mini-agent projects backlog world_simulator list|add`。
- 发起复盘：`mini-agent projects review world_simulator`（需要
  `project.yaml` 里 `review.enabled: true`）。

## 变更记录

- 2026-09-16：由 external-project-manager skill 生成初始骨架。
- 2026-09-16：完成阶段一（脚手架 + 核心闭环）：`state_model.py` /
  `store.py` / `engine.py` / `spec_generator.py`、`generate_scenario`/
  `advance_step` 两个 workflow、`life-sim-template` skill（阶段一首个
  模板）、四个 entrypoint（`create_simulation`/`advance_simulation`/
  `list_simulations`/`health`）、单元测试（`tests/test_state_and_
  store.py`、`tests/test_spec_and_engine.py`）。"意图→提案→确认→推进→
  查看历史"主链路在 CLI 层面可跑通。
- 2026-09-16：完成阶段二（独立看板 MVP）：新增 `app.py`（Streamlit，
  "夜航日志"主题）+ `world_simulator/engine.py::materialize_simulation()`
  （从 `create_simulation()` 拆出"落盘"这一步，供创建向导在生成草稿后
  插入用户编辑/确认环节）。`streamlit run app.py --server.headless
  true` 本地冒烟测试通过（HTTP 200，无异常日志）。分支/对比、自动挡、
  调度尚未实现，见上方"已知限制"与主方案文档第 7 节。
- 2026-09-16：完成阶段三（分支/对比）：新增
  `world_simulator/branch_manager.py`（`list_branches`/`fork_branch`/
  `switch_branch`/`compare_timelines`）及对应单测
  `tests/test_branch_manager.py`（7 个用例）；`app.py` 新增详情页
  "分支"区块与独立的"对比视图"页面。手工验证：`materialize_simulation`
  创建实例 → `fork_branch` 分叉 → `compare_timelines` 取数，原分支历史
  不受影响；`streamlit run app.py` 冒烟测试通过。自动挡、调度、多模板
  扩展、存档管理/游戏化视图尚未实现，见上方"已知限制"与主方案文档第 7
  节。
- 2026-09-16：完成阶段四（自动挡/代理执行）：新增
  `world_simulator/autopilot.py`（`run_autopilot_step`/
  `run_batch_autopilot`/`_build_decision_context`）；`engine.py`
  扩展 `advance()` 支持自动挡代选（读回并校验 `chosen_option_id`/
  `chosen_reason`）、新增 `set_pilot_config()`；`SimState` 新增
  `major_decision` 字段；`advance_step.yaml`/`life-sim-template`
  SKILL.md 补充自动挡选择规则；`advance_simulation.py --all-autopilot`
  落地，`project.yaml` 补上 `batch_advance_daily`；`app.py` 新增
  "推进模式"配置区块。新增单测 `tests/test_autopilot.py`（6 个用例，
  覆盖代选记录、拒绝编造 id、未开启报错、重大决策暂停、批量跳过手动挡
  实例、单实例失败不中断整批），累计 22 个测试全部通过。多模板扩展、
  存档管理/游戏化视图尚未实现，见上方"已知限制"与主方案文档第 7 节。
- 2026-09-16：完成阶段五（batch_advance 调度 + 注册可见性）：用真实
  CLI（`mini-agent projects register/status/unregister`）验证了本项目
  的 `project.yaml` 符合 daemon 接入契约——注册后 `status` 能看到全部
  entrypoint（含 `batch_advance_daily` 的 cron 声明）与健康状态；不
  注册时 CLI/entrypoint 也能独立正常运行。没有新增业务代码，验证完毕
  已 unregister。多模板扩展、存档管理/游戏化视图尚未实现，见上方"已知
  限制"与主方案文档第 7 节。
- 2026-09-16：完成阶段六（多模板扩展）：新增第二个场景模板
  `skills/group-evolution-template/SKILL.md`（群体演化），`engine.py`/
  `spec_generator.py`/workflow yaml 零改动；新增
  `tests/test_multi_template.py`（2 个用例）验证"新增模拟类型不改引擎
  代码"这条设计假设成立；`app.py`/`create_simulation.py --template`
  同步暴露新选项。累计 24 个测试全部通过。存档管理/游戏化视图（阶段
  七，可选）尚未实现，见上方"已知限制"与主方案文档第 7 节。
- 2026-09-16：完成阶段七（游戏化视图深化 + 存档管理）：新增
  `world_simulator/engine.py::delete_simulation()`（删除实例目录，不可
  逆）、新文件 `world_simulator/achievements.py`（`compute_achievements`/
  `achievement_progress`，6 个成就徽章的纯函数计算，无新增持久化结构）；
  `app.py` 新增「存档管理」页面（实例总览 + 删除，`st.popover` 二次
  确认）与「游戏化视图」页面（成就徽章墙 + 按章节翻页的故事回顾，纯
  只读，不提供推进入口），侧边栏补上「🗄 存档管理」入口，实例详情页
  补上「📖 游戏化视图」跳转按钮。新增单测 `tests/
  test_delete_and_achievements.py`（7 个用例，覆盖删除成功/删除不存在
  的实例报错/删除不影响其它实例、4 组成就解锁场景），累计 31 个测试
  全部通过。至此方案第 5 节规划的全部 6 个页面（模拟列表/创建向导/
  实例详情/对比视图/存档管理/游戏化视图）均已落地，方案第 7 节分阶段
  实施计划全部完成。
- 2026-09-16：新增 `docs/` 目录（`README.md`/`overview.md`/
  `testing_guide.md`），补齐面向使用者的说明文档：项目是什么、核心
  概念、目录结构、两种启动方式（`docs/overview.md`），以及如何测试/
  验证功能正常——自动化单元测试怎么跑、端到端手动验证按"输入什么→
  预期结果→怎么算通过"逐条列出（`docs/testing_guide.md`）。不改动
  任何业务代码。
- 2026-09-16：修复"未注册进 daemon 时无法继承主项目 LLM 配置"的问题
  （报错表现为 `generate_scenario workflow 执行未成功……Anthropic
  requires an API key`）：新增 `world_simulator/config.py::
  load_llm_cfg()`/`ensure_main_project_root_env()`/
  `_detect_in_place_main_project_root()`，本项目所有 LLM 调用路径
  （`app.py::_load_cfg()`、三个 entrypoint）统一改走这一个入口——
  仍然完全复用 `mini_agent.config.load_config()` 的既有 provider/
  api_key/重试逻辑，不新写一套；只是在调用它之前，补上"本项目原地
  挂在某个 mini_agent 主仓库的 `external_projects/` 下时，自动设置
  `MINI_AGENT_MAIN_PROJECT_ROOT` 环境变量"这一步，让它不需要注册表
  记录或手动 `export` 也能找到主项目配置，环境变量/注册表仍然优先级
  更高（不会被覆盖）。新增单测 `tests/test_config_llm_inheritance.py`
  （7 个用例），累计 38 个测试全部通过。
- 2026-09-17：修复"自动挡推进失败：advance_step workflow 执行未成功
  （skill=life-sim-template）：status=failed；step(failed): Prompt
  占位符缺失：'"field": ..., "confidence": ...'"这个真实运行时 bug。
  根因：`workflows/advance_step.yaml`（阶段十一为 `uncertain_fields`
  新增的可选输出说明）里用省略号 `...` 举例 JSON 结构，写成了
  `{"field": ..., "confidence": "high"|"medium"|"low", "note":
  "..."}`；`mini_agent.workflow.runner.WorkflowRunner._resolve_prompt`
  的占位符正则会把整个大括号内容当成一个占位符，一旦内容里出现
  "."（省略号本身就是三个"."）就会被当成 `{step_id.field}` 形式去查
  `step_results`，查不到对应 step 就抛 `KeyError`，最终表现为
  workflow 执行失败。修复：把举例里的 `...` 换成不含"."的具体占位
  文本（如 `"字段名"`），`generate_scenario.yaml` 与两个模板
  `SKILL.md` 里同类写法一并排查修正。新增回归测试
  `tests/test_workflow_prompt_placeholders.py`：对 `workflows/*.yaml`
  做静态扫描，任何"大括号内容含'.'但不是合法 `{step_id.field}` 占位符
  格式"的写法都会被测试捕获，防止同类 bug 再次绕过既有的 mock 化
  workflow 执行测试悄悄潜入（那些测试打桩了 workflow 执行器本身，
  根本不会走到真实的 `_resolve_prompt`，无法发现这类问题）。累计
  82 个测试全部通过。
- 2026-09-17：完成阶段十六（资源转移关系建模——通用规则引擎的最小
  可行版本，见演进计划 4.8 节）：新增 `state_model.SimState.
  relation_violations` / `SimManifest.settings.resource_relations`、
  `engine.py::_normalize_resource_relations()`/`_check_resource_
  relations()`（`advance()` 落盘前对声明的 `transfer` 关系做一致性
  检查，不拒绝推进、不修改数值，只留痕）、`spec_generator.
  ScenarioDraft.resource_relations`（skill 建议值承接）；
  `generate_scenario.yaml`、两个模板 `SKILL.md` 补充可选输出说明；
  `app.py` 创建向导与"模拟设置"区块新增"高级：声明资源转移关系"折叠
  区，时间线（含游戏化视图）新增不一致提示行。落地过程中发现并规避
  了一处新的"prompt 举例大括号含'.'"风险（`tests/test_workflow_
  prompt_placeholders.py` 回归测试按设计捕获到），改用不含"."的占位
  写法。新增单测 3 个（`tests/test_spec_and_engine.py`）+ 1 个
  （`tests/test_state_and_store.py`），累计 98 个测试全部通过（`cd
  external_projects/world_simulator && PYTHONPATH=../../src:. python3
  -m pytest tests/ -q`）。`production`（生产/持续产出关系）仍未
  实现，按演进计划节奏留给收集到真实反馈后再评估；阶段十七~十八
  （Belief/图结构、Hierarchical Agent、Reality Sync）仍是条件触发项
  目，尚未启动。
- 2026-09-18：完成阶段十七（Belief 与 State 分离 + Entity/
  Relationship 图结构最小可行版本，见演进计划 4.9 节）。**⚠ 特殊
  说明**：演进计划把这一节列为"条件触发"项目（要求先出现真实的
  "多主体信息不对称"场景），本阶段是在触发条件未满足的情况下应用户
  明确要求提前实施的，执行前已提醒风险并建议更保守的替代方案，用户
  仍选择完整实现，完整背景见演进计划 4.9 节"实施记录"与本文件对应
  阶段十七条目，不应被当成"条件触发原则不再适用"的先例。交付内容：
  新增 `state_model.SimManifest.settings.multi_entity_mode`（默认
  `False`，为真时 `vars` 顶层按 `entities`/`shared_vars` 结构组织，
  引擎本身不解析/不校验，`vars` 依旧不透明）；新增第三个场景模板
  `skills/negotiation-template/SKILL.md`（多方谈判/博弈场景，而不是
  改造现有两个模板）；`spec_generator.resolve_hints()` 新增
  `multi_entity_mode_hint`，`generate_scenario.yaml`/
  `advance_step.yaml` 接入对应占位符；`app.py` 新增
  `_render_vars_display()`，`multi_entity_mode` 开启且 `vars.
  entities` 非空时按主体分 tab 展示（含"共享信息"tab），否则安全
  退化为原样 `st.json` 展示；创建向导新增"多方谈判"模板选项、详情页
  "模拟设置"新增手动开关。新增端到端测试
  `test_negotiation_template_binds_correct_skill_and_preserves_
  entities_structure`（验证私有信息互不泄露）+ 2 个 `resolve_hints`
  用例，累计 101 个测试全部通过（`cd external_projects/world_
  simulator && PYTHONPATH=../../src:. python3 -m pytest tests/ -q`）。
  `Relationship` 完整图结构、Hierarchical Agent、Reality Sync 均仍
  未实现；**本阶段未接入真实 LLM 手动验证，也未经过真实多主体场景的
  使用反馈**，`entities`/`shared_vars` 的字段粒度和 UI 展示方式属于
  一次性设计判断，风险高于此前所有阶段，后续如出现真实使用反馈应
  优先按反馈调整。
- 2026-09-18：完成阶段十八（Reality Sync 轻量版，见演进计划 4.11
  节）与阶段十九（Hierarchical Agent 设计草案第一步，见演进计划
  4.10 节）。**⚠ 与阶段十七一样，均是在各自触发条件未满足的情况下
  应用户明确要求提前实施的**，完整背景见演进计划对应节"实施记录"
  与本文件对应阶段条目，不应被当成"条件触发原则不再适用"的先例。
  阶段十八交付：`SimManifest.settings.calibration_notes`（原样传入
  `generate_scenario`/`advance_step` 的 prompt，不做任何自动数据
  抓取/强制校准），`app.py` 创建向导/详情页新增对应文本框。阶段
  十九交付：`SimManifest.settings.hierarchical_agent_mode`/
  `background_entities`、`SimState.background_entities_applied`，
  `engine._apply_background_entity_extrapolation()`（对声明的背景
  角色用线性趋势外推**强制覆盖** LLM 输出，不采纳其推理结果），
  `spec_generator.resolve_hints()` 新增 `background_entities_hint`，
  `app.py` 新增对应折叠区与时间线信息提示。新增测试 5 个（4 个
  engine 级 + 1 个 resolve_hints 变体），累计 106 个测试全部通过
  （`cd external_projects/world_simulator && PYTHONPATH=../../src:.
  python3 -m pytest tests/ -q`）。阶段十九**只做了"分层"第一步，
  没有做"调度框架"**，且存在一个设计本身的已知局限：背景角色的数值
  在连续两步变化量相等时会永远冻结在初始值上，不会产生新趋势——这是
  "用简单规则替代真实推理"必然的代价，真实场景是否可接受尚未验证。
  三个"提前实施"项目（阶段十七/十八/十九）合计已经把演进计划 4.9~
  4.11 节全部落地，至此演进计划 4.1~4.11 节全部完成；但这三节的
  完成方式与阶段九~十六不同（未经真实需求驱动），风险明显更高，
  后续应该优先根据真实使用反馈调整而不是假定现有设计已经正确。
- 2026-09-18：完成阶段二十（Causal Knowledge Base：跨模拟复用的
  因果知识库，见 `next_doc/world_simulator_toward_universal_
  simulator_plan.md` 4.12 节）。**方针说明：本阶段所属的整份演进
  计划由用户明确要求跳过"触发条件"直接进入路线图**（与阶段十七~
  十九"个别提前实施"不同，这次是整份新文档从一开始就不设触发条件，
  见该文档"方针调整说明"一段），因此不再逐条标注"提前实施"，但同样
  提醒：本阶段未经真实使用场景验证价值，后续应优先按真实反馈调整。
  交付内容：新增 `world_simulator/knowledge_base.py`
  （`KnowledgeItem` 最小字段集：cause/effect/mechanism/confidence/
  source_sim_id/source_template/created_at/validated_count/
  contradicted_count；`record_causal_links()` 按关键词 Jaccard
  相似度合并重复因果关系、避免重复条目；`search()`/
  `format_for_prompt()`/`suggest_for_prompt()` 三层检索-格式化
  接口；`record_contradiction()` 为阶段二十四预留，本阶段无调用方）。
  落盘位置 `data/_knowledge/causal_knowledge.jsonl`，独立于任何
  `sim_id` 目录，`.gitignore` 已同步排除。`engine.py`：`advance()`
  落盘 `next_state` 后旁路写入知识库（`_safe_record_causal_links()`
  吞掉异常，不影响本次推进），构造 `advance_step` prompt 输入时按
  `manifest.intent + current.summary` 检索知识拼入
  `relevant_knowledge_hint`（`_safe_suggest_knowledge()` 同样吞掉
  异常）；`create_simulation()` 把 `data_dir` 转给
  `generate_scenario()`。`spec_generator.generate_scenario()` 新增
  可选参数 `data_dir`，为 None 时退化为占位文案，不影响生成本身。
  两个 workflow prompt 模板新增"系统从以往其它模拟中沉淀的相关已知
  因果知识"占位符 `{relevant_knowledge_hint}`。`app.py` 三处
  `generate_scenario()` 调用补上 `data_dir=DATA_DIR`。新增
  `tests/test_knowledge_base.py`（12 个用例：写入/去重合并/检索
  排序/格式化/证伪计数/序列化往返/非法置信度兜底）+
  `test_spec_and_engine.py` 新增 4 个用例（advance 后写入知识库、
  知识库文件损坏时不影响推进、生成阶段检索命中/未命中两种情形），
  累计 127 个测试全部通过（`cd external_projects/world_simulator &&
  PYTHONPATH=../../src:. python3 -m pytest tests/ -q`）。**已知
  限制**：`causal_knowledge.jsonl` 与 `state_history.jsonl` 一样
  是"整体重写"落盘（`_save_all()` 每次全量重写），暂不支持并发
  写入，数据量大了之后需要评估换成真正的追加写；相似度判定用字符级
  关键词 Jaccard（不引入分词库/语义模型），对"表达方式差异很大但语义
  相同"的因果关系识别能力有限，属于 4.12 节"范围克制"里明确认可的
  代价；知识库目前没有任何管理界面（增删改查），第一阶段只验证
  "自动写入 + 自动检索拼入 prompt"这条最基本的闭环。演进计划里剩余
  五个方向（阶段二十一~二十五：Hypothesis Engine、多尺度因果线、
  Model Regime Detection、Reality Loop 完整版、因果线 UI）尚未
  实施，见该文档第 5 节分期路线图。
- 2026-09-18：完成阶段二十二（Multi-Scale Causal Lines：多尺度因果
  线，Causal Line 成为一等公民，见 `next_doc/world_simulator_toward_
  universal_simulator_plan.md` 4.13 节）。**提醒同阶段二十**：本阶段
  未经真实使用场景验证价值，后续应优先按真实反馈调整。交付内容：
  `state_model.py` 新增 `SimManifest.settings.causal_lines`（因果线
  声明：`id`/`label`/`time_granularity`，留空表示不启用，完全向后
  兼容）和 `SimState.line_updates: Dict[str, Dict[str, Any]]`（key 是
  线 id，value 含 `time_label`/`summary`/`advanced`）；`causal_links`
  每项新增可选 `line_id` 字段用于归属到具体线。`spec_generator.py`：
  `ScenarioDraft` 新增 `causal_lines` 建议值字段；新增
  `_resolve_causal_lines_hint()`，接入 `resolve_hints()` 输出
  `causal_lines_hint`（未声明时明确告诉 skill 不需要输出
  `line_updates`，避免凭空发明线 id）。`engine.py`：`advance()` 解析
  落盘 `line_updates`（原样落盘，engine 不做任何调度决策——"这一步
  该更新哪些线"完全由 skill 判断）。两个 workflow yaml 新增
  `{causal_lines_hint}` 占位符。三个 `skills/*/SKILL.md` 同步补充
  `causal_lines`（创建阶段建议值）、`line_updates`/`causal_links.
  line_id`（推进阶段输出）的字段说明。`app.py`：创建向导 + 详情页
  "模拟设置"新增"声明多尺度因果线"折叠区（JSON 数组编辑，写入
  `settings.causal_lines`）；新增 `_line_updates_html()` 在主时间线
  和"游戏化章节"视图里展示每一步"有动静"的因果线标签（id→label 换算
  用 `causal_lines_meta`，未声明时退化为直接展示 id）；`_render_
  timeline()` 新增 `causal_line_filter` 参数，详情页新增"按因果线
  筛选"下拉框（仅当声明了 `causal_lines` 才出现），**不引入因果线
  之间的调度器**——不强制任何节奏控制，纯展示层过滤，为阶段二十五
  的完整"因果线 UI"打基础。新增/更新测试：`test_state_and_store.py`
  新增 `line_updates` 序列化往返用例；`test_spec_and_engine.py` 新增
  `causal_lines_hint` 变体测试、`ScenarioDraft.causal_lines` 解析
  测试、`advance()` 解析 `line_updates` 的集成测试，累计 132 个测试
  全部通过（命令同阶段二十）。**已知限制**：`line_updates` 完全依赖
  LLM 自觉遵守"用声明过的 id 作为 key"，engine 不做任何校验/纠正
  （不检查 key 是否在 `causal_lines` 里声明过、不检查是否每条线都
  被合理更新），行为不一致时只会体现为"时间线上这条线的标签消失/
  乱码"，不会报错也不会阻塞推进；因果线之间没有任何调度/依赖关系
  建模（比如"谈判线结束后技术线才能继续"这类跨线时序约束完全没有
  支持），纯粹是"分开标注、分开展示"的最小可行版本。演进计划里剩余
  四个方向（阶段二十一、二十三、二十四、二十五：Hypothesis Engine、
  Model Regime Detection、Reality Loop 完整版、因果线 UI）尚未
  实施，见该文档第 5 节分期路线图。
- 2026-09-18：完成阶段二十一（Hypothesis Engine：自动识别关键
  不确定性并生成多世界，见 `next_doc/world_simulator_toward_
  universal_simulator_plan.md` 4.15 节）。**提醒同阶段二十/二十二**：
  本阶段未经真实使用场景验证价值，后续应优先按真实反馈调整。交付
  内容：新增 `world_simulator/hypothesis.py`——`suggest_critical_
  uncertainties(manifest, current_state)` 从 `uncertain_fields`
  里挑出 `confidence == "low"` 的字段，结合 `causal_links.
  affected_fields` 出现次数给出"是否是多条因果链共同起点"的理由，
  纯只读、不发起任何 LLM 调用；`run_hypothesis_worlds()` **没有
  新增任何分叉/推进机制**，完全委托给已有的
  `autopilot.run_comparison_experiment()`——每个假设就是一份"只有
  一条 principle"的自动挡策略画像，复用阶段十就在用的"fork→切换→
  跑 steps 步→切回原分支"整套逻辑；`find_robust_outcomes()` 复用
  `analysis.aggregate_field_stats()`，套一层简单阈值（数值型：
  相对标准差 < 0.3；枚举型：众数占比 >= 0.7）区分"稳健结果"和
  "分歧结果"。**与 4.15 节原始方案的一处偏差**：未采用"把假设锚定
  写进 `manifest.settings.hypothesis_override`"的方案——`settings`
  是整个实例共享的，不是按分支隔离的，用它承载"每条假设分支各自
  不同的锚定"会在分支之间互相污染；改用 `autopilot.py` 已经验证过
  的"每条分支各自独立一份 `pilot_config.json`"机制，效果等价且
  复用度更高，符合 4.15 节"涉及文件"一段"优先选后者，减少对已有
  稳定接口改动"的取舍精神。`find_robust_outcomes()` 的字段参数也
  从文档原始签名的单个 `field` 放宽为 `fields: List[str]`，一次
  对比多个下游结果字段，逐个字段单独调用没有额外价值。`app.py`：
  对比视图页面新增"🔍 让系统建议关键不确定性"折叠区——分析实例 1
  当前状态、选字段、填假设方向（每行一条）、设置步数、运行、查看
  稳健性判断，全部在一个折叠区内完成，不单独开新页面。新增
  `tests/test_hypothesis.py`（9 个用例：建议接口的过滤/计数/边界
  情况，`run_hypothesis_worlds` 的分叉与跳过逻辑，
  `find_robust_outcomes` 的数值/枚举/缺失字段判断，以及一个端到端
  用例验证"假设锚定生效的字段呈现分歧、不受影响的字段呈现稳健"），
  累计 141 个测试全部通过（命令同阶段二十）。**已知限制**："假设
  锚定"完全依赖 LLM 理解并遵守 `decision_context` 里的自然语言
  指令，没有任何代码层面的强制约束——`find_robust_outcomes()` 的
  意义正在于事后检验"LLM 是否真的遵守了假设"，如果某个假设几乎没
  影响到该字段的实际走向，`find_robust_outcomes()` 只会诚实地把
  它判成"分歧不明显"，不会报错也不会二次纠正；分叉出的假设分支
  永久留在实例的分支列表里（不自动清理），大量试验后需要用户自己
  去"分支管理"删除不需要的分支。演进计划里剩余三个方向（阶段
  二十三、二十四、二十五：Model Regime Detection、Reality Loop
  完整版、因果线 UI）尚未实施，见该文档第 5 节分期路线图。
- 2026-09-18：完成阶段二十三（Model Regime Detection / Emergence：
  允许模型结构在线修正，见 `next_doc/world_simulator_toward_
  universal_simulator_plan.md` 4.14 节）。**提醒同阶段二十~二十二**：
  本阶段未经真实使用场景验证价值，后续应优先按真实反馈调整。交付
  内容：`state_model.py` 新增 `SimState.structural_change`（可选，
  默认 `None`）——`{detected, kind, description, proposed_fields,
  accepted, accepted_at}`，`kind` 限定 `new_entity`/`new_mechanism`/
  `regime_shift` 三选一；`engine.py` 新增 `_normalize_structural_
  change()` 在 `advance()` 落盘前校验 skill 原始输出（`kind` 不在
  三选一之内、或 `description` 为空都视为"没给"，不落一条内容不
  完整的提示进历史），并**强制**把 `accepted` 重置为 `False`——不
  信任 skill 自己声称"已采纳"。新增公开函数
  `apply_structural_change(data_dir, sim_id, *, step, branch=None)`：
  是 `SimState.structural_change` 与 `manifest.settings` 之间**唯一**
  的写入通道，只有用户在详情页对某一步的提示点击"采纳"才会调用，
  效果是把该 step 的 `structural_change.accepted` 置为 `True`、记录
  `accepted_at`，并把这条变化追加进 `settings.confirmed_structural_
  changes`（列表）——**不会**尝试自动改写 `vars`/`multi_entity_mode`
  的具体实体结构，engine 不猜"新实体具体应该长成什么 JSON 形状塞进
  `vars.entities`"，那仍然交给下一次 `advance_step` 由 LLM 结合
  "已知这个新实体存在"这条提示自行决定如何在叙事/`vars` 里体现。
  `advance()` 新增 `confirmed_structural_changes_hint` prompt 输入
  （`_format_confirmed_structural_changes()` 把已确认项拼成人类可读
  文本），让"已被采纳的新结构"在后续每一步推进时都能被 LLM 当成
  既有事实参考。`workflows/advance_step.yaml`、三个模板
  `skills/*/SKILL.md` 同步新增 `structural_change` 可选输出说明。
  `app.py`：时间线（`_render_timeline`）与"章节回顾"单章视图都新增
  `_structural_change_html()` 提示渲染 + "采纳为正式结构"按钮（只在
  未采纳时出现，点击后调用 `apply_structural_change()` 并
  `st.rerun()`）；对比视图等只读场景复用 `_render_timeline` 时不传
  `sim_id`/`source_branch`，不会长出这个按钮，同「创建分支」按钮的
  既有取舍。新增 4 个单测（`test_advance_parses_structural_change_
  from_llm_output`、`test_advance_ignores_structural_change_with_
  unknown_kind_or_empty_description`、
  `test_apply_structural_change_marks_accepted_and_updates_settings`、
  `test_apply_structural_change_rejects_missing_or_already_accepted`、
  `test_advance_formats_confirmed_structural_changes_hint_for_
  prompt`），累计 146 个测试全部通过（命令同阶段二十）。**已知
  限制**：延续 4.14 节设计本身的保守取舍——"是否固化"完全依赖用户
  认真核实"采纳"按钮背后的具体内容，系统不做任何二次校验；已确认
  的结构变化只是作为文本提示喂给下一步 prompt，不保证 LLM 一定会
  在 `vars`/叙事里正确体现它；`kind: "new_mechanism"` 报告的新机制
  目前不会自动关联到具体因果线（`causal_links.line_id`），需要用户
  或后续步骤自己在 `causal_links` 里补上归属，见 4.14 节"优先级与
  依赖"一段对这一点的说明。演进计划里剩余两个方向（阶段二十四、
  二十五：Reality Loop 完整版、因果线 UI）尚未实施，见该文档第 5
  节分期路线图。
