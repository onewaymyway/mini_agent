# world_simulator 从"最小起步版"推进到完整架构：多尺度独立推进 +
Influence Field + Event-Driven 决策引擎 + 反身性（第六轮）

> **状态**：本文档只做方案设计，尚未开始实施。按用户明确要求，把
> 之前几轮方案里标注为"最小起步版/暂不建议，需要触发条件"的几个
> 方向重新规划成可以真正推进到完整形态的批次，包括之前评估为
> "暂不建议"的 4.8（完整 Event-Driven 决策点引擎 + 反身性）。
>
> **实施进度**（2026-09-20 更新，见 `PROJECT.md` 阶段三十六）：
> - ✅ **第一批（2.1 节，Event-Driven 决策点引擎 + Observer View
>   完整版）已完成**：`engine/advance.py::fast_forward()`、`app.py`
>   "⏩ 快进"入口、`autopilot.py` 的 `settings.autopilot_fast_forward`
>   开关均已落地，新增 8 个测试用例，全量测试套件 380 个全部通过。
>   按方案原文建议，先只在自动挡场景观察几天真实运行的摘要质量，
>   再考虑要不要把手动挡"快进"按钮推广为默认可见入口（已经可用，
>   只是还没有大量真实使用反馈）。
> - ⏳ 第二批（2.2 节 Influence Field / Relationship 完整机制）、
>   第三批（2.3 节多尺度因果线真正独立推进）、第四批（2.4 节反身性
>   最小诠释）尚未开始，按方案第 3 节的顺序依次推进。
>
> **上游文档**：
> - `next_doc/world_simulator_universal_simulator_gap_analysis_and_
>   roadmap_v2_plan.md`（第二轮，阶段二十七~三十一）4.20/4.24/4.25
>   三节——这三节都**已完成"范围克制的最小起步版"**（分别是：给
>   LLM 一句节奏提示、一份不参与推进的静态关系列表、一句"多让背景
>   线自己演化"的提示），原方案标注的完整架构本文档才是要推进的
>   目标。
> - `next_doc/world_simulator_realism_transparency_and_
>   retrospective_roadmap_v3_plan.md`（第三轮，阶段三十二）4.8
>   节——之前评估为"P3，暂不建议"，本文档重新规划为可推进的方向。
>
> **和上游文档的关系**：不重复设计已经讨论过的背景/取舍分析，只在
> 每一节开头简述"最小版做了什么、完整版还差什么"，重点放在"完整
> 版具体怎么落地、边界在哪里、怎么控制风险"。

---

## 1. 背景：为什么这几个方向之前只做了最小版

第二轮方案（v2）里 4.20/4.24/4.25 和第三轮方案（v3）里 4.8 有一个
共同特点：它们都指向同一件事——**把"用户请求一步、LLM 同步算一步"
这个从阶段一沿用至今的推进模型，改造成更接近参考文档设想的"世界
按自己的节奏演化，决策点是被识别出来的，不是每一步都强行生成"**。
这类改动直接触及项目最核心的推进循环（`engine/advance.py`），
一直被判断为"改动面大、风险高、收益不确定"，所以前几轮都只做了
不触碰推进循环本身的"提示层"最小版本：

| 方向 | 上游节号 | 最小版做了什么 | 完整版还差什么 |
|---|---|---|---|
| 多尺度因果线独立推进 | v2 4.20 | 给 LLM 一句"这条线大概率没动静"的提示，不强制 | 每条线真正有自己的本地步数、按自己节奏独立发起 LLM 调用 |
| Influence Field / Relationship | v2 4.24 | `settings.relationships` 静态列表，`engine` 完全不读取 | 影响半径/延迟/传播路径喂给推进 prompt，形成"提示引擎记得住"的效果 |
| Observer View / 世界独立演化 | v2 4.25 | `observer_mode` 提示 LLM 少生成决策点 | 系统自己判断"这几步要不要真的停下来给用户看"，不只是让 LLM 少生成 |
| Event-Driven 决策点引擎 | v3 4.8（前半） | 无（v3 判定为暂不建议） | 与上面 Observer View 完整版本质是同一件事——见第 3.3 节合并处理 |
| 反身性 Reflexivity | v3 4.8（后半） | 无（v3 判定为暂不建议） | 单机单用户场景下缩小范围后的最小可行诠释 |

本文档按"现在就要推进"的前提重新规划这四个方向（Observer View
完整版和 Event-Driven 决策点引擎合并成一节，因为拆开做没有意义），
但**不改变项目一贯的风险控制原则**：每个方向都设计成"新增开关 +
旧路径完全保留"的形态，不会因为这轮推进而破坏任何已有行为。

---

## 2. 四个方向的完整方案设计

### 2.1（建议第一批）Event-Driven 决策点引擎 + Observer View 完整版

> **实施状态：✅ 已完成**（2026-09-20，`PROJECT.md` 阶段三十六第一批）。
> 落地情况与方案原文的对应关系：`fast_forward()` 完全按下面的方案
> 落地，唯一的补充是新增了 `decision_context`/`chosen_by`/
> `allow_custom_options` 三个透传参数（方案原文未展开这一点）——
> 自动挡场景如果不透传这些参数，快进期间遇到候选选项时会退化成
> "不做选择、由 skill 自行决定默认走向"，丢失用户设置的风险偏好/
> 原则/情境化策略，因此在实现时补上。

**为什么合并**：v2 4.25 想要的"系统自己判断该不该停下来给用户看"，
和 v3 4.8 想要的"平时快进、只在关键事件出现时暂停生成决策点"，
拆开看是同一个底层机制的两个说法——都是"把很多步的同步推进包装成
一次'快进到下一个值得看的点'的调用，而不是逐步展示"。

**关键发现（降低本批风险的基础）**：这个机制不需要重新设计推进
循环本身，因为两个关键信号已经存在：
- `SimState.major_decision`（阶段十九起就有）——LLM 自己判断"这一步
  是不是重大决策"，`review_mode == "pause_on_major_decision"` 已经
  在用它暂停自动挡。
- `SimState.options`——已经允许为空数组（5.2 节"DecisionOpportunity
  批次级聚合"的前提就是"很多连续步骤本来就没有 `options`"）。

也就是说，"世界快进、只在关键点停下"所需要的两个判断依据**已经
存在于每一步的返回结果里**，缺的只是"把很多步的循环包装起来，
遇到信号就停、没遇到就继续调用下一步"这层外壳，不需要改
`advance()` 内部的任何 prompt/字段逻辑。

**方案**：
- 新增 `engine/advance.py::fast_forward(cfg, workspace_root,
  data_dir, sim_id, *, max_steps, stop_on_major_decision=True,
  stop_on_options=True)`：循环调用已有的单步 `advance()`，每步后
  检查 `next_state.major_decision` 或
  `bool(next_state.options)`，命中就停止；如果 `max_steps` 步都
  没命中，也停止。跳过的中间步骤**仍然逐一完整落盘**（不能因为
  "快进"就产生历史空洞，`attribution.py`/`retrospective.py`/
  `quality_signals.py` 等下游统计功能都依赖完整历史），只是返回值
  里额外带一份"跳过了哪几步"的摘要——复用 `retrospective.py` 已有
  的"读历史、几句话概括"手法，不是重新设计一套摘要生成逻辑。
- `app.py` 新增"快进"入口（和现有"推进 1 步"按钮并列，不替换），
  用户设定 `max_steps` 上限，点击后调用 `fast_forward()`，展示
  "跳过了 N 步的摘要 + 当前停在哪一步"，摘要下方可以展开看被折叠
  的每一步原始细节（数据本来就在，只是 UI 默认折叠）——这就是
  v2 4.25 设想的"双视角"效果的最小成本实现：不需要重新设计存储层
  或搞两套视图组件，只是"折叠 vs 展开"两种展示状态。
- `autopilot.py::run_batch_autopilot()`（对应 `project.yaml` 的
  `batch_advance_daily`）新增开关 `settings.autopilot_fast_forward`
  （默认 `False`）。为 `True` 时，自动挡从"固定推进 `steps` 步"
  改为调用 `fast_forward(max_steps=steps)`——这是 `observer_mode`
  的自然延伸：`observer_mode` 调整 LLM 的产出倾向（少生成决策点），
  `autopilot_fast_forward` 调整调度层"要不要为每一步都停下来等
  用户"，两者互补、可以同时开启。

**范围克制（不做的部分）**：不做"世界不依赖任何调度触发、自己按
真实时钟持续跑"的完全异步后台循环——那需要一个真正常驻的后台
daemon 进程，超出 `project.yaml` 的 cron/entrypoint 调度模型能力
范围（每次 entrypoint 调用都是"启动-执行-退出"的一次性进程，不是
常驻服务），且项目是"本地单机小工具"定位，不需要为此重新设计
部署形态。`fast_forward()` 仍然是"被一次调用触发，内部循环快进"，
这是在现有调度模型约束下能做到的最接近参考文档设想的形态。

**涉及文件**：`world_simulator/engine/advance.py`（新增
`fast_forward()`）、`app.py`（快进入口 + 折叠/展开摘要 UI）、
`world_simulator/autopilot.py`（`run_batch_autopilot()` 新增分支）、
`state_model.py`（`settings.autopilot_fast_forward` 文档化）。

**验收点**：新增 `tests/test_fast_forward.py`——命中
`major_decision` 立即停止且返回正确的"跳过步数"；命中非空
`options` 停止；达到 `max_steps` 都没命中时停止并生成摘要；跳过的
每一步都能在 `SimStore.load_history()` 里完整读到（验证没有历史
空洞）；`autopilot_fast_forward=False`（默认）时 `run_batch_
autopilot()` 行为与现在完全一致。

**风险与建议节奏**：这是四个方向里改动面最小、复用度最高的一个
（复用 `advance()` 单步逻辑做外层循环封装，不改 `advance()` 内部
任何实现），建议排第一批。落地后先只接入自动挡
（`autopilot_fast_forward`）观察几天真实运行的摘要质量，确认
"跳过了 N 步"的摘要确实有信息量、不是空话之后，再接入手动挡 UI
的快进按钮。

---

### 2.2（建议第二批）Influence Field / Relationship 完整机制

**最小版回顾**：`world_simulator/relationship.py` 目前只有
`settings.relationships`（`from`/`to`/`kind`/`strength`/`note`）
静态列表，`engine.advance()` 完全不读取，纯粹是给用户查看的
结构化记录。

**方案**：
- 每条 `relationship` 新增三个可选字段（命名风格对齐 5.1 节
  `ChoiceOption` 的 `risk_level`/`reversibility`）：
  - `delay_steps`：整数，这条关系的影响延迟几步后才体现（默认 0，
    即时生效）。
  - `propagation_path`：字符串数组，如果是间接影响，经过哪些
    中间实体/因果线的 `id`（不填表示直接影响）。
  - `reversible`：`"reversible"`/`"hard_to_reverse"`/
    `"irreversible"` 之一，这条关系造成的影响好不好撤销。
- 新增 `spec_generator._resolve_relationship_hint(settings)`（同
  `_resolve_causal_lines_hint()` 的既有写法），把
  `settings.relationships` 转成一句话提示喂给 `advance_step`/
  `world_evolve` 的 prompt：明确写清楚"这是给你的参考信息，不是
  要你机械计算数值传播"，延续项目一贯"提示而非强制约束"的风格——
  **不做**自动化的数值传播计算引擎（比如图遍历、强度衰减公式），
  避免"看起来是精确的因果引擎、实际是规则拍脑袋"的伪确定性，这
  一点和 `declared_causal_graph`/`causal_lines` 的既有设计哲学
  完全一致。
- 新增 `relationship.py::queue_pending_effect()` /
  `due_pending_effects()` 一对轻量函数：当一条 `delay_steps > 0`
  的关系被判断为"源头已经触发"（由 LLM 在 `advance_step` 的输出
  里用一个新的可选字段 `triggered_relationships`——字符串数组，
  引用触发的 `relationship` 索引/id——自己声明，不是引擎自动侦测）
  时，记一条"预计第 N 步生效"的待办，存进
  `manifest.settings.relationship_pending_effects`；到了对应步数，
  `_resolve_relationship_hint()` 会在提示文本里特别标注"之前触发
  的这条关系这一步该体现效果了"，供 LLM 参考。

**范围克制（不做的部分）**：这仍然是"更丰富的提示"，不是参考
文档设想的、有精确影响半径公式和自动数值传播的完整 Influence
Field 引擎——项目一贯避免这类"伪装成精确因果计算、实际只是规则
拍脑袋"的东西，这个范围就是这个方向在本项目里"完整"的合理形态。

**涉及文件**：`relationship.py`、`spec_generator.py`、
`workflows/advance_step.yaml`/`world_evolve.yaml`（新增
`relationship_hint` 占位符和 `triggered_relationships` 输出字段
说明）、三个模板 `SKILL.md`、`state_model.py` 字段分组索引。

**验收点**：新增测试覆盖——关系提示文本生成正确（含
`delay_steps`/`propagation_path`/`reversible` 的自然语言转换）；
`queue_pending_effect`/`due_pending_effects` 的入队/到期正确性；
无 `relationships` 时提示文案为空；`triggered_relationships`
不存在或为空数组时不报错。

**风险**：只涉及 prompt 扩展和字段新增，不碰推进循环核心，风险
和第四/五轮里已完成的字段级扩展（5.1/5.3/5.4）同一量级，可控。

---

### 2.3（建议第三批，且要求先小范围验证）多尺度因果线真正独立推进

**最小版回顾**：`causal_lines[i].advance_every_n_steps` 只是喂给
LLM 的节奏提示（`_lines_due_this_step_hint()`），不强制；所有线
仍然共享同一个全局 `step`，一次 `advance_step`/`world_evolve`
调用里由同一个 LLM 判断所有线的变化。

**方案**：
- 每条因果线新增 `local_step`（这条线自己的推进计数，从 0 开始，
  只有真正被推进时才 +1）和 `owned_vars`（字符串数组，声明这条线
  独占哪些 `vars` 顶层字段，不同线的 `owned_vars` 不能重叠——这是
  避免"两条线各自独立发起调用、同时改同一个字段"数据竞争问题的
  前提条件，创建阶段就要求 LLM 或用户显式声明清楚，不能留空指望
  引擎自动推断）。
- 新增 `engine/advance_independent.py::advance_lines(cfg,
  workspace_root, data_dir, sim_id)`：全局 `step` 推进时，只对
  `local_step % advance_every_n_steps == 0` 的线分别发起一次
  **只包含这条线自己 `owned_vars` + 因果链片段**的独立 LLM 调用
  （新增 `workflows/line_evolve.yaml`），而不是像现在这样一次调用
  处理所有线。没有到点的线本步不发起任何调用，`vars` 里对应字段
  原样保留。其它线需要读取这条线状态时，读的是"上一次全局同步点"
  的快照（避免读到"本步还没跑完"的中间态）。
- 全局 `step` 仍然是唯一的"时钟心跳"，用户看到的"当前第几步"不变，
  只是背后决定了"这一步到底要为哪些线真正发起 LLM 调用"。

**范围克制（不做的部分）**：不做完全并行/异步的调用（多条线的
独立 LLM 调用仍然是本步内顺序执行，不是真正的并发），也不允许
跨线互相修改对方的 `vars`（跨线因果影响仍然只通过
`causal_links`/`declared_causal_graph` 以"提示"形式喂给对方，
下一次那条线被推进时自己决定要不要体现）——这是刻意避免"两条线
同时写同一份数据"的一致性问题，把"完整多尺度独立仿真"简化为
"错峰调用 + 只读快照"，这是能在现有单进程同步架构里落地、又不
引入数据竞争风险的最大范围。

**为什么放最后、要求先小范围验证**：这是四个方向里改动面最大的
一个，`owned_vars` 这种"字段归属声明"正是阶段十七暴露过风险的
那类设计（字段粒度一旦想岔了，后续很难改，且会连带影响下游所有
读 `vars` 的功能）。建议：
- 新增独立开关 `settings.independent_line_advance`（默认
  `False`），完全不改动现有 `advance()`/`advance_step.yaml` 的
  行为，两条路径长期共存。
- 只在 `life_sim` 模板小范围验证（同 `belief_state_separation`
  第一批的谨慎做法），先人工跑 3~5 次真实多因果线模拟，确认
  "错峰调用"不会让用户感觉"某条线莫名其妙很久没动静、体验割裂"，
  再考虑要不要推广到其它模板。

**涉及文件**：`state_model.py`（`causal_lines[i].local_step`/
`owned_vars`）、新增 `workflows/line_evolve.yaml`、新增
`engine/advance_independent.py`、`app.py`（因果线总览展示独立
推进状态）。

**验收点**：新增 `tests/test_independent_line_advance.py`——两条
`advance_every_n_steps` 不同的线，验证只有到点的线真正发起调用；
`owned_vars` 重叠时创建/推进阶段报错阻止；某一步没有任何线到点时
的空推进（全局 `step` 依然 +1，但没有任何 LLM 调用）；跨线读取
用的是快照而不是本步中间态。

---

### 2.4（建议排最后，且明确"可以直接放弃"）反身性（Reflexivity）最小诠释

**原方案判断保留**：参考文档设想的"反身性"（模拟预测本身传播到
现实世界，影响现实中的人的行为，从而反过来验证或推翻预测）在
"本地单机、单用户"的工具形态下，缺少"大量用户看到同一份预测"这个
前提条件——没有对应的现实场景可以承载，这一点不因为本轮"要推进
完整方案"而改变，本节不是要做参考文档设想的完整版本。

**范围极小的个人反身性诠释**：单用户工具里唯一有意义的"反身性"
落地形态，是"用户看到某次模拟的复盘结论/预测后，是否据此调整了
后续的行为模式"——这是反身性概念在个人场景下的最小映射（"预测
影响了后续决策"），不涉及"多用户""预测公开发布影响现实"这些
没有前提的完整设想。
- `retrospective.py` 生成的复盘报告落盘后，如果用户之后创建新
  模拟或继续推进已有模拟时的选择模式，与复盘报告里指出的"败因/
  建议"方向一致（比如复盘说"过度冒险是主要败因"，用户后续选择
  明显更保守），`knowledge_base.py` 里对应知识条目追加一条
  `[stated]` 风格的标注："用户在看到这条复盘后，后续表现出更
  保守的选择倾向"——注意这只是**记录一个观察到的相关性**，不是
  断言因果，也不用于任何自动化的画像调整或提示词注入。

**范围克制（不做的部分）**：不做任何基于这个标注的自动化行为
（不会因为记录了"用户变保守了"就自动调整后续 prompt 的策略画像
或建议倾向——那是 4.5 节 Agent Preview 反馈闭环的范畴，不是本节
该做的事）；不做跨模拟、跨用户的反身性统计。

**为什么排最后、且可以直接放弃**：这是本文档四个方向里价值最不
确定的一个——不确定用户是否关心"系统记录我有没有听劝"这件事，
甚至可能显得像监视/说教，与项目一贯"提示而非评判"的语气不符。
建议：先完成 2.1/2.2/2.3 三个方向，观察真实使用几轮之后再评估
要不要做这一节；如果用户反馈"不需要这个"或者觉得"被系统盯着
听没听劝"很奇怪，直接从计划里删除即可，不强行凑数实施。

**涉及文件（如果确认要做）**：`knowledge_base.py`（新增标注
写入逻辑）、`retrospective.py`（复盘报告落盘时记录一个可比对的
"建议方向"摘要，供后续比对用）。

**验收点（如果确认要做）**：新增测试验证"用户后续选择与复盘建议
方向一致时正确追加标注"、"不一致或无法判断时不追加"、"标注只是
记录，不影响任何 prompt 内容"。

---

## 3. 建议实施顺序（依赖关系 + 风险排序，不代表价值排序）

```text
第一批：2.1 Event-Driven 决策点引擎 + Observer View 完整版 ✅ 已完成
  复用 major_decision/options 两个已有字段做外层循环封装，
  不改 advance() 内部实现，改动面最小、用户直接受益最大
  （所有模拟都能用上"快进"），建议先做。

第二批：2.2 Influence Field / Relationship 完整机制
  只涉及 prompt 扩展和字段新增，不碰推进循环核心，风险和
  已完成的字段级扩展同一量级。

第三批：2.3 多尺度因果线真正独立推进
  改动面最大、涉及新的字段归属（owned_vars）设计，按方案要求
  新增独立开关、只在 life_sim 模板先小范围人工验证，确认体验
  和数据一致性都没问题后再推广。

第四批（可选，可放弃）：2.4 反身性最小诠释
  价值最不确定，建议观察前三批真实使用反馈后再决定要不要做。
```

每一批落地后都应该：（1）新增独立开关，默认关闭，不改变现有
路径的行为；（2）跑一次全量测试套件确认没有破坏任何已有逻辑；
（3）更新对应的 `PROJECT.md` 条目和本文档顶部的实施进度状态。

---

## 4. 风险与验证方式（汇总）

- **这是这几轮方案里首次真正触及推进循环外层调用方式的改动**
  （2.1/2.3 两节）——虽然都设计成"新增开关 + 旧路径完全保留"，
  但"新增一条完整可用的第二条路径"本身的实现和测试成本，比之前
  几轮"在现有单一路径上加字段"的改动模式要高，需要更谨慎地
  逐批验证，不要因为本轮是"用户明确要求推进"就跳过每批之间的
  全量测试和人工抽查。
- **2.3（独立推进）的 `owned_vars` 设计是本文档最大的风险点**：
  字段归属声明如果设计得不合理（比如某个字段其实需要被多条线
  共同影响，被迫塞进某一条线的 `owned_vars`），后续很难无痛
  修改，且会影响所有读 `vars` 的下游功能（分析统计、归因报告、
  Hypothesis Engine 等）。这也是本节被单独强调"先在 life_sim
  小范围人工验证"的原因，参照 `belief_state_separation_plan.md`
  第一批"先跑 3~5 次真实模拟确认效果"的既有节制原则——且这次
  务必真的执行这道验证，不要重蹈 4.3 节"验证步骤被跳过"的
  覆辙（见 `PROJECT.md` 阶段三十五记录的风险说明）。
- **2.1 的"快进摘要质量"是唯一的产出质量风险**：如果
  `fast_forward()` 生成的"跳过了 N 步"摘要空洞、没有信息量，
  这个功能会变成"用户点了快进但看不出跳过的这些步到底发生了
  什么"的体验倒退——建议参照 `retrospective.py` 落地时"先人工
  抽查几次真实输出，确认没有空泛评价再推广"的做法。
- **2.4（反身性）的最大风险是"用力过猛"**：如果标注/记录的
  方式让用户觉得"系统在评判我有没有听劝"，会破坏项目一贯"提示
  而非评判"的产品语气——这也是这一节被放在最后、且明确"可以
  直接放弃"的原因。

---

## 5. 参考资料

- 用户上传文档：《万能模拟器到底如何构建？——从世界模型、因果
  系统到可交互的未来世界引擎》第二十~二十二节（Event-Driven
  决策点）、第五十二节（反身性）。
- `next_doc/world_simulator_universal_simulator_gap_analysis_and_
  roadmap_v2_plan.md`（4.20/4.24/4.25 三节的最小起步版设计与
  实施记录）。
- `next_doc/world_simulator_realism_transparency_and_
  retrospective_roadmap_v3_plan.md`（4.8 节最初"暂不建议"的
  判断依据）。
- `next_doc/world_simulator_belief_state_separation_plan.md`
  （"先小范围人工验证再推广"的既有节制原则参照，以及"验证被
  跳过"的风险记录）。
