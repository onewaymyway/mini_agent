# 如何测试 world_simulator

本文档面向"想验证这个项目能不能正常工作"的使用者，分两条路径：

- **第一部分：自动化单元测试** —— 几秒钟出结果，不需要真实 LLM/网络，
  改完代码想快速确认"没搞坏东西"用这个。
- **第二部分：端到端手动验证** —— 需要真实 LLM 配置，按"输入什么 →
  预期看到什么 → 怎么判断通过"逐步走一遍主链路，验证的是"这个项目
  真的能用"而不只是"代码逻辑没错"。

两部分互补：单元测试打桩了 LLM 调用，验证的是"引擎传给 workflow 的
参数对不对、workflow 结果怎么解析回状态"；手动验证走真实 LLM，多验证
一层"提示词/模板产出的内容是否合理可用"。

---

## 第一部分：自动化单元测试

### 怎么跑

```bash
cd external_projects/world_simulator
pip install pytest
python -m pytest tests/ -v
```

### 预期结果

- 全部 38 个用例（7 个测试文件）应该全部通过（`38 passed`）。
- **前提**：需要能 `import mini_agent`（本项目在 mini_agent 仓库内跑
  测试时天然满足；如果单独把本项目搬到别的环境、还没装完整
  mini_agent 依赖（比如 `fastapi`），依赖 `mini_agent.workflow` 的
  几个测试文件会因为 `ImportError` 报错——这不代表本项目代码有问题，
  只是测试环境缺依赖，装好 `mini_agent` 及其依赖后重跑即可。不依赖
  `mini_agent.workflow` 的三个文件（见下表"无额外依赖"列）在任何
  环境下都应该能独立通过，可以用它们先确认"存储层/分支/删除/成就"
  这部分的代码没问题：

  ```bash
  python -m pytest tests/test_state_and_store.py \
                    tests/test_branch_manager.py \
                    tests/test_delete_and_achievements.py \
                    tests/test_config_llm_inheritance.py -v
  ```

  预期：`25 passed`。

### 各测试文件覆盖什么、怎么算通过

| 测试文件 | 是否需要 `mini_agent.workflow` | 覆盖什么 | 通过标准 |
|---|---|---|---|
| `test_state_and_store.py` | 否 | `SimState`/`SimManifest` 的序列化/反序列化往返；`SimStore` 落盘/读取当前状态与历史；读取不存在的实例报错 | 4 个用例全绿 |
| `test_branch_manager.py` | 否 | 分叉不销毁原时间线；不切换分叉（`switch=False`）；从非法 step 分叉报错；切换分支；切换到不存在的分支报错；跨实例对比时间线 | 7 个用例全绿 |
| `test_delete_and_achievements.py` | 否 | 删除实例后目录消失、不影响其它实例；删除不存在的实例报错；4 组成就解锁场景（初始状态无解锁/推进 1 步解锁"启程"/推进 10 步解锁到"长篇在望"/命中重大决策+自动挡+已结束同时解锁对应徽章） | 7 个用例全绿 |
| `test_config_llm_inheritance.py` | 否 | `world_simulator/config.py` 的"原地布局自动探测主项目根 + 设置 `MINI_AGENT_MAIN_PROJECT_ROOT`"逻辑：能识别 `src/mini_agent`/`agent_config.json` 两种信号、非该布局时不误判、已有环境变量时不覆盖 | 7 个用例全绿 |
| `test_spec_and_engine.py` | 是 | 意图→提案草稿的 skill 绑定与解析（打桩 LLM）；创建+推进的端到端闭环；`set_pilot_config`；`materialize_simulation` 不触发 LLM 调用；推进时传入未知选项 id 会被拒绝 | 5 个用例全绿 |
| `test_autopilot.py` | 是 | 自动挡代选记录、拒绝 LLM 编造的选项 id、未开启自动挡报错、重大决策触发暂停、批量推进跳过手动挡实例、单实例失败不中断整批 | 6 个用例全绿 |
| `test_multi_template.py` | 是 | 新模板（`group_evolution`）能正确绑定到对应 skill、端到端创建+推进跑通，验证"新增模板不改引擎代码" | 2 个用例全绿 |
| `test_branch_dynamic_state.py` | 是（打桩，无需真实 LLM） | 第二十二轮 WP0：因果树/待兑现关系按分支隔离——分叉后推进不污染主线、分叉即回滚、`explore_branches` 不污染主线、旧实例离开分支时提交、手改因果线不丢、合并后刷新、序列化往返 | 10 个用例全绿 |
| `test_consistency_guard.py` | 是（打桩，无需真实 LLM） | 第二十二轮 WP4：一致性守卫 C1–C8 的判定边界（成熟度跳级/倒退、分支复活、前置违规、非常规迁移、数值波动、事件密度、边覆盖、校准）、`advance()` 端到端记录 C2、开关与异常兜底、序列化往返 | 23 个用例全绿 |
| `test_backtest.py` | 是（打桩，无需真实 LLM；引擎/匹配 workflow 都用假实现） | 第二十二轮 WP5：案例校验（重复 id/前置成环等）、别名化与年份平移、候选抽取、规则/LLM 匹配器与覆盖、打分边界（τ/区间误差/前置违反/范围/无数据）、端到端落盘与隔离、A/B 汇总与小样本提示、示例案例有效、已核对（`verified: true`）且模拟跨度覆盖最后一条真值 | 43 个用例全绿（P6 起另有 `test_backtest_p6.py`；LLM 匹配器 2 个用例需要 `fastapi` 与 `mini_agent` 在路径上） |
| `test_backtest_p6.py` | 是（打桩，无需真实 LLM） | 第二十二轮 P6：时间线（有/无/混合/强制近似/非法值/历史空洞）、候选时间用时间线、技术节点到达时点还原（initial/registered/transition、快照沿用、倒退）、阶段迁移时点偏差（快/慢/起点已在/未到达/范围外/无数据）、端到端落盘、`time_basis` 校验、rescore 兼容旧结果、A/B 时间基准警告、CLI 输出 | 29 个用例全绿；变异验证 14 个全部转红 |
| `test_tech_model.py` | 是（无需真实 LLM；引擎 workflow 用桩） | 第二十二轮 WP1：R1–R7 各规则判定边界（T1–T11，含同步迁移不互相满足、`for_stage`、未登记前置不阻断）、`elapsed_days` 合法性与降级、投入/瓶颈/软前置系数、参数与先验优先级、提示词、`SimState` 新字段往返、C6 按 `elapsed_days` 归一、`advance()` 端到端（审计/快照/工作副本/开关关闭逐字节等价/技术模型出错不拖垮推进）、拆分模式、分叉回滚与种子锚定、界面 HTML 助手 | 54 个用例全绿 |
| `test_event_sampler.py` | 是（无需真实 LLM；引擎 workflow 用桩） | 第二十二轮 WP2：先验规整与丢弃原因、泊松概率公式与边界、种子可复现且对分支/步/salt/事件 id 各自敏感、抽样均匀性、单事件独立性、公共随机数、`elapsed_days` 中位数基准、冷却（按分支历史推导、被压掉的不启动冷却）、条件谓词（变量/技术阶段/AND/缺失即不满足）、上限、提示词、`SimState` 往返、`advance()` 端到端（注入/落盘/冷却/开关关闭逐字节等价/出错降级为静默步/用真实分支名抽样）、冷却不跨分支泄漏、拆分模式、界面 HTML 助手 | 39 个用例全绿 |
| `test_event_prior_proposals.py` | 是（打桩，无需真实 LLM） | 第二十二轮 P7：`confirmed` 语义（手写默认已确认/`llm_estimate` 默认未确认）、未确认先验不抽样且不扰动别的事件的抽样值、提议规整（强制来源/未核对/未确认、非法/重复/条件变量不存在/超限丢弃）、采用（只取勾选、改频率→`user_edited`、`verified` 恒假）、提示词开关、`ScenarioDraft`/`generate_scenario`（单次与拆分创建路径）、workflow 占位符与界面接线的静态检查 | 22 个用例全绿；变异验证 15+3 个全部转红 |
| `test_causal_engine.py` | 是（无需真实 LLM；引擎 workflow 用桩） | 第二十二轮 WP3 · P5a：边规整（旧格式兼容/未知值视为未声明/重复与自指丢弃）、四种触发源（含 `advanced:false`、被压掉事件、`held` 迁移不触发）、同边去重累加、条件/停用/队列上限、延迟（天数累计/按步降级/`delay_steps`）、处置校验 E1–E4 与自动结案、兑现统计口径、提示词（到期/索要 `elapsed_days`/小样本不显示历史率）、兜底 E0、`SimState` 往返、`advance()` 端到端（入队→提醒→结案/先处置后入队/开关关闭逐字节等价/出错不拖垮推进）、分叉回滚与两分支独立结案、两个 workflow 与三个 SKILL.md 含新字段、界面 HTML 助手 | 40 个用例全绿 |
| `test_p5b_followups.py` | 是（无需真实 LLM；workflow 用桩） | 第二十二轮 P5b：关系 `delay_days`（归一化/非法值/到期按天数/步长变化不影响/缺数据降级/旧待办不变/提示词与索要 `elapsed_days`/端到端）、兑现统计回写 `knowledge_base`（映射/幂等/文本匹配/Jaccard 兜底/开关/失败不拖垮推进）、技术违规修复调用（可修复码范围/约束/采纳/拒绝/各类失败降级/重裁出错还原/只调一次/开关关闭零调用/界面） | 51 个用例全绿 |
| `test_tree_grounding.py` | 是（无需真实 LLM；workflow 用桩） | 第二十二轮 P5c：分支新字段规整（只在填写时输出/非法写法忽略/旧形状不变）、开关与子开关、前置解析口径、G1 降级与审计修正/不追溯/新增即 active/`resolved` 不拦、G2/G3 互斥组（按线作用域/同步先到先得/同步释放）、触发条件建议 vs 自动 G4（缺变量/非法/被前置或互斥拦住/技术阶段/列表全满足）、G5 落败者（不动已终结与 active 的同组分支）、入参不被修改与 G0 兜底、提示词、校准账本（终结时刻档位/新出现即终结不记/最小样本/倒挂/名义值）、`SimState` 往返、`advance()` 端到端（降级持久化到历史/快照/settings、开关关闭逐字节等价且 C3 仍告警、自动激活触发因果队列、出错不拖垮推进、分支作用域）、两个 workflow 与三个 SKILL.md | 45 个用例全绿 |
| `test_tree_effects.py` | 是（无需真实 LLM；workflow 用桩） | 第二十二轮 P5d：分支 `effects_if_active` 形状规整（只在填写时输出/非法条目与字段丢弃）、语义规整（sign 别名/未知取值）、开关（需同时开因果引擎）、入队口径（新变为 active/持续 active 不重复/被降级不入队/新增即 active/再激活累加 retrigger/condition/多条声明）、E5/E6/E7、入参不被修改与 E0 兜底、`needs_elapsed` 联动、到期提示带分支名与处置结案、树边不回写知识库、`advance()` 端到端（入队→到期→交代→统计、开关逐字节等价、不开接地也能用、接地降级不入队、出错不拖垮推进、分支作用域）、两个 workflow 与三个 SKILL.md | 22 个用例全绿 |
| `test_causal_view.py` | 是（无需真实 LLM；`app.py` 渲染用记录型 `st` 桩） | 第二十二轮 P5e：边状态分类（假设/已观察/已证伪/未定/已停用的阈值边界、单次抵消不判证伪、仅部分兑现不算证伪、`min_realized` 与兑现率阈值各自独立、停用优先）、参数覆盖与非法值忽略、边视图（声明边顺序/显示名回退/树边/声明已删但有历史的树边/不修改入参）、着色 DOT（各状态颜色线型/兑现率标签/转义/树分支椭圆/空图）、到期时间线（到期优先与忽略次数/天精度先于步精度/缺 `elapsed_days` 降级按步/引擎关闭时 open 为空/最近记录倒序与条数上限/树边显示 `线/分支`/脏数据容错）、`app.py` 两个渲染函数 | 22 个用例全绿 |
| `test_advance_lines_mechanisms.py` | 是（打桩，无需真实 LLM；`mini_agent.workflow` 用假实现） | 第二十二轮 P8：机制全关时提示词为空且不跑处理链、空推进不抽样不调用、全局 `elapsed_days` 取各线申报最大值（非法值忽略/全缺时退化可见）、事件投放（owned_vars/线 id/空 affects/没到点线/上限压掉）与 `delivered_to`、技术提议裁决与快照与多线重复（`T10`）、因果入队/只给目标线看待兑现项/目标线处置/没到点的挂起而不是被忽略（并对照主路径仍累计忽略）、树接地与一致性守卫接线、技术种子锚定、与 `advance()` 对等的技术结果、`merge`/投放/挂起纯函数 | 33 个用例全绿；变异验证 24 个全部转红 |
| `test_p9_kb_writeback.py` | 是（打桩，无需真实 LLM） | 第二十二轮 P9：条目新字段向后兼容、校准/树声明写入（阈值、绝对值覆盖幂等、未变化不重写、按实例/分支/档位分键）、P9 条目不被旧来源合并/命中/现实反馈证伪、校准条目不进检索、提示词自报标注（含 P5b 条目）、撤销（P9/旧来源独占/合并条目不动/前缀碰撞/dry-run/空库）、开关与前置条件矩阵、账本与树统计的 `own_after` 过滤、`advance()` 端到端（含分叉去重、元信息缺失不写、单路失败隔离）、`advance_lines()` 路径、命令行 `list`/`retract`、界面静态接线 | 54 个用例全绿；变异验证 37 个全部转红（首轮存活 6 个，补用例后清零） |
| `test_html_export_mechanisms.py` | 是（无需真实 LLM；Graphviz 路径用桩，另有一次真实 `dot` 的手工端到端核对） | 第二十二轮 P10：无数据/开关开但无数据时不输出任何新区块与 CSS、每步提示覆盖全部机制且按步归位、自由文本转义、体检（仅有守卫痕迹时出现）、技术树（快照优先于工作副本、其它分支不借用工作副本、未验证时长标注、软前置虚线）、事件先验（未确认/未核对标注、不剧透）、因果引擎（状态表、到期时间线、树声明、自报声明）、树接地建议、各开关关闭时不渲染、区块失败降级 | 28 个用例全绿；变异验证 22 个全部转红（首轮存活 3 个：事件/树接地的开关关闭用例缺失、降级用例抛的异常恰被变异接住，补用例后清零） |
| `test_element_registry.py` | 否（纯函数 + 假目录） | 第二十三轮 E1：元素字段规整（旧式线逐字节不变、非法值丢弃）、id/label/别名解析（只做精确规范化匹配、不模糊）、领域分组、技术节点↔`lifecycle` 无损往返、写回（建线/更新/摘除/并入同 id 旧线）、旧 `tech_state` 折叠（幂等、与领域线 id 冲突改名并改写前置引用）、新实例开关默认开与种子折叠 | 28 个用例全绿 |
| `test_element_tech_adapter.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E1：**契约**——同一组提议分别走旧存储与元素存储，`apply_step` 的审计/违规/节点读出完全一致；修复调用回滚点（含被撤销的技术登记放回）；锚定与快照形态判断；旧形态快照恢复时折叠；回测对两种快照形态结果一致；`{"element": id}` 条件别名；`advance` 端到端（旧种子+开关、预折叠种子）的快照形态、分叉回滚、分支互不污染；修复调用 accepted/rejected/重裁失败在元素存储下的行为 | 29 个用例全绿 |
| `test_element_discovery.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E3：运行期参数、发现登记/关键性门槛/候选池（按步提次、转正、清扫、条数上限）、别名与 id 漂移去重（`gpt5`/`gpt_5`、全半角、不模糊合并）、预算（`null`=不限、已有元素不被挤掉）、补全（只补缺、宽限、兜底、晚补全）、引用即登记（`line_updates`/`causal_links`/`tree_updates`/`effects_if_active`/结构化条件/`tech_updates` 别名、冲突合并不丢字段、入参不被改）、`lifecycle_seed` 经技术裁决（T5）、关系→派生因果边（声明优先、分支隔离）、prompt 段落与疑似重复提示、`advance()` 端到端（审计落盘、同步 `tree_updates` 引用新元素、元素模式关时与旧行为等价、分支隔离、异常兜底） | 82 个用例全绿 |
| `test_element_tiers.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E4：分级参数（`null`=不限、`0`≠不限、非法回退并提示、与前两阶段共用 `element_params`）、分级规则逐条（刚登记/`line_updates`/`tree_updates`/技术迁移与倒退、别名解析历史、活跃分支、`tier_pin`/用户意见、到期待兑现因果、事件命中与领域展开、因果边一跳传播、窗口按线节奏缩放、扫描有界）、预算（降为 watch、`null`/`0`、排序、入度平局、钉住不受裁剪、纯函数）、分支正确、prompt 渲染（active 完整/终态分支只计数/`var_refs` 当前值、watch 一行、休眠索引上限与"另有 N 个"、retired/merged 不出现、到点/陈旧提示随分级收窄）、元素模式关闭时逐字节不变、**prompt 规模**（10→100→1000 元素 active 固定、索引封顶后不再增长）、`advance()` 端到端（休眠元素仍可更新并在下一步展开、事件命中展开、分叉各自分级） | 57 个用例全绿 |
| `test_element_linkage.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E6：领域源头 `domain_child`（其它领域/已退场/advanced=False 不触发、旧实例不触发、树更新与技术迁移触发）、事件 `affects` 领域/别名展开与 `expand_affects` 规则、目标领域提示（active 子元素/无 active/非领域/旧实例）、显示名跟随合并、树影响目标别名与合并规范化（旧实例不变、未知目标 E7 仍入队）、`线/分支` 限定前置（两处检查器）、C9 `element_health`（规模/待补全/兜底/旧实例 None/`analyze_history` 键门控）、`advance_lines`（领域事件投放、`discovered_elements` 合并登记、元素段提示仅元素模式、旧实例忽略）、`element_view`（分组/徽标/未归类/合并折叠与链式/类型与隐藏过滤/历史重定向、编辑校验与原子作废、九个预算参数与“不限”/0 区分/非法拒绝）、静态导出分组与合并历史、体检导出含 C9 | 54 个用例全绿；5 组变异（domain_child、领域投放、advance_lines 登记、编辑 parent 等）全部转红 |
| `test_element_ops.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E5：`normalize_op` 别名/垃圾输入、merge（别名/关系改指/分支前缀追加与前置互斥改名/影响改指与自指去掉/链式合并/校验：自并、未登记、领域线、已退场、已被并入、带 lifecycle）、`resolve` 跟随与 `redirect_merged` 精确 id/环安全、发现与补全命中被合并名字落到目标、分级历史索引（id/名称/别名）、retire（标记/幂等/领域线拒绝/不被再发现复活/入队跳过与已入队保留/旧实例不受影响/树影响源与目标退场有正向对照）、reparent、split（继承 parent/重名跳过/预算/技术种子走裁决）、声明边端点读取时改指、每步上限与单条回滚、协议提示含四种操作、周期扫描（间隔解析/建议规整/输入组装/三种失败/与发现同一套校验/预算/`scan_now` 不改入参/`safe_scan_in_step` 门控与异常吞掉）、`advance()` 端到端（审计+持久化+快照/分支作用域/元素模式关闭忽略/坏输入不拖垮推进/扫描先于快照） | 47 个用例全绿；34 个变异全部转红 |
| `test_element_creation.py` | 是（打桩，无需真实 LLM） | 第二十三轮 E2：预算参数（`null`=不限、0≠不限、非法回退）、元素规整/去重（只看 id 与别名）/parent 解析/`lifecycle_seed`、预算裁剪（总数/每领域/优先级/候选不丢/顺序保持）、创建 prompt 新旧分支、`generate_scenario` 单次与拆分两条路径端到端、旧草稿仍可落盘、领域+元素草稿落盘后每条线有树 | 28 个用例全绿 |
| `test_anatomy.py` | 否（纯函数，无需真实 LLM） | 第二十四轮 A1：剖面规整（非法丢弃/不补缺省/幂等/体积上限/id 与数值规则/`sourced` 无证据降级/趋势无白名单/条件树）、模板、`anatomy_params` 回退、存取（领域线与退场拒绝）、`normalize_element` 契约（无剖面逐字节不变）、快照往返、新实例开关、合并保护、统计/体检/累计日、档案视图门控 | 31 个用例全绿；8 个变异全部转红 |
| `test_anatomy_creation.py` | 是（打桩，无需真实 LLM） | 第二十四轮 A2：重点元素选择（先验边>技术>顺序、`null`/0、领域/旧式线/退场不参与）、种子落成草稿（清 basis/引擎状态、meta 重写、空壳、非重点丢弃、种子键不落盘、关闭只摘键）、创建提示开关与逐字节前缀、单次与拆分两条路径一致、审阅（确认保留证据/驳回删除/reviewed 状态）与重点勾选纯函数、落盘 | 17 个用例全绿；9 个变异全部转红 |

**如何判断"没测通过"**：任何一条 `FAILED`（而不是因为上述 `fastapi`
之类的环境缺依赖导致的 `ImportError` collection error）都说明改动
引入了回归，需要看具体的 assertion 信息定位。

---

## 第二部分：端到端手动验证（需要真实 LLM）

适用场景：确认整个项目在真实环境下（真实 LLM 配置、真实看板）能正常
跑通主链路："意图 → 提案 → 确认创建 → 推进 → 分叉/对比 → 自动挡 →
存档管理 → 游戏化视图"。

### 前置条件

- 已安装 mini_agent 框架，且能加载到有效的 LLM 配置（`agent_config.
  json`/`providers.json`，或者本项目已经 `mini-agent projects
  register` 到某个能提供配置继承的宿主）。
- `cd external_projects/world_simulator && pip install -r requirements.txt`

### 路径 A：命令行验证（最快，适合先确认"能不能跑通"）

| 步骤 | 输入 | 预期结果 | 怎么算通过 |
|---|---|---|---|
| 1. 健康检查 | `python entrypoints/health.py` | 标准输出打印 `ok`，退出码 0 | 不需要 LLM 就能过；如果这一步都失败，说明存储层/依赖本身有问题，先解决这个再往下走 |
| 2. 创建实例 | `python entrypoints/create_simulation.py "模拟一个刚毕业、在读研和工作之间犹豫的年轻人的人生" --template life_sim` | 标准输出打印 `sim_id=life_sim_xxxxxx` 和 `title=...`；`data/<sim_id>/manifest.json`、`state_current.json`、`state_history.jsonl` 三个文件被创建，`state_history.jsonl` 只有一行（step 0） | 命令退出码为 0，且 `data/` 下确实多出对应目录；`title`/`summary`（可用 `cat data/<sim_id>/state_current.json` 查看）应该是和输入意图相关的合理内容，而不是空的或明显文不对题 |
| 3. 查看列表 | `python entrypoints/list_simulations.py` | 输出一行，形如 `life_sim_xxxxxx  template=life_sim  status=active  step=0  pilot_mode=manual  <title>` | 步骤 2 创建的实例出现在列表里，字段值符合预期（`status=active`、`step=0`） |
| 4. 推进一步（默认走向） | `python entrypoints/advance_simulation.py <sim_id>`（`<sim_id>` 换成步骤 2 拿到的值） | 输出 `step=1`、`summary=...`，可能还有 `narrative=...` 和若干 `option: <id> — <label>：<description>` 行 | `step` 从 0 变成 1；`summary`/`narrative` 内容应该是"承接上一步状态、合理往后发展"的叙事，不是不相关的胡言乱语；如果本步给出了候选选项，选项应该是"在当前处境下说得通"的几个方向 |
| 5. 推进一步（指定选项） | 先跑一遍步骤 4 拿到某个 `option id`，再执行 `python entrypoints/advance_simulation.py <sim_id> --choice <option_id>` | 输出 `step=2`，`summary`/`narrative` 内容应该明显承接"你选的那个方向"，而不是另一个方向的走向 | 对照 `--choice` 传入的选项描述，看新状态是不是真的往那个方向发展了 |
| 6. 再次查看列表 | `python entrypoints/list_simulations.py` | 该实例的 `step=2` | `step` 字段与步骤 5 后的实际推进次数一致 |

**批量自动挡验证**（可选，验证 `batch_advance_daily` 对应的代码路径）：

1. 用看板（见路径 B）或直接改 `data/<sim_id>/manifest.json` 把
   `pilot_mode` 改成 `"autopilot"`、`autopilot.enabled` 改成 `true`、
   补上 `principles`（数组，随便写一两条原则）。
2. 执行 `python entrypoints/advance_simulation.py --all-autopilot --steps 1`。
3. **预期**：输出 `<sim_id>: ok, next_step=<原 step + 1>`；如果
   `review_mode` 是 `pause_on_major_decision` 且这一步被判定为重大
   决策，输出后面会带 `（触发暂停等待确认）`，且该实例的
   `manifest.json.status` 会变成 `paused`。
4. **怎么算通过**：新增的历史节点里 `chosen_by` 字段是 `"autopilot"`
   且带有 `chosen_reason`（可以 `cat data/<sim_id>/state_history.jsonl`
   查看最后一行确认）。

### 路径 B：看板验证（更直观，覆盖分支/对比/存档/游戏化视图）

```bash
streamlit run app.py --server.port 8502
```

浏览器打开 `http://localhost:8502` 后按下表操作：

| 页面 | 操作（输入什么） | 预期结果 | 怎么算通过 |
|---|---|---|---|
| 模拟列表 | 打开页面 | 看到路径 A 创建的实例卡片（标题/状态/步数/推进模式） | 卡片信息与 CLI 查到的一致 |
| 创建向导 | 输入一句话意图，选模板，点「生成提案草稿」 | 几秒后出现可编辑的草稿（标题/摘要/关键变量 JSON/初始候选方向） | 草稿内容与输入意图相关；点「确认创建」后跳转到该实例的详情页 |
| 实例详情 | 点某个候选方向卡片，或点「按默认走向推进」 | 出现推进中的 spinner，几秒后时间线顶部新增一个"章节卡片"，`当前第 N 步` 数字 +1 | 新增章节的摘要/叙事承接了你的选择；如果卡片渲染失败或报错，说明这一步没通过 |
| 实例详情 → 分支 | 展开「从历史节点开一条新分支」，选一个较早的 step，点「创建分支并切换过去」 | 出现新的分支 id（如 `br_xxxxxx`），当前活跃分支变成它 | 原分支仍在分支列表里、切回去后历史没有丢失内容 |
| 对比视图 | 分别为「实例 1」「实例 2」选好 实例+分支 组合（可以是同一实例的两条分支） | 并排展示两条时间线的章节卡片，下方是按 step 对齐的关键变量表格 | 两侧内容确实来自不同分支/实例，且在分叉点之前的历史一致、之后开始分叉 |
| 实例详情 → 推进模式 | 展开「配置自动挡」，勾选「开启自动挡」，填几条原则，选风险偏好，点「保存自动挡配置」 | 页面提示保存成功，「当前：自动挡（代理代选）」文案出现 | 用「手动触发自动挡推进一步」按钮跑一次，时间线里新增的章节带有"→ 代理选择了「…」"和"理由：…"字样 |
| 存档管理 | 打开页面，看到全部实例列表；点某个不再需要的测试实例的「🗑 删除」→ 勾选「我确认要删除这个实例」→「确认删除」 | 提示"已删除「…」"，该实例从列表消失 | 用 `ls data/` 确认对应目录确实不在了；**注意这是破坏性操作，只在测试实例上做** |
| 游戏化视图 | 在实例详情页点「📖 游戏化视图」 | 顶部出现成就徽章墙（已解锁的高亮、未解锁的灰显）+ 进度条；下方是可翻页的章节回顾 | 至少推进过 1 步的实例应该点亮"启程"徽章；点「上一章/下一章」或拖动"跳到章节"滑块，展示内容随之切换且与该 step 的实际历史一致 |

### 判断"整个项目是好的"的最低标准

如果你只有几分钟时间，建议至少做到：

1. 第一部分的四个"无额外依赖"测试文件全绿（`25 passed`）——确认
   存储/分支/删除/成就/LLM 配置继承这些不依赖 LLM 的核心逻辑没问题。
2. 路径 A 的步骤 1-4 走一遍——确认真实 LLM 环境下"创建→推进"这条
   最核心的链路能跑通、产出内容合理。
3. 路径 B 打开看板确认页面能正常加载（无报错堆栈），且步骤 2 创建的
   实例能在列表里看到、能打开详情页。

三条都过，说明这个项目在你的环境里是可用的。

---

## 常见报错排查

### `generate_scenario workflow 执行未成功……Anthropic requires an API key`

说明"LLM 调用本身能发起"，卡在最后一步"找不到可用的 API key"。按顺序
排查：

1. **主项目本身配好 LLM 了吗？** 去 mini_agent 主仓库根目录确认
   `providers.json`（或 `agent_config.json` 里的 `llm_fallback_chain`）
   配了 key，或者启动看板/CLI 的这个终端会话里导出了对应环境变量（如
   `ANTHROPIC_API_KEY`）。如果主项目自己都没配，这一步会一直失败，
   属于预期行为——先在主项目层面配好。
2. **本项目找得到主项目吗？** `world_simulator/config.py::
   load_llm_cfg()` 会依次尝试：环境变量 `MINI_AGENT_MAIN_PROJECT_ROOT`
   → 已注册到 daemon 的记录 → "原地布局自动探测"（本项目仍挂在某个
   mini_agent 主仓库的 `external_projects/` 下时自动生效，不需要手动
   配置）。如果本项目已经被搬到独立路径、也没注册过，以上都找不到，
   需要显式 `export MINI_AGENT_MAIN_PROJECT_ROOT=<主项目路径>` 或先
   `mini-agent projects register <本项目路径>` 一下。
3. **验证方法**：`python -c "from world_simulator.config import
   load_llm_cfg; load_llm_cfg()"` 能正常返回（不报
   `ModuleNotFoundError: mini_agent`）就说明"能找到主项目"这一步没
   问题，报错会具体停在哪一步（找不到 mini_agent 包 / 加载配置失败 /
   真正发起 LLM 调用时缺 key）。

### `ModuleNotFoundError: No module named 'mini_agent'`

本项目所在的 Python 环境没有安装 mini_agent 框架本身（或缺它的某个
依赖，比如 `fastapi`）。这与"LLM 配置继承"是两个独立问题——需要先让
`import mini_agent` 本身能成功（通常是 `pip install -e .` 装好主项目
及其依赖），再回头看上面那条排查 API key 的问题。
