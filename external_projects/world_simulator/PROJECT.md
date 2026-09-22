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
- 2026-09-19（同日追加四）：完成 4.5 第三批（用户反馈反哺画像，见
  `next_doc/world_simulator_agent_preview_and_adaptive_policy_plan.
  md` 第 5、7 节）——至此 4.5 三个批次全部完成代码交付；4.3（单主体
  State/Belief 分离）第二批因仍卡在"第一批需要人工验证"这个前置
  条件（见该子方案第 7 节），本轮未推进，见下方"已知限制"：
  新增 `world_simulator/policy_feedback.py`
  （`PolicyFeedbackNote`/`record_feedback()`/`acknowledge()`/
  `find_for_step()`/`summarize_feedback()`），落盘
  `data/<sim_id>/policy_feedback.jsonl`（同 `reality_checks.jsonl`
  平级模式，追加写入）。**实施时发现一处与子方案假设不符**：子方案
  5.1 节假设"自动挡运行已有 review_mode（人工复核）机制，用户可以
  在复核时标记'不符合预期'并写理由"，但实际代码里 `review_mode`
  只是"重大决策时暂停连续推进"，并没有现成的"标记不符合预期"入口
  ——因此在 `app.py` 时间线的自动挡步骤（`state.chosen_by ==
  "autopilot"`）上新增了一个独立的"🚩 反馈这次代理的选择"折叠区
  （只在这一步写反馈理由，不做"符合预期"的正向记录，同子方案 5.1
  节只关心"不符合预期"方向的取舍一致）；`autopilot.py` 本身未改动。
  画像编辑页（"配置自动挡"折叠区）新增"📋 最近的反馈"折叠区，展示
  当前分支未确认的反馈列表 + 命中关键词分组（"太保守/太谨慎/太
  犹豫" vs "太冒险/太激进/不计后果"，各自出现 ≥3 次触发一条归纳
  提示，纯计数统计，不做语义分析）达到阈值时的归纳提示，每条反馈
  可点"已阅"置 `acknowledged=True`（不删除记录）。全程不改动
  `risk_preference`/`conditional_policies` 的任何取值——调整与否
  完全由用户自己在同一页手动操作，符合"系统只展示/建议，画像调整
  必须由用户手动操作"的透明度红线。
  **验收**：新增 10 个测试用例（`tests/test_policy_feedback.py`），
  加上原有 238 个用例，全部通过（248 passed）。已用本地 `pytest`
  真实跑过（补装 `pytest`/`streamlit` 依赖）。
  **已知限制**：4.3 第二批（`ScenarioDraft.belief_fields`/初始
  `beliefs`、创建向导声明入口、推广到其它模板）仍未实施——按子
  方案第 7 节，需要先完成"第一批需要人工跑 3~5 次 `life_sim` 真实
  模拟验证 LLM 能否稳定输出有意义的认知偏差"这一步，这一步本身
  无法仅靠代码改动替代，本轮未执行，因此第二批按计划继续搁置，
  留待后续有人工验证条件时再排期。
- 2026-09-19（同日追加五）：**应用户明确要求"不等验证、直接实现"**，
  跳过 `next_doc/world_simulator_belief_state_separation_plan.md`
  第 7 节要求的"第一批人工验证通过"这道前置条件，直接实施 4.3
  第二批（推广批）：
  1. `spec_generator.ScenarioDraft` 新增可选字段 `belief_fields`
     （skill 建议的认知偏差字段列表）、`beliefs`（初始认知偏差
     快照，格式同 `SimState.beliefs`）；`generate_scenario.yaml`
     新增一段 prompt，说明可选输出这两个字段——**不是**
     `advance_step` 那种"仅当已声明 belief_fields 才提示"的条件式
     写法，而是同 `resource_fields`/`uncertain_fields` 一样的"skill
     主动判断、无条件可选建议"写法（因为创建阶段用户还没有机会
     预先声明 `belief_fields`）。
  2. `engine.materialize.materialize_simulation()` 新增 `beliefs`
     参数，落到 `state0.beliefs`；`create_simulation()`（CLI 一步
     到位路径）透传 `draft.beliefs`，同 `field_provenance`/
     `uncertain_fields` 的既有透传方式。
  3. `app.py` 创建向导新增"认知偏差声明字段"文本框（同
     `resource_fields` 的既有 UI 模式：草稿建议值展示 + 可编辑，
     结果存进 `settings.belief_fields`），并展示 skill 给出的初始
     认知偏差摘要（纯展示，不提供额外编辑入口，同 `field_
     provenance` 的既有取舍）。
  4. **未做**"根据第一批验证结果调整不同模板 prompt 措辞"这部分
     ——因为第一批验证从未执行，没有任何结果可供参考；`belief_
     fields` 相关 prompt 对所有模板一视同仁，未针对任何具体模板
     调整或验证过。
  **验收**：新增 4 个单元测试（`ScenarioDraft.from_dict` 解析
  `belief_fields`/`beliefs`、`materialize_simulation`/
  `create_simulation` 落盘与透传），加上原有 248 个用例，全部通过
  （252 passed）。已用本地 `pytest` 真实跑过。
  **已知限制（如实记录，非常重要）**：本次实施**完全跳过**了子
  方案第 7 节要求的人工验证环节——第一批"人工跑 3~5 次 `life_sim`
  真实模拟观察 LLM 是否会为未变化字段乱填 `beliefs`、认知偏差是否
  有意义"这一步从未执行；第二批"推广批"是在没有任何验证结果的
  情况下直接铺开到全部模板的，风险敞口比原计划的"先在 `life_sim`
  小范围验证"更大。目前只能保证：数据结构定义完整、`ScenarioDraft`
  解析正确、落盘路径通畅、单元测试全绿——**完全不能保证** LLM 真的
  会稳定产出有意义的认知偏差，也不能排除"认知值总是约等于真实值"
  或"为没有认知变化的字段瞎编更新"这两种子方案原文点名的失败模式
  正在发生。如果后续真实使用中出现这些迹象，应按子方案第 7 节
  建议的方向直接放弃这个功能，而不是继续投入调 prompt。
- 2026-09-20：**阶段三十三第一批**——按用户明确要求"全部列为要
  改进内容，不做取舍"重新做的第四轮差距分析，见 `next_doc/
  world_simulator_potential_causal_space_and_decision_engine_
  plan.md`（13 条差距、7 个批次）。本轮完成该方案第 5 节的
  **第一批**：4.1（选项数量作为推演结果，而非 UI 目标值）+ 4.5
  （选项必须是现实行动语义，不能是内部指标调节）+ 4.8（对应的
  prompt/SKILL.md 文件同步改造）：
  1. `spec_generator.py::resolve_hints()`：`option_count_hint`
     措辞从"N 个左右"改为"最多不超过 N 个（软上限，用于防止候选
     列表刷屏，不是目标数量……）"。
  2. `workflows/advance_step.yaml`：新增完整的"候选分支选项的
     生成流程"说明（先判断是否有分岔点→没有给空数组/只有一个
     给一个/多个有区分度都列出→超软上限优先合并精简）、现实行动
     语义硬性约束（正反例）、可干预性下沉指导（宏观事件不直接
     包装成选项，沿因果链下沉到主体可干预位置）、`continue_`
     前缀的"维持现状"选项约定（仅当不作为是真实后果分支时才
     显式列出，不是机械项）。
  3. `workflows/generate_scenario.yaml`：同步软上限措辞 + 现实
     行动语义约束（初始状态候选方向同样适用）。
  4. 三个模板 `SKILL.md`（life-sim-template/negotiation-template/
     group-evolution-template）：删除"候选选项数量按用户设置来，
     不要自己拍脑袋"（及谈判模板自己的简化版本）这条方向相反的
     旧指令，替换为软上限判断流程说明；各自补充贴合自己场景的
     现实行动语义正反例（life-sim：职业转型/财务决策；
     negotiation：报价让步/延长交付周期换让步；group-evolution：
     集体决议/暂停扩张 vs 抽象指标调节），不是三个模板抄同一组
     "职业线"例子。
  5. `app.py` 两处 `st.number_input`（创建向导 + 运行中设置面板）
     文案改为"候选方向数量上限（软上限，实际数量由情境决定）"。
  **验收**：更新 `tests/test_spec_and_engine.py` 里对
  `option_count_hint` 的断言（原先按"4 个左右"字面匹配，改为按
  新措辞匹配）；新增 `tests/test_decision_engine_prompts.py`（9
  个用例）：断言旧措辞已在三个模板里被删除、`option_count_hint`
  确实是软上限语义、两个 workflow 的软上限/现实行动语义/可干预性
  下沉/`continue_` 约定文案已写入、三个模板各自有独立的正反例
  （不是复制粘贴同一组）。加上原有 252 个用例，全部通过（**261
  passed**）。`tests/test_workflow_prompt_placeholders.py`（防止
  prompt 里的示例大括号被误判为占位符导致运行时 `KeyError` 的
  回归测试）同步跑过，未受影响。
  **已知限制**：这一批只解决了"数量语义"和"选项内容语义"这两个
  最直接的痛点，全部是 prompt/措辞层面的改动，**没有任何新增数据
  结构**——`decision_reason`/`action_reason`/`urgency`/
  `time_window` 等字段级扩展是下一批（第二批，4.2+4.3+4.6）的
  内容，本批尚未实施；辅助校验（4.4/4.5 方案里提到的"启发式重合
  检测"）也留给第三批，本批只做了纯 prompt 约束，没有加任何
  `engine.py` 侧的校验/`warnings` 逻辑。方案第 6 节提示的"prompt
  膨胀"和"新约束是否被 LLM 真正遵循"两项风险，仍需要后续人工跑
  真实/接近真实场景核对，本批未执行人工验证，只保证"改动确实落地
  到文件里"这一层。
- 2026-09-20（同日追加）：**阶段三十三第二批**——完成方案第 5 节
  第二批：4.2（Decision Reason/Action Reason 拆分）+ 4.3（紧急度
  独立建模：四档 + 时间窗口 + 与暂停机制联动）+ 4.6（显式的"维持
  现状/不作为"基准选项语义，落地到数据结构/展示层）：
  1. `state_model.py`：`ChoiceOption` 新增 `action_reason`（为什么
     这个具体行动现在值得列入候选，区别于 `description` 说的"选了
     会怎样"）、`urgency`（`low`/`medium`/`high`/`critical` 四档，
     归一化规则同 `risk_level`——不认识的取值退化为 `medium`，
     `None` 表示未声明）、`time_window`（人类可读的时间窗口描述）；
     `SimState` 新增 `decision_reason`（这一批 `options` 共享的
     背景原因，回答"为什么现在需要做决定"，区别于每个选项各自的
     `action_reason`）。均为可选字段，`to_dict`/`from_dict` 完整
     支持，旧数据缺字段时按"未声明"处理，向后兼容。
  2. `engine/advance.py`：非空 `options` 数组里缺失 `action_reason`
     的项自动补一句通用占位文案（"未说明具体原因，按情境综合
     判断"），避免展示层出现空白；新状态的 `options` 中存在任意
     一项 `urgency == "critical"`，直接把 `next_state.major_
     decision` 置为 `True`——复用现有 `pause_on_major_decision`
     判定路径，不新增独立开关，`major_decision` 字段本身继续保留
     供不使用 `urgency` 的旧场景/模板正常工作。
  3. `autopilot.py`：`_CONDITION_IF_VALUES`/`_CONDITION_LABELS` 新增
     `urgency_low`/`urgency_medium`/`urgency_high`（加前缀避免与
     `risk_level` 的同名 `low`/`medium`/`high` 混淆；`critical` 不
     放入条件策略——它直接触发暂停，不需要"倾向"这种软策略）；
     `_build_decision_context()` 新增一句关于 critical/high 紧急度
     选项的通用提示（"critical 会自动暂停，你不需要处理；high 请
     优先处理，不要因犹豫不决而放着不选"），无论是否声明
     `conditional_policies` 都会输出。
  4. `workflows/advance_step.yaml`：新增 `decision_reason`/
     `action_reason`/`urgency`/`time_window` 四个可选输出字段的
     完整 prompt 说明（含判断依据、四档含义、`critical` 会触发
     暂停的警示），并明确区分"为什么现在需要做决定"（`decision_
     reason`，批量共享）与"为什么这个行动值得列入"（`action_
     reason`，逐项独立）。
  5. 三个模板 `SKILL.md`：`options`/结果字段清单里同步补充这四个
     新字段的说明（不重复完整 prompt，指向 `advance_step.yaml`）。
  6. `app.py`：新增紧急度 badge 样式（`ws-urgency-badge-low/medium/
     high/critical`，`critical` 用最醒目的红色加粗）和 `continue_`
     前缀的"维持现状"标签样式；`_option_meta_html()` 渲染紧急度
     badge、`time_window`、`action_reason`（均为"有值才展示，未声明
     不伪造"）；推进面板在候选列表上方展示 `decision_reason`（有
     值才展示），并把 `id` 以 `continue_` 开头的选项统一排到候选
     列表最后（不改变其它选项的相对顺序，用稳定排序实现）。
  **验收**：新增/更新测试——`tests/test_state_and_store.py` 新增 2
  个（`ChoiceOption` 新字段序列化/归一化，含"不认识的取值退化为
  medium"和"critical 是合法值不会被误归一化"两种情况；`SimState.
  decision_reason` 往返 + 旧数据兼容）；`tests/test_autopilot.py`
  新增 3 个（`urgency == "critical"` 选项在没有显式 `major_
  decision` 字段时也能触发暂停；`urgency_*` 条件策略被正确识别、
  `urgency_critical`/裸 `critical` 被拒绝；`_build_decision_context`
  始终包含 critical/high 紧急度的处理提示）；`tests/test_spec_and_
  engine.py` 更新了一处因 `ChoiceOption` 新增字段而需要同步的
  `to_dict` 快照断言；`tests/test_decision_engine_prompts.py` 新增
  7 个（`decision_reason`/`action_reason`/`urgency`/`time_window`
  的 prompt 文案断言、数据结构支持断言、`engine/advance.py` 兜底
  逻辑的源码断言、三个模板都提到这两个新字段）。加上原有 261 个
  用例，全部通过（**273 passed**）。
  **已知限制**：这一批只做了"字段级扩展 + 最直接的行为联动"（critical
  暂停、action_reason 兜底、continue_ 展示排序），**没有做**方案
  4.4/4.13 提到的"辅助校验"（宏观事件下沉的启发式重合检测、跨线
  级联提示）——这些留给第三批；也没有做任何"引擎侧主动判断紧急度"
  的逻辑，紧急度仍然完全由 LLM 判断，engine 只做归一化和
  critical→暂停这一条纯规则触发。方案第 6 节的"新字段被 LLM 敷衍
  填写"风险（`action_reason`/`decision_reason` 可能沦为"综合判断，
  没有明确依据"这类低信息量兜底）本批未做任何代码层面的质量校验，
  只能后续人工观察实际输出。
- 2026-09-20（同日追加）：**阶段三十三第三批**——完成方案第 5 节
  第三批：4.4（可干预性下沉的辅助校验）+ 4.13（跨线级联的主动
  计算）。4.4/4.5 的**硬性 prompt 约束**（宏观事件不能包装成选项/
  选项不能是内部指标调节）已经在第一批落地，本批只补上方案里明确
  写出的"辅助的、弱信号的、不阻断流程"启发式校验，以及 4.13 把
  历史因果耦合聚合结果反过来喂回推进循环这两件事：
  1. **新模块 `world_simulator/engine/option_heuristics.py`**：
     `detect_macro_overlap_warnings()`（4.4 节，选项文本与本步
     `key_drivers` 关键词高度重合时提示"可能把宏观事件直接包装成了
     选项"，短于 4 个字的驱动因素不参与检测）、
     `detect_metric_adjustment_warnings()`（4.5 节，选项文本出现
     "提升/增加/降低 + 数字/百分比"或裸的 `+0.1` 这类模式时提示
     "疑似内部指标调节"，数字后紧跟常见量词——年/月/天/周/次/岁/
     个/元/%——的情况不算命中，避免"减少 1 个月"这类正常时间描述
     被误伤）、`compute_option_warnings()` 汇总两者。两条规则都是
     纯字符串/正则匹配，**刻意接受误报**（比如"提高利率 0.25%"这种
     合理现实行动也会被指标调节规则命中）——这是方案原文明确认可的
     取舍："只是辅助人工发现问题的信号，不是强制校验，避免误伤真正
     合理的选项"。
  2. **`state_model.py`**：`SimState` 新增 `option_warnings`（列表，
     每项 `{"option_id", "kind", "note"}`），`to_dict`/`from_dict`
     完整支持，旧数据缺字段时按空列表处理，向后兼容。
  3. **`engine/advance.py`**：解析出 `next_state`（含 `options`/
     `key_drivers`）之后，调用 `compute_option_warnings()` 填充
     `next_state.option_warnings`——纯审计信息，不修改任何选项内容、
     不影响推进流程本身。
  4. **`spec_generator.py`** 新增 `resolve_causal_graph_hint(settings,
     history)`（4.13 节）：复用阶段二十七的 `causal_graph.
     build_causal_graph()`，只保留 `source_line_id` 属于当前已声明
     因果线、`source_line != target_line`（排除同线内部关系）、历史
     出现次数 `total >= 2` 的边，取出现次数最高的最多 3 条，拼成
     "根据以往记录，`line_A` → `line_B`（关系类型，出现 N 次）……
     仅作为提示参考"这样一句话；没有声明因果线、历史为空、或没有
     满足条件的边时返回空字符串。这是聚合结果第一次从纯展示层
     （`app.py`）被反馈进推进循环本身，仍然是"用历史统计做提示"的
     轻量级主动化，不做贝叶斯网络/结构方程这类真正的因果推断。
  5. **`engine/advance.py`**：在构建 `advance_step` workflow 的
     `inputs` 时新增 `causal_graph_hint` 一项，调用
     `resolve_causal_graph_hint(manifest.settings,
     store.load_history(branch))`——单独计算，不并入
     `resolve_hints()`（后者的 `settings`-only 签名要同时服务没有
     历史的 `generate_scenario` 创建阶段，不适合再塞一个依赖历史的
     参数）。
  6. **`workflows/advance_step.yaml`**：在因果线设置提示后面新增
     `{causal_graph_hint}` 占位符及说明文字，明确"仅供参考、不代表
     这一步一定会发生"。
  7. **`app.py`**：新增 `_option_warnings_html()`（渲染
     `option_warnings`，措辞用"建议检查"而不是"已经错了"，选项
     标题按 `option_id` 反查 `options` 列表，找不到则退化展示 id
     本身）+ 对应 CSS（`ws-chapter-option-warning`，弱化的灰色斜体，
     和"审计信息、非报错"的既有视觉语言一致），接入时间线渲染和
     "游戏视图"两处已有的章节卡片拼装逻辑。
  **验收**：新增 `tests/test_option_heuristics.py`（10 个用例，
  覆盖两条规则的命中/不命中边界，以及"提高利率 0.25%"这类刻意接受
  的误报场景）；`tests/test_causal_graph.py` 新增 6 个（`resolve_
  causal_graph_hint()` 在未声明因果线/无历史/次数不足阈值/同线
  自环/正常命中/超过 3 条边截断六种情况下的输出）；`tests/
  test_decision_engine_prompts.py` 新增 5 个（`causal_graph_hint`
  占位符断言、确认它不在 `resolve_hints()` 返回值里、`engine/
  advance.py` 源码里两处新逻辑的接线断言、`option_heuristics`
  模块的函数存在性断言、`SimState.option_warnings` 往返断言）；
  `tests/test_state_and_store.py` 新增 1 个（`option_warnings`
  往返 + 旧数据兼容）。加上原有 273 个用例，全部通过
  （**295 passed**）；`tests/test_workflow_prompt_placeholders.py`
  回归测试同步跑过，未受影响。
  **已知限制**：两条辅助校验规则纯粹是字符串/正则匹配，不做语义
  理解，误报（比如"提高利率 0.25%"）和漏报（比如用更委婉的说法
  绕开"提升/增加/降低 + 数字"这个模式）都是预期内的，不打算靠加
  更多正则来"堵漏洞"——这类弱信号规则的边际收益本身就在收窄；
  `causal_graph_hint` 只统计"发起线属于当前已声明因果线"这一种
  情况，`target_line` 是 `(未归属)`（`causal_links` 没填
  `line_id`）的边不会被排除、也不会被特殊过滤，展示上仍会原样
  拼进提示句子，这属于沿用 `causal_graph.py` 既有的"未归属也是一种
  合法状态、不特殊处理"的取舍，不是本批遗漏；4.4/4.13 之外，方案
  第 5 节第四批（4.9 KeyNode 6 态生命周期 + 4.11 渐进式展开）尚未
  开始。
- 2026-09-20（同日再追加）：**阶段三十三第四批**——完成方案第 5 节
  第四批：4.9（KeyNode 6 态生命周期）+ 4.11（渐进式展开）。两节都
  是在阶段二十六已有的 `causal_tree.py`（因果线未来树）基础上扩展
  形状，不是新开一套并行结构：
  1. **`causal_tree.py` 状态生命周期从 3 态（旧代码实际是 4 态：
     `open`/`confirmed`/`diverged`/`pruned`——比方案文本描述的"3 态"
     多一个 `diverged`，属于方案写作时间早于该状态落地的既存偏差，
     本批一并纳入迁移而不是忽略）扩展为 6 态：`dormant`（潜伏）→
     `emerging`（正在形成）→ `active`（已激活）→ `resolved`（已
     解决）/`expired`（错过窗口）/`invalidated`（因世界变化而
     失效）。新增 `canonical_status()` 做旧→新的映射：`open→
     dormant`、`confirmed→resolved`、`pruned→invalidated`；
     `diverged→expired` 是本批新增的对应关系（语义最接近：两者都是
     "没有走这个分支，但不是被主动排除"），不属于方案原文，是实施
     时按语义就近补的映射，PROJECT.md 在此明确记录以便后续核对。
     `_normalize_branch()`/`set_branch_status()` 都统一经过
     `canonical_status()`，历史数据不需要批量迁移，读取时自动转换。
  2. **`causal_tree.py` 分支（KeyNode）新增 4.9 节六个可选字段**：
     `semantic_event`（一句话现实语义）、`trigger_conditions`
     （触发条件）、`prerequisites`（前置节点 id 列表）、
     `candidate_actions`（一旦激活通常对应的行动方向）、
     `time_window`（复用 4.3 节措辞）、`urgency`（low/medium/high/
     critical，复用 4.3 节四档，非法值回退 medium）。全部可选，
     缺省时给空字符串/空列表/`None`，不编造内容。
  3. **`causal_tree.py` 新增 4.11 节渐进式展开字段**：分支级
     （不是树级）的 `expansion_level`（`compressed`/`expanded`，
     默认 `compressed`）+ `sub_branches`（形状同 `branches`，递归
     用同一个 `_normalize_branch()` 规整，与父层共享 `used_ids`
     去重集合）。选择做成分支级而不是方案文本字面提到的"顶层"，
     是因为方案后半段的可操作规格明确是"把某个分支标为 expanded 并
     给出 sub_branches"，分支级更贴合这个操作粒度，也更符合"具体
     哪条线的哪个节点值得深挖"这个自然语义——PROJECT.md 同样在此
     记录这处对原文的取舍调整。
  4. **`apply_tree_updates()` 新增两个可选的 `tree_updates` 字段**：
     `status_updates`（数组，`{"branch_id","status"}`，用于声明
     `confirmed_branch`/`pruned_branches` 覆盖不到的 `emerging`/
     `active` 等状态，接受新 6 态或旧 4 态写法，统一按
     `canonical_status()` 落盘，引用不存在的分支或无法识别的状态
     值直接忽略该项，不报错、不影响其它项）、`expand_branches`
     （数组，`{"branch_id","sub_branches"}`，把已有分支标记为
     `expanded` 并可选整体替换其 `sub_branches`；只给 `branch_id`
     时只切换标记、保留原有子分支）。`confirmed_branch`/
     `pruned_branches` 两个已有字段行为不变，只是落盘的状态名从
     `confirmed`/`pruned` 改成新 6 态里的 `resolved`/`invalidated`。
  5. **`spec_generator._resolve_causal_lines_hint()`**：advance 阶段
     的分支状态提示同步改成 6 态标注（`[dormant]`/`[emerging]`/...），
     `tree_updates` 的输出格式说明补充 `status_updates`/
     `expand_branches` 两个新字段的用法说明，并给 `new_branches`
     追加可选的 `semantic_event`/`trigger_conditions`/
     `candidate_actions`/`urgency` 字段说明；create 阶段的初始
     `future_tree` 要求也补了一句"每个分支还可以选填这些 KeyNode
     字段，创建阶段留空也可以，后续推进时再补充"。
  6. **`app.py`**：因果线总览页的状态图标/文案改成 6 态
     （dormant○/emerging🌱/active◐/resolved●/expired⌛/
     invalidated✕），手动按钮从"标为已印证/已排除"改名为"标为已
     解决/已失效"，写入时调用 `set_branch_status()` 走新的
     `resolved`/`invalidated`；渲染时读取分支状态先过
     `causal_tree.canonical_status()`，历史实例里还留着旧 4 态
     原始字符串也能正确显示图标（不要求用户手动迁移数据）。
     另有两处纯文案提示同步改名。
  7. **`state_model.py`**：`SimState.line_updates` docstring 里
     `future_tree` 示例和状态取值说明同步更新为 6 态。
  **验收**：`tests/test_causal_tree.py` 从 6 个用例扩到 20 个——
  除了同步修正两个因为状态名迁移而需要更新预期值的既有用例
  （`test_apply_tree_updates_confirm_prune_and_new_branch`、
  `test_set_branch_status_manual_override`），新增 `canonical_
  status()` 的旧→新映射/透传/未知值兜底、KeyNode 六个新字段的
  默认值与声明值往返、非法 `urgency` 回退、`expansion_level`/
  `sub_branches` 的规整与递归过滤、`status_updates`（含接受旧状态名
  写法、忽略未知分支或非法状态值）、`expand_branches`（含带/不带
  `sub_branches` 两种调用方式、忽略不存在的分支 id）等 14 个用例。
  加上原有 295 个，全部通过（**308 passed**）。
  **已知限制/对原文的偏差**（已在上面各条目里就地标注，这里汇总）：
  `diverged→expired` 的映射关系、`expansion_level`/`sub_branches`
  放在分支级而不是树级，都是方案文本本身表述不够精确（3 态 vs 实际
  4 态、"顶层字段" vs 后文"给某个分支标记"自相矛盾）情况下按语义
  就近做的实施选择，不是简单照抄原文；`dormant→emerging→active` 的
  推进判断依据仍然完全交给 LLM 在 `tree_updates.status_updates` 里
  显式声明，engine 侧不做任何自动推断规则（这点和 4.4 节紧急度
  判断的取舍一致，见第二批记录）；4.9/4.11 之外，方案第 5 节
  第五批（4.10 DecisionOpportunity 一等公民对象）尚未开始。
- 2026-09-20（同日再追加）：**阶段三十三第五批**——完成方案第 5 节
  第五批：4.10（DecisionOpportunity 作为一等公民对象，"摘要级容器"
  最小可行版本）：
  1. **`state_model.py`**：`SimState` 新增 `decision_opportunity:
     Optional[Dict[str, Any]] = None`，形如 `{"trigger_line_ids",
     "trigger_node_ids", "decision_reason", "context_note"}`；
     `options` 为空时为 `None`，非空时总是一个 dict（哪怕内部字段
     都是空）。`to_dict`/`from_dict` 完整支持，非 dict 的非法值
     兜底为 `None`，不报错。
  2. **对方案原文的一处偏差（已记录）**：方案要求
     `decision_reason` 从 `SimState` 顶层"收纳"进
     `decision_opportunity`，作为唯一存储位置。实际选择保留顶层
     `decision_reason` 字段并做**双写**（两处值始终保持镜像同步），
     而不是硬迁移删除顶层字段——这是为了不用同时处理"新数据只有
     容器字段、旧数据只有顶层字段"两套形状并存的兼容分支，也不用
     改动所有已经引用 `current.decision_reason` 的既有代码路径。
     `app.py` 展示层优先读 `decision_opportunity.decision_reason`，
     读不到（旧数据没有这个容器）再退回顶层字段。
  3. **`engine/advance.py`** 新增两个私有辅助函数：
     `_collect_trigger_node_ids()`（从 `_apply_tree_updates()` 的
     审计结果里收集这一步被印证、被声明为 emerging/active、或新增的
     分支 id，按出现顺序去重）+ `_build_decision_opportunity()`
     （组装最终容器，`options` 为空时返回 `None`）。调用点在
     `next_state.tree_updates = _apply_tree_updates(...)` 之后、
     `store.append_state()` 之前——`trigger_node_ids` 依赖刚生成的
     审计数据，顺序不能颠倒。`trigger_line_ids` 直接取自这一步
     `line_updates` 的 key，不需要 skill 额外声明。
  4. **`app.py`**："为什么现在需要决定"小节改为优先读
     `decision_opportunity.decision_reason`，`decision_reason` 为空
     时不展示（不看容器本身是否为 `None`），符合验收点要求。
  **验收**：`tests/test_state_and_store.py` 新增 1 个（序列化往返 +
  `options` 为空/旧数据缺字段/非法值三种兜底为 `None` 的场景）；
  `tests/test_spec_and_engine.py` 新增 2 个（`options` 非空时端到端
  验证 `decision_opportunity` 各字段的组装结果，含从 `tree_updates`
  审计正确收集 `trigger_node_ids`；`options` 为空时确认为 `None`）。
  加上原有 308 个，全部通过（**311 passed**）。
  **已知限制**：`context_note` 字段目前恒为空字符串——没有专门的
  输出通道产出它，是预留字段，不是遗漏；`trigger_node_ids` 的收集
  逻辑只覆盖 `confirmed_branch`/`status_updates`
  （emerging/active）/`new_branch_ids` 三类来源，`pruned_branches`
  （对应 `invalidated`）不计入——"排除了一个分支"本身通常不构成
  "触发这批选项出现"的理由，这是有意的范围收窄，不是遗漏；4.10
  仍然是"这一个状态节点的背景说明"，不做跨状态追踪同一个决策机会
  演变过程的 id 化持久对象，与方案"不做的部分"一致。方案第 5 节
  第六批（4.7 action_type 组合/条件行动标记）尚未开始。
- 2026-09-20（同日再追加）：**阶段三十三第六批**——完成方案第 5 节
  第六批：4.7（组合行动/条件行动的最小结构化支持），本身独立、不
  依赖前面任何一批：
  1. **`state_model.py`**：`ChoiceOption` 新增
     `action_type: str = "single"`（`single`/`combo`/
     `conditional` 三选一），归一化规则和 `risk_level` 类似但没有
     `None` 这个中间态——不认识的取值或缺省一律退化为 `single`，
     不存在"未声明"这个状态（`action_type` 本身默认就该是
     `single`）。
  2. **刻意不做的部分**（严格按方案执行）：没有新增任何"子步骤
     数组"结构——`combo`/`conditional` 选项"组合了什么/条件是
     什么"仍然只写在 `description` 自然语言里，`action_type` 只是
     一个展示层提示标签；选中后 `advance()` 依旧只推进一步，不会
     跨状态强制执行中间步骤，条件是否触发完全交给下一次
     `advance_step` 时 LLM 根据 `narrative` 里的既成事实自行判断，
     engine 侧不做任何特殊记账——工程量意义上比"给选项加个标签"更
     重的完整步骤序列状态机被有意推迟到 4.9/4.10 落地之后。
  3. **`workflows/advance_step.yaml`**：新增一段说明，讲清楚
     `action_type` 三个取值的含义、"组合/条件内容写进
     description，不要拆成额外字段"这条约束，以及"选中后引擎不会
     自动执行后续步骤"这条局限。`generate_scenario.yaml`（`state0`
     创建阶段）未同步添加——沿用 `action_reason`/`urgency`/
     `time_window` 这几个同样"只在 advance 阶段有意义"的字段的既有
     取舍，不是遗漏。
  4. **三个模板 SKILL.md**（`life-sim`/`negotiation`/
     `group-evolution`）：选项可选字段列表统一补上 `action_type`。
  5. **`app.py`**：`_option_meta_html()` 新增 `action_type` 徽章
     （`combo`→"🧩组合方案"、`conditional`→"🔀条件方案"），默认值
     `single` 不展示徽章（延续"未声明/默认值不展示"的一贯风格）。
  **验收**：`tests/test_state_and_store.py` 新增 1 个（`action_type`
  归一化：默认值、合法值透传、大小写不敏感、非法值回退、序列化
  往返）；`tests/test_decision_engine_prompts.py` 新增 4 个
  （`advance_step.yaml` 提到 `action_type`/`combo`/`conditional`、
  说明"只推进一步、不做强制执行"这条局限、三个模板都提到
  `action_type`、`ChoiceOption` 默认值断言）；另修正
  `test_create_and_advance_simulation_end_to_end` 里一处因为新增
  字段导致的 `chosen_option_json` 精确 JSON 断言。加上原有 311
  个，全部通过（**316 passed**）。
  **已知限制**：`action_type` 纯粹是展示层提示，不做任何语义校验——
  一个标了 `combo` 但 `description` 里其实只写了单一动作的选项，
  代码层面无法识别、也不打算识别（同 4.4/4.5 节辅助校验"弱信号、
  不做语义理解"的一贯取舍）；方案第 5 节里跨越多个批次的第 4.12
  条（引擎与 LLM 分工的渐进式重构）本身不排入固定批次，见方案原文
  第 7 步说明——但方案原文明确建议 4.12 第一步（校验前移到
  `decision_validation.py`）随第二批一起做、第二步（状态推导建议）
  随第四批一起做，实际第二批/第四批都没有触碰这两步，这是本轮六批
  执行下来唯一一处和方案建议的时间点不一致的地方，如实记录在此，
  不是发现晚了才补记；至此方案第 5 节列出的六个固定批次
  （4.1+4.5+4.8、4.2+4.3+4.6、4.4+4.13、4.9+4.11、4.10、4.7）全部
  完成，但 4.12 的三步（含"随批次同步做"的前两步）都还没有开始，
  不应该被理解成"方案已经全部落地"。
- 2026-09-20（同日再追加）：**阶段三十三第七批**——补做方案（`next_doc/
  world_simulator_potential_causal_space_and_decision_engine_plan.md`）
  4.12 节跨批次的第一、二步（第三步仍是带触发条件的观察项，不在本批
  范围内）：
  1. **第一步（校验前移）**：新建 `world_simulator/decision_
     validation.py`，作为"引擎侧决策校验层"的统一入口：
     - `normalize_risk_level()`/`normalize_urgency()`/`normalize_
       action_type()`——把原来散落在 `state_model.py::ChoiceOption.
       from_dict()` 里的三段内联归一化逻辑原样搬迁过来，`state_
       model.py` 改为调用这三个函数，行为完全不变（不是重写判断
       逻辑，纯粹是收敛位置）；
     - `compute_option_warnings()`——转发给阶段三十三第三批已经
       实现的 `engine/option_heuristics.py`（宏观事件重合/指标调节
       语言两条弱信号校验），`engine/advance.py` 的调用方从直接
       依赖 `engine.option_heuristics` 改为依赖这个新入口；
       `option_heuristics.py` 本身和它的两个具体检测函数不变，
       `tests/test_option_heuristics.py` 仍直接测试细节实现。
     - **循环导入的处理**：`decision_validation.py` 对 `engine.
       option_heuristics` 的导入放在函数体内部而不是模块顶层——
       `state_model.py` 在模块顶层导入本模块的归一化函数，如果本
       模块在顶层导入 `option_heuristics`（它又在顶层导入
       `state_model.ChoiceOption`），会触发 `state_model →
       decision_validation → option_heuristics → state_model` 的
       循环导入，改成函数体内导入后规避。
  2. **第二步（节点状态推导前移到引擎）**：`causal_tree.py`：
     - `future_tree.branches` 的每一项新增 `first_seen_step` 字段
       （分支第一次出现时对应的 step，向后兼容——旧数据没有这个
       字段时为 `None`，不编造假的起点）；`_normalize_branch()`
       负责解析/校验这个字段，`build_default_future_tree()`（兜底
       模板）和 `apply_tree_updates()`（`new_branches`/
       `expand_branches.sub_branches` 两处"分支真正第一次出现"的
       地方）负责在创建时把它填成当前 `as_of_step`；
     - 新增 `suggest_status_transitions(line, current_step, *,
       stale_multiplier=3)`：纯规则计算——只处理当前状态为
       `dormant`/`emerging` 的**顶层**分支（`sub_branches` 不
       递归处理，理由见函数 docstring），阈值 = 该线
       `advance_every_n_steps`（阶段三十一既有字段，未声明按 1
       算）× `stale_multiplier`（默认 3 倍，经验值），超过阈值仍
       停留在 `dormant`/`emerging` 的分支给出"建议 expired"的提示，
       不直接修改任何分支状态——是否采纳完全由 LLM 判断；
     - `spec_generator.py` 新增 `_stale_branch_suggestions_hint()`，
       在 `_resolve_causal_lines_hint()`（`stage="advance"` 分支）
       里紧跟在既有的 `_lines_due_this_step_hint()`（阶段三十一
       "预期这一步哪些线会动"提示）之后拼接进 prompt，措辞明确
       "仅供参考，请结合实际情境自行判断是否采纳，不是强制要求"；
       没有任何建议时返回空字符串，不给 prompt 增加噪音。
  3. **刻意不做的部分**（严格按方案原文的取舍）：不做 4.12 第三步
     （把 `advance_step.yaml` 拆成 `world_evolve`/`decision_
     generate` 两次调用）——方案原文给出的触发条件是"4.1~4.11 落地
     后，实测发现一次调用里 LLM 同时兼顾世界演化和决策生成、顾此
     失彼的问题依然突出"，目前尚未进入这个观察期，继续按方案搁置；
     不做"引擎直接改写分支状态"——`suggest_status_transitions()`
     的结果始终只是提示文本，避免"引擎单方面的规则判断和 LLM 叙事
     对不上"（方案原文 4.12 节第二步原话）；不递归处理
     `sub_branches`（4.11 节渐进式展开的下一层）——这层结构目前
     刻意保持"整体替换、不做局部推导"的简单语义。
  **验收**：`tests/test_decision_validation.py`（新建，11 个）覆盖
  三个归一化函数的 `None`/合法值/非法值三种输入，以及
  `compute_option_warnings()` 转发行为、`ChoiceOption.from_dict()`
  确实在调用新模块；`tests/test_causal_tree.py` 新增 7 个（`suggest_
  status_transitions()` 的"超过阈值触发"/"未到阈值不触发"/"缺
  `first_seen_step` 跳过"/"忽略 active 及以下终态"/"未声明
  `advance_every_n_steps` 按 1 计算"五种情况，以及 `apply_tree_
  updates()`/`build_default_future_tree()` 正确写入 `first_seen_
  step` 两个）；`tests/test_decision_engine_prompts.py` 新增 3 个
  （`_stale_branch_suggestions_hint()` 空/非空两种情况、
  `_resolve_causal_lines_hint()` 在 `advance` 阶段确实拼接了这条
  建议）。加上原有 316 个，全部通过（**337 passed**）。
  **已知限制**：`suggest_status_transitions()` 的"3 倍节奏"阈值是
  一个经验取值，没有做成可配置项（`manifest.settings` 目前没有为
  这类内部启发式参数开放配置入口，同项目一贯"先给一个合理默认值，
  真正需要可配置时再加"的取舍）；旧的因果线数据（阶段三十三第七批
  之前产生的历史 `future_tree`）里的分支永远拿不到 `first_seen_
  step`（读取时按 `None` 处理，不做批量回填），所以这条建议对老
  模拟实例的旧分支永远不会触发，只在这批改动之后新产生的分支上
  生效——这是"不编造假起点"这条既有原则的直接后果，不是遗漏；
  至此方案（`next_doc/world_simulator_potential_causal_space_and_
  decision_engine_plan.md`）第 3 节列出的 13 条差距（4.1~4.13）
  全部落地完成，包括之前唯一悬空的 4.12 第一、二步，只剩 4.12
  第三步作为方案原文明确写出的"带触发条件的观察项"继续观察。

- 2026-09-20（同日再追加）：**阶段三十三第八批**——应用户明确要求
  "不再等待触发条件观察，直接实施"，补做方案（`next_doc/
  world_simulator_potential_causal_space_and_decision_engine_plan.md`）
  4.12 节第三步：把 `advance_step.yaml` 一次调用同时产出世界状态
  变化和候选选项的方式，拆成两个独立的单步 workflow：
  1. **`workflows/world_evolve.yaml`**：只产出 `next_summary`/
     `narrative`/`next_vars`/`time_label`/`next_time_granularity`/
     `granularity_reason`/`uncertain_fields`/`key_drivers`/
     `causal_links`/`line_updates`/`tree_updates`/`beliefs`/
     `structural_change`，以及"这一步用户/代理选了哪个选项"的落地
     逻辑（`chosen_option_id`/`chosen_reason`/`custom_option_label`/
     `custom_option_description`——这部分逻辑直接决定 `next_vars`/
     `narrative` 怎么变化，理应留在"世界演化"这一步）；**明确不产出
     `options`**。
  2. **`workflows/decision_generate.yaml`**：把 `world_evolve` 的
     完整输出（`evolved_summary`/`evolved_narrative`/
     `evolved_vars_json`/`evolved_time_label`/
     `evolved_time_granularity`/`evolved_key_drivers_json`/
     `evolved_tree_updates_json`）作为既成事实喂进 prompt，专门负责
     "这一步是否形成决策机会、生成什么样的候选选项"，只产出
     `options`/`decision_reason`（选项字段规则——现实行动语义/
     可干预性下沉/紧急度/action_type 等——与原 `advance_step.yaml`
     完全一致，原样保留）。
  3. **`world_simulator/engine/advance.py`**：新增
     `manifest.settings.split_decision_calls` 开关，默认 `False`
     （沿用原单次调用 `advance_step.yaml`，完全向后兼容，不改变
     任何现有行为）；为 `True` 时改为依次调用 `world_evolve` →
     `decision_generate` 两个 workflow，按字段合并两次结果为同一份
     `data: Dict[str, Any]`（`{**data_decide, **data_evolve}`，
     `data_evolve` 放在后面覆盖，保证世界状态字段始终来自负责它的
     那次调用），合并后完全复用原有"解析 `chosen_option_id`/构造
     `next_state`/资源校验/因果树合并/`decision_opportunity` 构造"
     等下游逻辑，不需要区分走的是哪条路径。
  4. **UI**：`app.py` 创建向导和详情页"模拟设置"面板都新增了对应
     的勾选开关（默认不勾选），文案明确提示"会翻倍延迟和 token
     成本"。
  5. **`SKILL.md`**：三个模板（`life-sim-template`/
     `negotiation-template`/`group-evolution-template`）都补充了一段
     说明——拆分模式下会被 `world_evolve`/`decision_generate` 两个
     workflow 分别挂载两次，各自 prompt 已经写清楚这次调用该输出
     哪些字段，原"推进一步"章节的字段规则/正反例仍然完全适用，不
     需要在 SKILL.md 里重复维护两份规则。
  6. **`state_model.py`**：`SimManifest.settings` 文档补充
     `split_decision_calls` 字段说明及分组索引。
  **刻意不做的部分**：不做"根据实测选项质量自动切换单次/拆分调用"
  这种动态判断——是否承受两次调用的成本完全交给用户自己决定，不做
  成代码自动侦测"选项质量是否不达预期"后自动切换，那需要一套独立
  的质量评估机制，属于比这条改动本身更大的新课题；两次调用之间没有
  做"世界状态字段"之外的额外校验层（比如强制要求 `decision_generate`
  重新确认 `evolved_vars_json` 与自己理解一致）——`decision_generate`
  的 prompt 已经明确说"不需要、也不应该重新生成这些字段"，信任这条
  指令，不额外加代码层面的一致性校验，与项目一贯"宽松兜底"的风格
  一致。
  **验收**：新建 `tests/test_split_decision_calls.py`（2 个）：
  `test_advance_split_mode_calls_world_evolve_then_decision_generate`
  验证拆分模式下两个 workflow 被正确加载、按序调用、`decision_
  generate` 确实收到了 `world_evolve` 的输出（`evolved_vars_json`/
  `evolved_summary`/`evolved_tree_updates_json`）、两次结果正确
  合并进同一个 `next_state`（世界状态字段来自 `world_evolve`，
  `options`/`decision_reason` 来自 `decision_generate`，
  `decision_opportunity` 基于合并后结果正确构造）；
  `test_advance_default_mode_does_not_touch_split_workflows` 验证
  未声明 `split_decision_calls`（默认路径）时只加载/调用
  `advance_step`，完全不触碰 `world_evolve`/`decision_generate`，
  确认默认行为向后兼容。另外新增两个 workflow yaml 后触发了
  `tests/test_workflow_prompt_placeholders.py` 的既有回归测试（防止
  prompt 里举例用的大括号被误判成 `{step_id.field}` 占位符），已
  按测试要求把 `tree_updates` 示例从字面 JSON 大括号改写成纯文字
  描述，修正后该测试通过。加上原有 337 个，全部通过（**339
  passed**）。
  至此方案（`next_doc/world_simulator_potential_causal_space_and_
  decision_engine_plan.md`）4.12 节三个子步骤全部完成，第 3 节列出
  的 13 条差距（4.1~4.13）在方案范围内没有任何遗留项。

- 2026-09-20（同日再追加）：**阶段三十四第一批**——按第五轮方案
  （`next_doc/world_simulator_decision_engine_round2_gap_analysis_
  plan.md`）建议的实施顺序，完成第一批：5.2（DecisionOpportunity
  批次级聚合）+ 5.6（选项相似度警告），两条都是纯 Python 聚合/
  统计逻辑，不涉及任何 prompt/workflow 改动，不新增 LLM 调用。
  1. **5.2**：`world_simulator/engine/advance.py::
     _build_decision_opportunity()` 新增三个聚合字段——
     `max_urgency`/`max_risk`（取这一批 `options` 里已声明
     `urgency`/`risk_level` 的选项中最高的一档，全部未声明时为
     `None`；`opportunity_window` 按方案设计不单独存储，因为
     语义上就是"最紧急那个选项的 `time_window`"，避免同一份信息
     两处维护）、`baseline_option_id`（第一个 `id` 以 `continue_`
     开头的选项 id，把既有的 4.6 节命名约定显式暴露成结构化引用）。
     `app.py` 推进面板在"为什么现在需要决定"旁边新增"整体紧急度"
     徽章展示（复用既有 `_URGENCY_LABELS`/`ws-urgency-badge-*`
     样式，`max_urgency` 为空不展示）。
  2. **5.6**：`world_simulator/engine/option_heuristics.py` 新增
     `detect_similar_option_warnings()`——同一批候选选项两两比较
     `label` 的字符 2-gram jaccard 相似度，超过 0.6 阈值时提示
     "可能过于相似，考虑合并"（纯文本层面的粗略估计，不做语义
     去重、不自动合并或删除任何选项，延续本模块其它两个检测函数
     "宁可漏报、不做过度推断"的一贯风格）；短于 4 个字的 label 不
     参与比较；同一对选项不会因为在三个以上选项的两两组合里重复
     出现而被记两次警告。已接入 `compute_option_warnings()`（经
     `decision_validation.py` 转发的既有入口），`engine/advance.py`
     不需要任何改动即可生效。
  **刻意不做的部分**：5.2 不做"决策机会本身独立于任何单个选项的
  urgency/risk"这种语义（详见方案 5.2 节的取舍说明——审视下来
  和"最紧急选项的 urgency"几乎总是同一个信息，重新让 LLM 单独
  判断一次容易两个数字互相矛盾）；5.6 不引入 embedding 做语义
  相似度（避免新依赖和额外调用成本，且方案本身接受"漏报语义相似
  但用词不同"的选项这个精度取舍）。
  **验收**：新建 `tests/test_decision_opportunity_aggregation.py`
  （7 个用例，覆盖三个聚合字段的取值/边界情况——无选项时返回
  `None`、部分选项未声明 `urgency` 时不参与比较、`baseline_
  option_id` 有/无 `continue_` 前缀选项时的正确性、既有四个字段
  不受影响）；`tests/test_option_heuristics.py` 新增 6 个用例
  覆盖 `detect_similar_option_warnings()`（命中/不命中/短标题
  忽略/单选项不触发/三选项不重复计入同一对/汇总进
  `compute_option_warnings()`）；同时更新了既有测试
  `test_advance_builds_decision_opportunity_when_options_present`
  的断言（补上三个新字段的默认 `None` 值）。加上原有 339 个（阶段
  三十三第八批之后的基数），全部通过（**352 passed**）。

- 2026-09-20（同日再追加）：**阶段三十四第二批**——按第五轮方案第
  4 节顺序，完成第二批：5.1（`ChoiceOption` 补前置条件/分层后果
  字段，`next_doc/world_simulator_decision_engine_round2_gap_
  analysis_plan.md`）。
  1. **`state_model.py`**：`ChoiceOption` 新增两个可选字段——
     `prerequisites`（字符串数组，选择这个方向前需要满足的前提
     条件，默认空列表）、`consequences`（`{"short_term": "...",
     "long_term": "..."}`，短期/中长期后果，只认这两个 key，清理
     后整体为空归一化为 `None`，不留一个空字典）。`from_dict()`
     同步补上对应的解析/清理逻辑。
  2. **三个 workflow yaml**（`advance_step.yaml`/`decision_
     generate.yaml`/`generate_scenario.yaml`）的候选选项字段规则
     统一补充这两个新字段的说明；三个模板 `SKILL.md`
     （`life-sim-template`/`negotiation-template`/`group-
     evolution-template`）同步更新。
  3. **`app.py`**：`_option_meta_html()` 新增 `prerequisites`/
     `consequences` 的渲染（字段为空/`None` 时完全不渲染对应
     小节，不用"未知"占位制造伪信息）。
  **刻意不做的部分**：不单独区分"前提条件"和"约束"两个字段（合并
  进 `prerequisites` 表达）；不做 `time_cost`/`resource_cost` 这类
  量化成本字段（避免退化成 4.5 节明确反对的"内部指标调节"语义，
  时间/资源成本信息交给自然语言描述承担）——详见方案 5.1 节的取舍
  说明。
  **验收**：`tests/test_state_and_store.py` 新增 3 个用例（默认值/
  往返序列化、`consequences` 只认两个合法 key 且空值归一化为
  `None`、`prerequisites` 去空白/丢弃空字符串项）；同步修正了
  `tests/test_spec_and_engine.py::
  test_create_and_advance_simulation_end_to_end` 里
  `chosen_option_json` 的断言（补上两个新字段的默认值）；新增
  `workflows/*.yaml` 里的字段说明文本经
  `tests/test_workflow_prompt_placeholders.py` 校验后，把最初写的
  字面 JSON 大括号示例（`{"short_term": "...", ...}`）改写成纯
  文字描述，避免被误判为 `{step_id.field}` 占位符。加上原有 352
  个，全部通过（**355 passed**）。

- 2026-09-20（同日再追加）：**阶段三十四第三批**——按第五轮方案第
  4 节顺序，完成第三批：5.3（`CausalLine` 补充"当前趋势" trend
  字段）+ 5.4（因果图从"历史统计聚合"升级为"可声明的先验关系"，
  `next_doc/world_simulator_decision_engine_round2_gap_analysis_
  plan.md`）。
  1. **5.3**：`causal_tree.py` 新增 `normalize_line_trend()`——
     `line_updates[line_id].trend` 四选一（`accelerating`/
     `steady`/`decelerating`/`reversing`），合法值透传，非法值/
     未声明统一忽略（不像 `expansion_level` 那样退化到一个默认档，
     趋势判断本身要求有实际依据，猜不出就不猜）。`engine/advance.py`
     新增 `_normalize_line_update()`，推进时对每条 `line_updates`
     记录的 `trend` 子字段做归一化。`spec_generator.
     _resolve_causal_lines_hint()` 的 `advance` 阶段两个分支
     （有/无已声明因果线）都补充了 `trend` 字段的 prompt 说明。
     `app.py`「因果线总览」标题旁新增趋势徽章（取这条线最近一次
     合法 `trend` 记录，复用 `ws-uncertain-badge` 胶囊底样）。
  2. **5.4**：`spec_generator.resolve_causal_graph_hint()`（实际
     位置在 `spec_generator.py`，方案文档里写的 `causal_graph.py`
     是笔误——该模块只有纯聚合函数 `build_causal_graph()`，反馈进
     prompt 的 `resolve_causal_graph_hint()` 一直都在
     `spec_generator.py`）改写为两段式渲染：第一段"先验声明"读取
     新增的 `manifest.settings.declared_causal_graph`（创建时一次性
     声明、模拟过程中只读不改的静态先验，不依赖历史数据），第二段
     "历史统计聚合"是原有逻辑（`build_causal_graph(history)` 聚合
     实际发生过的 `causal_links`）。两段各自独立缺省，互不影响；
     `settings.causal_lines` 为空时两段都不展示（未声明因果线，
     "线到线"关系无从谈起）。`spec_generator.ScenarioDraft` 新增
     `declared_causal_graph` 字段 + `from_dict` 解析，`_resolve_
     causal_lines_hint()` 的 `create` 阶段 prompt 新增对应的可选
     输出说明（因果线之间存在创建时就能判断的先验关系时才给）。
     `state_model.py::SimManifest.settings` 文档补充字段说明 +
     字段分组索引更新。`app.py` 创建向导和详情页"模拟设置"都新增
     "手动调整因果图先验声明"折叠区（JSON 数组编辑，校验规则同
     `causal_lines` 的既有模式），确保 skill 给出的建议值不会被
     创建流程悄悄丢弃，也支持推进过程中手动增删（不支持系统自动
     更新，符合"静态先验，只读不改"的设计）。
  **刻意不做的部分**：5.3 不做"变化速度"的量化数值（同"不做伪
  精确"的一贯风格，`trend` 四档分类已经承载了真正有用的定性信息）；
  5.4 不做"因果图是持久化的、模拟过程中可以增删边"的完整图数据库
  式实现——`declared_causal_graph` 只支持创建时声明 + 事后在"模拟
  设置"里手动整体覆盖，不支持系统自动增删边；详见方案 5.3/5.4 节的
  取舍说明。
  **验收**：`tests/test_causal_tree.py` 新增 2 个用例（`normalize_
  line_trend()` 合法值透传/非法值及缺失统一忽略）；`tests/
  test_spec_and_engine.py` 新增 1 个用例（`advance()` 端到端验证
  `line_updates.trend` 的归一化，含一条合法值大小写/前后空格清理、
  一条非法值被剔除）；`tests/test_causal_graph.py` 新增 4 个用例
  （无声明时两段皆空、先验声明独立于历史数据展示、非法/自环/缺字段
  的先验条目被过滤、两段同时存在时的顺序与内容）。加上原有 355
  个，全部通过（**362 passed**）。
  另外，验证测试环境本身缺 `fastapi`/`json_repair` 两个第三方依赖
  （历次改动都没有触发过，这次跑全量套件时才发现，与本批改动无关，
  补装后不影响任何既有行为）。
  5.5（创建阶段拆分调用）仍按方案 5.5 节"先观察再决定"，不排入
  固定批次；5.7（质量信号自评模块）、5.8（三层未来空间展示层核查）
  待续。

- 2026-09-20（同日再追加）：**阶段三十四第四批**——按第五轮方案第
  4 节顺序，完成第四批：5.7（评估标准 8 条的轻量自评模块，
  `next_doc/world_simulator_decision_engine_round2_gap_analysis_
  plan.md`）。
  1. 新增 `world_simulator/quality_signals.py`，提供纯函数
     `summarize_quality_signals(history, causal_lines=None)`，不发起
     任何新的 LLM 调用，基于已有字段做五项客观计数统计：
     **可解释性密度**（有 `decision_reason`/`ChoiceOption.
     action_reason` 的决策点占比）、**跨线影响密度**（`causal_links`
     里带 `source_line_id` 的条目占比）、**分支差异性**（`options`
     数量 >= 2 的决策点占比）、**渐进展开使用率**（`future_tree`
     里 `expansion_level == "expanded"` 的分支占比，新增
     `_iter_branches()` 递归遍历 `children`/`sub_branches` 两种
     嵌套形状统计全部层级）、**不确定性标注覆盖率**（`uncertain_
     fields` 非空的步数占比）。输入既接受 `SimState` 对象也接受
     形状相同的 dict（`_get()` helper 统一处理，同 `causal_graph.
     build_causal_graph()` 的既有取舍）；分母为 0 时对应 `ratio` 为
     `None`（区分"没有发生"和"没有数据"，不伪造成 0）。
  2. `app.py` 详情页新增"📐 质量信号（统计代理指标，非评分）"折叠区
     （默认折叠，位置紧跟"预测准确性统计"之后、"模拟复盘"之前），
     展示上面五项统计数字，`_pct()` helper 把 `None` 渲染成"暂无
     数据"而不是"0%"；文案明确标注"数字高不代表这次模拟一定更好"、
     "不是给这次模拟打分"，并说明因果一致性/决策真实性这两条评价
     标准暂无自动化衡量方式，仍需自行阅读叙事/因果图判断。
  **刻意不做的部分**：不做因果一致性/决策真实性这两条的自动化
  统计（本质需要理解语义内容，勉强做关键词匹配的伪指标反而误导
  用户）；不做打分/排名（比如"这次模拟得 82 分"），只展示原始
  统计数字；详见方案 5.7 节的取舍说明。
  **验收**：新增 `tests/test_quality_signals.py`，8 个用例（空
  `history` 全零/`None` 默认值；可解释性密度同时识别顶层
  `decision_reason` 和选项级 `action_reason`；分支差异性/跨线影响
  密度/不确定性覆盖率的已知输入正确性；渐进展开使用率递归穿透
  `children`+`sub_branches` 两层嵌套的正确计数；无 `causal_lines`
  时该项为 `None`；同时接受 `SimState` 对象和 dict 两种输入形状）。
  加上原有 362 个，全部通过（**370 passed**）。
  5.5（创建阶段拆分调用）仍按"先观察再决定"，不排入固定批次；5.8
  （三层未来空间展示层核查）待续。

- 2026-09-20（同日再追加）：**阶段三十四第五批**——按第五轮方案第
  4 节顺序，完成第五批：5.8（三层未来空间补第三层"已发生"的显式
  状态，`next_doc/world_simulator_decision_engine_round2_gap_
  analysis_plan.md`）。
  1. **先人工核查结论**：现有 `future_tree.branches` 六态生命周期
     （`dormant`/`emerging`/`active`/`resolved`/`expired`/
     `invalidated`）在数据层面已经覆盖参考文档第二十七节"三层未来
     空间"（潜在/活跃/已发生）的语义——`resolved` 本身就承担"已
     发生/已确认"这个含义，**不新增任何持久化字段**，与方案 5.8
     节的取舍判断一致。
  2. **展示层调整**：`app.py` 因果线详情页原来把 `branches` 按
     `future_tree.branches` 原始顺序摊平展示，虽然每条分支前有
     状态图标/中文标签，但"已发生"和"仍在演化中"混排在一起，
     用户需要逐条读状态标签才能拼出"这条线到底哪些已经确认、哪些
     还悬而未决"。现按生命周期阶段分成三组展示：**"🌱 仍在演化中
     （潜在 / 活跃）"**（`dormant`/`emerging`/`active`）、
     **"● 已发生（已确认/已解决）"**（`resolved`）、**"✕ 已排除
     （错过窗口/已失效）"**（`expired`/`invalidated`——不属于
     参考文档三层里的任何一层，是"已排除"的分支，单独成组，不
     强行并入前两组造成误导），每组只在非空时渲染，组内顺序和
     原有单条展示逻辑（含"标为已解决"/"标为已失效"两个按钮）
     完全不变。
  **刻意不做的部分**：不新增数据模型字段——审视结论是"现有六态
  生命周期已经覆盖参考文档的三层语义，只是展示层原来没有按这个
  方式分组"，属于纯展示层调整，不是数据结构缺口；详见方案 5.8
  节的取舍说明。
  **验收**：本批是纯 Streamlit 展示层调整，项目里对 `app.py`
  没有直接的 UI 单元测试覆盖（同既有测试组织方式一致——`app.py`
  的展示逻辑历来靠人工核查，不在自动化测试范围内），运行全量
  既有测试套件确认未破坏任何逻辑层行为，370 个全部通过
  （**370 passed**，与阶段三十四第四批数量一致，因为本批没有新增
  测试文件，只改了 `app.py` 一处渲染逻辑）。
  至此第五轮方案 8 条改进方向中，5.1~5.4/5.6~5.8 共 7 条全部完成；
  5.5（创建阶段拆分调用）仍按方案本身"先观察再决定"的建议保留
  开关待启用，不视为遗留差距。

- 2026-09-20（同日再追加）：**阶段三十四第六批**——按用户明确要求
  "把所有开关都打开，然后继续后续的改动"，完成两件事：（1）落地
  5.5（创建阶段拆分调用，`next_doc/world_simulator_decision_engine_
  round2_gap_analysis_plan.md`）；（2）把创建向导里的实验性开关
  默认值改为开启。
  1. **5.5 落地**：新增 `manifest.settings.split_creation_calls`
     开关。为 `True` 时，`spec_generator.generate_scenario()` 不再
     用单次 `generate_scenario.yaml` 调用，而是拆成两次：
     - `workflows/world_builder.yaml`：只产出"世界状态本身"——
       `title`/`summary`/`vars`（含多主体模式下的 `vars.entities`）/
       `time_label`/`time_granularity`，以及与 `vars` 强相关的可选
       建议字段（`resource_fields`/`resource_relations`/
       `uncertain_fields`/`field_provenance`/`objectives`/
       `belief_fields`/`beliefs`）——这些字段本质是"世界状态怎么
       描述"，划给第一步。
     - `workflows/causal_space_builder.yaml`：把第一步的
       `title`/`summary`/`vars` 当既成事实喂进去（`built_title`/
       `built_summary`/`built_vars_json`），专门产出候选行动
       `options`、因果线声明 `causal_lines`（含 `future_tree`/
       KeyNode 字段）、因果线先验关系 `declared_causal_graph`——
       对应参考文档"Causal Line Generator + 关键节点生成 + 触发
       条件生成 + 候选行动生成 + 跨因果线关系建立"。
     字段划分依据 `advance.py` 拆分调用的既有原则："世界状态类
     字段"归第一步，"因果/决策类字段"归第二步，两者互不重叠，
     `spec_generator.generate_scenario()` 里两份结果按
     `{**data_causal, **data_world}` 直接展开合并（同
     `advance.py::advance()` 的既有合并方式）；`ScenarioDraft.
     from_dict()` 不需要区分走的是单次调用还是拆分调用。默认
     `False` 时行为完全不变，仍是原来的单次 `generate_scenario.
     yaml` 调用。三个模板 `SKILL.md`（`life-sim-template`/
     `negotiation-template`/`group-evolution-template`）都补充了
     对应说明段落，同 `split_decision_calls` 既有段落的写法。
  2. **实验性开关默认开启**：用户要求"把所有开关都打开"——创建
     向导（`app.py`）里三个原本默认关闭/未提供入口的实验性开关
     改为创建时默认勾选（用户仍可取消）：
     - `split_decision_calls`（阶段三十三第八批）：向导里的复选框
       默认值由 `False` 改为 `True`。
     - `split_creation_calls`（本批新增）：默认 `True`。
     - `observer_mode`（阶段三十一 4.25 节）：此前只能在创建*之后*
       的详情页"模拟设置"里开启，这次向导新增了对应复选框，默认
       `True`，创建时随其它设置一起存入 `settings`。
     **刻意不做的部分（取舍说明）**：没有改动这三个开关在引擎侧
     读取时的兜底默认值（`advance.py::advance()` 的
     `manifest.settings.get("split_decision_calls")`、
     `spec_generator.generate_scenario()` 的
     `(settings or {}).get("split_creation_calls")`、
     `autopilot.py` 的 `(manifest.settings or {}).get("observer_
     mode")`——这三处的"键不存在时"兜底值仍然是 `False`/`None`）。
     原因：这三个开关一旦默认在引擎层面打开，会影响所有*不经过*
     创建向导构造的 manifest（CLI `create_simulation()` 直接调用、
     测试里手工构造的 manifest、旧数据），而全量测试套件里有十余
     个测试文件（`test_spec_and_engine.py`/`test_autopilot.py`/
     `test_multi_template.py` 等）在构造 `advance()`/
     `generate_scenario()` 调用时完全没有设置这些开关、只 mock 了
     单次调用路径的 workflow——如果引擎侧默认值也一起改成 `True`，
     这些测试会全部按拆分调用路径走而找不到对应的 mock，需要逐一
     重写，属于"为了一个默认值偏好而制造大量无实质收益的测试改动
     和真实行为风险"，不划算。改成"创建向导默认勾选"能达到用户
     实际想要的效果（新建的模拟默认启用这些能力），同时不改变
     任何已有实例/测试路径的既有行为——两种做法对新建模拟而言
     最终效果一致，只是"默认值生效的位置"不同（UI 层显式写入
     `settings` vs 引擎层兜底），后者风险小得多。
  **验收**：新增 `tests/test_split_creation_calls.py`，2 个用例
  （`split_creation_calls=True` 时正确调用 `world_builder` →
  `causal_space_builder` 并按字段归属正确合并结果；未声明该设置时
  仍只调用单一的 `generate_scenario` workflow，不触碰拆分后的两个
  新 workflow）；`tests/test_workflow_prompt_placeholders.py` 对
  两个新 yaml 文件的静态占位符扫描通过（新增的 JSON 示例大括号里
  不含"."，不会被误判为 `{step_id.field}` 占位符）。加上原有 370
  个，全部通过（**372 passed**）。

- 2026-09-20（同日再追加）：**阶段三十六第一批**——按
  `next_doc/world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 的建议顺序，落地第一批 2.1 节（Event-Driven 决策点引擎 +
  Observer View 完整版），四个方向里改动面最小、复用度最高的一批。
  1. **`engine/advance.py::fast_forward()`**：新增外层循环，连续调用
     已有的单步 `advance()`（默认走向，`choice_option_id=None`，与
     详情页"按默认走向推进"按钮同一种调用方式），每步后检查
     `next_state.major_decision`（`stop_on_major_decision`，默认
     `True`）或 `bool(next_state.options)`（`stop_on_options`，默认
     `True`），命中就停止；达到 `max_steps` 都没命中也停止。不改动
     `advance()` 内部任何 prompt/字段逻辑，跳过的每一步依然逐一完整
     调用 `store.append_state()` 落盘，不产生历史空洞。返回值
     `FastForwardResult` 携带 `final_state`/`skipped_states`/
     `stop_reason`（`major_decision`/`options`/`max_steps`/`ended`/
     `paused`）/`summary_text`（直接拼接被跳过步骤已有的 `summary`
     字段，不发起新的 LLM 调用，复用 `retrospective.py`"只读取已经
     存在的信息"的一贯做法）。新增 `decision_context`/`chosen_by`/
     `allow_custom_options` 透传参数，供自动挡场景保留风险偏好/原则
     画像（见下）。
  2. **`app.py` 新增"⏩ 快进"入口**：详情页推进面板里与"推进 1 步"
     并列（不替换），用户设定 `max_steps` 上限，点击后展示"跳过了
     N 步"的摘要 + 停止原因，摘要下方可展开查看每一步原始
     `summary`/`narrative`（数据本来就完整落盘，只是 UI 默认折叠）
     ——这就是 v2 4.25 节设想的"双视角"效果的最小成本实现，不需要
     重新设计存储层或两套视图组件。
  3. **`autopilot.py::run_batch_autopilot()` 新增
     `settings.autopilot_fast_forward` 开关**（默认 `False`，manifest
     级别）：为 `True` 时，该实例的自动挡从"固定推进 `steps` 步、
     每步单独判断 `review_mode`"改为调用一次
     `fast_forward(max_steps=steps, stop_on_major_decision=True,
     stop_on_options=False, decision_context=..., chosen_by=
     "autopilot", ...)`——`stop_on_options` 固定传 `False`：自动挡的
     意义就是不需要为候选选项停下来等真人，是否暂停完全交给
     `stop_on_major_decision` + `review_mode` 判断；`decision_context`
     透传 `_build_decision_context(manifest)`，保证快进期间遇到候选
     选项时仍然应用用户设置的风险偏好/原则/情境化策略，不会退化成
     "不做选择、由 skill 自行决定默认走向"。命中 `major_decision` 且
     `review_mode == "pause_on_major_decision"` 时按原有语义调用
     `set_status(paused)`。与 `observer_mode` 互补：`observer_mode`
     调整 LLM 产出倾向（少生成决策点），`autopilot_fast_forward`
     调整调度层"要不要为每一批候选选项都单独走一遍暂停判断"，两者
     可以同时开启。
  4. **`state_model.py`**：文档化 `settings.autopilot_fast_forward`
     字段，纳入"世界独立演化（实验性）"分组索引。
  **范围克制（按方案要求，不做的部分）**：不做真正常驻的后台
  daemon 进程——`project.yaml` 的 cron/entrypoint 调度模型是"启动-
  执行-退出"的一次性进程，不是常驻服务，`fast_forward()` 仍然是
  "被一次调用触发，内部循环快进"，这是在现有调度模型约束下能做到的
  最接近参考文档设想的形态，方案原文 2.1 节已明确这一取舍。
  **验收**：新增 `tests/test_fast_forward.py`，8 个用例——命中
  `major_decision` 立即停止且返回正确的跳过步数/摘要文本；命中非空
  `options` 停止；达到 `max_steps` 都没命中时停止并生成摘要；
  `max_steps < 1` 时报 `ValueError`；跳过的每一步都能在
  `SimStore.load_history()` 里完整读到（验证没有历史空洞）；
  `autopilot_fast_forward` 默认关闭时 `run_batch_autopilot()` 行为
  与之前完全一致（逐步调用，不走 `fast_forward`）；开启后改为一次
  `fast_forward()` 调用，命中 `major_decision` 时按 `review_mode`
  正确暂停；开启后不会因为出现候选 `options` 就中途停下。加上原有
  372 个，全部通过（**380 passed**）。
  **后续节奏（按方案原文第 3 节）**：按建议先只观察
  `autopilot_fast_forward` 几天真实运行的摘要质量，确认"跳过了 N
  步"的摘要确实有信息量、不是空话之后，再推广到更多场景；第二批
  （2.2 节 Influence Field / Relationship 完整机制）、第三批（2.3
  节多尺度因果线真正独立推进，要求先在 `life_sim` 模板小范围人工
  验证）、第四批（2.4 节反身性最小诠释，价值最不确定、可直接放弃）
  留待后续按顺序推进，不在本批一次做完。

- 2026-09-20（同日再追加）：**阶段三十六第二批**——按
  `next_doc/world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 2.2 节，落地 Influence Field / Relationship 完整机制
  （在阶段三十一最小起步版结构化列表的基础上扩展，仍然不做任何
  自动化的数值传播计算，只是把结构化信息转成更丰富的 prompt 提示）。
  1. **`relationship.py`**：`normalize_relationships()` 新增三个可选
     字段——`delay_steps`（非负整数，默认 `0` 即时生效，非法/负数
     归一化为 `0`）、`propagation_path`（字符串数组，间接影响经过的
     中间实体/因果线 id，非列表归一化为空列表）、`reversible`
     （`reversible`/`hard_to_reverse`/`irreversible` 三选一，不认识
     的取值归一化为未声明，不像 `strength` 那样给一个默认档位）。
     新增 `queue_pending_effect()`/`due_pending_effects()` 一对轻量
     纯函数：前者把一条被声明为"源头已触发"的延迟关系记一笔
     "预计第 N 步生效"的待办（按引用去重），后者按 `current_step`
     筛出已到期的待办，两者都不做文件 IO，落盘由调用方负责。
  2. **`spec_generator.py`**：新增 `_resolve_relationship_hint()`，
     把 `settings.relationships` 转成一句话提示（含
     `delay_steps`/`propagation_path`/`reversible` 的自然语言转换），
     明确告诉 LLM"这是参考信息，不是要你机械计算数值传播"；到期的
     `relationship_pending_effects` 会额外追加一句"该体现效果了"的
     提醒。接入 `resolve_hints()` 输出的 `relationship_hint` key，
     未声明 `relationships` 时返回空字符串（不给没启用的模拟增加
     prompt 噪音）。
  3. **`workflows/advance_step.yaml`/`world_evolve.yaml`**：新增
     `{relationship_hint}` 占位符，以及可选输出字段
     `triggered_relationships`（字符串数组，引用被触发的关系
     `id`/下标）的说明。
  4. **`engine/advance.py`**：推进落盘前解析 LLM 输出的
     `triggered_relationships`，逐条调用 `queue_pending_effect()`
     写入 `manifest.settings.relationship_pending_effects` 并随
     `manifest` 一并落盘；未输出该字段时不产生这个 key（延续"未声明
     就不出现"的一贯风格）；引用不存在的关系时静默退化为 `delay_
     steps=0`（下一步就提醒），不中断推进。
  5. **`state_model.py`**：更新 `relationships` 字段文档说明三个
     新字段，新增 `relationship_pending_effects` 字段文档，字段
     分组索引把 `relationship_pending_effects` 并入"多主体"分组。
  6. **三个模板 `SKILL.md`**（life-sim/negotiation/group-evolution）：
     在 `tree_updates` 之后补充 `triggered_relationships` 输出字段的
     说明，指向共同的格式约定。
  **范围克制（按方案要求，不做的部分）**：不做自动化的数值传播计算
  引擎（图遍历、强度衰减公式）——是否触发、触发后具体怎么体现仍
  完全由 LLM 判断，避免"看起来是精确的因果引擎、实际是规则拍脑袋"
  的伪确定性，这个范围就是这个方向在本项目里"完整"的合理形态。
  **验收**：`tests/test_relationship.py` 新增 12 个用例，覆盖
  `delay_steps`/`propagation_path`/`reversible` 的归一化（含非法值/
  缺省的安全默认）、`queue_pending_effect()` 按 `id`/下标解析引用并
  正确计算 `due_step`、找不到引用时退化为零延迟、同一引用同一步
  去重、`due_pending_effects()` 按步数正确筛选且不修改入参；
  `tests/test_spec_and_engine.py` 新增 2 个用例，覆盖
  `triggered_relationships` 正确写入
  `settings.relationship_pending_effects`，以及未输出该字段时
  `settings` 里不凭空出现这个 key。加上原有 380 个，全部通过
  （**394 passed**）。
  **后续节奏（按方案原文第 3 节）**：第三批（2.3 节多尺度因果线
  真正独立推进，要求先在 `life_sim` 模板小范围人工验证）、第四批
  （2.4 节反身性最小诠释，价值最不确定、可直接放弃）留待后续按
  顺序推进。

- 2026-09-20（同日再追加）：**阶段三十六第三批**——按
  `next_doc/world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 2.3 节，落地多尺度因果线真正独立推进（这是四批里改动面
  最大的一批，按方案要求以独立开关 + 独立入口函数的形式与既有
  `advance()` 长期共存，完全不改动 `advance()`/`advance_step.yaml`
  的既有行为）。
  1. **`state_model.py`**：`causal_lines[i]` 新增两个可选字段——
     `owned_vars`（字符串数组，声明这条线独占哪些 `vars` 顶层字段，
     必须显式声明，引擎不自动推断；不同线之间不允许重叠）、
     `local_step`（整数，这条线自己的推进计数，只有真正被独立推进
     时才 +1，用于"错峰调用"判断）。新增 `settings.independent_
     line_advance`（布尔，默认 `False`）字段文档及分组索引。
  2. **`workflows/line_evolve.yaml`（新增）**：只传入一条线自己
     `owned_vars` 子集的独立调用模板，明确要求不输出 `options`、
     不输出声明范围之外的字段。
  3. **`engine/advance_independent.py`（新增）**：`advance_lines()`
     ——全局 `step` +1，只对本步到点（`local_step %
     advance_every_n_steps == 0`）且声明了 `owned_vars` 的线分别
     发起独立调用；`_validate_owned_vars_no_overlap()` 在推进前
     统一校验字段归属不重叠，重叠直接抛 `OwnedVarsOverlapError`
     拒绝这次推进；跨线读取用的是调用前的 `current.vars` 快照，
     不是同批次内其它线刚算出来的中间结果；没有任何线到点时仍然
     正常落盘（全局 step 照常 +1），不发起任何 workflow 调用。
     新增 `engine/errors.py::OwnedVarsOverlapError`。
  4. **`app.py`**：因果线总览为声明了 `owned_vars` 的线标注"独立
     推进 · 本线第 N 步"；`independent_line_advance` 开启且存在
     至少一条声明了 `owned_vars` 的线时，详情页新增"🧵 独立推进
     因果线"入口，调用 `advance_lines()`，与原有"推进"/"快进"入口
     并列、不替换。
  **范围克制（按方案要求，不做的部分）**：不做真正的并行/异步调用
  （本步内到点的多条线仍按声明顺序依次同步执行）；不允许跨线互相
  修改对方的 `vars`（跨线影响仍然只通过 `causal_links`/
  `declared_causal_graph` 以"提示"形式喂给对方，由那条线自己决定
  要不要体现）；独立推进的线不产出候选决策分支，决策点判断仍然
  完全由主 `advance()` 路径负责。
  **验收**：`tests/test_independent_line_advance.py`（新增，4 个
  用例）覆盖：两条节奏不同的线只有到点的线真正发起调用、`owned_
  vars` 重叠时在任何调用之前就报错阻止且全局 step 不前进、所有线
  都不到点时的空推进（step 照常 +1、不发起任何调用）、同批次内
  两条线到点时互相读到的是调用前的快照而非中间态。加上原有 394
  个，全部通过（**398 passed**）。
  **重要提醒（按方案原文，不是代码层面的限制）**：代码已完整落地，
  但方案原文明确要求"先在 `life_sim` 模板小范围人工验证（3~5 次
  真实多因果线模拟），确认错峰调用不会让体验割裂，再考虑推广到
  其它模板"——这一步需要使用者自己实际跑几次来验证，`independent_
  line_advance` 默认关闭，开启前建议按这个节奏来。
  **后续节奏**：第四批（2.4 节反身性最小诠释）价值最不确定，方案
  原文明确"可以直接放弃"，是否继续待后续评估。

- 2026-09-21：**阶段三十六第四批（收官）**——按
  `next_doc/world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 2.4 节，落地反身性（Reflexivity）最小诠释。方案原文明确
  这是价值最不确定、"可以直接放弃"的一批，本批按最小范围落地，且
  严格遵守"不做任何基于标注的自动化行为"的范围克制。
  1. **`world_simulator/reflexivity.py`（新增）**：
     - `detect_suggested_direction(report)`：用关键词重叠（同
       `knowledge_base.py` 一贯"不引入语义模型"的取舍）从复盘报告的
       `lessons`/`what_to_reflect_on` 文本判断这份复盘是不是在建议
       "后续更保守"（`reduce_risk`）或"后续更敢冒险"
       （`increase_risk`）；两类关键词都命中或都不命中时返回
       `None`，不勉强给结论。
     - `evaluate_and_annotate(data_dir, sim_id, branch=...)`：找到
       这个分支最近一份尚未处理过的复盘记录，比较该复盘 `up_to_
       step` 前后用户选择的 `ChoiceOption.risk_level`
       （low/medium/high 打分为 1/2/3）均值：`reduce_risk` 且均值
       明显下降、或 `increase_risk` 且均值明显上升（阈值 0.5，样本
       不足 2 个时不下结论、也不标记为已处理，留给下次调用），才
       调用 `knowledge_base.annotate_reflexivity_observation()`
       追加标注；无论是否一致，只要判断过了（不是因为样本不足），
       都会把该复盘标记为 `reflexivity_annotated=True`，避免重复
       判断同一份复盘。
  2. **`knowledge_base.py`**：`KnowledgeItem` 新增 `notes: List[str]`
     字段（默认空列表，向后兼容旧数据）；新增
     `annotate_reflexivity_observation(data_dir, query_text, note,
     limit=3)`——不新建知识条目，只对关键词检索到的最相关的已有
     条目追加一条 `note`（同一句话不重复追加），检索不到任何相关
     条目时返回空列表，不勉强附加到不相关的条目上。
  3. **`retrospective.py`**：`RetrospectiveRecord` 新增
     `suggested_direction`（`reduce_risk`/`increase_risk`/`None`，
     `generate_retrospective()` 落盘时用 `reflexivity.
     detect_suggested_direction()` 算出）、`reflexivity_annotated`
     （布尔，默认 `False`）两个字段；新增
     `mark_reflexivity_annotated()`（整体重写落盘，同
     `knowledge_base._save_all()` 的取舍——这是本文件里唯一一处
     "原地更新既有记录"的操作，其余写入仍是纯追加）。
  4. **`engine/knowledge.py`**：新增安全包装
     `_safe_evaluate_reflexivity()`，同 `_safe_record_causal_links`
     的既有约定——旁路操作，异常吞掉不向上抛出。
  5. **`engine/advance.py`**：`advance()` 落盘 `manifest` 之后调用
     `_safe_evaluate_reflexivity()`，让"选择模式是否与复盘建议一致"
     的判断能随着每一步新的选择自然推进（不需要用户额外触发）。
  **范围克制（按方案要求，不做的部分）**：不做任何基于这个标注的
  自动化行为——不会因为记录了"用户变保守了"就自动调整后续 prompt
  的策略画像或建议倾向；不做跨模拟、跨用户的反身性统计；不实现
  参考文档设想的"预测公开传播、影响现实中大量人的行为"的完整反身
  性——本地单机单用户工具没有对应的现实场景可以承载这个设想，本批
  只是"预测是否影响了用户自己后续决策"这个最小映射。
  **验收**：新增 `tests/test_reflexivity.py`，10 个用例，覆盖关键词
  方向判断（含"都不命中"/"都命中"两种不给结论的情况）、
  `annotate_reflexivity_observation()` 的匹配追加/去重/无匹配不写入、
  `evaluate_and_annotate()` 的样本不足暂不处理、方向一致才追加标注、
  方向不一致时判断过但不追加、无明确方向时直接标记已处理不追加、
  已处理过的复盘不会被重复处理。加上原有 398 个，全部通过
  （**408 passed**）。
  **至此**：`world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 规划的四个批次（2.1/2.2/2.3/2.4）全部完成。

- 2026-09-21（同日再追加）：**阶段三十七第一批**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 2 节，落地知识库补 Evidence/Valid Range/
  Version 字段 + 只读浏览页。
  1. **`knowledge_base.py`**：`KnowledgeItem` 新增三个可选字段——
     `evidence`（字符串数组，格式 `"{sim_id}#step{N}"`，去重记录
     来源）、`valid_range`（自由文本，适用范围说明）、`version`
     （整数，默认 1，仅 `valid_range` 变为不同的非空取值时才 +1）。
     `record_causal_links()` 新增可选 `step` 参数：命中已有条目时
     追加 `evidence`（去重）、`valid_range` 变化时更新并递增
     `version`；新建条目时按声明的 `valid_range` 初始化、按
     `step` 初始化 `evidence`。旧数据（没有这三个字段的历史 jsonl
     行）通过 `from_dict()` 默认值安全兼容。
  2. **`engine/knowledge.py`/`engine/advance.py`**：
     `_safe_record_causal_links()` 新增可选 `step` 参数并透传给
     `record_causal_links()`；`advance()` 调用处传入
     `next_state.step`。
  3. **`app.py`**：新增"📚 知识库"只读浏览页（侧边栏新入口，
     `view == "knowledge"`）——按置信度/印证次数排序展示全部知识
     条目，展开可看机制/适用范围/来源引用/观察标注（阶段三十六第
     四批的 `notes` 字段）。**不做**任何编辑/删除入口，延续 4.12
     节原方案"不做知识库管理 UI"的既有判断，只补"能看见"这一层。
  **范围克制（按方案要求，不做的部分）**：不引入向量检索/语义
  相似度模型；`valid_range` 不做结构化约束、不参与任何自动判断，
  纯展示信息；知识库浏览页不支持手工编辑或删除。
  **验收**：`tests/test_knowledge_base.py` 新增 6 个用例，覆盖
  `evidence` 的记录/去重、无 `step` 时不记录、`valid_range` 变化
  触发版本递增/不变时不递增、旧数据兼容读取、新字段的完整往返。
  加上原有 408 个，全部通过（**414 passed**）。
  **后续节奏（按方案原文第 1 节）**：批次二（Resource/Rule 补
  `production`）、批次三（Hypothesis 半自动实验设计建议）与本批
  互相独立，可任选顺序继续；批次四/五/六留待后续按顺序推进。
- 2026-09-21（同日再追加）：**阶段三十七第二批**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 3 节，`resource_relations` 新增
  `production`（持续产出）关系类型。
  1. **`engine/resource_guard.py`**：`_normalize_resource_relations()`
     新增识别 `type == "production"` 的项，归一化出 `field`（必填，
     受影响资源字段路径）、`amount_per_step`（可选，声明的每步理论
     产出速率，缺省/非法安全落为 `None`）、`source_line_id`（可选，
     产出来源因果线 id，缺省为空字符串，本批只记录不参与任何计算）、
     `tolerance`（复用 `transfer` 现有默认 0.1）。原有 `transfer`
     归一化结果内部补一个 `"type": "transfer"` key 便于
     `_check_resource_relations()` 分支判断，对外返回形状（`from`/
     `to`/`tolerance`）不变。`_check_resource_relations()` 新增
     `production` 分支：仅当声明了 `amount_per_step` 时，对比这一步
     `field` 实际变化量和声明速率，偏差超出容差（复用 `transfer`
     现有的"按两者绝对值较大者衡量"判断逻辑）时追加一条不一致提示
     ——**只提示，不阻断推进、不修改数值**，和 `transfer` 现有行为
     一致；不一致项形如 `{"kind": "production", "field": ...,
     "amount_per_step": ..., "actual_delta": ...}`，用 `kind` key
     区分，`transfer` 的不一致项形状（`from`/`to`/`delta_from`/
     `delta_to`，无 `kind`/`type` key）保持阶段十六上线时完全一致，
     不破坏既有调用方/测试。未声明 `amount_per_step` 的 `production`
     关系只是结构化记录，不参与任何数值核对。
  2. **`state_model.py`**：`SimManifest.settings.resource_relations`
     与 `SimState.relation_violations` 字段文档更新，补充
     `production` 类型的格式说明与不一致项形状。
  3. **`spec_generator.py`**：`ScenarioDraft.resource_relations`
     字段文档补充 `production` 类型的示例。
  4. **`app.py`**：`_relation_violations_html()` 新增按 `kind ==
     "production"` 分支的展示文案（"「字段」这一步产出与声明速率
     明显不符（声明每步 X，实际变化 Y）"），`transfer` 分支渲染逻辑
     不变。
  **范围克制（按方案要求，不做的部分）**：不做"自动帮 LLM 计算并
  写入产出数值"；`source_line_id` 本批只做记录，不接入
  `_resolve_relationship_hint()` 之类的 prompt 提示生成，留待后续
  如果发现有需要再加。
  **验收**：新增 `tests/test_resource_production_relation.py`，13
  个用例覆盖：`production` 关系的归一化（含完整字段/可选字段缺省/
  `amount_per_step` 非法回退/`field` 缺失跳过/未知 `type` 跳过）、
  `_check_resource_relations()` 对 `production` 的偏差检测/容差内
  不触发/未声明速率不核对/非数字字段跳过、`transfer` 与 `production`
  混合声明时 `transfer` 检查结果形状不受影响（回归）、`engine.
  advance()` 端到端集成（偏差记入 `relation_violations` 且不改
  数值、未声明速率时不产生任何记录）。加上原有 414 个，全部通过
  （**427 passed**）。
  **后续节奏（按方案原文第 1 节）**：批次三（Hypothesis 半自动
  实验设计建议）与本批互相独立，可继续推进；批次四依赖批次一的
  `evidence` 字段（已具备）；批次五/六留待后续按顺序推进。
- 2026-09-21（同日再追加）：**阶段三十七第三批**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 4 节，Hypothesis Engine 新增半自动实验
  设计建议。
  1. **`hypothesis.py`**：新增 `suggest_experiment_design(cfg,
     workspace_root, uncertainties, *, max_combinations=4)`——把
     `suggest_critical_uncertainties()` 识别出的不确定字段列表喂给
     一次轻量 `type: agent` workflow 调用（同 `retrospective.py` 的
     调用手法：`WorkflowStore`/`WorkflowRunner` + `extract_agent_
     json_output()` 从 `StepResult.output` 解析），要求 LLM 挑出
     最多 `max_combinations` 组"最有区分度"的组合并各给一句"为什么
     测这组"，返回 `[{"combination": {...}, "why": "..."}]`。
     `uncertainties` 为空时直接返回空列表，不发起任何调用；LLM 回复
     里格式不对的组合项（`combination` 非字典/空字典）逐项跳过，不
     中断其它组合解析；返回结果按 `max_combinations` 做最后一道
     截断兜底。新增 `ExperimentDesignError` 异常类型（同
     `RetrospectiveError` 的一贯设计）。
  2. **`workflows/experiment_design.yaml`**：新增 workflow 定义，
     `type: agent`（同 `retrospective.yaml` 的取舍——通用推理能力，
     不依赖具体场景模板），prompt 要求 LLM 先为每个不确定字段想象
     2~3 个有区分度的候选取值，再从中挑组合，最终只回复一个 JSON
     对象；范围克制段落写明"不是严谨正交实验设计，不需要穷举/计算
     信息增益"。
  3. **`app.py`**："让系统建议关键不确定性"折叠区里，在原有"选一个
     字段分叉假设世界"手动交互之前新增一层"💡 让系统建议一组实验
     设计"：点击后展示建议组合列表（每组带复选框 + "为什么"说明），
     用户勾选后点击"采纳选中组合到假设列表"，只是把选中组合拼成的
     假设方向文本回填到下方文本框，**不自动触发分叉**——用户仍然
     需要照原有路径点击"运行假设世界"才会真的调用
     `run_hypothesis_worlds()`；用户也可以完全不点建议按钮，直接
     手动填写文本框，原交互路径不变。
  **范围克制（按方案要求，不做的部分）**：不做完整的 Experiment
  Design Engine（统计学意义上的正交实验设计/最大信息增益）；不做
  "自动触发分叉"，分叉本身有真实计算成本，必须用户点击确认。
  **验收**：新增 6 个测试用例（`tests/test_hypothesis.py`），覆盖
  `uncertainties` 为空时不发起调用、workflow 输入正确组装
  （`uncertainties_json`/`max_combinations`）、返回结果正确解析为
  建议列表、格式不对的组合项被跳过、结果按 `max_combinations` 截断、
  workflow 定义缺失/执行未成功时正确抛出 `ExperimentDesignError`。
  加上原有 427 个，全部通过（**433 passed**）。用户未确认前不触发
  `run_hypothesis_worlds()` 这一条属于纯 UI 交互行为，按方案第 4 节
  验收点说明以代码走查（`app.py` 新增代码块里"采纳"按钮只回填文本框、
  分叉调用仍绑定在原有"运行假设世界"按钮上）确认，不是自动化测试能
  替代的部分。
  **后续节奏（按方案原文第 1 节）**：批次四（因果线可视化整合）
  依赖批次一的 `evidence` 字段（已具备），建议下一步推进；批次五/
  六留待之后按顺序推进。
- 2026-09-21（同日再追加）：**阶段三十七第四批**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 5 节，因果线可视化整合（"🕸️ 关系图"
  图形化子视图）。
  1. **`app.py`**：`_render_causal_graph_section()`（"🔗 跨线影响
     关系"折叠区）新增 `st.radio` 切换"📋 列表"（原有文字聚合视图，
     不变）/"🕸️ 关系图"两种查看方式，不替换原有视图，用户可以随时
     切回列表。"关系图"用 `st.graphviz_chart()` 渲染
     `causal_graph.build_causal_graph()` 边集合转换出的 DOT 图——
     节点是因果线 id，边上标注聚合过的 `relation_type` 次数，和列表
     视图统计口径完全一致（新增纯函数 `_causal_graph_edges_to_dot()`
     负责这层转换，正确转义节点名里的双引号/反斜杠，避免生成非法
     DOT 语法）。图下方新增节点选择下拉框，选中某个节点后调用新增的
     `_render_causal_graph_node_detail()`：
     - 如果这个节点是 `settings.objectives` 里注册过的目标字段，
       展示 `attribution.summarize_contributions()` 的贡献拆解报告
       （复用已有归因引擎，不发起任何新的 LLM 调用）；
     - 如果第八轮批次一新增的 `evidence`/`valid_range` 字段已具备，
       额外展示这个节点参与过的因果关系在知识库里精确匹配到的
       `evidence`/`valid_range`（按 `cause`/`effect` 精确匹配，
       不引入模糊/语义相似度匹配，同项目一贯取舍）；
     - 两层都没匹配到时，退化展示这个节点参与过的因果关系示例
       （不强行凑一份报告）。
  2. **`requirements.txt`**：新增 `graphviz>=0.19.0`——
     `st.graphviz_chart()` 渲染 DOT 字符串需要这个包，不装的话只有
     "关系图"这一个子视图会报错，其它功能不受影响。
  **范围克制（按方案要求，不做的部分）**：不引入 NetworkX/D3.js 等
  重量级图计算库或前端可视化框架，用 Streamlit 内置的
  `st.graphviz_chart()` 加一个纯字符串拼接函数就完成图形渲染；不做
  力导向自动布局优化（交给 Graphviz 默认布局）；节点详情的知识库
  匹配保持精确匹配，不做语义相似度。
  **验收**：新增 `tests/test_causal_graph_visual.py`，5 个测试用例
  覆盖 `_causal_graph_edges_to_dot()`：空列表返回空图、节点/边标签
  正确生成、节点名含双引号反斜杠时正确转义、共享节点去重、特殊占位符
  `(未归属)` 正常处理。这是这一批唯一自动化测试覆盖的部分——方案
  原文明确"图是否可读需要人工过一遍"，节点详情展开（贡献拆解/知识库
  匹配）的正确性依赖真实 Streamlit 会话状态，留给人工走查。加上原有
  433 个，全部通过（**438 passed**）。
  **后续节奏（按方案原文第 1 节）**：批次五（较大改动，决策引擎/
  多轮反事实对比整合）、批次六留待之后按顺序推进。
- 2026-09-21（同日再追加）：**阶段三十七第五批**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 6 节，多主体 Entity/Relationship：从
  自由文本到结构化连通图。
  1. **新增 `multi_entity.py`**：`build_entity_graph(relationships_
     raw, entities) -> Dict[str, List[str]]`——遍历
     `settings.relationships`（复用 `relationship.normalize_
     relationships()` 清洗），把 `from`/`to` 都能在 `entities`（
     `vars.entities` 字典本身或 id 可迭代对象）里找到对应 id 的
     记录，构造一个**无向**邻接表；匹配不上真实 entity 的记录静默
     跳过，不阻断不报错；同一对实体间多条关系去重成一条边；自环
     跳过但节点仍保留。签名和方案原文稍有出入：接收
     `relationships_raw`/`entities` 两个参数而不是单个 `manifest`
     ——排查后确认 `entities` 实际存放在 `SimState.vars`（不是
     `SimManifest`），两参数签名更直接、更容易单测，模块 docstring
     里说明了这个偏差和原因。`find_relationship_path(graph,
     start_id, end_id, *, max_depth=3)`——简单 BFS 找最短连通路径，
     `start_id == end_id` 返回单节点路径，找不到/超出 `max_depth`
     返回 `None`；不做加权最短路径，`strength`/`reversible` 不参与
     计算。
  2. **`app.py`**：新增 `_render_entity_relationship_path_tool()`，
     在多主体实例详情页"关键变量"展示之后新增一个"🔗 关系路径查询"
     折叠区——`multi_entity_mode` 未开启、`vars.entities` 为空、或
     图为空（没有任何关系能匹配上真实 entity）时静默不渲染；否则
     提供两个下拉框选起点/终点实体，展示 `find_relationship_path()`
     的结果。**只展示给用户看，不反过来影响推进 prompt**——本批次
     不做"把路径信息自动喂给 `advance_step`"这一层。
  **范围克制（按方案要求，不做的部分）**：不做加权最短路径/传递闭包
  之类更复杂的图分析；不做"路径信息自动接入 prompt"；不引入图数据库
  或专门的图计算库，标准库 `collections.deque` 就够用。
  **验收**：新增 `tests/test_multi_entity.py`，15 个测试用例覆盖
  `build_entity_graph()`（基本无向邻接、跳过未知实体、接受
  dict/可迭代两种 `entities` 输入、空输入、去重、自环、脏数据
  跳过）和 `find_relationship_path()`（最短路径、直接相邻、起终点
  相同、不可达、节点缺失、`max_depth` 生效、多条路径时选更短的）。
  加上原有 438 个，全部通过（**453 passed**）。
  **后续节奏（按方案原文第 1 节）**：批次六（Branch Engine merge +
  skill/prompt 版本记录，建议放最后）是六个批次里最后一个，可以
  继续推进。
- 2026-09-21（同日再追加）：**阶段三十七第六批（收官）**——按
  `next_doc/world_simulator_c_category_precision_upgrade_
  improvement_plan.md` 第 7 节，Branch Engine：merge + skill/prompt
  版本记录。至此六个批次全部完成，`world_simulator` 第八轮升级方案
  收官。
  1. **`branch_manager.py`**：新增 `merge_branch(data_dir, sim_id, *,
     source, target, from_step)`——退化为"指针切换"而不是真正的
     字段级三路合并（原因见上游盘点文档 4.6 节，两条分支的 `vars`
     可能已经不可调和地分歧，自动合并大概率产生语义错误的结果）：
     把 `target` 分支从 `from_step` 之后的历史替换成 `source` 分支
     从 `from_step` 之后的历史。**硬性前置条件**：`from_step` 及
     之前两条分支历史必须逐步（`step` 序号 + `to_dict()` 内容）
     完全一致，否则拒绝执行并抛 `BranchError`，不猜"该听谁的"。
     `source`/`target` 相同、任一分支不存在、`from_step` 为负数、
     任一分支缺少 `step <= from_step` 的历史都视为非法输入直接拒绝。
     合并后如果 `target` 恰好是当前活跃分支，同步刷新
     `manifest.current_step`（同 `switch_branch()` 的一贯取舍）；
     `pilot_mode`/`autopilot` 是每条分支独立存储的配置，合并历史
     不影响，不需要动。
  2. **`state_model.py`**：`SimState` 新增可选字段 `skill_version`
     （默认空字符串），docstring 说明"只是文件时间戳，不是语义化
     版本号"的范围克制，`from_dict()` 同步处理旧数据缺失该字段的
     兼容。
  3. **`engine/ids.py`**：新增 `_read_skill_version(workspace_root,
     template)`——读取对应模板 `skills/<skill_name>/SKILL.md` 的
     `os.stat().st_mtime`，格式化成 ISO 字符串；文件不存在/读取
     失败时静默返回空字符串，不抛错、不阻断推进。
  4. **`engine/advance.py`**：`advance()` 构造 `next_state` 时调用
     `_read_skill_version()` 填入 `skill_version` 字段。**范围
     确认**：只接入主 `advance()` 路径，独立推进路径
     （`advance_independent.py`，走的是 `workflows/line_evolve.yaml`
     而不是模板 skill 本身）和初始状态构造
     （`engine/materialize.py`）不在方案原文"涉及文件"范围内，
     未接入。
  **范围克制（按方案要求，不做的部分）**：不做真正的字段级合并
  算法（两边都有价值的内容需要人工判断冲突，不是自动合并能安全
  处理的问题）；不引入版本控制系统依赖，`skill_version` 只是文件
  时间戳；本批不新增 `app.py` UI 入口——方案原文这一批"涉及文件"
  明确只列了 `branch_manager.py`/`engine/advance.py`/
  `state_model.py` 三个后端文件，UI 留待后续按需接入。
  **验收**：新增 `tests/test_branch_manager.py` 8 个 `merge_branch()`
  测试用例（正确替换 target 后缀、`from_step` 之前分歧时正确拒绝、
  `source`/`target` 相同或缺失/负数时正确拒绝、任一分支缺少前缀
  历史时正确拒绝、合并后历史连续无空洞、`target` 是/不是活跃分支时
  `manifest.current_step` 分别正确更新/不受影响）；新增
  `tests/test_skill_version.py` 7 个测试用例（正确读取真实文件
  mtime、文件不存在/模板不存在时安全返回空字符串、文件被修改后
  版本值跟着变、`SimState.skill_version` 序列化往返 + 旧数据兼容、
  `engine.advance()` 端到端集成用仓库里真实的 `life-sim-template/
  SKILL.md` 验证读到的时间戳完全一致）。加上原有 453 个，全部通过
  （**468 passed**）。
  **风险提示（供人工验收参考）**：方案原文标注本批"改动面最大"，
  建议在测试环境用几个真实分支实际验证 `merge_branch()` 之后再在
  生产数据上使用；`merge_branch()` 目前没有 `app.py` UI 入口，
  仅可通过直接调用模块函数使用（同 CLI/脚本场景），这是本批刻意
  留白的部分，不是遗漏。

至此，`world_simulator_c_category_precision_upgrade_improvement_
plan.md` 六个批次全部完成，全量测试套件从升级前的 414 个增长到
468 个，全部通过。

- 2026-09-21（同日再追加）：**补记缺失的变更记录**——按 `next_doc/
  world_simulator_eleventh_round_remaining_gaps_plan.md` 第 4 节。
  `SimState.problems`（`next_doc/world_simulator_problem_capability_
  gap_plan.md` 2.1 节，字段：`id`/`symptom`/`blocked_goal`/
  `missing_capabilities`/`status`）与 `SimState.capabilities_gained`
  （同方案 2.3 节，字段：`capability`/`enables`/`limitations`）两个
  字段在代码里落地时（早于第九轮理论差距分析文档写作时间）没有在
  本文件补一条变更记录，导致后续差距分析文档（第九轮、第十轮）要靠
  翻代码才能确认它们已经存在。本条为纯补记，不涉及任何代码改动，
  字段定义与取舍详见 `world_simulator/state_model.py` 对应 docstring。

- 2026-09-21（同日再追加）：**第十一轮验证债务：决定继续搁置**——
  按 `next_doc/world_simulator_eleventh_round_remaining_gaps_plan.md`
  第 1 节。三项验证（`belief_fields`/`beliefs` 分叉验证、Agent
  Preview 命中率验证、`independent_line_advance` 割裂感验证）本轮
  仍然没有跑——这已经是第三次被写进待办（`world_simulator_
  remaining_minimal_systems_gap_survey_plan.md` → 第九轮 → 本轮）。
  **搁置原因**：这三项验证的本质是"用真实的多步剧情推进 + 人工
  判断叙事是否合理"，需要交互式地实际运行 Streamlit 应用、反复调用
  真实 LLM 生成叙事内容并做主观判断，这类工作依赖交互式人工使用
  过程，不是本轮以自动化方式一次性执行能完成的任务，因此继续搁置，
  改为优先推进第 2 节里风险明确、可以直接实施验收的代码任务（2.1～
  2.4）。**这不是关闭这三项验证**，任何一位实际使用者在日常使用
  过程中留意验证步骤里描述的现象（认知与现实是否分叉、预览是否
  拦下过明显不合理的自动挡选择、独立线是否被晾置太久），随时可以
  把观察结论直接写回本文件或 `next_doc/world_simulator_eleventh_
  round_remaining_gaps_plan.md` 第 6 节，不需要重新走一遍立项流程。

- 2026-09-21（同日再追加）：**第十一轮 2.1**——按 `next_doc/
  world_simulator_eleventh_round_remaining_gaps_plan.md` 第 2.1 节，
  `desired_state` 动态提示（不自动改写，只做不打断流程的提示）。
  1. **`app.py`**：新增纯函数 `_should_prompt_desired_state_review
     (state)`——判断"当前展示的这一步"是否满足"`capabilities_gained`
     非空"或"某条 `problems` 状态是 `solved`/`transformed`"任一
     条件，只读 `advance()` 已落盘的字段，不新增任何持久化标记、
     不做"值不值得改"的启发式过滤。`page_detail()` 在渲染"当前状态"
     卡片之前，条件满足时展示一条 `st.info` 提示条 + "⚙️ 去看看"
     按钮；点击后把 `st.session_state["_jump_to_settings_desired_
     state"]` 置位并 `st.rerun()`，"⚙️ 模拟设置（候选方向数量 /
     时间粒度）"折叠区读取并消费这个一次性标记，作为 `expanded=`
     参数值展开自身（消费后立即弹出该 key，不做"已读/已处理"状态
     追踪，同方案原文"范围克制"要求一致——用户看到但没点，下一步
     再满足条件时继续提示是可以接受的）。
  2. **`tests/test_desired_state_review_hint.py`**（新文件）：8 个
     测试，覆盖空/缺省字段不触发、`capabilities_gained` 非空触发、
     `problems.status` 为 `solved`/`transformed` 触发、
     `emerging`/`active` 不触发、格式不对的条目被跳过不报错、多条
     `problems` 中只要有一条满足即触发。
  **验收**：新增 8 个测试，加上原有的全部通过（**511 passed**）；
  `streamlit run app.py --server.headless true` 冒烟测试通过
  （HTTP 200，无异常日志）；人工验证建议：实际推进出现一次能力/
  问题状态变化，确认提示条出现且点击后设置区展开。
  **风险确认**：纯 UI 提示 + 一个纯函数，未改动任何数据结构或
  引擎逻辑，符合方案标注的"低风险"评估。

- 2026-09-21（同日再追加）：**第十一轮 2.4（跳过人工验证，用户
  明确要求直接接入）**——按 `next_doc/world_simulator_eleventh_
  round_remaining_gaps_plan.md` 第 2.4 节"做法"，把历史累计的
  `capabilities_gained` 接入生成 `options` 的 prompt 输入侧。
  **决定记录**：方案原文明确建议"先在测试实例上人工小范围验证喂
  能力清单是否真的提升选项质量，不理想就不接入正式 prompt"，本轮
  用户在确认后明确要求跳过这一步、直接接入正式 prompt——如实记录
  这是主动跳过方案建议的验证节奏，不是本轮判断"不需要验证"。
  1. **`world_simulator/spec_generator.py`**：新增
     `resolve_capabilities_hint(history)`，按 `capability` 字段
     精确字符串匹配去重（复用第十一轮 2.3 节的既有取舍），把每项
     能力的最新记录（含 `maturity_stage`/`enables`）拼成一段"已获得
     能力"提示；没有任何历史累计记录时返回空字符串（新实例/还没
     获得过能力时 prompt 组装行为与改动前完全一致）。措辞明确"仅
     作参考，不强制"，不做"能力→可选行动"的结构化自动映射表（同
     方案"刻意不做的部分"）。
  2. **`world_simulator/engine/advance.py`**：`advance()` 组装
     `shared_inputs` 时新增 `capabilities_hint`（复用已加载的
     `history_for_prompt`，避免重复调用 `store.load_history()`），
     单次调用（`advance_step.yaml`）和拆分调用（`world_evolve.yaml`
     + `decision_generate.yaml`）两条路径都共享同一份
     `shared_inputs`，天然都能拿到这个新字段。
  3. **`workflows/advance_step.yaml`/`workflows/decision_generate.
     yaml`**：`options` 生成段落前补充 `{capabilities_hint}` 占位符
     及说明文案，明确要求"候选行动应该体现已获得能力带来的新可能
     性，而不是忽略它们"；`world_evolve.yaml` 不产出 `options`，
     不需要这个提示，未改动。
  **验收**：新增 10 个测试——`tests/test_capabilities_hint.py`
  （`resolve_capabilities_hint()` 纯函数单测 8 个：空历史/字段缺省/
  格式非法跳过/精确匹配去重保留最新/不模糊合并/`enables`+未知阶段
  取值原样展示/兼容 `SimState` 对象/措辞含"仅供参考"）、
  `tests/test_spec_and_engine.py` 新增 2 个集成回归测试（新实例
  `capabilities_hint` 为空字符串；上一步落盘的能力记录体现进下一次
  推进的 `capabilities_hint`），加上原有的全部通过（**537
  passed**）。至此第十一轮计划第 2 节全部代码任务（2.1～2.4）
  完成。

- 2026-09-21（同日再追加）：**第十一轮 2.3**——按 `next_doc/
  world_simulator_eleventh_round_remaining_gaps_plan.md` 第 2.3 节，
  Capability 生命周期字段：`capabilities_gained` 新增可选字段
  `maturity_stage`，对照参考文档第十二、十三、十六节"能力应该有
  阶段性生命周期"，精简成六段枚举，纯存储 + 展示，不做自动推进。
  1. **`state_model.py`**：`SimState.capabilities_gained` docstring
     补充 `maturity_stage` 六选一说明：`lab`/`expert`/`developer`/
     `consumer`/`cheap_at_scale`/`infrastructure`（参考文档九段里
     最后两段"大规模应用"和"社会常态化"合并成 `infrastructure`
     一段）。不认识的取值原样保留、不校验，同 `problems.status` 的
     既有取舍一致；不填表示"这一步没有明确判断出所处阶段"。
  2. **`app.py::_collect_capability_maturity_timeline()`**（新增）：
     `_collect_problem_graph_nodes()` 的姐妹实现，按 `capability`
     字段**精确字符串匹配**（不做模糊匹配/语义归并）遍历历史归并，
     聚合出每项能力按出现顺序排列的 `maturity_stage` 时间线。
  3. **`app.py::_render_capability_maturity_section()`**（新增）：
     "📈 能力成熟度时间线"只读折叠区，一个能力一行展示阶段演进
     （如"能力甲：实验室可行 → 开发者可用"），不引入 graphviz，
     位置紧跟"🕸️ 问题关系图"折叠区之后。
  4. **`app.py::_capabilities_gained_html()`**：这一步的能力标签
     追加 `maturity_stage` 中文后缀（有值时），如
     `🆙 能够自动生成周报 [开发者可用]`，未知取值原样展示、缺省不
     追加任何后缀。
  5. **`workflows/advance_step.yaml`/`workflows/world_evolve.yaml`**：
     `capabilities_gained` 相关 prompt 段落补充 `maturity_stage`
     六选一说明，判断不出来就留空。
  **范围克制（按方案要求，不做的部分）**：不做自动阶段推进算法——
  阶段变化完全由 LLM 在叙事里体现、由 skill 声明，代码只做存储和
  展示；不做跨步骤的能力模糊匹配/语义归并——按 `capability` 精确
  字符串匹配，"新能力 A"和"能力 A（升级版）"刻意展示成两个不同的
  能力，交给用户自己判断。
  **验收**：新增 10 个测试（`tests/test_capability_maturity_
  timeline.py`：聚合函数覆盖空历史/格式非法跳过/字段缺省/按名称
  精确匹配归并保序/不模糊合并/相同阶段重复不去重/多能力独立时间线
  7 个；`_capabilities_gained_html()` 后缀渲染覆盖已知阶段/未知
  取值原样展示/缺省不追加后缀 3 个），加上原有的全部通过（**527
  passed**）。旧数据兼容性：`capabilities_gained` 缺失
  `maturity_stage` 字段时聚合/展示都不报错，天然兼容。

- 2026-09-21（同日再追加）：**第十一轮 2.2**——按 `next_doc/
  world_simulator_eleventh_round_remaining_gaps_plan.md` 第 2.2 节，
  Causal Engine 字段扩展：`causal_links` 新增可选字段 `delay_steps`/
  `magnitude`，不做真正的传播计算，只是让记录更接近参考文档设想。
  1. **`state_model.py`**：`SimState.causal_links` docstring 补充
     两个可选字段说明：`delay_steps`（整数，隔几步生效）、
     `magnitude`（自由文本定性描述，如"轻微"/"明显"/"剧烈"）。
     `causal_links` 本身是自由字典透传存储，不需要改 `from_dict`/
     `to_dict` 代码，旧数据缺失这两个字段天然兼容。
  2. **`world_simulator/causal_graph.py`**：`CausalEdge` 新增字段
     `has_delay`（默认 `False`）；`build_causal_graph()` 聚合时，
     一条边只要有任意一次原始 `causal_links` 给出了能解析成正整数
     的 `delay_steps`，就标记该边 `has_delay=True`（解析失败/
     `<= 0`/缺省都不计入，同 `relationship.py::normalize_
     relationships()` 对 `delay_steps` 的既有取舍一致）。
  3. **`app.py::_causal_graph_edges_to_dot()`**：`has_delay=True`
     的边额外加 `style="dashed"`，在因果关系图上把"滞后影响"和
     "即时影响"区分开；不影响 `has_delay=False`（含历史数据没有这个
     字段）的边的默认线型。
  4. **`workflows/advance_step.yaml`/`workflows/world_evolve.yaml`**：
     `causal_links` 相关 prompt 段落补一句，如果能判断出来，顺带给
     `delay_steps`/`magnitude`，判断不出来留空，不强行编造——措辞
     风格同第十轮批次二对 `problem_discovery.yaml` 的改法。
  **范围克制（按方案要求，不做的部分）**：不做任何基于 `delay_
  steps` 的自动调度/提醒（比如"这条延迟因果关系还有几步生效，到时
  提醒用户"）——这会让"记录"变成"引擎逻辑"，超出本节范围；`magnitude`
  目前只随 `causal_links` 原样落盘展示，不参与 `causal_graph.py`
  的任何聚合或图上区分（方案原文只要求 `delay_steps` 影响线型）。
  **验收**：新增 6 个测试（`causal_graph.py` 聚合逻辑覆盖有/无
  延迟、聚合多条链只要有一条延迟即标记、`delay_steps` 为 0/非数字/
  负数时不计入；`_causal_graph_edges_to_dot()` 覆盖虚线样式/默认
  线型两种场景），加上原有的全部通过（**517 passed**）。旧数据
  兼容性：`causal_links`/`CausalEdge` 缺失这两个字段时解析/展示都
  不报错，天然兼容。

- 2026-09-21（同日再追加）：**第十轮批次一**——按 `next_doc/
  world_simulator_tenth_round_problem_discovery_automation_plan.md`
  第 3 节，Problem Discovery Engine 从"手动挡"到"引擎自动运行"：
  扫描潜在问题不再要求用户记得去点按钮，手动挡/自动挡都能按周期
  自动触发。
  1. **`problem_discovery.py`**：新增 `_safe_auto_scan_problems()`——
     按 `manifest.settings["problem_discovery_auto_scan_interval"]`
     （整数，默认 0=关闭，向后兼容）周期触发一次 `suggest_
     problems()`，结果写入 `settings["last_auto_problem_scan"]`
     （`{"step", "source": [sim_id, branch], "suggestions",
     "scanned_at"}`）；自动挡场景（`auto_confirm=True`）额外自动
     把建议写入 `settings["confirmed_problem_suggestions"]`（自动挡
     下没有人来点"确认关注"，这一步等价于代理替用户做了这个操作）。
     任何异常吞掉，不影响本次推进——写法同 `engine/knowledge.py`
     里 `_safe_record_causal_links`/`_safe_evaluate_reflexivity` 的
     既有"安全包装"约定。
  2. **`engine/advance.py`**：`advance()` 末尾、`store.save_
     manifest(manifest)` 之前调用 `_safe_auto_scan_problems()`，
     用既有信号 `effective_chosen_by == "autopilot"` 区分手动挡/
     自动挡——**不需要改动 `autopilot.py`**，手动挡和自动挡都经过
     `advance()` 这一个入口，这是本批相比方案原文设想更省改动面的
     实现方式（原方案设想在 `autopilot.py` 里单独再接一次，实地
     实现时发现不需要）。
  3. **`app.py`**：\"⚙️ 模拟设置\"新增\"问题自动扫描间隔\"数字输入框
     （0=关闭）；\"🔍 扫描潜在问题\"折叠区在本次会话还没手动点过按钮
     时，自动展示上一次自动扫描的结果（不重新发起 LLM 调用），手动
     按钮保留，两者不是替换关系。
  **范围克制（按方案要求，不做的部分）**：不做"每一步都自动扫描"
  的默认行为（`interval` 默认仍是 0）；不做"根据剧情复杂度动态调整
  扫描频率"的智能节奏（留给批次三讨论）；自动扫描结果仍然只是下一次
  `advance_step` prompt 里的一句 hint，不直接改写任何已落盘的
  `SimState.problems`，"建议而非强制"这条项目底线没有被突破。
  **验收**：新增 5 个 `_safe_auto_scan_problems()` 单元测试（覆盖
  `interval=0`/未到间隔时不触发、手动挡只记录不自动确认、自动挡
  自动确认、内部异常被吞掉），加上此前已有的全部用例，全量测试套件
  跑通，全部通过（**488 passed**）。
  **后续节奏**：批次二（Problem 结构化字段 `depends_on`/
  `root_causes`/`candidate_solutions`）待实施，依赖批次一但可以
  并行开发；批次三（问题图可视化 + 可选信号触发）待批次二落地后
  再排期。

- 2026-09-21（同日再追加）：**第十轮批次二**——按 `next_doc/
  world_simulator_tenth_round_problem_discovery_automation_plan.md`
  第 3 节，Problem 结构化字段：`depends_on`/`root_causes`/
  `candidate_solutions`。
  1. **`state_model.py`**：`SimState.problems` 每项新增三个可选
     字段（docstring 补充说明；`problems` 本身是 `List[Dict[str,
     Any]]` 透传存储，不需要改 `from_dict`/`to_dict` 代码——旧数据
     缺失这三个字段天然兼容）：`depends_on`（字符串数组，引用其它
     问题）、`root_causes`（字符串数组，1~3 条短语）、`candidate_
     solutions`（字符串数组，1~3 条短语，不是 `ChoiceOption`）。
  2. **`workflows/problem_discovery.yaml`**：prompt 更新，要求 LLM
     在给出 observed/latent 建议时，如果能判断出来，顺带给这三个
     字段；判断不出来给空数组，明确要求不强行编造。
  3. **`problem_discovery.py`**：`suggest_problems()` 的 `_parse_
     list()` 解析这三个新字段（找不到时退化为空数组）；
     `adopt_problem_suggestion()` 新增同名可选参数并透传进
     `confirmed_problem_suggestions`；`_safe_auto_scan_problems()`
     自动挡自动确认时同样透传这三个字段。
  4. **`app.py`**："🔍 扫描潜在问题"建议列表在有值时展示"根因/候选
     方向/依赖"三行说明；"确认关注"按钮把这三个字段一并传给
     `adopt_problem_suggestion()`。
  **范围克制（按方案要求，不做的部分）**：不做"问题自动判重/合并"；
  不做根因的自动推断算法；`depends_on` 引用的 `id`/描述是否真的
  存在不做强制校验——同 `problems.id`/`causal_links`/
  `relationships` 现有取舍一致，纯声明式，"提示而非强制"。
  **验收**：新增 6 个测试（`suggest_problems()` 透传新字段且缺省时
  退化为空数组、`adopt_problem_suggestion()` 带结构化字段/默认空
  数组两种场景、自动挡自动确认时结构化字段一并透传），加上原有的
  全部通过（**492 passed**）。
  **后续节奏**：批次三（问题图可视化 `depends_on` 连边 + 可选信号
  触发的扫描节奏）依赖本批次的 `depends_on` 字段，待实施。

- 2026-09-21（同日再追加）：**第十轮批次三**——按 `next_doc/
  world_simulator_tenth_round_problem_discovery_automation_plan.md`
  第 3 节，问题图可视化（第十轮"必做"部分到此全部完成；"可选加强"
  的信号触发扫描节奏按方案原文建议暂不做，见下方说明）。
  1. **`app.py`**：新增"🕸️ 问题关系图"只读折叠区，放在"🔍 扫描潜在
     问题"折叠区旁边。复用 `_causal_graph_edges_to_dot()`
     （第八轮批次四）已验证过的实现思路：
     - `_collect_problem_graph_nodes(history)`：遍历当前分支历史
       里所有 `problems`，按 `id` 去重、保留最新 `status`；没有
       `id` 的条目各自独立展示，不参与去重。
     - `_problem_graph_edges_to_dot(nodes)`：节点列表 → Graphviz
       DOT 字符串，节点按 `status`（emerging/active/solved/
       transformed）上色，边由 `depends_on` 生成，**不校验**引用
       是否真实存在——同 `depends_on` 字段本身"提示而非强制"的
       取舍一致。
     - `_render_problem_graph_section(history)`：用
       `st.graphviz_chart()` 渲染，没有任何结构化问题记录时不
       渲染图（同因果线关系图的既有取舍）。
  2. **`tests/test_problem_graph_visual.py`**（新文件）：11 个测试，
     覆盖节点收集（去重/保序/无 id 不合并/跳过格式不对的条目）和
     DOT 转换（空图/节点着色/边/长文本截断/未知 status 兜底色/
     引用不存在的 key 不报错/空 `depends_on` 条目被跳过）。
  **明确不做的部分（按方案原文建议，不是遗漏）**：可选加强的"信号
  触发扫描节奏"（`resource_guard.py` 检测到资源跌破下限、或
  `ChoiceOption.urgency == "critical"` 时额外触发一次扫描）——方案
  原文建议"先观察批次一固定间隔在实际使用中是否已经够用，避免同时
  改两个变量导致'扫描到底有没有用'这个问题更难判断清楚"，本轮不做，
  不是本轮的强制交付物。
  **验收**：新增 11 个测试，加上原有的全部通过（**503 passed**）。
  至此第十轮问题清单（`next_doc/world_simulator_tenth_round_
  problem_discovery_automation_plan.md`）的三个批次全部完成，"信号
  触发扫描节奏"这一个可选加强项留待后续观察真实使用效果后再决定。

- 2026-09-22：**第十二轮第 1 节**——按 `next_doc/world_simulator_
  twelfth_round_exploration_and_deferred_directions_plan.md` 第 1
  节，Exploration Mode（批量分支探索）。
  1. **`branch_manager.py`**：新增 `explore_branches()`——给定
     `sim_id`/`from_step`/`source_branch`/一组"路线"
     （`choice_option_id` 或 `custom_option`），对每条路线依次
     `fork_branch(switch=True)` → `engine.advance()` → 切回原分支，
     互相独立、失败路线不留半成品分支（`delete_branch()` 清理）。
     纯粹是在已有 `fork_branch`/`advance`/`delete_branch`/
     `switch_branch` 之上的编排，不重复实现这几个函数已有的逻辑。
     新增 `ExploreRouteResult` dataclass 描述单条路线的结果
     （`route_label`/`ok`/`branch`/`error`）。
  2. **`app.py`**："推进下一步"候选选项列表下新增"🧭 批量探索所有
     候选"折叠区：路线来源可选"当前候选选项"或"问题的候选解决方向"
     （`problems[].candidate_solutions`）；执行前需要勾选"我确认要
     发起 N 次 LLM 调用"才能点击"开始批量探索"（同 `fast_forward`
     一贯的成本确认交互）；探索完成后把成功的分支自动加入"对比
     视图"的 `compare_selection`，并展示每条路线的成功/失败结果。
  **范围克制（按方案要求，不做的部分）**：不做多步递归自动探索
  （只做向前一步）；不做"自动结束/自动评分选出最优分支"；不做
  "探索出来的分支自动合并回主线"。
  **验收**：新增 `tests/test_explore_branches.py`（4 个测试：全部
  成功/部分失败不留半成品分支/候选路线为空报错/`custom_option`
  路线），加上原有的全部通过（**545 passed**）。

- 2026-09-22（同日再追加）：**第十二轮第 2 节**——按 `next_doc/
  world_simulator_twelfth_round_exploration_and_deferred_directions_
  plan.md` 第 2 节，多主体各自的 Desired State。
  1. **`state_model.py`**：`desired_state` docstring 补充新的可选
     顶层 key `per_entity`（字典，键是主体名字，需与
     `vars.entities` 一致，值是同样的 conditions/constraints/
     assumptions 三段式结构）；只有 `multi_entity_mode == True` 时
     才有意义，纯文档说明，`SimState`/`SimManifest` 不需要改任何
     `from_dict`/`to_dict` 代码——`desired_state` 本身透传存储。
  2. **`spec_generator.py`**：`_resolve_desired_state_hint()` 拆出
     公共的 `_format_desired_state_triplet()` 辅助函数（整体提示和
     每个主体的提示共用同一段三元组拼接逻辑）；`multi_entity_mode
     == True` 且声明了 `per_entity` 时，额外逐主体列出各自的理想
     状态，并附一句"不同主体的理想状态可能互相冲突……候选行动可以
     体现这种冲突或权衡"；`multi_entity_mode == False` 时
     `per_entity` 即使被写入也不出现在提示里。
  3. **`app.py`**："模拟设置"→"高级：理想状态"折叠区：开启多主体
     模式时额外提示 `per_entity` 用法+示例+已声明的背景主体名字，
     复用同一个 JSON 编辑框（不新增控件，`update_settings()` 原样
     透传 `desired_state` 整个字典，不做字段级解析）。
  **范围克制（按方案要求，不做的部分）**：不做主体间目标冲突的
  自动检测/裁决算法；不要求每个 entity 都填 `per_entity`；不做
  "理想状态随时间自动演化"。
  **验收**：`tests/test_spec_and_engine.py` 新增 3 个测试
  （`per_entity` 需要 `multi_entity_mode` 才生效、与全局
  `conditions` 共存、跳过非法条目），加上原有的全部通过
  （**544 passed**）。

- 2026-09-22（同日再追加）：**第十二轮第 3 节**——按 `next_doc/
  world_simulator_twelfth_round_exploration_and_deferred_directions_
  plan.md` 第 3 节，Reality Renderer 独立分层 + 五层输出框架 +
  First Possible Event（合并处理的最小可行版本，不新建独立渲染层/
  计算引擎）。
  1. **`state_model.py`**：`capabilities_gained[]` docstring 补充
     三个新的可选字段：`first_occurrence`（布尔，标注"这是不是这次
     模拟历史上第一次达成"）、`behavior_change`（一句话，对应五层
     框架 Behavior 层）、`structural_impact`（一句话，收敛
     Impact/Structure 两层）。`capabilities_gained` 本身透传存储，
     不需要改 `from_dict`/`to_dict` 代码。
  2. **`app.py`**：
     - `_capabilities_gained_html()` 更新：`first_occurrence ==
       True` 时图标从 🆙 换成 ⭐ 并追加"（首次达成）"字样；
       `behavior_change`/`structural_impact` 非空时作为补充说明
       加进展开详情。
     - 新增 `_collect_first_occurrence_milestones()`/
       `_render_first_occurrence_milestones()`，渲染"⭐ 首次达成的
       里程碑"只读折叠区，紧跟"能力成熟度时间线"之后，与
       `achievements.py` 固定徽章机制并列展示、互不联动。
  3. **`workflows/advance_step.yaml`**：`capabilities_gained` prompt
     段落补充三个新字段的判断依据，明确"不确定就不填，不要为了
     凑内容而每步都编造"；`world_evolve.yaml` 通过既有的"格式同
     单次调用版本"引用自动继承，不需要重复改。
  **不做的部分（明确记录，按方案要求）**：不新建"内部状态→结构化
  事件→渲染"的中间表示层；`narrative`/`next_vars` 继续在同一次 LLM
  调用里一起产出，不做强制分离渲染；不做"检测 narrative 和 vars
  是否对得上"的一致性校验；不做自动判断"是不是第一次"；不和
  `achievements.py` 的固定徽章机制合并或产生联动。
  **验收**：新增 `tests/test_first_occurrence_milestones.py`
  （8 个测试：三个新字段缺省/部分声明/全部声明、⭐ 标记与首次达成
  字样、里程碑聚合按出现顺序/跳过非法条目/无记录返回空列表），
  加上原有的全部通过（**552 passed**）。

- 2026-09-22（同日再追加）：**第十二轮第 4 节**——按 `next_doc/
  world_simulator_twelfth_round_exploration_and_deferred_directions_
  plan.md` 第 4 节，制度/组织/技术类型区分（纯枚举字段扩展 + 展示层
  分组/筛选，不新建独立数据类型/数据表）。
  1. **`state_model.py`**：`capabilities_gained[]` docstring 补充
     可选字段 `capability_kind`，三选一枚举：`"technology"`（默认，
     技术类）/`"organization"`（组织类）/`"institution"`（制度类）。
     不认识的取值原样保留、不做校验（同 `maturity_stage` 既有取舍）；
     不填按 `"technology"` 兜底展示。`capabilities_gained` 本身透传
     存储，不需要改 `from_dict`/`to_dict` 代码。
  2. **`app.py`**：
     - 新增 `_CAPABILITY_KIND_LABELS`/`_CAPABILITY_KIND_ICONS`
       （🔧 技术 / 🏢 组织 / 📜 制度）。
     - `_capabilities_gained_html()`：`first_occurrence == False`
       时图标按 `capability_kind` 选择（不再是通用 🆙，缺省按
       `"technology"` 兜底为 🔧）；`first_occurrence == True` 时
       继续优先展示 ⭐，不与类型图标叠加。类型中文名追加进方括号
       后缀，与 `maturity_stage` 用" · "拼接展示。
     - `_collect_capability_maturity_timeline()`：每个节点新增
       `capability_kind`（取该能力最新一次记录的声明，缺省按
       `"technology"` 兜底）。
     - `_render_capability_maturity_section()`：每行按
       `capability_kind` 加对应图标；新增一个可选的类型筛选下拉
       （纯展示层交互，不影响底层数据）。
  3. **`workflows/advance_step.yaml`**：`capabilities_gained` prompt
     段落补充 `capability_kind` 三选一的判断依据和例子；
     `world_evolve.yaml` 对应段落同步补充字段列表引用。
  **不做的部分（按方案要求）**：不做"制度是如何形成的"过程建模；
  不强制每条能力都归类，不确定就用默认值 `technology`；不给三种
  类型各自加专属子字段，保持和其它 `capabilities_gained` 条目完全
  一样的字段集合。
  **行为变化说明**：本节改变了 `_capabilities_gained_html()` 的默认
  图标（第十一轮通用 🆙 → 按类型区分，缺省 🔧）以及"无 `maturity_
  stage` 时不展示任何方括号后缀"这条旧行为（现在总是展示类型方括号
  后缀）——同步更新了 `tests/test_first_occurrence_milestones.py`/
  `tests/test_capability_maturity_timeline.py` 里断言旧图标/旧无
  后缀行为的既有测试用例，属于本节预期内的展示层变化，不是回归。
  **验收**：`tests/test_capability_maturity_timeline.py` 新增 8 个
  测试（图标按类型选择、未知取值原样保留、first_occurrence 优先于
  类型图标、聚合结果 `capability_kind` 缺省兜底/取最新记录），加上
  原有的全部通过（**559 passed**）。

- 2026-09-22（同日再追加）：**第十二轮第 5 节**——按 `next_doc/
  world_simulator_twelfth_round_exploration_and_deferred_directions_
  plan.md` 第 5 节，技术/组织/制度协同演化观察（纯统计展示，依赖
  第 4 节 `capability_kind` 字段，不新增任何数据字段）。
  1. **`app.py`**：新增 `_collect_capability_kind_coevolution()`：
     遍历完整历史里所有 `capabilities_gained` 记录，按 `step` 排序
     后用一个滑动步数窗口（默认 3 步，闭区间 `[s - window + 1,
     s]`）检测窗口内是否同时出现至少两种不同 `capability_kind`，
     命中则记录一次观察（窗口起止 step、涉及的类型、涉及的具体
     记录），**不做任何"谁驱动了谁"的因果判断**。新增
     `_render_capability_kind_coevolution_section()`：渲染"🔀
     技术/组织/制度协同演化观察"只读折叠区，紧跟"能力成熟度时间线"
     之后、"⭐ 首次达成的里程碑"之前，明确标注"这只是时间上接近，
     不代表存在因果关系"；没有任何观察时展示占位提示，不为了
     "看起来有内容"而制造虚假关联。
  **不做的部分（按方案要求）**：不做"协同演化模式"的分类/推荐；不做
  自动触发机制/提醒（只在用户主动展开折叠区时才有意义地查看结果，
  计算本身无副作用）；不新增任何数据字段，完全复用第 4 节
  `capability_kind` 的统计结果。
  **验收**：新增 `tests/test_capability_kind_coevolution.py`
  （10 个测试：空历史/单一类型不产生观察、缺省类型按 technology
  归并、默认窗口内命中、窗口边界闭区间精确测试、自定义更宽窗口扩大
  检测范围、跳过非法条目、跳过 `step` 缺失的状态、三种类型同窗口
  全部报告），加上原有的全部通过（**569 passed**）。

- 2026-09-22（同日再追加）：**第十二轮第 6 节（第十二轮方案全部
  完成）**——按 `next_doc/world_simulator_twelfth_round_exploration_
  and_deferred_directions_plan.md` 第 6 节，Fact/Assumption/
  Hypothesis/Prediction 类型标签。现状核实发现第九轮文档对
  `knowledge_base.py::KnowledgeItem.confidence` 的描述已过时——
  `confidence` 实际已经是 `confirmed`/`supported`/`hypothesis`/
  `speculative` 四态离散枚举，不是连续值，参考文档想要的类型区分
  在因果知识库层面已经存在，本节相应缩小为纯展示层的中文标签映射，
  不新增数据字段、不重命名枚举值。
  1. **`app.py`**：新增 `_CONFIDENCE_LABELS` 映射表 +
     `_confidence_label()` 辅助函数（`confirmed`→"已证实事实"、
     `supported`→"有依据的判断"、`hypothesis`→"待验证假设"、
     `speculative`→"推测"，未知/空取值原样返回取值本身，不报错）。
     `page_knowledge()` 知识库浏览页的条目标题改用
     `_confidence_label(item.confidence)` 展示中文标签。
  **不做的部分（按方案要求）**：不新增独立的 `Prediction`/
  `Observation` 类型（`causal_graph_hint`/`declared_causal_graph`
  已经是两段分开展示的内容，本质上是朴素的 Observation vs
  Assumption/Prediction 区分，重复建一层类型标签收益不明确）；不做
  `confidence` 枚举值的重命名（避免破坏存量数据兼容性）。
  **验收**：新增 `tests/test_confidence_label.py`（7 个测试：四个
  已知枚举值都有对应中文标签、未知/空取值原样兜底、覆盖
  `_VALID_CONFIDENCE` 全部取值的回归测试），加上原有的全部通过
  （**576 passed**）。

**至此，`next_doc/world_simulator_twelfth_round_exploration_and_
deferred_directions_plan.md` 第 1～6 节全部完成**（第 1、2 节两个
新差距 + 第 3～6 节四个此前搁置、本轮用户明确要求推进的方向），详见
该文档各节末尾的实施记录。

- 2026-09-22（同日再追加，bug 修复）：修复 `app.py` 界面上方出现
  不该出现的说明性文字的问题。根因：`_MATURITY_STAGE_LABELS`/
  `_CAPABILITY_KIND_LABELS`/`_PROBLEM_STATUS_COLORS`/
  `_CONFIDENCE_LABELS` 四个模块级字典赋值语句后面，各跟着一段用
  三引号字符串写的"字段说明"（本意是仿照函数 docstring 的写法留
  注释），但这几段字符串是脚本**顶层**（不在任何函数/类内部）的
  裸字符串表达式语句——Streamlit 有一个"magic"机制，会把脚本顶层
  出现的裸表达式语句（尤其是字符串）自动当成 `st.write(...)` 调用
  渲染到界面上，这四段字符串因此被原样显示在页面最上方。其中
  `_PROBLEM_STATUS_COLORS`/`_MATURITY_STAGE_LABELS` 两处是第十、
  十一轮遗留的既有 bug，`_CAPABILITY_KIND_LABELS`/`_CONFIDENCE_
  LABELS` 两处是第十二轮第 4、6 节本轮新增时复制了同样的错误写法。
  **修复**：四处全部改成 `#` 行注释，不改变任何注释内容本身，只是
  换成不会被当成语句执行/渲染的写法。全部 576 个测试不受影响（这
  几段字符串本来就不在任何被测试断言覆盖的函数体内），行为验证
  改为人工确认：界面顶部/相关折叠区附近不应该再出现这几段中文
  说明文字。

- 2026-09-22（同日再追加，性能优化第一阶段）：详情页"目标多了就卡、
  采纳类操作也要等很久"的根因分析 + 第一阶段修复（低风险，纯缓存/
  展示层改动，不改变任何数据结构）。
  根因：1）`get_simulation()` 每次调用都全量反序列化
  `state_history.jsonl`，而 Streamlit 任何按钮点击都会触发整页
  `st.rerun()`，等于每次点击（哪怕跟历史无关）都重新解析一遍历史；
  2）问题关系图/能力成熟度时间线/协同演化观察三个"遍历完整历史"的
  只读折叠区，同样在每次 rerun 时无条件重新计算；3）问题关系图的
  `st.graphviz_chart()`（起 Graphviz `dot` 子进程做布局）不受折叠区
  展开/收起状态影响，每次 rerun 都会跑一次子进程——这是最贵的一步。
  修复：1）新增 `get_simulation_cached()`（`app.py`），用
  `st.cache_data` 缓存 `store.load_history()` 的结果，缓存 key 用
  `(sim_id, branch, 文件 mtime, 文件 size)`，文件没变直接复用；
  5 个调用点全部从 `get_simulation()` 切到这个缓存版本。
  2）`_collect_problem_graph_nodes()`/`_collect_capability_maturity_
  timeline()`/`_collect_capability_kind_coevolution()` 各自新增一个
  `_cached` 缓存包装，key 用 `(sim_id, branch, len(history), ...)`，
  `history` 本身用 Streamlit 的下划线前缀约定跳过哈希（避免"为了
  避免重算而先对整份历史算一遍哈希"这种没有净收益的开销）；三个
  对应的 `_render_*_section()` 改成接收 `sim_id`/`branch` 参数、
  优先调用缓存版本。3）问题关系图的 `st.graphviz_chart()` 改成
  behind 一个"🔄 生成/刷新问题关系图"按钮，配合 `st.session_state`
  记住"本次会话是否已经点开过"，不再是折叠区一出现就无条件渲染。
  **验收**：新增 `tests/test_perf_caching.py`（6 个测试：缓存包装
  函数结果跟未缓存版本一致、不同 `sim_id`/`window` 不会互相读到
  对方缓存、`get_simulation_cached()` 在文件真的改写后能读到最新
  内容），加上原有的全部通过（**582 passed**）。跨线影响关系图的
  Graphviz 渲染此前已经是"默认列表视图、切到「🕸️ 关系图」才渲染"，
  本身不受这次改动影响，不需要额外处理。
  **未做（留给后续阶段，需要先验证兼容性）**：探索用 `st.fragment`
  把某个折叠区的交互隔离成局部刷新单元，避免任何按钮点击都触发
  整页 `page_detail()` 重新执行——这是更治本的方案，但要先确认跟
  现有"自动挡连续推进"的 `st.rerun()` 续跑逻辑不冲突。
