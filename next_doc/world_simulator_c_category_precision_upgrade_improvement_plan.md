# world_simulator C 类精简版系统补全计划（第八轮）：知识库字段/
Resource production/Hypothesis 实验设计/因果线可视化/多主体关系图/
Branch merge

> **状态**：本文档只做方案设计，尚未开始实施。这是
> `next_doc/world_simulator_remaining_minimal_systems_gap_survey_
> plan.md`（第七轮盘点）第 4 节"C 类：结构上仍是精简版，有明确
> 完整版补全方向"的专项细化方案——把盘点里的 7 条压缩为 6 条可
> 排期实施的批次（4.7 Hierarchical Agent 完整调度框架按盘点文档
> 建议**不纳入本轮**，理由见本文档第 6 节）。
>
> **上游文档**：
> - `next_doc/world_simulator_remaining_minimal_systems_gap_survey_
>   plan.md`（第七轮盘点，本文档所有背景分析的来源，不重复展开）
> - `next_doc/world_simulator_universal_world_model_upgrade_plan.md`
>   第 3 节覆盖度对照表（各系统"精简版做了什么"的原始出处）
>
> **和上游文档的关系**：上游文档只给了"现状 + 完整版方向 + 风险
> 等级"的概述，本文档针对每一批给出可以直接落地的字段设计/函数
> 签名/涉及文件/验收点，达到"看完这一节就能开始写代码"的细化程度，
> 这也是本文档存在的唯一理由——如果只是要一份方向概述，上游文档
> 已经够用。
>
> **实施进度**（2026-09-21 更新，见 `PROJECT.md` 阶段三十七）：
> - ✅ **批次一（第 2 节，知识库补 Evidence/Valid Range/Version +
>   只读浏览页）已完成**：`KnowledgeItem` 新增 `evidence`/
>   `valid_range`/`version` 三个字段，`record_causal_links()` 新增
>   可选 `step` 参数用于拼出 `evidence` 来源引用，`app.py` 新增
>   "📚 知识库"只读浏览页（侧边栏新入口）。新增 6 个测试用例，全量
>   测试套件 414 个全部通过。
> - ✅ **批次二（第 3 节，Resource/Rule 补 `production` 持续产出
>   关系类型）已完成**：`resource_guard.py` 的 `_normalize_
>   resource_relations()`/`_check_resource_relations()` 新增识别
>   `type == "production"` 的项（`field`/`amount_per_step`/
>   `source_line_id`/`tolerance`），声明速率与实际变化偏差超容差
>   时追加不一致提示（只提示不阻断，`transfer` 既有行为/返回形状
>   不变）；`app.py` 新增对应展示分支。新增
>   `tests/test_resource_production_relation.py` 13 个测试用例，
>   全量测试套件 427 个全部通过。
> - ✅ **批次三（第 4 节，Hypothesis Engine 半自动实验设计建议）
>   已完成**：新增 `hypothesis.suggest_experiment_design()`，把
>   `suggest_critical_uncertainties()` 识别出的不确定字段喂给一次
>   `workflows/experiment_design.yaml` 轻量 LLM 调用，建议最多
>   `max_combinations` 组"有区分度"的实验组合；`app.py` 在原有手动
>   交互之前新增建议展示 + 勾选采纳，采纳只回填文本框、不自动触发
>   分叉。新增 6 个测试用例，全量测试套件 433 个全部通过。
> - ✅ **批次四（第 5 节，因果线可视化整合）已完成**：
>   `_render_causal_graph_section()` 新增"🕸️ 关系图"子视图（和原有
>   列表视图并列切换），用 `st.graphviz_chart()` 渲染
>   `build_causal_graph()` 边集合转出的 DOT 图；节点详情展开贡献
>   拆解报告（复用 `attribution.py`）+ 知识库 `evidence`/
>   `valid_range`（精确匹配，复用批次一字段）。`requirements.txt`
>   新增 `graphviz` 依赖。新增 5 个测试用例（DOT 转换函数单测），
>   全量测试套件 438 个全部通过。
>
> **方针延续**：延续项目一贯原则——每一批都是"新增字段/开关 +
> 旧路径完全保留"，不引入自动化数值计算或语义相似度模型（因果/
> 关系判断仍然由 LLM 或用户决定，代码只做归一化、存储、事后一致性
> 检查），每批独立可验收、独立决定是否继续下一批。

---

## 1. 六个批次的依赖关系与建议顺序

```text
批次一：知识库补字段 + 只读浏览页（4.1）        —— 无依赖，最先做
批次二：Resource/Rule 补 production（4.5）      —— 无依赖
批次三：Hypothesis → 半自动实验设计建议（4.2）  —— 无依赖
批次四：因果线可视化整合（4.3）                 —— 依赖批次一的
                                                    evidence 字段
                                                    （图谱节点详情
                                                    展示用得上）
批次五：多主体 Entity/Relationship 图结构（4.4）—— 依赖阶段三十六
                                                    第二批的
                                                    `relationship.py`
                                                    结构化字段
批次六：Branch Engine merge + 版本记录（4.6）   —— 无强依赖，但
                                                    改动面最大、
                                                    涉及历史数据
                                                    结构调整，放最后
```

前三批互相独立，可以任选顺序甚至并行做；批次四建议在批次一之后
（图谱节点详情复用 `evidence` 字段最省事，不是强制依赖，没有批次一
也能做，只是节点详情少一层信息）；批次五、六保持上游文档给出的
"中等改动/最大改动"排序，建议放后面。

---

## 2.（批次一）知识库补 Evidence/Valid Range/Version 字段 + 只读浏览页

**现状**：`KnowledgeItem`（`knowledge_base.py`）只有 `id`/`cause`/
`effect`/`mechanism`/`confidence`/`source_sim_id`/`source_template`/
`created_at`/`validated_count`/`contradicted_count`/`notes`（阶段
三十六第四批新增）。没有"这条知识具体是哪次模拟哪几步支持的"、
"这条因果关系在什么条件下才成立"、"这条知识本身被修正过几次"这三层
信息，也没有任何界面能让用户看到知识库里到底存了什么。

**方案**：

1. `KnowledgeItem` 新增三个可选字段：
   - `evidence: List[str]`：来源引用列表，格式
     `"{sim_id}#step{N}"`（字符串拼接，不做成结构化对象——只是
     给用户点开看，不需要引擎解析）。`record_causal_links()` 写入
     新条目或匹配到已有条目递增 `validated_count` 时，都往对应
     条目的 `evidence` 追加一条当前来源（去重，同一个来源不重复
     记录）。
   - `valid_range: str`：适用范围的自由文本说明，默认空字符串。
     不新增 workflow 输出字段去"要求"LLM 必填——从 `causal_links`
     里 `link.get("valid_range")`（可选）读取，LLM 声明了就用，
     没声明就留空，同 `mechanism` 字段现有的"能拿到就拿，拿不到
     不强求"风格。
   - `version: int`：默认 `1`，任何一次对已有条目的**内容性**修改
     （目前只有 `valid_range` 被更新这一种情况——`cause`/`effect`
     本身不会被事后改写，`validated_count`/`contradicted_count`/
     `evidence`/`notes` 的增长不算"内容性修改"，不触发版本递增）
     才 `+1`。
2. `record_causal_links()` 内部逻辑调整：匹配到已有条目时，除了
   `validated_count += 1`，追加 `evidence`；如果这次 `link` 声明的
   `valid_range` 和条目已有的不同且非空，更新并 `version += 1`。
3. `app.py` 新增"📚 知识库"页面（和"模拟列表"同级的顶层导航项，
   不挂在某个具体实例详情页下面——知识库本身是跨实例的）：只读
   列表，按 `confidence`/`validated_count` 排序，每条展示
   `cause → effect`、`confidence`、印证/证伪次数、`valid_range`
   （有才显示）、`version`，点开可展开看 `evidence` 来源列表和
   `notes`。**不做**任何编辑/删除操作入口——4.12 节原方案的既有
   判断（"不做知识库管理 UI"）在这里仍然成立，本批只补"能看见"
   这一层。

**涉及文件**：`knowledge_base.py`（三个新字段 + `record_causal_
links()` 调整）、`app.py`（新增只读浏览页面）。

**验收点**：新增测试覆盖——`evidence` 正确追加且去重、
`valid_range` 变化触发 `version` 递增、`valid_range` 不变或未声明
时不递增、`to_dict()`/`from_dict()` 往返正确、旧数据（没有这三个
字段的历史 jsonl 行）能被 `from_dict()` 安全兼容读出（默认值：
`evidence=[]`/`valid_range=""`/`version=1`）。

---

## 3.（批次二）Resource/Rule 补 `production`（持续产出）关系类型

**现状**：`settings.resource_relations` 只有 `kind: "transfer"`
一种类型，`engine._check_resource_relations()`（`resource_guard.py`
或对应模块）只做转移关系的事后一致性检查。

**方案**：

1. `resource_relations` 单条记录新增支持
   `kind: "production"`（与 `"transfer"` 二选一），对应字段：
   - `field`：受影响的资源字段（必填，同 `transfer` 的 `to`）。
   - `amount_per_step`：数值，声明的每步"理论产出速率"（可选，
     不填表示"不做速率层面的核对，只是标记这是一个持续产出关系，
     供 prompt 提示用"）。
   - `source_line_id`：可选，产出来源是哪条因果线（不填表示背景
     产出，不归因到具体某条线）。
2. `_check_resource_relations()` 新增 `production` 分支：如果声明
   了 `amount_per_step`，对比这一步 `field` 实际变化量和声明速率，
   偏差超过一定容忍度（复用 `transfer` 现有的容忍度判断逻辑/常量，
   不新造一套阈值）时，在校验结果里追加一句提示（"该步 X 字段产出
   与声明速率明显不符"）——**只提示，不阻断推进、不自动纠正数值**，
   同 `transfer` 现有行为完全一致。未声明 `amount_per_step` 时，
   `production` 关系不参与任何数值核对，只是一条结构化记录，供
   `_resolve_relationship_hint()` 之类的提示生成函数将来引用（本批
   不需要接入 prompt 提示，只做数据结构 + 事后检查两层，接入 prompt
   留给后续如果发现有需要再加，不在本批范围内）。

**范围克制**：不做"自动帮 LLM 计算并写入产出数值"——这是
`transfer` 从阶段十六起就有的一贯取舍，`production` 保持同一个
克制程度。

**涉及文件**：`resource_relations` 相关归一化函数（同 `relationship.
py::normalize_relationships()` 的既有写法，大概率在 `state_model.py`
或独立的 `resource.py`，需要先定位现有 `transfer` 归一化逻辑所在
文件）、`engine/resource_guard.py`（新增 `production` 分支）、
`state_model.py`（字段文档更新）。

**验收点**：新增测试——`production` 关系的归一化（含非法/缺省
`amount_per_step` 的安全默认）、声明速率与实际变化匹配/不匹配时
的检查结果、未声明速率时不做数值核对、`transfer` 关系的既有行为
不受影响（回归测试）。

---

## 4.（批次三）Hypothesis Engine → 半自动实验设计建议

**现状**：`hypothesis.py::suggest_critical_uncertainties()` 识别
关键不确定字段后，"要分叉几个值、分叉哪几个组合"完全由用户手动
决定，没有"系统建议一组有区分度的实验方案"这一层。

**方案**：

1. 新增 `hypothesis.suggest_experiment_design(uncertainties,
   *, max_combinations=4)`：把 `suggest_critical_uncertainties()`
   识别出的不确定字段列表喂给一次轻量 LLM 调用（新增
   `workflows/experiment_design.yaml`），要求 LLM 从这些字段的
   候选取值里，挑出 `max_combinations` 组"最有区分度"的组合，并对
   每组给一句"为什么测这组"的说明。返回结构化列表
   `[{"combination": {...}, "why": "..."}]`。
2. `app.py` 在"反事实对比/多世界分叉"入口，如果检测到有识别出的
   `uncertainties`，先展示这份建议列表（每组带"为什么"），用户仍然
   需要**点击确认**某一组或几组才会真的调用
   `run_hypothesis_worlds()`——不做自动触发分叉，分叉本身有真实
   的计算成本。用户也可以完全忽略建议，自己手动选组合（原有交互
   路径不变、不替换，只是新增一层"建议"展示在原交互之前）。

**范围克制**：不做完整的 `Experiment Design Engine`（比如统计学
意义上的"正交实验设计"/"最大信息增益"这类严谨优化）——只是让 LLM
给一个"看起来合理"的建议，判断标准仍然是"提示而非精确计算"这个
一贯原则，用户可以不采纳。

**涉及文件**：`hypothesis.py`（新增 `suggest_experiment_design()`）、
新增 `workflows/experiment_design.yaml`、`app.py`（建议展示 + 确认
交互）。

**验收点**：新增测试（同 `test_retrospective.py` 的一贯打桩手法，
不真的调用 LLM）——workflow 调用正确组装输入、返回结果正确解析为
建议列表、`uncertainties` 为空时不发起调用直接返回空列表、用户
未确认前不触发 `run_hypothesis_worlds()`。

---

## 5.（批次四）因果线可视化整合：列表总览 → 图谱 + 贡献拆解

**现状**：`app.py::_render_causal_lines_overview()` 只是按线聚合
的时间点列表；`causal_graph.py`（因果图边集合）、`attribution.py`
（贡献拆解）是两个独立模块，因果线视图里看不到它们。

**方案**：

1. `app.py` 因果线总览标签页新增一个子视图"🕸️ 关系图"（和现有
   列表视图并列切换，不替换）：调用 `causal_graph.build_causal_
   graph(history)` 拿到边集合，配合 `causal_lines` 节点列表，用
   `st.graphviz_chart()` 画一张有向图（节点是因果线/关键字段，边是
   `causal_links` 声明的因果关系）。
2. 图上每个节点，点击后在旁边（`st.session_state` 记录选中节点）
   展开 `attribution.summarize_contributions()` 对该节点对应字段
   的贡献拆解报告（如果这个字段在 `settings.objectives` 里注册过
   objective；没注册的节点只展示因果链本身，不强行凑一份贡献报告）。
   如果批次一已完成，额外展示该因果关系在知识库里的 `evidence`/
   `valid_range`（跨模拟视角），没有批次一也不影响本批次独立可用。

**范围克制**：不引入 NetworkX/D3.js 等重量级图计算或前端库——
`st.graphviz_chart()` 是 Streamlit 内置能力，够用来画一张示意图；
不做"自动布局优化"这类图美化，Graphviz 默认布局即可。

**风险与建议节奏**：`st.graphviz_chart` 在因果线/因果链数量较多
时可读性未知，建议先用 `life_sim` 几个真实、跑得比较久的实例数据
实际跑一遍看图是否可读；如果一张大图挤在一起看不清，退回"按线
分组的多个小图"（每条线一张图，只画和这条线直接相关的边），不
需要重新设计数据结构，只是渲染时多循环几次。

**涉及文件**：`app.py`（新增图谱子视图）。不新增任何数据结构或
计算逻辑，纯展示层整合。

**验收点**：这一批主要是 UI 改动，自动化测试覆盖"边集合正确传给
`st.graphviz_chart` 的输入格式转换函数"（把 `build_causal_graph()`
输出转成 DOT 语法字符串的转换函数可以单独抽出来单测，不需要测
Streamlit 渲染本身）；人工验收"图是否可读"这条按上面的建议节奏
做，不是自动化测试能替代的。

---

## 6.（批次五）多主体 Entity/Relationship：从自由文本到结构化图

**现状**：`multi_entity_mode` 下 `entities` 是字典结构，但实体之间
的关系主要靠 `narrative` 自由文本表达；`settings.relationships`
（阶段三十六第二批已结构化 `delay_steps`/`propagation_path`/
`reversible`）里的 `from`/`to` 目前不要求、也不校验是否对应一个
真实存在的 `entities` id。

**方案**：

1. 新增 `multi_entity.build_entity_graph(manifest) ->
   Dict[str, List[str]]`：遍历 `settings.relationships`，把
   `from`/`to` 都能在 `manifest.settings.entities`（或
   `SimState.vars.entities`，需先确认 `multi_entity_mode` 下实体
   字典的实际存放位置）里找到对应 id 的记录，构造一个简单邻接表
   （`{entity_id: [相关 entity_id, ...]}`）。`from`/`to` 匹配不上
   真实 entity 的记录**静默跳过**，不阻断、不报错——`narrative`
   自由文本本来就不做强校验，结构化关系列表也不应该突然变严格。
2. 新增 `multi_entity.find_relationship_path(graph, start_id,
   end_id, *, max_depth=3)`：简单 BFS，返回 `start_id` 到 `end_id`
   之间最短的关系路径（entity id 列表），找不到或超过 `max_depth`
   返回 `None`。这是"从 A 到 B 有几步关系路径"这类查询的最小实现，
   不做加权最短路径（`strength`/`reversible` 这些字段不参与路径
   计算，只做纯连通性判断）。
3. `app.py` 多主体实例详情页新增一个可选的"关系路径查询"小工具：
   下拉选两个实体，展示 `find_relationship_path()` 的结果（如果
   有）。查询结果只是**展示给用户看**，不反过来影响推进 prompt——
   本批次不做"把路径信息自动喂给 `advance_step`"这一层（如果后续
   发现有用，可以在下一批加，不在本批范围内，同批次二 `production`
   关系"先建结构、prompt 接入留后面"的克制方式一致）。

**风险**：中——需要先确认 `entities` 字典在当前代码里的确切位置
和 id 命名规则（不同模板/不同阶段可能有细微差异），实现前建议先
读一遍 `multi_entity_mode` 相关的现有代码（`state_model.py`/
对应 engine 分支）确认接口，不要凭空假设结构。

**涉及文件**：新增 `multi_entity.py`（或者如果已有同名模块，追加
到其中）、`app.py`（关系路径查询小工具）。

**验收点**：新增测试——`build_entity_graph()` 对匹配上/匹配不上
entity id 的关系分别处理正确、`find_relationship_path()` 的 BFS
正确性（含找不到路径、起点终点相同、超过 `max_depth` 三种边界
情况）。

---

## 7.（批次六，建议放最后）Branch Engine：merge + skill/prompt 版本记录

**现状**：`branch_manager.py` 有 fork/switch/delete/compare，没有
"合并分支"操作，也没有记录"这段历史是用哪个版本的 skill/prompt
跑出来的"。

**方案**：

1. `merge` 退化为"指针切换"而不是真正的字段级三路合并（原因见
   上游盘点文档 4.6 节——两条分支的 `vars` 可能已经不可调和地分歧，
   自动合并大概率产生语义错误的结果）：新增
   `branch_manager.merge_branch(data_dir, sim_id, *, source,
   target, from_step)`——把 `target` 分支从 `from_step`
   **之后**的历史，替换为 `source` 分支从 `from_step` 之后的历史
   （`from_step` 及之前两条分支历史必须完全一致，否则拒绝执行并
   报错，避免用户在两条已经严重分歧的分支之间做出语义不明的合并）。
   本质是"以 `source` 为准覆盖 `target` 的后续部分"，不是合并两边
   都有价值的内容——这是能在现有存储模型上安全实现、又不需要
   人工介入判断"字段冲突时听谁的"的最大范围。
2. 版本记录：`SimState` 新增可选字段 `skill_version`——`advance()`
   落盘前尝试读取对应模板 `SKILL.md` 文件的最后修改时间戳（`os.
   stat().st_mtime`，格式化成 ISO 字符串），写入这一步的
   `skill_version`；文件读取失败（比如独立运行环境路径不对）时
   静默留空，不阻断推进。这是"不引入 git commit hash 等额外基础
   设施"前提下最小成本的版本区分方式——同一个 `SKILL.md` 文件只要
   没被修改过，`skill_version` 就不变，足够回答"某次 skill 改动
   之前/之后的预测质量对比"这类粗粒度问题。

**范围克制**：不做真正的字段级合并算法，不引入版本控制系统依赖；
`skill_version` 只是文件时间戳，不是语义化版本号（用户如果想要
更精确的版本管理，应该用外部 git 仓库管理 skill 文件本身，这不是
`world_simulator` 需要重新发明的能力）。

**风险**：本批改动面最大——`merge_branch()` 涉及历史数据的结构性
调整，需要仔细处理"合并后 `SimStore.load_history()`/`branch_
manager.list_branches_detailed()` 等既有读取逻辑是否还成立"，
建议放在六批里最后做，且落地后先在测试环境用几个真实分支验证一遍
再实际使用；`skill_version` 部分风险较低，可以考虑拆出来单独先做
（不强制要求和 `merge` 绑在一个批次）。

**涉及文件**：`branch_manager.py`（新增 `merge_branch()`）、
`engine/advance.py`（写入 `skill_version`）、`state_model.py`
（`SimState.skill_version` 字段文档）。

**验收点**：新增测试——`merge_branch()` 在 `from_step` 之前历史
一致时正确执行、不一致时正确拒绝并报错、合并后 `load_history()`
读到的历史连续无空洞；`skill_version` 正确读取文件时间戳、文件
不存在时静默留空不报错。

---

## 8. 不建议纳入本轮的方向

**4.7 Hierarchical Agent / Dynamic Cognition Router 完整调度框架**
——上游盘点文档已经给出理由：现有"分层第一步"
（`hierarchical_agent_mode` + `background_entities`）本身还没有
真实使用反馈证明这个方向有效，"动态路由"规则更是没有足够真实案例
可以归纳。这一条应该先归入"A 类验证清单"（见上游盘点文档第 2
节），真实跑几轮之后再决定要不要单独写完整版方案，本文档不展开。

---

## 9. 参考资料

- `next_doc/world_simulator_remaining_minimal_systems_gap_survey_
  plan.md`（第七轮盘点，本文档 6 个批次的直接来源）。
- `next_doc/world_simulator_universal_world_model_upgrade_plan.md`
  （各系统精简版的原始实施记录出处）。
- `next_doc/world_simulator_event_driven_engine_and_full_
  architecture_plan.md`（阶段三十六，`relationship.py` 结构化字段
  与"新增字段 + 旧路径保留"节奏的既有参照）。
