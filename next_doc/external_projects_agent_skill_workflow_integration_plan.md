# stock_watch 接入 agent/skill/workflow 机制 + 框架路径约定修正

> 背景：`stock_watch` 作为"外部项目机制"第一个落地案例，此前只用到了
> `entrypoints/`（headless 单次执行）+ daemon cron 调度这两层，完全没有
> 实际用过 `external_projects_workspace_plan.md` 承诺的"外部项目可以有
> 自己私有的 `skills/`、`workflows/`"这条能力——这条能力此前只停留在
> 设计文档里，从未被验证过真的能跑通。本文档记录：(1) 用"个股 AI 综合
> 研判"这个真实需求把它跑通一次；(2) 过程中发现框架代码的实际路径解析
> 落后于设计文档，已按设计修正框架代码，不是反过来改文档迁就代码。

## 1. 一个关键澄清：外部项目执行 vs 普通交互式 agent 执行，路径约定不同

这是本次改动里最重要的一条结论，单独提出来：

- **普通交互式 agent**（用户在某个项目目录下直接跑 `mini-agent`，没有
  `project.yaml`）：私有 skill/workflow 目录沿用历史约定
  `<root>/.claude/skills/`、`<root>/.agent/workflows/`，**行为完全不变**。
- **外部项目**（`<root>/project.yaml` 存在，即
  `Workspace.project_yaml_path` 指向的文件真实存在）：私有 skill/workflow
  目录是 `<root>/skills/`、`<root>/workflows/`——对应
  `external_projects_workspace_plan.md` 5.1 节画的标准目录结构，也对应
  `Workspace.skills_dir`/`Workspace.workflows_dir` 这两个属性本来就是
  这么定义的。

两者用 `<root>/project.yaml` 是否存在来判定，而不是"是否显式传了
`Workspace` 对象"——这样即使外部项目的 entrypoint 用
`mini-agent workflow run ... --project <外部项目路径>` 这种 CLI 方式驱动
（并没有走 Python 里的 `Workspace` 类），只要那个路径下有
`project.yaml`，就能被正确识别为外部项目根，用对目录约定，不依赖调用方
记得构造 `Workspace` 对象。

### 1.1 发现问题的过程

按 `external_projects_workspace_plan.md` 5.1 节的目录树给 `stock_watch`
新增 `skills/stock-analysis-judge/`、`workflows/stock_analysis_ai.yaml`
后，读码核实实际生效路径时发现：

- `config/prompt_builder.py::_resolve_skills_dir(root)` 原来的候选目录
  顺序是 `<root>/.claude/skills` → `~/.agent/skills` →
  `~/.claude/skills`，**根本不包含 `<root>/skills`**。
- `workflow/store.py::WorkflowStore` 原来硬编码
  `WORKFLOWS_DIR = ".agent/workflows"`，**同样不认 `<root>/workflows`**。

也就是说，`Workspace` 类本身（`workspace.py`）已经正确定义了
`skills_dir`/`workflows_dir` 两个属性指向 `<root>/skills`、
`<root>/workflows`，但**框架里真正做路径解析的两处代码从未真正读取过
`Workspace` 这两个属性**——`Workspace.apply_to()` 目前只设置
`cfg.project_root`，`cfg.skills_dir` 依然是 `_resolve_skills_dir()` 按
自己的硬编码候选列表算出来的，两条线没接上。`阶段1` 遗留下的这个缺口，
此前没有被任何外部项目实际用到 skill/workflow 私有目录这件事覆盖到，
所以一直没暴露。

### 1.2 修正方式（改代码，不改设计文档迁就代码）

- `config/prompt_builder.py::_resolve_skills_dir(root)`：候选列表里加
  一条——若 `(root / "project.yaml").exists()`，把 `root / "skills"`
  作为**第一候选**插入列表最前面。没有 `project.yaml` 的目录，候选列表
  和原来完全一样，行为不变。
- `workflow/store.py::WorkflowStore.__init__`：构造时检测
  `(project_root / "project.yaml").exists()`；成立则
  `self._dir = project_root / "workflows"`，否则维持原来的
  `project_root / ".agent" / "workflows"`。
- 两处改动都用同一个判定标准（`<root>/project.yaml` 是否存在），和
  `Workspace.project_yaml_path` 的语义完全一致，没有引入第二套判断
  逻辑。
- **已验证**：构造一个临时目录，分别在"无 `project.yaml`"和"有
  `project.yaml`"两种情况下检查 `WorkflowStore._dir` /
  `_resolve_skills_dir()` 的返回值，符合预期（见变更记录）。仓库里其余
  引用 `.claude/skills`/`.agent/workflows` 的地方（`resource_bundle.py`
  的"workflow 文件夹模式本地资源包"等）不在本次改动范围——那些是
  workflow 自身文件夹内的资源包目录，不是本文档讨论的"项目级私有
  skill/workflow 目录"，语义不同，不应该被这次改动误伤，本次也确认
  未触碰。

### 1.3 尚未做、刻意留白的部分

`Workspace.apply_to()` 目前仍然只设置 `cfg.project_root`，没有显式设置
`cfg.skills_dir`——这次修的是"按 `project.yaml` 存在与否自动路由"这条
路径解析逻辑本身，能覆盖"CLI `--project` 直接指向外部项目根"这个最常见
的驱动方式（`run_stock_analysis_ai.py` 用的就是这条路径），不依赖调用方
构造 `Workspace` 对象。如果未来出现"需要在 Python 代码里显式构造
`Workspace` 并驱动一次性、不落地 `project.yaml`"的场景，再回来补
`apply_to()` 显式写 `cfg.skills_dir = self.skills_dir` 这一步，本次不
提前做（避免在没有真实用例的情况下猜测该覆盖到什么粒度）。

### 1.4 LLM 配置（api_key/provider/model）同样要从主项目继承

修完 1.2 节两处路径解析后，实测触发 `stock_analysis_ai` workflow 仍然
在 2 秒内就失败、`workflow run` 的 CLI 子进程 `returncode=0`（见变更
记录里记的复现现象）。定位到两个问题，这里记第二个（第一个见 1.5 节
"改用 `WorkflowRunner` 直接调用，不再依赖 CLI 子进程退出码"）：

`config/loader.py::load_config(project_root=...)` 读取 `providers.json`
（含 API key）/`agent_config.json` 时，只认 `<project_root>/providers.json`
——外部项目自己的目录下本来就没有、也不应该有这两个文件（外部项目关心
业务逻辑，不应该重复维护一份 API key），于是 `api_key` 兜底到环境变量，
如果主 agent 的 API key 是放在主项目的 `providers.json` 里而不是环境
变量里，外部项目这边就拿到空 `api_key`，`skill_agent` step 一上来初始化
LLM 客户端就失败，正好对应"跑得飞快、CLI 退出码却是 0"这个现象（CLI
退出码本来就不代表 workflow 是否成功，见 1.5 节）。

**第一版修正（已废弃，见下方修正记录）**：曾经从 `root.parent.parent`
按"外部项目固定挂在 `<主项目根>/external_projects/<name>/` 下"这条
目录布局约定去猜主项目根——这个假设是错的：外部项目的 `path` 可以在
磁盘任意位置（`ExternalProjectRegistry` 本身就没有对路径位置做任何
约束，`register()` 接受任意绝对路径），不能从目录结构反推。

**修正后的方案**：改用两条不依赖目录布局的信息源，按优先级：

1. **环境变量 `MINI_AGENT_MAIN_PROJECT_ROOT`**——daemon/scheduler 拉起
   外部项目的 entrypoint 子进程时可以显式设置这个环境变量，最直接、
   无需查表，也是唯一对"本次调用明确知道自己是被谁、以什么身份触发"
   这件事最敏感的信息源（适合未来 daemon 侧接线时用）。
2. **`ExternalProjectRegistry`（`~/.agent/external_projects.json`）
   反查**——`RegisteredProject` 新增 `main_project_root` 字段，
   `register(name, path, ...)` 时默认记录"执行注册命令那一刻的
   `Path.cwd()`"（`mini-agent projects register` 的既有用法就是在主
   项目目录下执行，这个默认值覆盖最常见场景；需要跨目录注册可以显式
   传 `main_project_root` 参数覆盖）。`load_config()` 里新增
   `ExternalProjectRegistry.find_by_path(root)`，按外部项目的实际路径
   反查注册表，拿到对应记录的 `main_project_root`，与 `root` 本身在
   磁盘哪个位置完全无关。

两条都没查到时返回 `None`，最终落到"环境变量兜底 api_key"这条既有
路径，不报错——即使外部项目从未注册过、也没设环境变量，行为退化为
"这次拿不到继承的配置"，而不是抛异常炸掉整个 `load_config()`。

`agent_config.json`/`providers.json` 的解析都改成"先看外部项目自己
目录下有没有 → 没有则用上面两条信息源找到的主项目根下的同名文件 →
都没有才落到环境变量"，外部项目自己有配置时优先级不变（仍然是自己的
优先），只在自己没有时才继承主项目的。已用临时目录验证：外部项目和
主项目分别放在互不相关的两个目录树下（不满足任何父子路径关系）时，
注册表反查和环境变量两条路径都能正确拿到主项目 api_key/provider；
外部项目有自身配置时优先用自己的；普通交互式目录（无 `project.yaml`）
完全不受影响。

**已注册过的外部项目怎么办**：这次改动前注册的条目（比如
`stock_watch`）不会自动有 `main_project_root` 字段（JSON 里读不到时
`from_dict` 给空字符串），需要二选一：(a) `mini-agent projects
unregister stock_watch` 后在主项目目录下重新 `register`，让新记录带上
这个字段；(b) 手动跑本 entrypoint 时临时设一下
`MINI_AGENT_MAIN_PROJECT_ROOT` 环境变量指向主项目根，不用重新注册。

### 1.5 另一处实测踩坑：CLI 子进程退出码不代表 workflow 成败

除 1.4 节记的 LLM 配置继承问题外，`run_stock_analysis_ai.py` 最初版本
还有一个独立问题：用 `subprocess` 调 `mini-agent workflow run` CLI，而
`run_workflow_cli()` 的既有约定是"命令本身有没有跑起来"和"工作流执行
结果好不好"是两回事——前台同步执行即使工作流内部某个 step 失败，CLI
进程退出码依然是 0（结果只体现在打印到 stdout 的摘要文本里，跨进程用
文本解析结构化结果本来就脆弱）。已改为直接在 entrypoint 进程内调用
`WorkflowRunner.run()`，拿到结构化的 `WorkflowRunResult`
（`status`/`step_results[].error`），不再依赖子进程退出码或文本输出
判断成败，失败原因也能准确记进账本的 `detail` 字段，便于事后排查。



`run_stock_analysis.py`（既有）只抓材料、不调 LLM。新增
`run_stock_analysis_ai.py`，补上"AI 综合研判"这一步，同时是本文档
第 1 节机制修正的验证载体：

```
run_stock_analysis_ai.py <code> [name]
  1. collect(code, name)                       # 确定性 Python，复用既有代码，抓材料
  2. subprocess: mini-agent workflow run stock_analysis_ai <inputs_json> --project .
       └── workflow: workflows/stock_analysis_ai.yaml   （<root>/workflows/，见第1节）
             step "judge"（type: skill_agent，skill_name: stock-analysis-judge）
               → 只挂载 skills/stock-analysis-judge/SKILL.md（<root>/skills/，
                 见第1节）的最小 Agent，读材料 JSON，产出结构化研判
                 （verdict/summary/key_signals/risk_points/
                 action_suggestion/report_markdown），写入 result_file
                 （校验失败自动 resume/重开重试，SkillAgentStepExecutor 既有机制）
             step "save_report"（type: tool_call，write_file）
               → 把 report_markdown 落盘到 reports/analysis/<code>_<run_ts>_ai.md
  3. 校验预期报告文件确实存在，作为 entrypoint 成功与否的判定依据
```

设计取舍：

- **抓取不进 workflow**：抓取是确定性代码，进 workflow 只多一层调度
  开销、无收益；只有"理解材料给出研判"这一步真正需要 LLM 判断力，只让
  这一段发生在 workflow/skill 里。
- **`stock-analysis-judge` skill 的判断原则**（详见
  `external_projects/stock_watch/skills/stock-analysis-judge/SKILL.md`）：
  材料不足要明说、区分事实摘要与推断、不给确定性买卖指令（只给
  `positive_lean`/`neutral`/`caution` 三档信号强度 + 非指令式建议）、
  股吧情绪权重明显低于公告/新闻。
- **`run_stock_analysis` vs `run_stock_analysis_ai` 怎么选**：前者只要
  材料、不想产生 LLM 调用成本时用；后者需要研判结论时用。两者都不带
  `schedule`（`project.yaml`），按需对具体标的触发，不适合无差别定时跑
  全市场个股。
- **并发闸门**：`stock_analysis_ai` 会触发 LLM 调用，和
  `external_projects_cron_dispatch_plan.md` §1.3 的既有取舍一致——
  不单独开新的调度通道，未来若接入 daemon 定时触发，天然复用同一份
  `CronJobRunner` 并发资源。

## 2. 看板「外部项目」页新增注销功能

此前 `apps/mini_agent_kanban/app.py` 的外部项目页只有"注册"和"切换自动调度开关"，没有"注销"——`ExternalProjectRegistry.unregister()`本来就有，只是没接 HTTP 路由和 UI，且此前一直遗留一个待办："项目从 registry 整体移除时要联动清理 `ext:*` cron job"（见`external_projects_cron_dispatch_plan.md` §3.3）。本次一并补上：

- 新增 `DELETE /v1/external_projects/{name}` 路由（`api/routes.py`）：先 `registry.unregister(name)`，再复用既有的`ensure_external_project_cron_jobs()`——其docstring 里"`enabled=False`（含...注册表里查无此项目）时的清空分支"本来就是为这个场景准备的，不需要新写清理逻辑，只是把它接上。
- `apps/mini_agent_kanban/client.py` 新增 `unregister_external_project(name)`。
- 看板每个项目卡片底部新增"⚠️ 危险操作"折叠区，里面的"🗑️ 注销此项目"按钮走跟 cron job 删除同一套二次确认交互模式（`confirm_unregister_ext_proj_<name>` session_state 标记 + "确认注销"/"取消"两个按钮），避免误触。文案明确说明"只删注册表记录，不删项目文件，之后可以重新注册接回来"，降低用户对这个操作的顾虑——外部项目本来就是"引擎的调用方"，注销只是让 daemon 不再管理它，不是销毁数据。

## 3. workflow / hybrid_exec 分别适合股票系统的什么功能（设计思考，本轮暂不实现）

结合 `stock_watch` 现有四大功能（热点候选池、K 线批量、条件选股、个股
分析）和候选池状态跟踪机制，梳理"这类需求该用 skill、workflow 还是
hybrid_exec"的判断依据，供后续迭代参考：

- **单纯确定性批处理**（K 线批量生成、候选池打分排序、结果回溯核对）：
  维持纯 Python entrypoint 现状，不需要 agent/workflow/hybrid_exec 任何
  一层——这类工作没有"需要判断力"的子任务，引入 LLM 只会增加不确定性和
  成本，`entrypoint 直接调库函数`已经是最优解，本次不改动。
- **单一"材料 → 判断"环节，且判断逻辑相对独立、可复用**（本次落地的
  个股综合研判）：一个 skill（描述判断原则/输出契约）+ 一个两步
  workflow（`skill_agent` 判断 + `tool_call` 落盘）足够，不需要更复杂的
  编排。
- **多步骤、有分支决策、且分支之间有先后依赖的场景**——最典型的候选：
  **候选池状态变更建议**（现状：`change_pool_state.py` 是纯人工触发，
  人自己判断该不该把某标的从 `watching` 推进到 `focused`/
  `buy_suggested`）。这类场景更适合 workflow 而不是单个 skill_agent
  的原因：判断本身需要"先取数据 → 按阈值粗筛 → 命中阈值的才值得让 LLM
  细看 → LLM 给结论 → 结论只是建议、最终仍要人工在看板上点确认"这样一条
  有先后关系、且中间有确定性剪枝步骤的链路——纯确定性的"取数 + 粗筛"
  部分没必要用 LLM（浪费调用、还引入不必要的不确定性），纯 LLM 单步
  判断又丢了"先用确定性规则剪枝，只把真正模棱两可的标的送去给 LLM 看"
  这层节省成本和噪音的价值，workflow 的多 step + `condition` 分支机制
  正好用来表达这条链路，而不是把"取数 + 粗筛 + LLM 判断"全塞进一个
  skill 的 prompt 里让它自己既算数又判断。
- **抓取源本身不稳定、经常需要在"确定性脚本失败后，让 agent 现场看
  网页结构变化临时补救"之间切换的场景**——`hybrid_exec` 的用武之地。
  `stock_watch` 的 `data_sources.py`/`iwencai_api.py` 已经因为反爬/改版
  多次踩坑（见 `source_health.py` 专门记录数据源级别成败），这类"确定性
  脚本为主，脚本连续失败到一定阈值后临时切到 agent 现场诊断/修复抓取
  逻辑"的模式，正是仓库里 `myplugins/hybrid_step.py` 示例演示的能力。
  比"每次抓取都决定要不要用 agent"更合适的落点是：把 hybrid_exec 接入
  `source_health.py` 已经在记录的"某数据源连续失败次数"信号——达到阈值
  才触发一次 agent 介入去现场排查/修复，而不是每次抓取都判断一次，这样
  能复用已有的健康度信号，不用新造一套触发条件。
- 以上两条（候选池状态变更建议 workflow 化、hybrid_exec 接入
  `source_health` 阈值触发）**本轮不实现**，原因和
  `external_projects_workspace_plan.md` 第7节"长期规划方向"同样的
  理由——目前只有"个股综合研判"这一个真实落地案例验证过
  skill/workflow 机制本身能跑通，候选池状态变更建议涉及"人工确认权
  怎么和 workflow 的自动推进权衔接"这类还没有具体使用反馈的设计问题，
  过早实现容易设计错，留到看到"研判功能实际用起来之后暴露出的真实
  需求"时再动手。

## 4. 变更记录

- 2026-08-30：
  - 修正 `config/prompt_builder.py::_resolve_skills_dir()`、
    `workflow/store.py::WorkflowStore.__init__()`，按
    `<root>/project.yaml` 是否存在区分"外部项目"（用
    `<root>/skills`、`<root>/workflows`）与"普通交互式 agent 目录"
    （维持 `<root>/.claude/skills`、`<root>/.agent/workflows` 不变）。
    已用临时目录验证两种场景下路径解析符合预期。
  - `external_projects/stock_watch` 新增：
    `skills/stock-analysis-judge/SKILL.md`、
    `workflows/stock_analysis_ai.yaml`、
    `entrypoints/run_stock_analysis_ai.py`；`project.yaml` 注册
    `stock_analysis_ai` entrypoint；`PROJECT.md` 补充"AI 综合研判"
    章节及目录结构/独立运行章节同步更新。
  - 第 3 节"workflow/hybrid_exec 分别适合什么功能"的设计思考已记录，
    本轮不实现，留作后续迭代依据。
- 2026-08-30（补记，实测报错后的修正，见 1.4/1.5 节）：
  - `config/loader.py::load_config()` 新增 `_resolve_main_project_root()`，
    外部项目（`<root>/project.yaml` 存在且挂在
    `<主项目根>/external_projects/<name>/` 下）解析
    `agent_config.json`/`providers.json` 时，自己目录下没有则回退到
    主项目根目录下的同名文件，不再要求每个外部项目自己维护一份含
    API key 的配置。已用临时目录验证三种场景（外部项目无自身配置时
    继承主项目 api_key/provider；外部项目有自身配置时优先用自己的；
    普通交互式目录不受影响）均符合预期。
  - `external_projects/stock_watch/entrypoints/run_stock_analysis_ai.py`
    改为直接调用 `WorkflowRunner.run()`（不再 `subprocess` 调
    `mini-agent workflow run` CLI），原因见 1.5 节——CLI 子进程退出码
    不代表 workflow 内部成败，直接拿结构化 `WorkflowRunResult` 更可靠，
    失败原因也能准确记进账本。
- 2026-08-30（再次补记，纠正上一条的错误假设）：上一条记录的
  `_resolve_main_project_root()` 最初实现是从 `root.parent.parent`
  按"外部项目挂在 `<主项目根>/external_projects/<name>/` 下"这条目录
  布局猜主项目根——用户指出这个假设不成立，外部项目路径可以在磁盘任意
  位置，`ExternalProjectRegistry` 本身就不对注册路径做任何位置约束。
  已改为不依赖目录布局的方案：`RegisteredProject` 新增
  `main_project_root` 字段（`register()` 时默认记录 `Path.cwd()`，也
  可显式传入），`ExternalProjectRegistry.find_by_path()` 按外部项目
  实际路径反查该字段；`load_config()` 优先读环境变量
  `MINI_AGENT_MAIN_PROJECT_ROOT`，其次查注册表，都没有则回退环境变量
  兜底 api_key 这条既有路径，不报错。已用"主项目和外部项目分别放在
  互不相关的两棵目录树下"这种明确不满足父子路径关系的场景验证过。
  详见 1.4 节。
- 2026-08-30（补记）：看板「外部项目」页新增注销功能，新增
  `DELETE /v1/external_projects/{name}` 路由、
  `client.py::unregister_external_project()`、项目卡片"⚠️ 危险操作"
  折叠区里的二次确认注销按钮，联动清理该项目名下所有 `ext:*` cron job，
  顺带补上 `external_projects_cron_dispatch_plan.md` §3.3 此前一直
  遗留的"项目从 registry 整体移除时清理 ext:* job"待办。详见第 2 节。
