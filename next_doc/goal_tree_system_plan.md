# 目标树系统改进计划（人生目标层级管理）

> 本文档记录设计方案本身，供实施前确认；具体分阶段实施进度另见后续
> `*_implementation_record.md`（每个阶段完成后补一份，不混在本文档里）。
>
> **实施进度**：阶段一（数据模型）已完成，见
> `next_doc/goal_tree_system_phase1_implementation_record.md`。阶段二/
> 三/四尚未开始。

## 一、背景

`mini_agent` 现有的目标相关基础设施（`perception/goal_backlog.py` 的
`GoalNode`/`Direction`、`evolution/soft_goal_deriver.py`、
`evolution/growth_advisor.py`、`goal_mode/`、`evolution/goal_cron_bridge.py`）
已经解决了"单个目标怎么被 Agent 持续执行"这个问题（`GoalRunner` 多轮驱动直到
`GoalJudge` 判定 `DONE`；`ObjectiveExecutor` 拆 Step 执行；`goal_cron_bridge`
让 Goal 周期性重新触发），但整体上是**偏向"一个具体任务怎么被执行完"**，
缺一层"作为个人 AI 助手，应该怎么帮用户把人生目标从抽象拆到可执行、并持续
知道'现在这个阶段该做什么'"的管理能力，具体表现为三个缺口：

1. **层级是死的**：`GoalNode.level` 只有 `"goal"`/`"objective"` 两个取值，
   `Direction`（长期方向，如"工作项目""投资学习"）是外挂在 Goal 之上的一张
   独立扁平表，不参与树、不能再往下分层，更没有"整个人生只有一个顶层目标，
   下面按领域展开"这种根节点概念。
2. **分解是被动/单向的**：`soft_goal_deriver.py` 只会在 autonomous 档位往
   顶层**新增** Goal（从能力缺口/停滞工作线索/高频教训反推），不会针对
   "某个已有节点"主动建议"把它拆成几个子目标"；`goal_backlog.py` 里已有的
   `default_goal_to_objectives()`（Goal → Objective 的 LLM 拆解）和
   `GoalBacklog._llm_decompose()`（Objective → 单个 Task 的 LLM 拆解）都只
   服务于树的最下面两层，且是"调用方要用的时候手动调一次"，不是持续运转的
   机制。
3. **"现阶段目标"没有对应的实体**：现有排期（`compute_aging_boost()` 等）
   只解决"调度器该先跑哪个 Objective"这种执行层面的公平性问题，用户/Agent
   都无法直接从数据结构里读出"当前这个领域下，应该聚焦的是哪几件事"——
   这个概念只存在于打分排序的隐式结果里，没有显式落地。

## 二、目标

让目标系统从"单个任务的执行引擎"升级为"人生目标的管理系统"：

1. 用一棵**单根树**表达"整个人生的总目标 → 若干领域方向 → 阶段目标 →
   具体 Goal → Objective"的层级关系，用户能直接从根节点往下看到各领域现状，
   反过来也能通过各领域的进展去调整根节点/阶段目标本身。
2. Agent 能针对树上**任意节点**，持续（不是只在创建时）思考"这个节点该怎么
   往下拆"，生成候选子节点供用户确认，而不是只会在顶层凭空造新 Goal。
3. 树上每个非叶子节点都有一个持续更新的"现阶段焦点"（该关注哪几个直接子
   节点），随子节点完成情况自动刷新，让"现阶段目标是什么"这件事有处可查，
   而不是每次都要重新扫一遍打分表。
4. 用户能在 Streamlit 看板上以树形结构查看和手动管理（增删改、调整层级、
   拖动/重新挂载、手动 pin 焦点），Agent 的自动分解建议以"待确认候选"的
   形式挂在树上，用户可以采纳/忽略/编辑后采纳。

## 三、设计理念

1. **改造而不是并行造一套**：树的骨架（`parent_id`/`children_ids`）、执行
   引擎（`GoalRunner`/`ObjectiveExecutor`/`GoalJudge`/cron 绑定/执行规范）、
   LLM 拆解的落笔方式（`llm_helper.ask()`，独立轻量调用、不占主 Agent
   上下文）全部复用现有代码，只在"层级从两层放开到任意深度"和"分解触发时机
   从被动改成主动巡检"这两点上做改造。这是延续这个项目一贯的做法——
   `Direction`/`goal_execution_spec`/`goal_cron_bridge` 等都是在既有
   `GoalNode` 骨架上叠加字段，而不是另开一张表。
2. **执行语义只在树的下半段生效**：`ultimate`（终极目标）/`domain`
   （领域方向，即现有 `Direction` 并入树后的角色）/`stage`（阶段目标）这
   三层是纯结构+说明+聚合展示，不接入 `GoalJudge`、不会进入 `completed`
   终态判定、不会被 `GoalRunner`/cron 调度执行；`goal`/`objective` 两层
   保持现状，继续跑现有的整套执行机制。这跟现有 `Direction`"不会真正完成
   的东西不参与执行判定"的既定设计哲学是一致的，只是把它显式纳入树，而不是
   外挂。
3. **单根，不是森林**：整个人生只有一个根节点（`level="ultimate"`），
   下面按领域分若干 `domain` 子节点。好处是用户和 Agent 都能从根节点一处
   看到全貌，进而反过来调整根节点本身的表述或阶段目标的取舍——如果允许
   多个并列的顶层节点，就没有这个"一处通览、反哺顶层"的效果了。
4. **分解候选走既有的"生成 → 用户确认"范式，不新发明交互**：
   `soft_goal_deriver.py` 的 accept/reject 模式已经被用户接受，新的
   `GoalTreeDecomposer` 复用同一套"落一份待确认候选，用户显式 accept 才
   真正写入树"的流程，只是把触发范围从"只能加顶层"扩展到"针对任意节点"。
5. **LLM 拆解只用 `LLMHelper.ask()`**：跟 `default_goal_to_objectives()`/
   `GoalBacklog._llm_decompose()`/`growth_advisor` 里所有 LLM 调用点一样，
   是独立、轻量、不占主 Agent 上下文的调用，输出要求纯文本结构化（每行一个
   候选标题+一句话描述），不引入新的 LLM 调用范式。
6. **"现阶段焦点"是规则计算的派生字段，不是 LLM 判断**：复用现有
   `compute_aging_boost()`/fairness 排序的思路（优先级 + 停滞天数 + 完成度），
   跟"该不该往下拆"（LLM 判断，语义性强）明确分工——焦点选择是"矬子里拔
   将军"的排序问题，不需要 LLM，保持轻量、可预测、能挂在 tick 里高频跑。
7. **只改 Streamlit 看板**：`apps/mini_agent_kanban` 是目前唯一在用的看板，
   `apps/mini_agent_kanban_x`（React 版）本次不动，避免工作量翻倍且维护
   一个当前不用的界面。

## 四、方案

### 4.1 数据模型

- `GoalNode.level` 语义从"两值枚举"改成开放字符串，规划中的取值：
  - `ultimate`：终极目标，全局唯一根节点，无验收标准，不进入完成态。
  - `domain`：领域方向（现有 `Direction` 并入树后的角色，如"事业""健康"
    "家庭"），无验收标准，不参与执行判定。
  - `stage`：阶段目标（有粗粒度时间窗口的说法，如"未来一年"），无验收标准，
    不参与执行判定。
  - `goal` / `objective`：现状不变，保留全部执行相关字段
    （`work_thread_ref`/`execution_spec_confirmed`/`recurring`/cron 绑定/
    `criteria` 等），继续走 `GoalRunner`/`ObjectiveExecutor`/cron。
  - 允许的父子层级顺序：`ultimate → domain → stage → goal → objective`，
    但不强制每一层都必须存在——比如一个简单诉求可以是
    `domain → goal`，中间跳过 `stage`，具体校验规则见 §4.1.1。
- `GoalNode` 新增字段：
  - `current_focus_ids: list[str]`：仅非叶子节点（`ultimate`/`domain`/
    `stage`）使用，指向"当前应关注"的直接子节点 id，由 §4.3 的规则计算
    定期刷新（写入时机见 §4.3），用户可以覆盖。
  - `focus_pinned_ids: list[str]`：用户手动 pin 的子节点 id，持续生效直到
    用户取消（不是一次性标记），计算 `current_focus_ids` 时优先包含这些
    id，规则计算结果与 pin 结果去重合并。
  - `decompose_candidates: list[dict]`：`GoalTreeDecomposer` 生成、尚未被
    用户处理的候选子节点，每项
    `{"id", "title", "description", "level", "generated_at", "reason"}`；
    `id` 是候选自己的临时 id（不是真实 `GoalNode.id`），accept 后才会用它
    创建真正的节点并从本列表移除，reject 后直接从本列表移除并记一条
    "30 天内不再对同一节点重复生成同主题候选"的去重记录（复用
    `soft_goal_deriver.py` 已有的"reject 后一段时间内不重复"思路）。
- `Direction` 数据结构标记废弃，提供一次性迁移函数：把每条 `Direction`
  转成 `level="domain"` 的 `GoalNode`（`id` 复用，挂在根节点下），原来通过
  `direction_id` 关联的 `goal` 节点，改成直接 `parent_id` 指向对应的
  `domain` 节点；迁移前后旧版本代码路径（`direction_id` 字段）保留读取
  兼容，但不再写入，过渡期结束后再清理。
- 根节点：系统启动/首次使用时如果 `goals.json` 里没有任何 `level="ultimate"`
  节点，`GoalBacklog` 自动创建一个占位根节点（标题留空或"我的人生目标"，
  等用户在看板里编辑），保证任何时候都恰好存在一个根节点。

#### 4.1.1 父子层级校验

不强制"每层必须存在"，但父子之间的 `level` 顺序不能倒挂（比如 `goal` 不能
挂在 `objective` 下面）。具体规则：`ultimate` 只能是根（`parent_id=None`）
且全局唯一；`domain`/`stage` 的父节点必须是"顺序表里排在自己前面的层级"
之一（允许跳级）；`goal` 的父节点可以是 `domain`/`stage`/`goal`（`goal`
挂 `goal` 下——即"大目标拆小目标，小目标本身仍然要走 GoalJudge 判定"这种
场景，现状本来就没有禁止 Goal 挂 Goal，保留）；`objective` 的父节点只能是
`goal`（保持现状不变）。校验放在 `add_node()` 统一入口，不合法直接拒绝。

### 4.2 自动分解机制：`GoalTreeDecomposer`

新增 `src/mini_agent/perception/goal_tree_decomposer.py`（或直接作为
`goal_backlog.py` 的一部分方法，具体落位在实施阶段二再定，取决于代码量），
职责：

- **输入**：给定一个节点，拼装 prompt 时带上：该节点自身
  title/description/level、祖先链（从根到该节点，让 LLM 理解"这是在为哪个
  更大目标服务"，不是孤立看一句话）、已有的直接子节点（含状态，避免生成
  重复建议）、已被 reject 过的候选主题（避免重复打扰）。
- **调用方式**：`llm_helper.ask(prompt)`，跟
  `default_goal_to_objectives()`/`GoalBacklog._llm_decompose()` 同一种
  "纯文本输出、每行一个候选，程序侧解析"的轻量调用模式，不用工具调用、
  不占主 Agent 上下文。产出的子节点 `level` 由 LLM 按"父节点 level 的下一
  层"给出建议，程序侧做合法性兜底（不合法则拉回父节点的下一层）。
- **触发时机**（对应 §改进目标 2/3，全部走 cron/tick，不是"用户打开页面才
  算"）：
  1. **停滞巡检**（新增内置 cron job，暂定 `sys:goal_tree_decompose_scan`，
     每 24 小时一次，参考 `sys:goal_review` 的量级）：扫描全树，找"没有任何
     子节点，或所有子节点都已 `completed`/`abandoned`"且自身仍
     `active`/`paused` 的非叶子节点，达到停滞天数阈值（复用
     `compute_aging_boost` 里 `stale_days` 同一套口径）后触发一次分解建议。
  2. **完成态联动**：一个 `goal`/`stage` 节点被标记 `completed` 时，检查
     其父节点是否因此"没有其它 active 子节点了"，是则立即触发一次父节点的
     分解建议——这正是"不断更新现阶段目标"的落地：一个阶段目标完成后，
     系统主动想"下一个阶段目标该是什么"，而不是等用户想起来才问。
  3. **手动触发**：CLI/API 显式调用（看板"帮我拆解"按钮见 §4.4）。
- **节奏治理**：跟 `soft_goal_deriver.py` 一样，写一个最小触发间隔（同一
  节点两次分解建议之间至少间隔 N 天，避免同一停滞节点被反复打扰），以及
  "该节点已有未处理候选时跳过本次巡检"（避免候选堆积）。
- **落盘**：候选写入触发节点的 `decompose_candidates` 字段，不创建真实
  `GoalNode`。

### 4.3 现阶段焦点：`current_focus_ids` 的规则计算

- 计算函数（暂定 `compute_current_focus(node, children, now)`，纯规则、
  同步、不调用 LLM）：先并入 `focus_pinned_ids`（用户手动 pin 的，优先
  保留），再从剩余 active 直接子节点里，按"`priority` +
  `compute_aging_boost()` 老化加成"排序取 top-N（N 默认 1~3，可配置），
  合并去重后写回 `current_focus_ids`。全 `completed`/`abandoned`/无子节点
  时结果为空列表（意味着该节点该被 §4.2 的停滞巡检捕获，去生成新的候选）。
- **挂在 tick 上**：复用 daemon 现有的周期性机制，新增内置 cron job（暂定
  `sys:goal_tree_focus_recompute`，间隔比停滞巡检短，暂定每小时一次，这个
  只是纯规则计算、成本很低，可以比 LLM 驱动的巡检跑得更勤）从根节点开始
  自底向上重算全树的 `current_focus_ids`（子节点的完成状态变化要先反映到
  自己身上，再影响父节点的排序）。
- Agent 侧：`goal_cron_bridge`/晨报/`next_action_advisor` 等"主动推进"相关
  逻辑，后续可以优先读某个 `domain`/`stage` 的 `current_focus_ids` 决定
  "现在该往哪个方向使劲"（是否接入、接入到什么程度，留到阶段三/四实施时
  再具体设计，本文档先只定数据结构和计算逻辑）。

### 4.4 Streamlit 看板：树形视图

在 `apps/mini_agent_kanban` 的 Goals 相关 tab 里新增"🌳 目标树"子页
（与现有列表/看板视图并存，不替换——列表视图在"看所有 Objective 执行状态"
场景仍然更好用）：

- 用 `st.expander` 或自绘缩进树（具体实现方式在阶段四落地时再定，优先复用
  Streamlit 原生组件，不引入新前端依赖）展示从根节点开始的完整层级，节点
  标题前带 level 图标（🌍 ultimate / 🧭 domain / 📅 stage / 🎯 goal /
  📌 objective）+ 状态色 + 是否在父节点 `current_focus_ids` 里的高亮标记。
- 待确认的 `decompose_candidates` 以虚线/斜体样式挂在对应父节点下，提供
  "✅ 采纳"/"✖️ 忽略"/"✏️ 编辑后采纳"三个按钮，交互跟现有
  `growth_advisor`/`soft_goal_deriver` 候选处理面板保持一致的写法（复用
  `wiki_tab_async_changes` 里刚落地的 `start_async_job`/`run_async_job`
  异步模式，因为分解建议的 LLM 调用同样不该用固定超时同步等）。
- 手动管理：新建节点（选父节点+level+标题）、编辑（标题/描述/优先级）、
  修改 `parent_id`（下拉选择新父节点，而不是拖拽——拖拽在 Streamlit 里
  实现成本高，不是这次的重点）、pin/unpin 焦点、手动触发"帮我拆解此节点"。
- 根节点（`level="ultimate"`）常驻展示在树的最上方，标题可编辑，作为整个
  页面的"人生目标现状总览"入口。

### 4.5 CLI / API

- `GoalBacklog.add_node(level, title, parent_id, description="", ...)`：
  通用创建入口，做 §4.1.1 的层级校验；现有 `add_goal()`/`add_objective()`
  改造为对它的薄封装，保持向后兼容。
- `GoalBacklog.get_tree(root_id=None)`：返回以 `root_id`（默认全局根节点）
  为起点的完整子树（含候选），供看板/CLI 渲染。
- `GoalTreeDecomposer.decompose(node_id, llm_helper)`：手动触发单节点分解，
  返回生成的候选列表（同时落盘）。
- `GoalBacklog.accept_candidate(node_id, candidate_id, overrides=None)` /
  `reject_candidate(node_id, candidate_id)`：候选处理入口。
- `GoalBacklog.set_focus_pin(node_id, child_id, pinned: bool)`：手动
  pin/unpin。
- REST：`GET /v1/goals/tree`、`POST /v1/goals/{id}/decompose`、
  `POST /v1/goals/{id}/candidates/{candidate_id}/accept|reject`、
  `POST /v1/goals/{id}/focus_pin`（具体路由前缀/命名跟现有 `/v1/goals/*`
  路由风格对齐，实施时再核对）。
- CLI `agent goals` 命令族新增 `tree`（文本树形打印）、
  `decompose <id>`、`candidates <id> accept|reject <candidate_id>`。

## 五、分阶段实施规划

按上次讨论的顺序，每阶段完成后单独更新文档 + 打包该阶段新增/修改文件：

1. **阶段一（数据模型）**：`GoalNode.level` 开放、新增
   `current_focus_ids`/`focus_pinned_ids`/`decompose_candidates` 字段、
   §4.1.1 层级校验、`add_node()` 通用入口、`Direction → domain 节点`
   一次性迁移脚本（bring dry-run 预览）、全局根节点自动创建逻辑。这是
   地基，后面阶段都依赖它，且要保证现有 `goals.json` 平滑升级、不破坏
   现有 Goal/Objective 数据和依赖它们的全部现有功能（cron 绑定/执行规范/
   fairness 排序等一律不受影响）。
2. **阶段二（自动分解）**：`GoalTreeDecomposer`（含 §4.2 三种触发时机、
   LLM prompt、节奏治理）+ CLI/API 的 accept/reject 入口，先不做看板 UI，
   用 CLI 验证生成质量和触发逻辑。
3. **阶段三（现阶段焦点）**：`compute_current_focus()` + 两个新增内置
   cron job（`sys:goal_tree_focus_recompute`/
   `sys:goal_tree_decompose_scan`，后者其实是把阶段二的巡检触发正式接入
   cron，阶段二可以先手动触发验证逻辑，阶段三再接自动巡检）+ pin/unpin。
4. **阶段四（看板树形 UI）**：Streamlit "🌳 目标树" 子页 + 候选展示/
   采纳/忽略交互 + 手动管理（新建/编辑/改父节点/pin）。

## 六、待实施阶段确认的细节（非阻塞，先记录，各阶段动手前再逐一敲定）

- `current_focus_ids` 的 top-N 默认取值、停滞巡检阈值天数、分解建议最小
  触发间隔——数值定多少，等阶段二/三实施时参考现有
  `compute_aging_boost`/`soft_goal_deriver` 里同类常量的量级来定，不在本
  设计文档里先拍死。
- `sys:goal_tree_focus_recompute`/`sys:goal_tree_decompose_scan` 具体是
  走"轻量规则内部执行"（不占用 Agent 轮次，类似 `growth_advisor` 信号
  扫描阶段）还是"提交一个 task_template 走完整 Agent 轮次"（类似
  `sys:goal_review`），阶段三/二实施时对照现有两种 cron job 类型的取舍
  惯例再定。
