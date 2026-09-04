# 主动性子系统整合方案（目标树 / Goal 系统 / 成长顾问 / 能力学习）

> 前置阅读：
> - `next_doc/goal_tree_system_plan.md`（目标树）
> - `next_doc/growth_advisor_design.md` + `docs/growth-advisor-guide.md`（成长顾问）
> - `next_doc/persona_capability_learning_design.md`（能力学习）
> - `next_doc/growth_advisor_goal_cron_integration_plan.md`（已完成的窄范围
>   前置工作——成长顾问单向对接 Goal，见本文档 §1 与 §6 的关系说明）
> - `src/mini_agent/perception/goal_backlog.py`、
>   `src/mini_agent/evolution/soft_goal_deriver.py`、
>   `src/mini_agent/evolution/growth_advisor.py`、
>   `src/mini_agent/evolution/capability_learning.py`

## 0. 背景

mini_agent 现在有四个系统都在承担"主动性"相关的职责，但分布在两条没有
被显式设计过关系的轴线上：

**执行轴**（负责"怎么把一件事做完"）：
- 目标树（`goal_backlog.py`/`goal_tree_decomposer.py`）：层级结构、
  停滞检测、自动分解，只管结构不管目标从哪来。
- Goal 系统（`goal_mode/`）：真正的执行循环引擎，配合
  `ResourceArbiter`、fairness scheduling、judge 体系。

**信号轴**（负责"该主动帮谁做什么"）——三条**并行发育**的
"证据 → 候选 → 用户反馈"管线：
- `soft_goal_deriver`：扫 capability_map / work_index / lesson_review，
  产出候选直接写入 `GoalBacklog`（`source="agent_derived"`），是三条线
  里唯一真正打通到执行轴的一条。
- `growth_advisor`：扫用户 memory 的 tags/summary，产出"调研报告推给
  用户"，自成一套 `GrowthBacklog` + 报告生成 + 推送节流，服务对象是
  **用户自己的成长**。
- `capability_learning`：扫 wiki 知识大纲缺口，产出"Agent 该补哪块
  知识"，自成一套 `CapabilityTrack` + 异步问答队列，服务对象是
  **Agent 自己的人设/知识**。

三份设计文档里都写了同一句话——复用"证据 → 候选 → 采纳/忽略反馈"这个
范式——但复用的只是范式本身，没有复用实现，也没有打通通道。这是本方案
要处理的核心问题。

`growth_advisor_goal_cron_integration_plan.md` 已经把"成长顾问 ↔ Goal"
这一对关系单独打通了（对齐分析 + 一键落地 + 回访读取 Goal 状态），但
它明确是**窄范围**的两两打通，`capability_learning` 完全没有被纳入，
调研能力、推送节流、优先级排序也都还是三套独立实现。本方案是在那个
基础上做的**系统性**整合，而不是重复劳动。

## 1. 核心理念

产品的终极定位是成为用户的**个人 AI 助手 / 数字分身**：自动的、持续的
为用户提供调研、分析、执行等服务。这个定位要求的不是"三个功能都做得
更强"，而是三个前提：

1. **用户面对的应该是一个大脑，不是三个助理**——候选建议无论来自哪条
   管线，用户体验上应该是同一个收件箱、同一套心智模型，只是分类不同。
2. **调研能力应该被沉淀成基础设施，而不是每条线各写一遍**——"给一个
   主题做调研"这件事，不应该因为受益人是用户还是 Agent 自己，就要
   两套代码、两套合规过滤标准。
3. **一切主动产出都应该走同一条执行/资源仲裁通道**——"占用资源去做一
   件事"不该因为触发源不同就绕开已有的公平调度机制，这既是稳定性
   问题（避免叠加卡住 daemon），也是一致性问题（用户对"Agent 在忙
   什么"应该有统一的可观测入口）。
4. **知识的积累应该双向、跨越受益人边界**——用户认真采纳的成长报告，
   理应反哺 Agent 对这个用户所在领域的理解；不能"服务完用户就丢掉"。

这四条不是四个独立的改进点，而是同一句话的四个侧面：**"主动性"是
一个统一能力，四个系统只是它面向不同受益人（用户成长 / 用户目标
执行 / Agent 自身知识 / Agent 自身行为）的四个投影，投影可以不同，
底层机制应该共享。**

## 2. 现状的具体问题

1. **三个收件箱，一个大脑**：用户要在成长顾问 tab、能力学习 tab、
   目标树/goal backlog 里分别处理反馈，认知负担重，且互不感知彼此的
   存在（成长顾问不知道能力学习刚好也在调研同一个主题）。
2. **调研能力重复造轮子**：`capability_learning.py` 已经有相当完整的
   调研 SubAgent 基础设施（`make_web_search_retriever` /
   `make_agent_retriever` + `§13.3-g` 合规过滤），`growth_advisor.py`
   的报告生成是完全独立的另一套模板/LLM 起草逻辑，两边标准不一致。
3. **采纳后执行路径不统一**：只有 `soft_goal_deriver` 走目标树 →
   执行引擎 → `ResourceArbiter`；`growth_advisor`/`capability_learning`
   采纳后各自跑自己的 cron，有自己的并发/节流参数，不受统一的公平
   调度和资源仲裁约束。
4. **知识沉淀单向不闭环**：`capability_learning` 的调研结果沉淀进
   wiki 给 Agent 自己用；`growth_advisor` 的调研报告用户看完即止，
   即使用户认真采纳，Agent 也没有因此更懂这个领域。
5. **顶层信号源缺失**：三条管线各自独立扫描 `memory`/`capability_map`/
   `wiki_gap`，`work_index`/`WorkThread`（"用户当前在做什么"的雏形）
   只服务于 `soft_goal_deriver` 一条线，没有被其余两条线共享作为调研
   方向排序依据，容易方向冲突或重复劳动。
6. **推送节流各自为战**：`growth_advisor` 有自己的
   `notification_max_per_day`，`capability_learning` 问答队列、
   `watchlist` 通知又是各自的节流逻辑，缺一个跨系统的"今天总共该给
   用户推几条主动消息"总闸，整合之后如果不统一反而更容易骚扰用户。

## 3. 目的

- 让用户感知到的"主动服务"体验统一、可控、可预期，而不是三套互不
  相关的自动化脚本各自发消息。
- 让"调研"这个核心能力只维护一套实现、一套合规标准，降低维护成本，
  也让改进（比如提升调研质量）能同时惠及所有受益方。
- 让所有占用资源的主动行为可以被同一套公平调度/资源仲裁看到，降低
  daemon 卡死/资源抢占的风险，同时给用户一个统一的"Agent 在忙什么"
  可观测入口。
- 让 Agent 对用户所在领域的理解可以真正随时间积累，逐步逼近"数字
  分身"的定位，而不是每次调研都从零开始。

## 4. 改进方案

### 4.1 候选层对齐（数据模型级）

给 `soft_goal_deriver` / `growth_advisor` / `capability_learning` 三条
线的候选数据结构对齐一组公共字段：`domain`
（`user_growth` / `agent_knowledge` / `agent_behavior`）、
`source_initiator`、`evidence_refs`、`confidence`。**不要求合并底层
存储实现**——`GoalBacklog`/`GrowthBacklog`/`CapabilityTrack` 各自的
存储可以继续独立存在，只是对外暴露一个统一的只读聚合视图（新增一个
轻量的 `perception/initiative_inbox.py`，做法上参照
`perception/fairness_diagnostics.py`/`cross_goal_reference.py` 这类
"只读聚合、不侵入原模块"的既有模式），供 Kanban 消费。

### 4.2 采纳后统一走目标树执行

把 `growth_advisor`/`capability_learning` 的"候选被采纳"事件接入
目标树，生成对应的 `GoalNode`（`source_initiator` 标注真实来源），
交给已有的执行引擎 + `ResourceArbiter` + fairness scheduling 去跑，
逐步废弃两边各自独立的 cron 节流参数。`growth_advisor` 这一侧已经有
`adopt-goal` 单向对接可以复用/扩展；`capability_learning` 这一侧目前
完全没有对接，需要新增。

### 4.3 抽取共享 ResearchService

把 `capability_learning` 里已经跑通的调研 SubAgent
（`make_agent_retriever`/`make_web_search_retriever` +
`apply_compliance_filter`）抽成一个通用服务模块，`growth_advisor`
的报告生成、以及未来任何"需要调研"的 Goal 执行步骤都复用它，而不是
各写一份。抽取时保持向后兼容：两边现有的独立实现先并行保留，新调用
方优先接入共享服务，旧实现待验证稳定后再逐步退役，不做一次性替换。

### 4.4 双向知识沉淀

用户认真采纳（而不只是生成）的成长报告，回写进 wiki 知识库，打上
`source=user_growth` 区别于 Agent 自学的条目，复用
`capability_learning` 已有的 wiki 写入 + 判重路径。沉淀的判定标准
（"认真采纳"如何界定，例如是否要求用户停留时长/展开阅读/多次引用）
留到具体实现阶段结合现有反馈台账（`GrowthFeedbackLedger`）已有的信号
设计，本方案只定方向。

### 4.5 顶层"用户处境模型"驱动优先级

把 `work_index`/`WorkThread` 升级为三条线共享的顶层信号源：目标树的
目标、成长顾问该调研的方向、能力学习该补的知识大纲，从同一个"用户
当前处境"信号出发做统一优先级排序，而不是各自独立扫描后各出各的
候选、互不感知。这一步改动面最大，且直接影响候选生成的优先级排序，
建议先做**只读的排序建议**（比如在候选列表旁标注"与当前处境的
相关度"），不直接改变各模块现有的独立候选生成逻辑，避免一次性引入
不可预期的行为变化。

### 4.6 跨系统推送总闸

配合 4.2 的执行路径统一，在同一阶段把 `growth_advisor` 的
`notification_max_per_day`、`capability_learning` 问答队列、
`watchlist` 通知的节流参数收口到一个统一的"今日主动推送预算"，各
来源按候选的 `confidence`/`urgency` 抢占预算名额，而不是各自独立
配额简单叠加。

## 5. 为什么这样划分

- 4.1（收件箱统一）改动面最小、见效最快，且不侵入任何现有模块的
  内部逻辑，优先做。
- 4.2（执行路径统一）是解决"资源仲裁盲区"和"稳定性风险"的关键，
  也是让"数字分身能持续自主做事"这个定位真正落地的前提，其重要性
  仅次于 4.1，但涉及改动两个模块的对外行为，需要更谨慎的分阶段。
- 4.3（共享调研服务）是长期可维护性问题，不做也不影响当前功能正确
  性，但拖得越久两边实现分叉越大，未来合并成本越高，适合在 4.2
  验证稳定后紧接着做。
- 4.4（双向沉淀）依赖 4.3 提供的共享写入路径，逻辑上必须排在其后。
- 4.5（顶层信号源）改动面最大、影响最深（直接改变候选生成的优先级
  逻辑），且价值依赖前面几步先把通道打通才能体现，因此放最后，且
  第一版只做只读建议，不做强制改变，控制风险。
- 4.6（推送总闸）与 4.2 强相关（执行路径统一后节流逻辑天然需要
  收口），顺路在同一阶段处理。

## 6. 改进阶段划分

- **阶段一：候选层对齐 + 统一收件箱视图**（对应 4.1）—— **已完成**
  新增 `perception/initiative_inbox.py` 只读聚合三条线现有候选，
  Kanban 把三个独立 tab 收敛为一个带分类筛选的"主动建议"收件箱。
  不改动任何现有模块的内部逻辑与存储结构，纯展示层整合，风险最低，
  可独立上线。
  - 实现记录：新增 `GET /v1/self/initiative_inbox` 只读路由（异常隔离，
    单路来源读取失败不影响其它两路）；Kanban 新增「📥 主动建议」tab，
    支持按 `domain` 筛选，卡片给出跳转提示但不提供写操作（写操作仍在
    各自原生 tab，理由见 `docs/kanban-dashboard-guide.md` 对应小节）；
    `soft_goal_deriver` 一路的候选判定是"agent_derived 且
    created_at/last_touched_at 差值在 5 秒内"（即从未被单独 touch 过），
    不是新增字段，纯读取既有数据推断；新增
    `tests/test_initiative_inbox.py`（5 用例：空态/三路聚合/domain
    过滤/已处理 Goal 不重复展示/单路异常隔离），全部通过，回归测试
    `test_growth_advisor.py`+`test_capability_learning*.py`
    共 226 用例无影响。

- **阶段二：采纳后统一接入目标树执行**（对应 4.2 + 4.6）
  `capability_learning` 新增"候选采纳 → 生成 GoalNode"路径（参照
  `growth_advisor` 已有的 `adopt-goal` 机制）；两边采纳后的实际执行
  逐步迁移到目标树 + `ResourceArbiter`；同步收口跨系统推送预算。
  与 `growth_advisor_goal_cron_integration_plan.md` 已完成的部分衔接，
  不重复实现，只补齐 `capability_learning` 这一侧的缺口并把两边纳入
  同一套资源仲裁。

- **阶段三：抽取共享 ResearchService + 双向知识沉淀**（对应 4.3 + 4.4）
  抽取共享调研服务模块，`growth_advisor` 迁移到新服务（旧实现保留
  直至验证稳定）；新增"用户采纳成长报告 → 回写 wiki"路径。

- **阶段四：顶层用户处境模型驱动的候选排序**（对应 4.5）
  `work_index`/`WorkThread` 升级为三条线共享的排序参考信号，第一版
  仅做只读的"相关度"标注，不改变各模块现有候选生成逻辑本身。

每个阶段都可独立上线、独立验证，不要求一次性打包交付；阶段之间有
依赖关系（三→依赖二完成的执行通道、四→依赖前几阶段打通的信号共享
基础），但每个阶段本身的改动范围都收敛在"新增聚合/桥接层"，不重写
已经跑通的核心算法逻辑，符合仓库一贯的演进风格。
