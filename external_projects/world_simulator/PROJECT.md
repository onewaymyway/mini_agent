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
- 2026-09-18：完成阶段二十四（Reality Loop 完整版：模拟与现实的真正
  反馈闭环，见 `next_doc/world_simulator_toward_universal_simulator_
  plan.md` 4.16 节）。**提醒同阶段二十~二十三**：本阶段未经真实使用
  场景验证价值，后续应优先按真实反馈调整。交付内容：新增
  `world_simulator/reality_check.py`——`RealityCheck` 数据类
  （`id`/`sim_id`/`branch`/`step`/`predicted_summary`/`predicted_at`/
  `actual_outcome`/`recorded_at`/`verdict`），落盘到
  `data/<sim_id>/reality_checks.jsonl`（不按分支拆文件，`branch`
  字段自己标注归属）；`record_reality_check()` 只做最基础的校验
  （`actual_outcome` 非空、`verdict` 是 `matched`/`partially_matched`/
  `diverged` 三选一之一），**不做任何自动语义匹配判定**——`verdict`
  必须由用户自己选，同 4.16 节"范围克制"一段的要求；`find_for_step()`
  供 `app.py` 查询某一步已记录的历史反馈；`record_and_apply()` 是
  "记录 + 反向影响知识库"的组合快捷方式，`verdict == "diverged"` 时
  才调用知识库更新，`matched`/`partially_matched` 不触发任何知识库
  写入（避免和 `knowledge_base.record_causal_links()` 的跨模拟重复
  出现机制形成双重计数，见该函数 docstring）。`knowledge_base.py`
  新增 `update_confidence_from_reality_check(data_dir, causal_links)`
  ——复用 `record_causal_links()` 写入时同一套"cause/effect 关键词
  Jaccard 相似度匹配"逻辑找到对应知识条目，命中则调用已有的
  `record_contradiction()`（阶段二十当时就为本阶段预留好的接口，见
  该函数 docstring）把 `contradicted_count` 加一；旁路失败（比如
  知识库文件损坏）不影响 `record_and_apply()` 里"现实记录本身是否
  落盘成功"这个更重要的结果。`app.py`：时间线（`_render_timeline`）
  每一步新增"🔁 记录现实结果"折叠区——展示这一步已有的历史反馈列表、
  一个 `st.form` 填"后来实际发生了什么" + 三选一 `verdict` 单选、
  提交后展示"影响了 N 条知识"的反馈；对比视图等只读场景复用
  `_render_timeline` 时不传 `sim_id`/`source_branch`，不会长出这个
  入口，同「创建分支」/「采纳为正式结构」按钮的既有取舍（单章"章节
  回顾"视图本阶段未加，理由见"已知限制"）。新增
  `tests/test_reality_check.py`（8 个用例：记录/校验/查询、知识库
  联动的命中与不命中、`matched` 不触发知识库更新的对称性验证、
  端到端组合场景），累计 154 个测试全部通过（命令同阶段二十）。
  **已知限制**：延续 4.16 节"范围克制"——不做自动数据抓取，
  `actual_outcome` 完全靠用户手动回来填写，没有任何提醒/催办机制
  （比如"这一步过去多久了该去核实一下"），完全依赖用户自己记得回来
  填；`predicted_at` 字段本阶段始终留空——`SimState` 本身不记录墙钟
  时间戳（只有模拟内的 `time_label`），没有可用的真实时间可填，展示层
  应该用 `state.time_label` 代替；"章节回顾"单章视图（`app.py` 里
  `_render_state`/game 模式那部分）本阶段未加"记录现实结果"入口，
  只加了时间线视图，理由是"事后回来对照"这个场景更符合"翻看时间线找
  某一步"的使用路径，游戏模式的单章导航场景优先级更低，后续如有需要
  可以照搬同一套 `st.form` 逻辑补上。演进计划里剩余一个方向（阶段
  二十五：因果线 UI）尚未实施，见该文档第 5 节分期路线图。
- 2026-09-18：完成阶段二十五（因果线 UI：从状态卡片时间线到因果线
  总览视图，见 `next_doc/world_simulator_toward_universal_simulator_
  plan.md` 4.17 节）。至此该文档六个方向**全部完成**。**提醒同阶段
  二十~二十四**：本阶段未经真实使用场景验证价值，后续应优先按真实
  反馈调整。交付内容：`app.py` 新增 `_render_causal_lines_overview()`
  ——依赖阶段二十二落地的 `SimState.line_updates`/`causal_links.
  line_id` 结构，不引入任何新的持久化字段、纯展示层函数。对声明了
  `causal_lines` 的实例，按每条线把历史上所有 `line_updates` 里该线
  `advanced` 不为 `False` 的记录按 step 升序取出，渲染成"该线的时间
  点序列（time_label + 一句话摘要）横向排列，箭头连接"，多条线上下
  并排展示为独立卡片；每条线下方带一个"展开看关联因果链"折叠区，
  通过 `causal_links.line_id == 该线 id` 过滤展示对应的
  `driver → effect（影响字段）`条目，找不到关联条目时展示提示文案
  而不是空白。未声明 `causal_lines` 的实例（既有历史实例的默认情况）
  完全不受影响——`page_detail()` 原来"直接展示时间线"的行为原样
  保留；只有 `manifest.settings.causal_lines` 非空时，实例详情页的
  时间线区块才会多出「📊 因果线总览」「📜 时间线」两个子标签页
  （`st.tabs`），原有的「按因果线筛选」下拉框迁移进「📜 时间线」
  标签页内部，交互位置变化但行为不变。新增 CSS 类
  `.ws-causal-line-row`/`.ws-causal-line-track`/`.ws-causal-line-
  point`/`.ws-causal-line-empty` 等，延续项目"不引入专门前端图
  可视化库，用 Streamlit + 自定义 CSS 卡片"的既有风格，没有做力
  导向图/桑基图之类的复杂交互可视化。**已知限制**：延续 4.17 节
  "范围克制"——不做参考文档第五十二~五十三节"跨因果线连接"/
  "因果贡献拆解"的正式因果图数据结构和交互式布局，只做"总览 + 展开
  看该线内部因果链"两层；"因果线总览"视图本阶段只加在实例详情页，
  对比视图（`page_compare`/组对比）、单章"章节回顾"视图暂未接入，
  优先级更低，后续如有需要可以复用同一个 `_render_causal_lines_
  overview()` 函数直接接入；本次改动是纯展示层函数，`app.py` 本身
  在项目现有测试策略里不做 UI 单测（Streamlit 组件难以脱离运行时
  单独断言），验证方式是运行完整测试套件确认未破坏既有逻辑
  （`world_simulator/tests` 154 个用例全部通过，命令同阶段二十），
  以及人工过一遍"声明两条因果线 → 推进几步 → 详情页查看总览/展开
  因果链"的手动验收路径（见 4.17 节验收标准）。
- 2026-09-18：修复模拟列表排序 bug——`engine.list_simulations()`
  原先直接按 `list_sim_ids()` 的目录遍历顺序返回（`store.py` 里
  `sorted(data_dir.iterdir())`，即 `sim_id` 字典序），但 `sim_id`
  形如 `{template}_{6 位随机后缀}`（见 `_new_sim_id()`），不含时间
  信息，字典序与创建时间顺序完全无关，导致「模拟列表」页面看到的
  实例顺序是随机的，不是用户直觉预期的"最新的排在最前面"。修复：
  `list_simulations()` 内部改为按 `created_at`（`now_iso()` 生成的
  ISO 8601 字符串，可直接字符串倒序比较）显式排序，倒序排列，
  `created_at` 缺失的异常数据统一排到最后（`key=lambda m:
  m.created_at or ""`），不影响其它正常实例的排序也不报错。这是
  `list_simulations()` 唯一的落地位置，因此「模拟列表」页
  (`page_list()`)、「对比视图」/「存档管理」等所有复用这个函数的
  下拉框/列表也一并获得"最新排最前"的顺序，不需要在各个调用方
  分别处理。新增 `tests/test_spec_and_engine.py::
  test_list_simulations_sorted_by_created_at_desc_not_dir_name`
  （故意构造"目录名字典序更靠前但创建时间更晚"的场景，验证排序确实
  依据 `created_at` 而非目录遍历顺序），累计 155 个测试全部通过
  （命令同前）。**已知限制**：极端情况下两个实例在同一毫秒内创建、
  `created_at` 完全相同时，两者之间的相对顺序不保证稳定（`sort()`
  对相同 key 的元素保持原有的目录遍历顺序，不是问题，只是不特别
  声明这种情况下的确定性行为）；`created_at` 本身仍然是"实例创建
  时刻"，不是"最近一次推进"的时间，如果后续需要"按最近活跃时间
  排序"，应该改用 `updated_at`（`materialize_simulation()`/
  `advance()` 均会维护这个字段），本次改动按用户原话"按照时间进行
  排序"理解为创建时间，未做这个区分。
- 2026-09-18（用户新增需求）：因果线去前置条件 + 未来展望视图 +
  折叠面板/标签页配色修复。对应用户原话"因果线应该默认就有，不应该
  有什么前置条件，这是模拟的基本机制，基本基础"、"有一个视图，可以
  看到当前所有的因果线以及对应的未来发展……用户也可以提供修改意见，
  让系统修改修正这些因果线"、"标签按钮背景是白色的，看不清楚"。
  交付内容：
  1. **因果线默认化**：`spec_generator._resolve_causal_lines_hint()`
     未声明分支不再告诉 skill "不需要输出 line_updates"，改为要求
     skill 自行判断这次模拟存在哪几条因果线并自行起 id（一般
     2~4 条）；`engine.advance()` 新增
     `_auto_register_causal_lines()`，把 `line_updates`/
     `causal_links.line_id` 里出现的、`manifest.settings.causal_lines`
     还没登记过的 id 自动补登记（标注 `auto_discovered: True`），
     不需要用户确认（因果线只是展示/组织维度，风险远低于
     `structural_change`，不需要走那一套人工确认流程）。三个模板的
     `SKILL.md`（`life-sim-template`/`negotiation-template`/
     `group-evolution-template`）同步把"因果线可选、意图不明显就
     留空"的措辞改成"默认应该给出"。创建向导/设置面板里手填 JSON 的
     入口保留，但文案改为"手动覆盖/精修"，不再是必需的前置声明。
  2. **因果线未来展望**：新增 `hypothesis.project_line_futures()`
     ——延续 `suggest_critical_uncertainties()` 的克制思路，不发起
     新的 LLM 调用，纯粹从已落盘的 `causal_links`（`driver→effect`）
     配合 `uncertain_fields.confidence` 做确定性推演，按因果线给出
     "如果这条因果关系延续，接下来可能的走向"，标注维度
     （driver）、时间尺度（线的 `time_granularity`）、置信度。
     `causal_lines_meta` 为空、`line_id` 也没声明过 `label` 时一样
     能工作（退化为拿 id 当 label），呼应"不应该有前置条件"。
  3. **因果线 UI 改进**：`app.py` 的`_render_causal_lines_overview()`
     不再在 `causal_lines_meta` 为空时直接返回，改为同时扫描
     `history` 里实际出现过的 `line_id`；「📊 因果线总览」「📜 时间线」
     两个子标签页不再需要先声明因果线才出现，永远展示（未声明/
     未推进过任何线时给出引导文案）。每条线新增"🔮 未来可能的走向"
     展示区（调用 `project_line_futures`）和"✏️ 对这条线提修改意见"
     折叠区——用户填写的意见落到
     `manifest.settings.causal_lines[i].user_feedback`（通过既有的
     `update_settings()` 保存），`_resolve_causal_lines_hint()` 会把
     非空的 `user_feedback` 拼进下一步 `advance_step` 的提示文本，
     要求 skill "认真纳入考虑"。
  4. **配色修复**：`THEME_CSS` 新增针对 `div[data-testid="stExpander"]`
     （收起/展开/悬停各状态）和 `div[data-testid="stTabs"]`
     （tab-list/单个 tab 按钮/选中态）的显式深色覆盖——此前这两个
     Streamlit 原生组件的容器背景没有被 `.stApp` 的整体深色背景
     覆盖到，走的是 Streamlit 默认浅色主题的组件底色，配上本主题的
     浅色文字（`--ws-text`）就是"白底白字"看不清楚；现在统一固定成
     主题调色板，不依赖 Streamlit 当前用的是浅色还是深色主题。
  **已知限制**：`project_line_futures()` 的"未来展望"是基于已有
  因果链的确定性外推，不是真正的多世界模拟——如果需要更严谨的
  "多个可能未来"，应该结合已有的 Hypothesis Engine
  （`suggest_critical_uncertainties`/`run_hypothesis_worlds`）实际
  分叉出几条世界跑几步，这里只是"总览视图能立刻看到点什么"的轻量
  版本，本次改动没有把两者打通（比如"未来展望"里点一个维度直接触发
  `run_hypothesis_worlds`），后续如果验证下来用户确实想要这个联动，
  可以再单独接入。用户对因果线的"修改意见"目前只是原样拼进
  prompt 提示文本，不做任何语义解析/结构化校验，LLM 有没有真的照办
  完全靠它自己的推理质量，`engine.py` 不做任何代码层面的强制。
  新增/修改测试：`tests/test_spec_and_engine.py` 里
  `test_resolve_hints_causal_lines_hint_variants`/
  `test_resolve_hints_causal_lines_hint_ignores_entries_without_id`
  更新为校验新文案的语义（不再依赖"未声明"这句具体措辞），新增
  `test_resolve_hints_causal_lines_hint_includes_user_feedback`/
  `test_advance_auto_registers_undeclared_causal_line_ids`/
  `test_advance_does_not_duplicate_already_declared_causal_lines`；
  `tests/test_hypothesis.py` 新增 `project_line_futures` 相关四个
  测试。累计 162 个测试全部通过（`python3 -m pytest tests/ -q`）。
- 2026-09-18（用户新增需求，独立方案见 `next_doc/
  world_simulator_causal_line_future_tree_plan.md`）：因果线的
  "未来因果树"。对应用户原话"现在因果线只有已进行的，应该还能显示
  未来可能发展的不同可能线条的显示……当创建模拟的时候，就应该创建
  出主要的核心的因果线了，每个因果线都应该有未来的发展因果树。这些
  因果树，也可以在模拟过程中不断地修正优化"。上一条交付记录的
  "已知限制"已经预告了这个后续需求。交付内容：
  1. **新增 `world_simulator/causal_tree.py`**：
     `ensure_future_trees()`（保证因果线列表非空、每条线都有合法
     `future_tree`，列表整体为空时兜底生成一条 `main_line`）、
     `normalize_future_tree()`/`build_default_future_tree()`（校验
     与通用兜底模板："延续现状/加速好转/遇阻受挫"三个维度）、
     `auto_register_lines()`（`_auto_register_causal_lines()` 的
     核心逻辑迁移过来，新登记的线同样带默认未来树）、
     `apply_tree_updates()`（合并 `advance_step` 可选输出的
     `tree_updates`：标记分支"已印证/已排除"、追加新分支，只做
     合并不做判断）、`set_branch_status()`（供用户在 UI 上手动
     修正分支状态，不需要走"提议→确认"两步，因果线本身的风险
     量级远低于 `structural_change`）。
  2. **创建即有核心因果线+未来树**：`engine.materialize_simulation()`
     落盘前统一调用 `ensure_future_trees()`，不论调用方是独立看板
     创建向导还是 CLI/entrypoint，行为完全一致——不再要求"先推进
     一步才看得到因果线"。`spec_generator._resolve_causal_lines_hint()`
     新增 `stage` 参数，创建阶段（`stage="create"`）明确要求 skill
     规划 2~4 条核心因果线且每条线给出初始 `future_tree`（2~3 个
     有实质区分度的分支），推进阶段（`stage="advance"`）列出各线
     当前的未来分支状态，并说明 `tree_updates` 的可选输出格式。
  3. **推进中允许修正因果树**：`SimState` 新增 `tree_updates`
     字段（审计摘要，记录这一步印证/排除/新增了哪些分支 id，不
     重复存储分支全文）；`engine._apply_tree_updates()` 在
     `_auto_register_causal_lines()` 之后、`store.append_state()`
     之前调用，合并结果同时写回 `manifest.settings.causal_lines`
     和 `next_state.tree_updates`。
  4. **UI 展示**：`app.py::_render_causal_lines_overview()` 每条线
     新增"🌳 未来因果树"区块——用缩进列表+状态图标（●已印证/○开放/
     ◐已偏离/✕已排除）展示各分支，每个分支旁边有"标为已印证/标为
     已排除"按钮，点击直接调用 `causal_tree.set_branch_status()`
     写回；原有的`project_line_futures()`单路径历史外推区块保留、
     重新定位为"🔮 简单历史外推（辅助参考）"；时间线新增
     `tree_updates` 审计提示（"🌳 未来树更新 — ..."）；创建向导
     默认展示含 `future_tree` 的因果线 JSON（不再是空文本框）。
  5. **三个模板的 `SKILL.md`** 同步补充 `future_tree`（创建时）/
     `tree_updates`（推进时）的输出格式说明。
  **已知限制**：树的合理性完全依赖 LLM 输出质量，`normalize_
  future_tree()` 只校验形状（有没有 id/description/合法枚举值），
  不校验内容是否真的"有实质区分度"；分支状态手动修正没有撤销 UI
  （审计记录仍在 `tree_updates` 里，理论上可回溯，但没有做撤销
  入口）；因果线 UI 仍然是纯列表展示，不是图形化树，如果后续需要
  更直观的可视化需要额外评估引入图形库/Mermaid 的成本；不做分支
  概率归一化，保持和 `confidence: high/medium/low` 一致的克制
  风格。新增 `tests/test_causal_tree.py`（7 个单元测试）；更新
  `tests/test_spec_and_engine.py` 里
  `test_generate_scenario_binds_skill_and_parses_draft`/
  `test_advance_auto_registers_undeclared_causal_line_ids`/
  `test_advance_does_not_duplicate_already_declared_causal_lines`
  以匹配"创建即有 `main_line`"的新行为。累计 169 个测试全部通过
  （`python3 -m pytest tests/ -q`）。
- 2026-09-19：完成阶段二十七（因果线耦合结构化，见
  `next_doc/world_simulator_universal_simulator_gap_analysis_and_
  roadmap_v2_plan.md` 4.19 节）。交付内容：
  1. `SimState.causal_links` 每项新增两个可选字段（`state_model.py`
     不需要任何代码改动——`causal_links` 本来就是原样透传的自由
     `Dict[str, Any]` 列表，只补充了 docstring 说明）：
     `relation_type`（`"one_way"`/`"two_way"`/`"indirect"`/
     `"feedback_loop"` 四选一，对应参考文档第九节的四种因果线关系，
     不给按单向处理）、`source_line_id`（配合既有的 `line_id` 表达
     "从 A 线影响到 B 线"，不给表示同线内部关系或发起线不明确）。
  2. 新增 `world_simulator/causal_graph.py`：`build_causal_graph
     (history)` 纯函数，从历史 `causal_links` 聚合出"线到线"邻接
     关系视图（`CausalEdge`：source_line/target_line/各
     `relation_type` 出现次数/最多 3 条示例），按总出现次数降序
     排列；`relation_type_label()`/`format_edges_for_display()`
     辅助展示。不落盘任何新数据结构、不做图数据库，调用方需要看
     的时候现算，延续 `analysis.py`/`achievements.py` 的既有取舍。
  3. `workflows/advance_step.yaml`、`skills/life-sim-template/
     SKILL.md` 补充 `relation_type`/`source_line_id` 的可选输出
     说明（`group-evolution-template`/`negotiation-template` 两个
     模板原文写的是"格式与用途与 life-sim-template 完全一致"，
     自动继承新增说明，未单独改动）。
  4. `app.py` 新增 `_render_causal_graph_section()`：在"因果线
     总览"视图下方新增"🔗 跨线影响关系"折叠区，展示
     `build_causal_graph()` 的聚合结果（"发起线 → 落地线：N 次
     某类型影响" + 最多 3 条具体因果关系示例）；无可聚合记录时
     展示引导文案而不是空白。
  5. 新增 `tests/test_causal_graph.py`（11 个用例：空输入/跳过
     无因果链的步骤/多步骤聚合/字段缺失兜底/未知 `relation_type`
     兜底/按总数降序排序/示例条数上限/忽略非字典条目/`to_dict()`
     结构/中文标签转换/文本摘要格式化），累计 180 个测试全部通过
     （`cd external_projects/world_simulator &&
     PYTHONPATH=../../src:. python3 -m pytest tests/ -q`）。
  **已知限制**：`relation_type`/`source_line_id` 完全依赖 LLM
  输出质量，`causal_graph.py` 不做任何校验/纠正（未知的
  `relation_type` 静默兜底为单向，不报错也不提示"AI 给了一个不
  认识的类型"）；聚合视图目前只统计"出现次数"，不做传播强度/
  时间延迟等定量分析（参考文档第十一节 Influence Field 的完整
  维度暂不实现，见方案文档 4.24 节"暂不建议"的说明）；这是后续
  4.20（多尺度真正并行）/4.21（归因/贡献拆解）/4.22（反事实矩阵
  向导）/4.26（开放世界闭环收尾）几个方向的数据基础，尚未实施。
- 2026-09-19：完成阶段二十八（工程债务·存储层追加写，见
  `next_doc/world_simulator_universal_simulator_gap_analysis_and_
  roadmap_v2_plan.md` 4.18 节；范围较原方案草案收窄）交付：
  1. `store.py::SimStore.append_state()` 从"整体重写"改成真正的
     追加写——不再 `load_history()` 读回全部历史再 `atomic_write_
     jsonl()` 整体落盘，改为直接 `path.open("a", ...)` 追加一行
     JSON；`state_current.json` 的写入方式不变（本来就只有一条
     记录，没有"追加"的概念）。理由：`state_history.jsonl` 每条
     记录只在生成时写一次、之后永不修改，天然适合纯追加，重写
     代价从 O(历史长度) 降到 O(1)。`engine.py` 里两处需要"修改
     历史里最后一条"的场景（自动挡代选回填 `chosen_option`/
     `apply_tree_updates`）不受影响——它们本来就是"读回整份历史→
     内存改最后一条→整体重写"，这属于"更新"而不是"追加"，逻辑
     不变，只更新了一处过时的代码注释。
  2. `world_simulator/knowledge_base.py::_save_all()` **评估后
     刻意保留整体重写**，不改成追加写——`record_causal_links()`/
     `record_contradiction()` 会原地更新*已有*知识条目（相似度
     匹配到重复因果关系时合并计数），这种"可原地更新既有记录"的
     语义和 `state_history.jsonl` 的"只增不改"完全不同，勉强套用
     纯追加写需要额外的 compaction 才能让"更新"生效，得不偿失；
     已在 docstring 里记录这个评估结论，避免未来重复纠结这个
     取舍。
  3. **`engine.py` 拆分成多个模块**（4.18 节方案原始范围的另一
     半）**本阶段未实施**——52K 单体文件拆分涉及内部大量交叉引用，
     一次性拆分的回归风险明显高于存储层这个改动，且没有清晰的
     "拆到什么粒度算完成"的验收标准；决定先把"存储层追加写"这个
     范围明确、验收标准清晰（"多次 `append_state()` 不再触发
     `load_history()`"）的子任务做完，`engine.py` 拆分留给后续
     单独评估/排期，不在本阶段范围内强行一起做。
  4. 新增 `tests/test_state_and_store.py::test_append_state_is_
     true_append_write_not_full_rewrite`：验证多次追加后文件行数
     与调用次数一致，并通过把 `store.load_history` 替换成"调用即
     报错"的替身，断言 `append_state()` 确实不再依赖读回整份历史
     这条路径。累计 181 个测试全部通过（`cd external_projects/
     world_simulator && PYTHONPATH=../../src:. python3 -m pytest
     tests/ -q`）。
  **已知限制**：单行追加不是跨平台意义上的原子操作（极端情况下
  进程在写入中途崩溃可能留下不完整的最后一行）——`load_history()`
  在这种情况下会在 `json.loads()` 抛出异常，本阶段未新增对"最后
  一行不完整"的容错解析（`knowledge_base.py::_load_all()` 已有
  "单行损坏跳过"的先例，如果这个问题在真实使用中出现，可以照搬
  同样的容错逻辑）；"暂不支持并发推进同一实例"这条已知限制不受
  本阶段影响，仍然成立，加锁是另一个独立的改动。
- 2026-09-19：完成阶段二十九（归因/贡献拆解报告 + 开放世界闭环
  收尾，见 `next_doc/world_simulator_universal_simulator_gap_
  analysis_and_roadmap_v2_plan.md` 4.21/4.26 节，路线图"第二批"
  两项）交付：
  1. **4.21 归因/贡献拆解报告**：新增 `world_simulator/
     attribution.py::summarize_contributions(history, target_
     field)` 纯函数——从历史 `causal_links` 里筛出 `affected_
     fields` 命中目标字段的条目（支持嵌套路径按末段匹配，比如
     `resources.cash` 命中 `cash`），按来源线（优先
     `source_line_id`，其次 `line_id`，都没有则 `(未归属)`）分组
     统计出现次数、`relation_type` 分布、最多 3 条具体因果关系
     示例；相关程度按出现占比分"高/中/低相关"三档（延续项目
     "不做伪精确"的一贯风格，不是统计显著性检验）；如果目标字段
     曾被任意一步的 `uncertain_fields` 标注过，附带"这个结果本身
     包含较大不确定性，归因仅供参考"的提示。不发起任何新的 LLM
     调用，不做敏感性量化打分/反事实验证（那是 4.22 节的范畴）。
     `app.py` 新增 `_render_attribution_section()`：在实例详情页
     "关键变量"下方新增"🧭 这个结果是怎么来的"折叠区，从
     `manifest.settings.objectives` 里声明过 `field` 的目标里选一个
     展示归因清单；没有声明可排序字段时展示引导文案而不是强猜。
     新增 `tests/test_attribution.py`（12 个用例：空历史/空目标
     字段/无匹配因果链/按来源线分组/缺失字段兜底为未归属/嵌套
     路径匹配/忽略非字典条目/示例条数上限/按次数降序排序/
     `uncertain_fields` 命中附带提示/未命中不附带提示/`to_dict()`
     结构）。
  2. **4.26 开放世界闭环收尾**：`engine.py::apply_structural_
     change()` 采纳 `kind in ("new_mechanism", "regime_shift")`
     的结构性变化时，顺带用 `causal_tree.build_default_future_
     tree()` 生成一条默认因果线草稿，追加进新增的
     `settings.suggested_causal_lines`（**不**自动登记进
     `settings.causal_lines`——是否要为这个新结构开一条独立因果线
     仍由用户决定）；`kind == "new_entity"` 不生成建议，新实体更
     多体现在 `vars.entities` 里，不强制每个新实体都对应一条因果
     线。新增 `engine.py::accept_suggested_causal_line()`（把建议
     正式追加进 `causal_lines`，从建议列表移除）/
     `reject_suggested_causal_line()`（直接从建议列表清除，幂等，
     重复调用不报错）。`app.py::_render_causal_lines_overview()`
     新增 `_render_suggested_causal_lines()`：在"因果线总览"页面
     渲染"💡 因果线建议"折叠区（有待处理建议时才出现，默认展开），
     每条展示来源 step/描述/建议命名，提供"接受"/"忽略"两个按钮。
     `tests/test_spec_and_engine.py` 新增 5 个用例（`new_mechanism`
     生成建议且不改动既有 `causal_lines`/`new_entity` 不生成建议/
     接受建议后正确迁移进 `causal_lines` 并清空建议/接受未知 id
     报错/拒绝建议后不影响 `causal_lines` 且拒绝操作本身幂等）。
     累计 198 个测试全部通过（`cd external_projects/world_
     simulator && PYTHONPATH=../../src:. python3 -m pytest
     tests/ -q`，另需 `pip install fastapi --break-system-
     packages` 满足 `mini_agent.workflow` 的间接依赖，这是既有
     环境依赖问题，不是本阶段引入的）。
  **已知限制**：归因清单完全基于已经落盘的 `causal_links` 做
  统计聚合，`affected_fields` 标注不全或字段名写法不一致（比如
  同一个字段一次写 `cash` 一次写 `资金`）会导致归因清单遗漏部分
  因果链，本阶段不做字段名归一化/别名映射；因果线建议的默认命名
  直接截取 `structural_change.description` 前 24 个字符，可能不够
  精炼，用户可以在因果线总览页面用既有的"手动调整因果线声明"入口
  重命名；4.20（多尺度真正并行）/4.22（反事实矩阵向导）/4.23
  （顺势逆势判断）仍在"待观察真实使用反馈"状态，未实施。
- 2026-09-19：完成阶段三十（工程债务·`engine.py` 拆分，见
  `next_doc/world_simulator_universal_simulator_gap_analysis_and_
  roadmap_v2_plan.md` 4.18 节剩余部分——阶段二十八只落地了存储层
  追加写，`engine.py` 拆分当时评估后决定单独排期，本阶段完成）
  交付：
  1. 原来 1143 行的单体 `world_simulator/engine.py` 按职责拆成
     `world_simulator/engine/` 包，下设 10 个子模块：
     `errors.py`（`SimEngineError`/`SimAlreadyEndedError`/
     `SimPausedError`）、`ids.py`（`_new_sim_id`/
     `_skill_name_for_template`）、`knowledge.py`（知识库安全
     包装）、`resource_guard.py`（资源字段下限校验 + 转移关系
     一致性检查）、`background_entities.py`（背景角色线性外推）、
     `materialize.py`（`materialize_simulation`/
     `create_simulation`）、`structural_change.py`（结构性变化
     解析/采纳，含阶段二十九新增的因果线建议
     `accept_suggested_causal_line`/`reject_suggested_causal_
     line`）、`causal_lines.py`（因果线自动登记 + 未来树合并）、
     `advance.py`（核心推进循环 `advance()`）、`management.py`
     （实例状态/自动挡/设置/重命名/删除/查询）。
  2. 每个子模块内部函数的实现**逐行保持不变**，只是换了文件
     位置、把跨职责的调用改成显式 `import`；`world_simulator/
     engine/__init__.py` 把所有对外公开的函数/异常重新导出，
     `from world_simulator.engine import advance`（`app.py`/
     `autopilot.py`/entrypoints 的既有写法）和 `import
     world_simulator.engine as engine_mod`（测试文件的既有写法）
     两种导入方式都不需要任何改动继续可用。
  3. 不改变任何持久化格式，也没有修改任何测试文件——回归测试
     （拆分前 198 个用例）作为唯一验收标准，全部通过（`cd
     external_projects/world_simulator && PYTHONPATH=../../src:.
     python3 -m pytest tests/ -q`）。
  **已知限制**：`state_model.py`/多个模块的 docstring 里仍然沿用
  `engine.py::函数名` 这种旧的引用写法（比如"见 `engine.py::
  advance()`"）——这是纯文档层面的表述惯例，指代的是"engine 模块
  里的这个函数"，拆分后这些引用依然可以正确定位到
  `world_simulator/engine/advance.py` 等具体文件，不逐一批量改写
  （改动量大、且不影响任何行为，风险收益比低于价值）；如果后续
  哪个子模块被进一步拆分/合并，行内 docstring 到时候顺手更新即可。
  `state_model.py`本身的分组/精简（4.18 节原方案提到的"是否需要
  同步精简"）本阶段**未纳入范围**——`SimState`/`SimManifest.
  settings` 的字段数量问题和 `engine.py` 文件大小是两个独立的
  复杂度维度，前者的精简涉及数据结构语义、风险等级不同于纯代码
  重组，留待后续单独评估。
- 2026-09-19：完成阶段三十一（4.20/4.22/4.23/4.24/4.25 五个"待观察
  反馈"/"暂不建议"方向 + `state_model.py` 字段分组索引，见
  `next_doc/world_simulator_universal_simulator_gap_analysis_and_
  roadmap_v2_plan.md` 第 5 节路线图第三批 + 暂不建议部分——按用户
  明确要求，不再等待真实使用反馈，全部落地为**范围克制的最小可行
  版本**，完整架构仍按原方案标注的触发条件保留）交付：
  1. **4.20 多尺度真正并行（最小版：节奏提示，非独立引擎）**：
     `causal_lines` 每条线新增可选 `advance_every_n_steps`（默认
     1），`spec_generator._lines_due_this_step_hint()` 据此算出
     "这一步哪些线预期有动静/维持不变"的提示，拼进 `advance` 阶段
     的 `causal_lines_hint`——只是提示，`engine.py` 不做任何强制
     过滤；`resolve_hints()`/`_resolve_causal_lines_hint()` 新增
     `current_step` 参数（`engine.advance()` 传 `current.step + 1`，
     即"即将产生的下一个状态"的 step），`app.py` 因果线总览对
     节奏慢的线在标题上标注"约每 N 步一动"。
  2. **4.22 反事实矩阵向导**：`hypothesis.build_counterfactual_
     matrix()`（+ `CounterfactualVariant`），复用
     `run_hypothesis_worlds()` 已验证的
     `autopilot.run_comparison_experiment()` 机制，把"单个字段的
     多个假设方向"扩展成"多个字段各自的多个候选取值"，**只做
     单变量控制**（每个世界只变一个字段，其它声明字段不额外设定
     方向，不做多变量交叉的完整析因设计）；`app.py`「对比实验」
     页面新增"反事实矩阵向导"折叠区，按变化的字段分组展示结果。
  3. **4.23 顺势/逆势/改变趋势判断**：新增
     `world_simulator/trend.py::classify_trend()`，基于 4.21 归因
     结果的启发式分类——`causal_links` 里出现"自己所在线 → 反向
     影响外部线"的记录判定为"正在改变趋势"（优先判断）；否则若
     贡献高度集中在外部线，给出"顺势/逆势"判断（**不拆分方向**，
     因为 `causal_links` 不记录数值变化方向，系统无法自动区分
     顺势与逆势，交给用户结合因果链明细自行判断）；两种信号都
     没有则"无法判断"。`app.py` 归因折叠区下方新增"自己是哪条
     因果线"选择器 + 判断结果展示。
  4. **4.24 Influence Field/Relationship（最小起步版，明确非完整
     架构）**：新增 `world_simulator/relationship.py`
     （`normalize_relationships()`/`summarize_by_subject()`），
     `settings.relationships` 承载 `{from, to, kind, strength,
     note}` 的结构化关系列表——**没有**影响半径/传播路径/时间
     延迟/自动推进逻辑，`engine.advance()` 完全不读取也不消费；
     `app.py`"模拟设置"新增 JSON 编辑入口。完整的 Influence Field
     架构仍按原方案"多主体数量明显增多且 entities/shared_vars
     不足以表达"的触发条件搁置。
  5. **4.25 世界独立演化 / Observer View（最小起步版，非独立推进
     循环）**：新增 `settings.observer_mode`（默认 `False`），为
     `True` 且 `pilot_mode == "autopilot"` 时，
     `autopilot._build_decision_context()` 追加一段提示，要求这
     一步优先让背景/宏观因果线自然演化、尽量不产生需要立刻打断的
     "人生重大决策"新分支——**仍然是** `batch_advance_daily`/
     自动挡代理发起的同步推进，没有独立的后台推进循环，也不是
     真正的双视角架构，只是提示（不强制）。`app.py`"模拟设置"
     新增对应勾选项。
  6. **`state_model.py` 字段分组索引**（4.18 节末尾遗留的评估项）：
     在 `SimManifest.settings` docstring 末尾追加一段按主题分组
     的字段索引（节奏/候选、资源与守恒、目标与归因、多主体、
     因果线、校准与结构演化、世界独立演化），纯文档层面的可读性
     改进，不拆分成子对象、不改变任何字段语义或存取路径。
  **验收**：新增 21 个测试用例（`tests/test_trend.py`、
  `tests/test_relationship.py`，以及 `test_spec_and_engine.py`/
  `test_hypothesis.py`/`test_autopilot.py` 里针对上述五点的新增
  用例），加上原有 198 个用例，全部通过（219 passed）。
  **已知限制**：4.23 的"顺势/逆势"判断需要用户手动指定"哪条线
  代表自己"，系统无法自动识别；判断本身是启发式分类，不追求
  精确，这在原方案里就已经标注为"十个方向里价值相对不确定的
  一个"。4.24/4.25 的最小版本刻意没有实现任何传播/独立推进逻辑，
  如果后续真的出现原方案标注的触发条件（多主体数量明显增多、
  用户明确要求世界在无操作时也能演化），仍需要在此基础上做更
  大范围的架构改动，不能只靠扩展现有 hint 字段解决。
- 2026-09-19：完成阶段三十二（4.1/4.2/4.4/4.6 四个方向，见
  `next_doc/world_simulator_realism_transparency_and_retrospective_
  roadmap_v3_plan.md` 第 5 节路线图第一、二批——4.3/4.5 按原方案
  建议暂缓，单独出细化子方案后再实施；4.7/4.8 按原方案暂不建议，
  未纳入本轮）交付：
  1. **4.1 Action Space 结构化**：`state_model.ChoiceOption` 新增
     可选字段 `risk_level`（`low`/`medium`/`high`，不认识的值归一化
     为 `medium`）、`reversibility`（`reversible`/`hard_to_reverse`/
     `irreversible`）、`affected_lines`（引用的因果线 id 列表）、
     `key_uncertainty`（一句话最大不确定性）；`generate_scenario.
     yaml`/`advance_step.yaml` 的 prompt 增加对应说明，不做代码层面
     强制校验（同 `causal_links` 等既有结构化字段的一贯做法）；
     `app.py` 选项卡片渲染风险/可逆性徽章、涉及因果线、最大不确定性
     （未声明的字段不展示，不用"未知"占位）。
  2. **4.2 Fact/Assumption/Inference/Unknown 分层标注**：
     `state_model.SimState.field_provenance`（初始状态才有意义，
     `advance` 产生的后续状态不强制维护）+
     `spec_generator.ScenarioDraft.field_provenance`，
     `generate_scenario.yaml` prompt 要求 LLM 对初始 `vars` 的每个
     顶层字段标注来源；`engine/materialize.py` 的
     `materialize_simulation()`/`create_simulation()` 透传这个字段；
     `app.py` 创建向导 + 详情页初始状态展示分别渲染"你说的/系统假设/
     系统推断/未知，先占位"四色徽章，不新增修正入口（复用已有的
     "编辑初始 vars"能力）。
  3. **4.4 预测错误分类 + 模型/Skill 版本号**：`reality_check.
     RealityCheck` 新增 `error_category`（`data_error`/
     `causal_error`/`agent_behavior_error`/`random_event`/
     `unknown_variable`/`none` 六选一，不合法值静默归空）、
     `model_version`（记录 `settings.model_version` 的快照）；新增
     `stats_by_error_category()` 按分类/版本做最小汇总统计；
     `app.py` 回填表单新增错误分类下拉（仅 `verdict == diverged` 时
     生效）、"⚙️ 模拟设置"新增可编辑的 `model_version` 文本框、详情页
     新增"📊 预测准确性统计"折叠区（仅在已有回填记录时展示）。
  4. **4.6 模拟复盘 / 经验教训总结（用户本次明确要求）**：新增
     `world_simulator/retrospective.py`
     （`generate_retrospective()`/`RetrospectiveReport`/
     `RetrospectiveRecord`）+ `workflows/retrospective.yaml`
     （`type: agent`，不挂载具体模板 skill——复盘是通用能力，不依赖
     场景私有规则）。素材收集只读取已存在的历史记录（`state_
     history`/`causal_graph`/按 `objectives` 声明字段算出的归因
     摘要/顺势逆势判断/`reality_checks`/`settings.
     counterfactual_summaries`），单项读取失败旁路降级为空，不阻断
     整份复盘；prompt 明确要求"只总结已发生内容，不做新预测"、
     每条 `what_to_reflect_on`/`lessons` 必须附具体依据；`caveats`
     缺失时代码层兜底补一条固定的免责声明。落盘
     `data/<sim_id>/retrospectives.jsonl`，追加写入、不覆盖历史
     版本；`app.py` 详情页新增"📖 模拟复盘"折叠区（手动触发按钮 +
     历史复盘记录列表，`ended` 状态额外提示"是个复盘的好时机"）。
  **验收**：新增 12 个测试用例（`tests/test_retrospective.py`
  4 个 + `tests/test_reality_check.py` 新增 3 个），加上原有 219
  个用例（其中 1 个因 `ChoiceOption` 序列化多出新字段而更新了断言，
  行为本身未变），全部通过（226 passed）。已在本地补装
  `mini_agent`（`pip install -e . --no-deps`）+ `fastapi` 依赖后
  跑过真实 `pytest`，不是仅语法检查。
  **已知限制**：4.3（单主体最小 State/Belief 分离）与 4.5（Agent
  Preview + 情境化条件策略）本轮**未实施**——原方案第 5 节路线图
  就建议这两条"先小范围验证或单独出细化子方案"，不与 4.1/4.2/4.6
  一起批量上马，本轮按此建议搁置，后续需要单独排期。`retrospective.
  py` 的"反事实矩阵"素材读取依赖调用方自行在
  `settings.counterfactual_summaries` 写入摘要——当前代码库里还没有
  任何模块会自动写这个字段（`hypothesis.build_counterfactual_
  matrix()` 的结果目前只落在对比实验页面展示，不回写
  `settings`），这部分素材在现状下恒为空数组，复盘报告暂时无法
  引用"如果当初选了别的会怎样"的历史对比结果，需要后续单独评估是否
  要把反事实矩阵结果回写进 `settings` 供复盘引用。
- 2026-09-19（同日追加）：针对阶段三十二暂缓的 4.3/4.5 两个方向，
  按用户要求"在文档补充出子方案"，新增两份细化子方案文档（纯文档，
  未涉及代码改动）：
  1. `next_doc/world_simulator_belief_state_separation_plan.md`
     （4.3 单主体 State/Belief 分离）：给出 `settings.belief_
     fields`/`SimState.beliefs` 的具体数据结构、`advance_step`
     prompt 片段、`app.py` 真实值/认知值对比展示方案，并拆成
     "第一批（仅 life_sim 模板验证）→ 第二批（推广）"两批，明确
     第一批需要人工跑几次真实模拟验证"LLM 能否稳定输出有意义的
     认知偏差"，验证不通过则建议直接放弃而非继续调 prompt。
  2. `next_doc/world_simulator_agent_preview_and_adaptive_policy_
     plan.md`（4.5 Agent Preview + 情境化条件策略 + 用户反馈反哺
     画像）：拆成三个独立批次——情境化条件策略（依赖 4.1 的
     `reversibility`/`risk_level`）、Agent Preview（内置测试情境、
     独立于正式模拟数据的 LLM 调用）、用户反馈反哺画像（复用
     `review_mode`，简单关键词统计生成提示，不做 LLM 语义聚类）；
     明确标注实施前需要先核实 `autopilot.py` 画像当前的存储作用域
     （单实例 vs 全局），本方案编写时未去核实这一点。
  两份子方案都重申了"系统只展示/建议、画像调整必须由用户手动操作"
  的透明度红线，以及父方案已有的范围克制项。父文档
  `world_simulator_realism_transparency_and_retrospective_roadmap_
  v3_plan.md` 的 4.3/4.5 节状态标注已同步更新，指向这两份新文档。
  **均未实施**，按各自子方案里的分批计划，需要单独排期，且 4.3
  第一批要求真实跑模拟做人工验证，不能仅凭代码实现就判定完成。
- 2026-09-19（同日追加二）：按子方案分批实施 4.3/4.5 各自的"第一批"
  （其余批次仍未实施，见各子方案文档的分批计划）：
  1. **4.3 第一批（`life_sim` 验证批，见 `next_doc/world_simulator_
     belief_state_separation_plan.md` 第 7 节）**：`state_model.
     SimState.beliefs`（稀疏字段，只在认知发生变化的那一步才有
     记录，展示层沿用最近一次记录）；`spec_generator.
     _resolve_belief_fields_hint()` + `resolve_hints()` 新增
     `belief_fields_hint`；`advance_step.yaml` prompt 按
     `belief_fields_hint` 是否非空条件性拼接说明；
     `engine/advance.py` 解析 `beliefs` 输出；`app.py` 新增
     `_latest_beliefs()`/`_belief_comparison_html()`，在详情页
     "关键变量"折叠区渲染"真实 X · 你以为 Y"对比（仅在两者不同时
     展示），"⚙️ 模拟设置"新增 `belief_fields` 编辑入口。**尚未
     进行子方案要求的人工验证**（真实跑 3~5 次 `life_sim` 模拟，
     观察 LLM 是否会为未变化字段乱填、认知偏差是否有意义）——
     代码层面已具备验证条件，但本轮未实际执行验证，不代表 4.3
     已经"完成"，只是完成了子方案里的"第一批"代码交付部分。
  2. **4.5 第一批（情境化条件策略，见 `next_doc/world_simulator_
     agent_preview_and_adaptive_policy_plan.md` 第 7 节）**：
     `autopilot._normalize_conditional_policies()`（校验 `if`
     只能是 4.1 已有的 `risk_level`/`reversibility` 六个枚举值
     之一，`then` 为空或 `if` 不认识的项静默丢弃）+
     `_build_decision_context()` 拼接对应自然语言提示；`app.py`
     "配置自动挡"折叠区新增可增删的条件/倾向行编辑器（下拉框限制
     `if` 取值，从源头避免出现无法识别的条件），保存进
     `autopilot.conditional_policies`。**顺带确认了子方案里标注
     的未知项**：画像（`manifest.autopilot`）的存储作用域是
     "每条分支各自独立"（`engine/management.py::set_pilot_
     config()` 文档已写明），不是全局配置，第 3 批"用户反馈反哺
     画像"如果后续实施，`PolicyFeedbackNote` 应该挂在
     `sim_id`+`branch` 下，不是全局的。
  **验收**：新增 8 个测试用例（`test_spec_and_engine.py` 4 个
  belief 相关 + `test_autopilot.py` 4 个 conditional_policies
  相关），加上原有 226 个用例（其中 2 个因新增 `belief_fields_
  hint` 输入项而更新了断言，行为本身未变），全部通过（234
  passed）。
  **已知限制**：4.3 第二批（`ScenarioDraft.belief_fields`/初始
  `beliefs`、创建向导声明入口、推广到其它模板）与 4.5 第二、三批
  （Agent Preview、用户反馈反哺画像）**均未实施**，按各自子方案
  的分批计划，需要后续单独排期；4.3 第一批的人工验证也需要在
  真实使用中另行进行。
- 2026-09-19（同日追加三）：完成 4.5 第二批（Agent Preview，见
  `next_doc/world_simulator_agent_preview_and_adaptive_policy_plan.
  md` 第 4、7 节）：
  新增 `world_simulator/agent_preview_scenarios.py`（4 个固定内置
  测试情境：突然失业、创业公司股权邀约、持续但不严重的身体不适、
  大额消费决策，每个情境的候选选项都带 4.1 的 `risk_level`/
  `reversibility`，覆盖"可逆/不可逆"" 高/低风险"的组合）+
  `world_simulator/agent_preview.py`（`run_agent_preview()`，复用
  `autopilot._build_decision_context()` 渲染画像文本，用一个不落盘
  的临时 `SimManifest` 传参，不需要画像挂在真实模拟实例上）+
  `workflows/agent_preview.yaml`（`type: agent`，同
  `retrospective.yaml` 的模式，不挂载具体模板 skill；prompt 要求
  LLM 如实说明"是否明确依据某条已声明的原则"，允许说"没有明确
  依据"）。每个测试情境独立 try/except，单个情境失败不影响其它
  情境的结果（`PreviewResult.error`）。`app.py`"配置自动挡"折叠区
  新增"运行 Agent Preview"按钮 + 结果展示，运行前用当前表单里
  （尚未点保存的）画像内容跑，运行不写入任何 `data/<sim_id>/`
  下的正式数据，按钮旁提示会产生的调用次数。
  **验收**：新增 4 个测试用例（`tests/test_agent_preview.py`），
  加上原有 234 个用例，全部通过（238 passed）。
  **已知限制**：本轮**未做**子方案 4.7 节要求的人工验证（真实跑
  几次 Preview，检查测试情境的选项设计是否真的能体现风险/可逆性
  维度上的选择差异，以及情境化策略——4.5 第一批——是否真的影响了
  选择）；4.5 第三批（用户反馈反哺画像）仍未实施。
