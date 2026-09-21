# world_simulator 现存"最小版"系统全面盘点：从哪来、还差什么、
建议怎么补成完整版（第七轮差距分析）

> **状态**：本文档只做盘点 + 方案设计，尚未开始实施。这是用户在
> 阶段三十六（`world_simulator_event_driven_engine_and_full_
> architecture_plan.md` 四个批次全部完成）之后，明确要求"把项目里
> 还剩哪些最小版系统都规划一下"的产物。本文档**不是**要一次性把
> 下面所有方向都做完，而是先把全貌摆出来，供用户决定下一步先做
> 哪个、要不要做。

---

## 1. 怎么整理出这份清单

翻遍 `next_doc/world_simulator_*.md` 全部方案文档的实施状态标注，
把"标注为最小可行版本、完整版还有明确差距"的系统挑出来，分成三类：

- **A 类：代码已完成，只是缺真实使用验证**——不是设计/实现的差距，
  是"按方案要求应该先人工跑几次确认效果，但被跳过了"。
- **B 类：明确"暂不建议"、需要外部触发条件才启动**——不是能力不足，
  是项目主动判断"现在做性价比不高"。
- **C 类：结构上就是精简/最小版，完整版有明确可落地的补全方向**——
  这是本文档的重点，也是用户问题里"如何改进成完整版本"真正指向
  的那部分。

---

## 2. A 类：代码已完成，缺真实使用验证（建议：先去跑，不是先写代码）

| 系统 | 方案文档 | 现状 |
| --- | --- | --- |
| 单主体 State/Belief 分离（`belief_fields`/`beliefs`） | `world_simulator_belief_state_separation_plan.md` | 第一批要求的"`life_sim` 小范围人工验证 3~5 次"从未执行，第二批在没有验证结果的情况下就推广到全部模板 |
| Agent Preview（自动挡决策预览） | `world_simulator_agent_preview_and_adaptive_policy_plan.md` | 第二批"真实运行几次 Preview 检查效果"的人工验证尚未做 |
| 多尺度因果线独立推进（`independent_line_advance`） | `world_simulator_event_driven_engine_and_full_architecture_plan.md` 2.3 节 | 要求"先在 `life_sim` 小范围人工验证 3~5 次真实多因果线模拟"，代码已完成但这一步还没做 |

**这三个不需要再写代码**，需要的是使用者自己开几个 `life_sim`/自动挡
实例，实际跑几轮，看看：`beliefs` 里的认知值是不是真的会和真实值
产生有意义的偏差（而不是 LLM 偷懒让两者永远相等）、Preview 是否真的
帮用户提前发现了自动挡要犯的错、独立推进因果线是否让人觉得"某条线
很久没动静、体验割裂"。三者中任何一个如果验证结果不理想，方案文档
里都写好了"直接放弃/退回默认关闭"的退路，不需要勉强凑合。

---

## 3. B 类：暂不建议，等待触发条件（建议：不主动投入，出现信号再评估）

| 系统 | 方案文档 | 触发条件 |
| --- | --- | --- |
| Base Rate 外部数据接入（真实统计基准喂给 prompt） | `world_simulator_realism_transparency_and_retrospective_roadmap_v3_plan.md` 4.7 节 | 出现明确、稳定、可合法接入的统计数据源，且用户高频反馈"模拟数字没有现实依据" |

这一条不建议规划实施批次——项目是本地单机小工具，联网抓取/维护
外部数据源的成本和这个定位不匹配，触发条件也不是"规划一下就能
满足"的，属于外部环境变化后再评估的方向。

---

## 4. C 类：结构上仍是"精简版"，有明确的完整版补全方向

以下按"改动面从小到大"排序，不代表价值排序。每一条都延续项目一贯
原则：新增开关/字段，旧路径完全保留，不引入自动化的数值计算或语义
模型（因果关系、置信度等仍然由 LLM/用户判断，代码只做归一化和存储）。

### 4.1 因果知识库（`knowledge_base.py`）：补 Evidence/Valid Range/Version 字段 + 管理界面

**现状**：`KnowledgeItem` 只有 `cause`/`effect`/`mechanism`/
`confidence`/`validated_count`/`contradicted_count`（+ 本次新增的
`notes`）六七个字段，参考文档设想的 `Evidence`（具体是哪次模拟的
哪几步支持这条知识）、`Valid Range`（这条因果关系在什么条件/量级下
成立，比如"只在初创期成立，规模化后不成立"）、`Version`（知识本身
也可能被后续经验修正，需要版本号）完全没有。而且没有任何管理界面，
用户看不到、改不了知识库里到底存了什么。

**完整版方向**：
- `KnowledgeItem` 新增 `evidence`（字符串数组，引用 `sim_id`+`step`
  的来源列表，`record_causal_links()` 写入时自动带上）、
  `valid_range`（自由文本，可选，由 LLM 在声明因果链时顺带给一句
  适用范围说明，不做结构化约束）、`version`（整数，每次内容被
  实质性修改时 +1，同 `causal_links` 一贯的"提示而非强制"风格）。
- `app.py` 新增一个只读的"知识库浏览"页面/标签页（不做增删改
  UI——4.12 节原方案就明确不做管理界面，这里只补"能看见"这一层，
  改动最小）：按 `confidence`/`validated_count` 排序展示，点开看
  `evidence` 来源。

**风险**：低，纯字段扩展 + 只读展示，同已完成的字段级扩展批次
同一量级。

### 4.2 Hypothesis Engine → 半自动 Experiment Design Engine

**现状**：`hypothesis.py` 能识别关键不确定性、分叉多世界、找稳健
结果，但"要不要分叉""分叉几个值"每次都需要用户手动确认，没有
参考文档设想的\"系统自己设计一组有区分度的实验方案"这一层。

**完整版方向**：新增 `hypothesis.suggest_experiment_design()`——
在 `suggest_critical_uncertainties()` 已经识别出的不确定字段基础上，
让 LLM 额外给出"建议测试哪几个取值组合、为什么这组组合比其它组合
更有区分度"的说明，作为**建议**展示给用户，用户仍然点击确认才会
真的调用 `run_hypothesis_worlds()`——不做成自动触发，分叉终究是有
成本的操作，决定权继续留给用户。

**风险**：低，只是给已有的分叉入口加一句"建议怎么分"的 LLM 提示，
不改分叉本身的执行逻辑。

### 4.3 因果线可视化：从"列表总览"到"图谱 + 贡献拆解"

**现状**：`app.py::_render_causal_lines_overview()` 只是按线聚合
时间点序列的列表，`attribution.py`（贡献拆解）和 `causal_graph.py`
（因果图边集合）是两个独立模块，没有在因果线视图里可视化呈现，
用户看不到"这几条线之间到底是怎么互相影响的"这张全局图。

**完整版方向**：
- `app.py` 新增一个"因果关系图"标签页，用已有的 `causal_graph.
  build_causal_graph()` 输出的边集合 + `causal_lines` 节点，画一张
  简单的节点-边示意图（用 Streamlit 能力范围内的现成图表组件，比如
  `st.graphviz_chart`，不引入新的重量级前端依赖）。
- 图上的节点可以点开看 `attribution.summarize_contributions()`
  对应这条线的贡献拆解——把三个已有但割裂的模块（因果线列表/
  因果图/归因报告）串成一张互相能跳转的视图，不新增任何数据结构
  或计算逻辑，纯展示层整合。

**风险**：中——展示层改动，不碰引擎，但 `st.graphviz_chart` 在
数据量大（因果线/因果链很多）时的可读性需要实际验证，建议先用
`life_sim` 几个真实实例的数据跑一遍看图是否可读，不可读就退回简化
布局（比如按线分组的多个小图，而不是一张大图）。

### 4.4 多主体 Entity/Relationship：从"自由文本"到"结构化图"

**现状**：`multi_entity_mode` 下 `entities` 是字典结构，但实体之间
的关系仍然主要靠 `narrative` 自由文本表达；阶段三十六第二批
（`relationship.py`）虽然给 `settings.relationships` 加了
`delay_steps`/`propagation_path`/`reversible`，但那是"提示层"
增强，不是把 `entities` 之间的关系变成一个可遍历、可查询的图结构。

**完整版方向**：新增 `multi_entity.build_entity_graph(manifest)`——
把 `settings.relationships` 里 `from`/`to` 都能匹配到 `entities`
某个 id 的记录提取出来，构造一个简单的邻接表（不引入图数据库/
NetworkX 这类重量级依赖，一个 `Dict[str, List[str]]` 级别的结构
足够），供"这个实体和哪些实体有关系""从 A 到 B 有几步关系路径"这类
查询使用。查询结果继续只作为**提示**喂给 prompt 或展示给用户，
不做自动化的图遍历数值计算（同 4.1 节一贯取舍）。

**风险**：中——这是"精简版"里改动面较大的一个，`entities` 目前
不保证每个关系里的 `from`/`to` 一定能匹配上一个真实存在的 entity
id（`narrative` 自由文本本来就不做强校验），需要先设计"匹配不上时
怎么办"（建议：静默忽略，不阻断，同项目一贯的宽松容错风格）。

### 4.5 Resource/Rule：补 `production`（持续产出）关系类型

**现状**：`resource_relations` 只做了 `transfer`（转移）一种关系
类型的事后一致性检查，参考文档设想的 `production`（比如"这条因果线
每步固定产出一定数量的某资源"）完全没做。

**完整版方向**：`resource_relations` 新增
`kind: "production"`（与现有 `"transfer"` 并列），声明
`field`/`amount_per_step`/`source_line_id`（可选，产出来自哪条
因果线，不填表示背景产出）。`engine._check_resource_relations()`
新增对应分支：只做"事后一致性检查"（这一步 `field` 的变化量是否
和声明的产出速率明显不符，不符就在校验结果里给一句提示），**不**
自动帮 LLM 计算并写入产出数值——同 `transfer` 现有做法完全一致的
取舍，避免"看起来是精确引擎、实际是规则拍脑袋"。

**风险**：低，复用 `transfer` 已经验证过的"事后检查 + 提示，不
自动计算"模式，只是新增一种关系类型分支。

### 4.6 Branch Engine：补 `merge`（分支合并）+ skill/prompt 版本记录

**现状**：`branch_manager.py` 有 fork/switch/delete/compare，没有
`merge`（把某个分支的后续发展"合并"回主线，或者反过来"以某个分支
为准覆盖主线"这类操作），也没有记录"这个分支是用哪个版本的
skill/prompt 跑出来的"。

**完整版方向**：
- `merge` 从参考文档设想的"自动合并两条分支的状态"退化为项目
  一贯的最小可行版本：`branch_manager.merge_branch(source, target)`
  只做"把 `target` 分支的历史指针切换到复用 `source` 分支从某一步
  起的后续状态"（本质是"以谁为准"的一次性切换，不做真正的字段级
  三路合并——两条分支的 `vars` 可能已经产生不可调和的分歧，自动
  合并大概率产生语义错误的结果，不如让用户自己决定"以哪条为准"）。
- 版本记录：`SimManifest`/`SimState` 新增可选字段 `skill_version`
  （落盘时读取对应模板 `SKILL.md` 的文件修改时间戳或一个用户手动
  维护的版本号——不引入 git commit hash 这类需要额外基础设施的
  方案），供之后回答"改了 skill 之后预测质量是不是真的变好了"这类
  问题时，能把不同版本的历史分开统计（配合 `reality_check.py` 已有
  的预测质量记录一起看）。

**风险**：中偏高——`merge` 哪怕退化成"指针切换"也涉及历史数据的
结构调整，需要仔细设计不破坏 `SimStore.load_history()` 的既有
读取假设，建议放在这批里最后做，且先在测试/小范围验证。

### 4.7 Hierarchical Agent / Dynamic Cognition Router：从"分层第一步"到完整调度框架

**现状**：`settings.hierarchical_agent_mode` + `background_entities`
只做了"分层"的第一步（把背景角色和主角分开处理），参考文档设想的
"根据情境动态路由到不同认知层级/不同复杂度的推理"完整框架没有做，
且这部分本身也是"提前实施、未经真实场景验证"（同 A 类的性质）。

**建议**：这一条不建议现在规划完整版——一是它本身还没有真实使用
反馈证明"分层第一步"这个方向本身是有效的，二是"动态路由"要做好
需要先有足够多真实案例观察"什么情境下需要更深层的认知"，纯靠
方案设计猜不出规则。建议先归入本文档第 2 节"A 类"的验证清单，
真实跑几轮之后再决定要不要写完整版方案。

---

## 5. 建议的下一步（不代表必须马上做）

```text
第一优先（如果要继续写代码）：
  4.1 知识库补字段 + 只读浏览页  —— 改动最小、纯扩展
  4.5 Resource/Rule 补 production —— 复用已验证的 transfer 模式
  4.2 Hypothesis → 半自动实验设计 —— 只加建议提示，不改执行逻辑

第二优先：
  4.3 因果线可视化整合 —— 展示层为主，需要先用真实数据验证图的可读性
  4.4 多主体 Entity/Relationship 图结构 —— 中等改动面，需要设计容错

第三优先（改动面最大，建议放最后）：
  4.6 Branch Engine merge + 版本记录

不建议规划：
  4.7 Hierarchical Agent 完整调度框架 —— 先验证"第一步"有效性
  第 3 节 B 类（Base Rate 外部数据）—— 等外部触发条件

平行于以上代码工作、优先级更高的其实是第 2 节 A 类——
belief_state_separation / agent_preview / independent_line_advance
三项已完成的代码都在"等一个真实验证"，如果迟迟不去跑，继续在
其它方向堆代码只会让"到底哪些机制真的有用"这件事越来越难看清楚。
建议的实际顺序是：**先抽时间跑一遍 A 类的验证，再决定 C 类从哪条
开始**，而不是接着这一轮的节奏不停开新方向。
```

---

## 6. 参考资料

- `next_doc/world_simulator_universal_world_model_upgrade_plan.md`
  第 3 节覆盖度对照表（本文档 C 类清单的主要来源）。
- `next_doc/world_simulator_realism_transparency_and_retrospective_
  roadmap_v3_plan.md` 4.7/4.8 节（B 类、已完成的 4.8）。
- `next_doc/world_simulator_belief_state_separation_plan.md`、
  `next_doc/world_simulator_agent_preview_and_adaptive_policy_plan.md`、
  `next_doc/world_simulator_event_driven_engine_and_full_architecture_
  plan.md` 2.3 节（A 类三项的验证要求原文）。
