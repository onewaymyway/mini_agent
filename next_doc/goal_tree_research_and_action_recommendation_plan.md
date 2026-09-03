# 目标树 × 自主调研 × 行动推荐 打通计划

> 本文档记录设计方案本身，供实施前确认；具体分阶段实施进度另见后续
> `*_implementation_record.md`（每个阶段完成后补一份，不混在本文档里）。
>
> **实施进度**：阶段一（绑定对象迁移）已完成，见
> `next_doc/goal_tree_research_action_phase1_implementation_record.md`。
> 阶段一落地时发现 `external_input/goal_relevance.py` 并未像最初调研时
> 以为的那样绑定 `direction_id`（它本来就直接读 `GoalBacklog` 的
> `GoalNode`），真正的缺口是扫描范围只到叶子 `goal` 层、漏掉了"现阶段
> 焦点恰好停在 domain/stage 层"的情况，实施记录里有详细说明，§4.1 原文
> 保留供参考、不回改。阶段二（焦点驱动调研）已完成，见
> `next_doc/goal_tree_research_action_phase2_implementation_record.md`。
> 阶段二落地时同样发现 `growth_advisor.py` 的 `auto_pursue_candidate()`/
> `GrowthBacklog` 本来就是按 `GoalNode` id（而非 `direction_id`）运作，
> 不需要迁移绑定对象；新增的 `FocusResearchTrigger` 直接复用
> `GrowthBacklog.add_or_merge()` 现有的"生成 → 用户确认"候选队列，没有
> 新开一条调研素材流，比 §4.2 原文设想的改动量更小，详见实施记录。
> 阶段三（焦点行动建议）已完成，见
> `next_doc/goal_tree_research_action_phase3_implementation_record.md`。
> `next_action_advisor.py` 新增 `Candidate.kind = "focus_next_step"`，
> 默认关闭（`cfg.next_action_focus_next_step_enabled`），"有新调研素材
> 待查看"这条规则直接查 `GrowthBacklog.pending()` 里 `origin==
> "focus_research"` 的候选数量，没有复用 §4.3 原文设想的"素材参与度
> 信号"（服务于更靠后的场景，语义不匹配），详见实施记录。阶段四
> （自动巡检 + 看板展示 + CLI/API 收尾）已完成，见
> `next_doc/goal_tree_research_action_phase4_implementation_record.md`。
> 至此本方案 §五 分阶段实施规划全部完成。
>
> **前置阅读**：`next_doc/goal_tree_system_plan.md`（目标树结构，四个阶段
> 已全部完成）、`docs/growth-advisor-guide.md` 1.5 节"设计理念"、
> `next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`（成长
> 顾问理想形态对照，本文档延续其"感知/规划/执行/反馈/关系"五维度分析
> 框架，但视角从"成长顾问自身怎么打磨"切到"怎么跟目标树打通"）、
> `src/mini_agent/evolution/next_action_advisor.py` 顶部注释（现有排序层
> 定位）。

## 一、背景

`mini_agent` 目前有两条基础设施都已经相当成熟，但彼此几乎不认识：

1. **目标树**（`perception/goal_backlog.py`）：`ultimate → domain → stage
   → goal → objective` 单根树，`current_focus_ids` 由
   `compute_current_focus()` 规则计算持续刷新"现阶段该关注哪个子节点"，
   `GoalTreeDecomposer` 能对任意节点生成分解候选。这条链路解决的是
   "人生目标该怎么拆、现在该聚焦哪"，但**它自己不会去找信息、不会主动
   建议具体该做什么**——`current_focus_ids` 算出来之后，除了在看板高亮
   展示，没有下游消费方。
2. **自主调研/成长顾问**（`evolution/growth_advisor.py` +
   `external_input/`：`watchlist.py`/`poller.py`/`tech_radar_search.py`/
   `knowledge_extractor.py`/`goal_relevance.py` 等）：已经具备"持续扫描
   外部信息源 → 判断跟用户方向是否相关 → 生成候选/素材 → 用户 accept/
   reject → 反馈回流调权重"的完整闭环，`next_action_advisor.py` 还有一层
   "在已有候选里排序、决定这次该提醒哪个"的逻辑。但这套闭环目前**只绑定
   `Direction`/`growth_pursuit`**（`Direction` 本身在阶段一已经被标记
   废弃、迁移为 `level="domain"` 的树节点，但 `growth_advisor` 侧的调研
   逻辑并没有跟着迁移到树上，而是继续用旧的 direction_id 语义在跑）。

结果是：树能告诉你"现在该关注家庭这个领域下的'孩子择校'这个阶段目标"，
但不会主动帮你查学校信息、不会告诉你"下一步具体该做什么"；调研引擎能
主动查到"XX 学校今年招生政策变了"，但这条信息不知道该挂在树的哪个节点
下，也不知道这是不是用户"现阶段焦点"，只能进 `growth_pursuit` 那条独立
的素材流。**这正是"能不能自动自主为用户调研+推荐操作"这个诉求下最大的
一块空白**——两套系统各自很强，但没有对上号。

## 二、目标

让目标树从"结构化的静态地图"升级为"能驱动自主调研、能给出具体行动
建议的活地图"：

1. 树上任意节点（尤其是 `current_focus_ids` 指向的现阶段焦点）都能被
   自主调研引擎感知到，作为"该往哪个方向找信息"的输入，而不是调研引擎
   自己按 `Direction` 各按各的节奏空转。
2. 调研产出（素材、候选、外部信号）能落回触发它的树节点，用户在树上
   就能看到"这个节点相关的调研进展"，不用切到另一套独立的成长顾问界面
   去找。
3. 系统能针对"现阶段焦点"主动给出"具体下一步该做什么"的建议（而不只是
   "该不该拆子节点"这种结构性建议），建议本身要能说清楚"为什么现在推
   这个、依据是什么"，不能是空泛的鼓励。
4. 全程保持现有两条链路各自的克制原则（不无脑打扰、候选要能被拒绝、
   频率有节制、LLM 调用轻量化），打通是"接线"，不是"推倒重来"。

## 三、设计理念

1. **树是"该关注什么"的权威来源，调研/推荐引擎是"消费方"**：
   `current_focus_ids`/`decompose_candidates` 已经是规则计算+持续刷新
   的权威数据，新增的调研/推荐逻辑一律"读树决定该做什么"，不在树之外
   另建一套"该关注什么"的判断逻辑，避免出现两套优先级互相打架。
2. **调研产出挂回节点，而不是另开一条素材流**：现有 `growth_pursuit`
   给 `Direction` 追加 wiki 素材的模式，改造为给对应的树节点（迁移后是
   `domain`/`goal` 等）追加，复用同一套"持续追加、饱和度判断、增量质量
   校验"的机制，只是把挂载对象从 `direction_id` 换成 `node_id`——这是
   `goal_tree_system_plan.md` 原则一"改造而不是并行造一套"的延续。
3. **"具体该做什么"是排序+讲道理，不是重新发明分解**：`next_action_
   advisor.py` 已经明确定位为"排序层，不重新做候选发现"，本方案延续
   这个分工——新增的"焦点行动建议"从已有信息（`execution_spec`、调研
   素材、`decompose_candidates`）里挑，不重新造一套生成候选的机制；
   真要"造新内容"，交给 `GoalTreeDecomposer`/调研引擎在各自职责内做。
4. **分层节奏，越往叶子越贴近执行**：`ultimate`/`domain` 层级的调研应该
   低频、偏"了解动态"，`goal`/`objective` 层级应该更贴近"能直接用于
   推进当前任务"，复用 `soft_goal_deriver`/`GoalTreeDecomposer` 已有的
   "按层级/停滞天数控制触发节奏"思路，不是所有节点一个频率。
5. **只在必要处新增字段，优先复用现有存储结构**：调研产出优先追加到
   `GoalNode` 已有的 `decompose_candidates` 同级新字段或独立的、以
   `node_id` 为 key 的关联表（参考 `growth_state.json` 现有的按
   `direction_id` 存储素材/饱和度的方式），不改动 `goals.json` 里已有
   字段的语义。
6. **LLM 调用轻量化、可关闭**：新增的"焦点行动建议"默认走规则层（复用
   `next_action_advisor` 已有的"规则层默认、LLM 排序层 opt-in"两段式
   设计），调研触发本身沿用 `external_input`/`growth_advisor` 现有的
   `llm_helper.ask()` 轻量调用方式，不引入需要长上下文的新调用范式。

## 四、方案

### 4.1 调研引擎绑定对象从 `Direction` 迁移到树节点

- `growth_advisor.py`/`external_input/goal_relevance.py` 等目前以
  `direction_id` 为主键的关联结构，改为以树节点 `id` 为主键（`Direction`
  阶段一迁移后 `id` 本来就复用为对应 `domain` 节点的 `id`，理论上是
  "换个字段名读同一批数据"，不是数据迁移）。
- 兼容策略：读取时若旧结构仍有独立 `direction_id` 记录且找不到对应树
  节点，保留只读兼容（不新增此类记录，跟阶段一 `Direction` 迁移的
  兼容策略一致），过渡期结束后清理。
- 调研的"该往哪个方向找"不再只能是顶层 `Direction`（现 `domain`），
  而是任意树节点都可以被 `goal_relevance.py` 判断相关性——尤其是
  `goal`/`objective` 这两层（有明确验收标准/执行内容），调研素材可以
  更具体地针对"当前这个目标缺什么信息"。

### 4.2 焦点驱动调研：`FocusResearchTrigger`

新增职责（具体落位在阶段一实施时再定，可能是 `growth_advisor.py` 新增
方法，也可能独立成 `evolution/focus_research_trigger.py`）：

- **输入**：某次 `sys:goal_tree_focus_recompute` 巡检后，`current_focus_
  ids` 发生变化的节点（新进入焦点的子节点，是最该触发一次调研的时机——
  "刚成为现阶段该关注的事"）。
- **判断**：复用 `goal_relevance.py` 现有相关性判断逻辑，决定是否值得
  为这个节点触发一次调研（不是每个焦点节点都无脑触发，比如已经有近期
  未过期的调研素材就跳过，见 §4.4 节奏治理）。
- **触发**：调用 `external_input` 现有的调研能力（`watchlist.py`/
  `tech_radar_search.py`/`knowledge_extractor.py` 视节点内容匹配现有
  哪类信息源，不新增信息源类型，本方案只改"触发对象和挂载对象"，不
  扩展信息源覆盖面），产出素材追加到该节点关联的素材流（§4.1）。
- **与 `GoalTreeDecomposer` 的关系**：`GoalTreeDecomposer`（阶段二已有）
  负责"这个节点该不该拆子节点"，`FocusResearchTrigger` 负责"这个节点
  该不该主动查点相关信息"，两者触发时机不同（前者是停滞巡检+完成态
  联动，后者是焦点变化），互不覆盖，都是通过 §4.1 的相关性判断做节奏
  控制，避免同一节点被两套机制同时高频打扰。

### 4.3 焦点行动建议：`next_action_advisor` 新增候选类型

- 新增 `Candidate.kind = "focus_next_step"`：针对每个（或按配置采样部分）
  `current_focus_ids` 指向的节点生成，规则层判断逻辑：
  - 若该节点是 `goal`/`objective` 且已有 `execution_spec_confirmed`：
    建议"继续推进"，附最近一次 `GoalRunner`/`ObjectiveExecutor` 执行
    记录里的进度摘要（只读现有执行记录，不重新计算）。
  - 若该节点是 `goal`/`objective` 但还没有 `execution_spec`：建议
    "先确认执行规范"，指向现有 `goal_execution_spec` 生成入口。
  - 若该节点是 `domain`/`stage`（非叶子）且 `decompose_candidates` 非空：
    建议"有 N 个待确认的分解候选"，指向看板树形视图对应节点。
  - 若该节点关联的素材流（§4.1）有未读的新调研素材：建议"有新调研
    素材待查看"（复用 `growth_advisor` 已有的"素材参与度信号"设计，
    见 `growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 方向一，
    本方案里把该信号的适用范围也从"仅 `growth_pursuit`"扩展到树节点）。
- 排序：这类候选跟现有 `stale_goal`/`attention_mismatch` 候选一起，走
  `next_action_advisor` 已有的规则排序/（opt-in）LLM 排序层，不新开
  一条排序逻辑。
- LLM 排序层（opt-in）要求候选带 `evidence_refs` 的既有约束，对
  `focus_next_step` 同样适用——不能给"泛泛的鼓励式建议"，必须能指向
  具体的执行记录/候选/素材。

### 4.4 节奏治理

- 复用 `soft_goal_deriver.py`/`GoalTreeDecomposer` 已有的"同一节点两次
  触发之间最小间隔+去重"思路，`FocusResearchTrigger`/`focus_next_step`
  候选都受这层节流约束，具体数值参考现有同类常量的量级，实施阶段二/
  三再定，不在本文档拍死。
- 分层默认频率（对应设计理念第四条）：`domain`/`stage` 层级的调研触发
  间隔明显长于 `goal`/`objective`（暂定量级：前者以周为单位、后者以天
  为单位，具体数值实施时对照 `growth_advisor` 现有 cron 间隔常量核对）。
- 一个节点同一时间只保留一份"待处理的调研素材通知"和一份"待处理的
  行动建议"，不重复堆积——跟现有 `growth_advisor` 通知节流、
  `GoalTreeDecomposer` "已有未处理候选时跳过本次巡检"的克制原则一致。

### 4.5 看板展示

在 `apps/mini_agent_kanban` 阶段四已落地的"🌳 目标树"子页基础上：

- `current_focus_ids` 高亮的节点旁，新增"📄 相关调研"入口（有素材才
  显示，跟 `growth_pursuit` 现有"📄 素材"按钮交互一致，复用同一套
  展示组件，只是数据源从 `direction_id` 换成 `node_id`）。
- 树形视图新增"💡 建议"标记：该节点若有未处理的 `focus_next_step`
  候选，标题旁加提示图标，点开展示具体建议内容和依据。
- 不新增独立页面，全部挂在现有树形视图和现有 `next_action_advisor`
  的展示入口（比如晨报/daemon 推送）上，避免用户要在"目标树页"和
  "成长顾问页"之间来回切换才能看全信息。

### 4.6 CLI / API

- `GoalBacklog`/`growth_advisor` 新增只读查询：给定 `node_id`，返回该
  节点关联的调研素材列表、最近一次调研触发时间、待处理的
  `focus_next_step` 建议——供看板/CLI 复用。
- CLI `agent goals` 命令族新增 `research <id>`（手动触发一次该节点的
  调研，跳过节流限制，用于调试）、`next-steps`（打印当前所有
  `focus_next_step` 候选，文本形式，不依赖看板）。
- REST：`GET /v1/goals/{id}/research`、`POST /v1/goals/{id}/research/
  trigger`、`GET /v1/goals/next_steps`（具体路由前缀跟现有 `/v1/goals/*`
  风格对齐，实施时再核对）。

## 五、分阶段实施规划

1. **阶段一（绑定对象迁移）**：`growth_advisor.py`/`external_input/
   goal_relevance.py` 等从 `direction_id` 迁移到树节点 `id`（§4.1），
   含旧数据只读兼容。这是地基——不迁移干净，后面几个阶段接的都是错误
   的挂载对象。验证标准：现有 `growth_pursuit` 相关全部测试在迁移后
   继续通过，且能确认素材确实挂在了正确的树节点上。
2. **阶段二（焦点驱动调研）**：`FocusResearchTrigger`（§4.2）+ 节奏
   治理（§4.4 的调研部分），先用 CLI 手动触发验证"焦点变化 → 调研触发
   → 素材挂回节点"这条链路，不接自动巡检。
3. **阶段三（焦点行动建议）**：`next_action_advisor` 新增
   `focus_next_step` 候选类型（§4.3），复用现有排序/展示机制先在
   CLI/晨报里验证建议质量。
4. **阶段四（自动巡检 + 看板展示）**：`FocusResearchTrigger` 正式接入
   `sys:goal_tree_focus_recompute` 巡检后的联动触发（焦点变化即触发，
   不用再等新的独立 cron job）+ 看板"🌳 目标树"子页新增素材/建议展示
   （§4.5）+ CLI/API 收尾（§4.6）。

每阶段完成后单独更新文档 + 打包该阶段新增/修改文件，格式跟
`goal_tree_system_plan.md` 一致。

## 六、待实施阶段确认的细节（非阻塞，先记录）

- `focus_next_step` 候选是否需要区分"给用户看的建议文案"和"给 Agent
  自动执行用的结构化指令"——本文档默认先只做前者（给用户看，用户仍要
  手动决定是否推进），"Agent 读到建议后自动去执行"是更大的自主性跃迁，
  留到本方案验证过一轮之后再评估要不要做、怎么加安全约束。
- 调研触发的节流具体数值（间隔天数、`domain`/`goal` 分层的倍数关系）
  留到阶段二实施时参考 `growth_advisor`/`soft_goal_deriver` 现有常量
  量级来定。
- `Direction` 到树节点的绑定迁移，跟阶段一遗留的"过渡期兼容读取"清理
  时机是否合并处理，实施阶段一时再评估工作量。
