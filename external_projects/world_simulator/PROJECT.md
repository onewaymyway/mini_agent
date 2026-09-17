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
