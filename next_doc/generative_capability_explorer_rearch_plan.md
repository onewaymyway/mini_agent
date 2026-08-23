# Generative-Capability 探索机制重构方案

- **版本**: v1.1（阶段一已实施，阶段二~五待实施）
- **关联文档**:
  - `next_doc/generative-capability-skill-plan.md`（第 6 节 explore()/distill()、
    第 8 节安全边界、实施记录阶段一~阶段十九——本方案是对其"探索"这一环节的
    重构，不改动 resolve/execute/生命周期状态机/健康巡检等其余环节）
  - `src/mini_agent/skills/generative_capability/explorer_runtime.py`（待重构）
  - `src/mini_agent/skills/generative_capability/distiller.py`（待扩展）
  - `src/mini_agent/orchestrator/sub_agent.py`（复用对象）
- **触发来源**: 用户在实测中发现，探索子agent受限于自造的工具白名单/步数预算
  机制，天花板过低（如站点抓取需要识别公开 API/处理签名请求时，浏览器操作型
  探索子agent天然到顶，与安全无关）。

---

## 1. 问题描述

现状：`explorer_runtime.py::build_llm_explorer()` 是一个**手写的多轮 LLM 决策
循环**——自己维护 `messages` 历史、自己拼工具 schema、自己数步数
（`max_steps`）、自己数耗时（`max_seconds`），工具执行则通过调用方注入的
`tool_executor` 分发，且只允许调用 `capability.yaml -> explorer.tool_allowlist`
中列出的工具名，越权直接拒绝。

这套机制在实测中暴露出三个问题（用户原话，均成立）：

1. **为什么不直接复用现有的 subagent 机制**：`orchestrator/sub_agent.py::
   SubAgent` 已经是系统里成熟的"隔离执行单元"——独立 context/session、继承或
   覆盖 LLM 配置、`PermissionGuard`+`sandbox` 安全边界、`max_turns`+成本统计
   预算、`task.allowed_tools/allowed_tool_groups` 工具过滤、失败重试。探索子
   agent想要的隔离性/限权/限预算，`SubAgent` 全都有，且是全平台统一维护、
   持续在打磨的基础设施。`explorer_runtime.py` 是在另起炉灶，重复造一个**能力
   更弱**的轮子：只会调 `finish`/`report_failure`/白名单原语，不能读文件、不能
   跑代码、不能用 SubAgent 已有的任何通用工具。

2. **为什么要限制可用工具**：现在 `tool_allowlist.json` 同时承担了两个语义完
   全不同的角色，被混在一份配置里：
   - **(a) 安全白名单**——防越权。这件事系统里已经有通用机制
     （`PermissionGuard`+`sandbox`+`task.allowed_tools`），不需要
     generative-capability 自己再造一份平行的安全体系。
   - **(b) 蒸馏词汇表**——`distiller.py` 的蒸馏策略是"把 trace 里的
     `(tool, input)` 序列原样固化成重放脚本"，它只认识**"一串工具调用"**这一
     种动作形状。如果探索时用 bash 跑了 curl、写了段 Python 直接调 API，蒸馏
     器根本不知道怎么把这个动作变成 `run(input)->dict` 脚本——这不是权限问题，
     是蒸馏器的"理解能力"问题。

   这两者被塞进同一份 allowlist，真正卡住能力上限的其实是 (b)：知乎抓不到如果
   是因为需要识别公开 API/处理签名请求，一个"只会点浏览器按钮"的探索agent天
   生到顶，加多少浏览器操作原语工具都没用，因为目标解法根本不是"浏览器操作
   序列"这种形状。

3. **为什么要限制时间/步数**：作为成本边界本身合理（防止无限重试烧钱），但不
   该是 `explorer_runtime.py` 自己拿 `time.time()`/循环计数器数出来的一套平
   行预算体系，应该复用 `SubAgent`/`Task` 已有的 `max_turns`+成本统计基础设施
   ——这套体系本来就承担着系统里所有子agent的预算控制职责。

## 2. 为什么要改：现有设计的根因

现有探索机制把**"执行体的能力上限"**和**"产物的可蒸馏边界"**这两个本该正交
的约束，叠成了同一根天花板（同一份 `tool_allowlist.json`），并且都用手写代码
（而非复用现有基础设施）实现。结果是：

- 探索子agent的能力被"蒸馏器认识什么"反向锁死，而不是被"这个任务客观需要
  什么工具"决定——这是本末倒置：应该先让探索子agent有能力解决问题，再讨论怎
  么把解法固化下来复用，而不是为了让固化变简单，先把探索子agent的手脚捆住。
- 手写循环/手写预算与系统里 `SubAgent` 已有的、更完善的等价机制重复，且两者
  会持续不同步演进（例如阶段十九给 explorer_runtime 单独加了"剩余时间不足提
  醒"，这类改进 SubAgent 体系里可能已有更好的等价物，或者以后 SubAgent 演进
  了这里也不会跟着受益）。

## 3. 新方案

核心判断：**把执行体能力上限和产物可蒸馏边界拆成两个独立约束，分别用系统里
已有的机制满足，不再自造。**

### 3.1 探索执行体：改为构造真实 SubAgent

`explore(request, intent_schema, explorer_config)` 内部不再手写决策循环，而是
构造一个 `SubAgent`（真实 `Agent` + 完整工具生态：bash/python/文件读写/浏览器
等系统已注册的全部工具），并驱动它跑完一次真实的 agent turn：

- **隔离性**：沿用 `SubAgent` 现成的独立 context/session 机制（探索子agent不
  携带主对话历史，这条既有约束保持不变）。
- **安全边界**：交给 `PermissionGuard`+`sandbox`+`task.allowed_tools/
  allowed_tool_groups`。领域如果确实想收窄探索子agent能碰的工具面（例如
  `text-transform-capability` 不需要浏览器工具），仍然可以表达，但表达方式是
  系统统一的 task 工具过滤配置，语义上只是"安全/范围限制"，不再兼职"蒸馏词
  汇表"。
- **预算**：复用 `task.max_turns`（以及 SubAgent 已有的 turn/token/成本统
  计）。超预算就是一次普通的 SubAgent 失败结果，直接对应 explore() 既有的
  "判定失败"分支，不再维护 `stop_reason` 这套平行状态。
- **`finish`/`report_failure` 契约保留**：这是 explore() 对"结构化产出 vs 如
  实报告失败"的语义边界，与"执行体是手写循环还是 SubAgent"无关，属于合理约
  束，不动。

`explorer_runtime.py` 从"手写决策循环"退化为一层薄适配层：把
`request`/`intent_schema` 组织成探索任务的 prompt/`system_extra`，构造对应的
`Task`/`TaskRecord`，注入 `finish`/`report_failure` 两个工具（作为该 SubAgent
可用工具集的补充），交给 `SubAgent` 跑，解析其终态产出。阶段九"接入
LLMHelper 而非自拼 API"的成果（provider 无关、跟随 `/model` 切换、复用
`LLMClientPool`）在这个改造里自然继承——因为这些能力本来就是 `SubAgent`→
`Agent` 链路自带的，比之前单独在 explorer_runtime 里接一次更彻底。

### 3.2 蒸馏边界：从"引擎事后猜 trace 形状"改为"探索者自己交付蒸馏候选"

现在的问题根源是蒸馏器在**事后**、**不参与探索过程**的情况下，试图理解一段
它没有产生的 trace，天然只能认识最简单的形状（工具调用序列）。更合理的做法
是把"这段探索能不能被蒸馏、怎么蒸馏"交还给探索者自己判断：

- `finish` 工具签名扩展为 `finish(data, script_source?)`：探索子agent本身是
  一个有代码能力的完整 Agent，它自己知道自己是靠一串浏览器点击拿到的数据，
  还是靠写了段 Python 调 API 拿到的数据。如果是后者，探索子agent可以在
  `finish` 时**自己**把这段可复用逻辑整理成符合 `run(input)->dict` 接口约定
  的 `script.py` 源码一并提交（探索 prompt 需要包含这个接口约定和"如果你的
  解法可以参数化复用，请一并提交 script_source"的引导，但不强制——省略时走
  兜底路径）。
- `distiller.py` 的角色从"猜怎么把 trace 变成脚本"降级为纯粹的**校验者 +
  落盘者**：
  - 有 `script_source` → 在沙箱内实际跑一遍（用探索时同一份/等价的 request
    作为 `run(input)` 的输入）、用 `intent_schema` 校验结果，通过才原子化落
    盘；不通过则丢弃，不落盘（第 8 节安全边界 3/4/5 完全不变，只是候选脚本
    的来源从"引擎生成"变成"探索子agent自带"）。
  - 无 `script_source`（探索子agent自己判断"这次解法就是一串工具调用，走
    机械重放更简单可靠"，或该次探索本来就走的是纯浏览器操作序列）→ 沿用现
    有的 trace→参数化重放脚本策略（阶段三/阶段六 trust_trace_data 兜底逻辑
    原样保留）。
  - 两条路径的落盘校验流程（沙箱自测→intent_schema 校验→原子写入
    `members/`/`registry.json`/`_index.json`）完全一致，只是候选脚本的来源
    不同，不引入新的安全边界，也不放松既有校验。

这样一来，`tool_allowlist.json` 承担的两个语义被分开表达：
- 安全约束 → `task.allowed_tools`/`allowed_tool_groups`（系统级、跨领域统
  一，替代 `explorer.tool_allowlist.json` 的安全职责）
- "这个领域的探索倾向于产出可机械蒸馏的动作序列"这件事 → 变成
  `capability.yaml` 里一个可选的**提示**字段（例如 `preferred_primitives`），
  写进探索 prompt 引导探索子agent优先尝试这些更简单/更廉价的路径，但不是
  强制上限——探索子agent仍然可以在这些路径走不通时使用其被允许的完整工具
  集，并自带蒸馏候选。

### 3.3 不改动的部分

- resolve() 两级检索（domain_matchers/关键词精确匹配 + LLM 裁决）不动。
- execute() 的 intent_schema 强制校验、registry 更新（success_count/
  fail_count）不动。
- 第 7 节 member 生命周期状态机（probation/trusted/degraded/dead）不动。
- 第 8 节安全边界 3（产物强制 schema 校验）、4（蒸馏产物强制沙箱自测）、5
  （原子化写入）、6（健康巡检）原样保留，只是 4 里"蒸馏"的实现方式从单一策
  略变成两条路径（自带脚本优先校验 / trace 重放兜底）。
- `health_patrol.py`、`registry.json`/`status_changed_at` 等阶段五/十九的既有
  成果不受影响。

## 4. 影响面

- `explorer_runtime.py`：改造为基于 `SubAgent` 的薄封装（阶段九的 LLMHelper
  接入工作大部分随 SubAgent 链路自然继承，不是推倒重来）。
- `distiller.py`：新增"校验并落盘外部提供的 `script_source`"分支，原有
  trace-replay 分支保留不动，两者共用同一套沙箱自测+落盘代码路径。
- `capability.yaml` schema：`explorer.tool_allowlist` 字段语义变更/迁移为
  `explorer.allowed_tools`（映射到 `task.allowed_tools`）+ 可选
  `explorer.preferred_primitives`（蒸馏提示，非限制）。已有三个 skill
  （`browser-site-scraper`/`text-transform-capability`/`doc-template-
  generation`）的 `capability.yaml` 与 `explorer/tool_allowlist.json` 需要
  跟着迁移，但都是配置迁移，不涉及各自 member 脚本本身。
- 阶段十六/十七围绕 `browser-site-scraper` 做的调试基础设施
  （`capture_debug_context`/`browser_get_debug_snapshot` 等）不受影响，会被
  探索用的 SubAgent 正常复用（它跑的还是同一套 browser-core 工具，只是不再
  被 allowlist 卡死在"只能用这几个"）。
- 需要新增/迁移测试：`tests/test_exploration_outcome_recording.py` 及
  generative_capability 相关测试需要覆盖"SubAgent 驱动的 explore()"、
  "`script_source` 校验落盘路径"、"无 `script_source` 走兜底重放路径"三类
  场景。

## 5. 实施步骤（分阶段）

### 阶段一 —— explore() 切换到 SubAgent 驱动

- 新增/改造 `explorer_runtime.py`：构造 `Task`/`TaskRecord` 并用 `SubAgent`
  跑探索任务，替换手写循环；`finish`/`report_failure` 作为该 SubAgent 的补充
  工具注入。
- 预算改为透传 `task.max_turns`；移除自造的 `max_steps`/`max_seconds`/
  `stop_reason` 计时逻辑，改为直接读取 SubAgent 执行结果判定失败原因。
- `capability.yaml` 的 `explorer.tool_allowlist` 迁移读取路径改为
  `task.allowed_tools`（保留旧字段名一个版本的兼容读取 + 迁移提示，遵循项目
  "config flags default to non-breaking behavior"惯例）。
- `build_stub_explorer()` 桩实现保留，供离线自测/CI 使用，不受本阶段改动
  影响。

### 阶段二 —— distiller 支持 `script_source` 自带蒸馏路径

- 扩展 `finish` 工具的 `input_schema`，新增可选 `script_source` 字段。
- `distiller.py` 新增分支：`script_source` 存在时，沙箱内实例化并调用
  `run(request-like input)`，校验 `intent_schema`，通过后走与现有 trace-
  replay 分支相同的原子化落盘（`meta.json` 增加字段标记
  `distill_source: "self_authored" | "trace_replay"`，保持阶段五就有的"如实
  记录、可审计"惯例）。
- 探索 prompt 中加入 `run(input)->dict` 接口约定说明 + "可参数化复用时请一
  并提交 `script_source`"引导。

### 阶段三 —— `capability.yaml` schema 迁移 + 三个已有 skill 配置更新

- 更新 schema 文档（本文档 3.3 节 + `generative-capability-skill-plan.md`
  第 4/8 节相应措辞）。
- 迁移 `browser-site-scraper`/`text-transform-capability`/
  `doc-template-generation` 三个 skill 的 `capability.yaml`：
  `tool_allowlist.json` → `allowed_tools`（安全范围）+
  `preferred_primitives`（蒸馏提示，可选）。

### 阶段四 —— 测试与验证

- 新增/改造测试覆盖：SubAgent 驱动的 explore() 成功/失败/超预算三种终态；
  `script_source` 校验通过/校验不过/沙箱执行异常三种情形；无
  `script_source` 时兜底重放路径不回归。
- 端到端验证：至少在一个已有 skill（建议 `browser-site-scraper`，已有阶段
  十六/十七的调试基础设施可以复用排障）上跑一次真实 hit/fail/miss→
  explore→distill→re-hit 循环，确认与阶段五验证过的闭环等价。

### 阶段五 —— 文档收尾

- 更新 `generative-capability-skill-plan.md` 第 6/8 节措辞与本方案对齐，并在
  该文档"实施记录"追加阶段二十小节，指向本方案文档。
- 更新 `docs/` 下对应的用户可见说明（如有）。

---

每个阶段完成后会更新本文档对应小节的状态，并打包该阶段修改/新增的文件（保
持目录结构）供下载。

---

## 6. 实施记录

### 阶段一 —— explore() 切换到 SubAgent 驱动（已完成）

**改动文件**:
- `src/mini_agent/skills/generative_capability/explorer_runtime.py`
- `src/mini_agent/skills/generative_capability/__init__.py`
- `src/mini_agent/tools/capability_call.py`
- 新增 `tests/test_explorer_runtime_subagent.py`

**实现摘要**:
- 新增 `build_subagent_explorer(base_cfg, *, tool_executor=None, session_id=None,
  session_dir=None, shared_tool_cache=None, override_model=None,
  override_provider=None)`，返回符合 `CapabilityEngine.explore()` 签名的
  explorer。内部构造真实 `Task`/`TaskRecord`/`SubAgent`，复用
  `SubAgent._build_agent()` 拿到一个装配了系统全部已注册工具
  （bash/python/文件读写等）的 `Agent`，再调用 `SubAgent._run_with_capture()`
  跑一次真实 `agent.run_turn()`（含既有的 5xx/超时自动重试）。
- 预算改为透传 `task.max_turns`（`explorer_config` 里的 `max_turns` 优先，
  否则兼容旧字段名 `max_steps`）；不再自造 `max_seconds`/`stop_reason` 计时
  循环——`max_seconds` 字段仍允许存在于旧 `capability.yaml` 里（不报错），
  只是不再被读取，这是刻意的非破坏性忽略。
- `finish`/`report_failure` 从手写循环里的两个特判分支，改为动态注册到探索
  用 `Agent.registry` 上的两个真实工具（`ToolDef`，`requires_approval=False`）。
  `finish` 新增可选 `script_source` 字段（阶段二的落点，`ExploreTrace` 已
  同步新增 `script_source` 字段透传）。
- **实施中发现并处理了方案文档未预料到的问题**：`browser_navigate` 等领域
  声明的底层原语，并不是 `Agent` 自身注册表里的工具（`Agent` 内置的是
  bash/python/文件读写等通用工具），而是历史上通过调用方注入的
  `tool_executor(name, input) -> dict` 单独分发（`real_tools.py` +
  `.claude/skills/browser-core/impl/tools_impl.py` 等）。若不处理，切到
  "真实 SubAgent + 系统通用工具"后，探索子agent会直接失去这批已经接好的
  真实能力，是一次不小的能力倒退。解决方式：`build_subagent_explorer()`
  为 `capability.yaml`/`tool_allowlist.json` 里声明的每个领域工具名，各自
  包一层桥接 `ToolDef`（`fn` 内部转发给 `tool_executor`），动态注册到探索
  用 `Agent.registry`——探索子agent因此同时拥有"系统通用工具"与"领域底层
  原语"两类工具，不再互斥，也不再被后者反向限制上限（对应第 1 节问题 1）。
  新增 `_resolve_domain_tool_names()` 兼容三种历史写法：`capability.yaml
  -> explorer.allowed_tools`（新增，内联列表）、`tool_allowlist.json ->
  {"allowed_tools":[...]}`（browser-site-scraper/doc-template-generation
  现有写法）、`tool_allowlist.json -> {"tools":[{"name":...}]}`
  （text-transform-capability 现有写法），最终兜底 `explorer.base_tools`。
- **`ExploreTrace.steps` 未被放弃**：新增 `_extract_steps_from_agent()`，从
  探索用 `Agent` 内部的 `HistoryManager._history`（与
  `history_manager.py` 内部消息约定完全一致，provider 无关）里按
  `tool_use.id` 匹配 `tool_result`，尽力还原出 `(tool, input, output)`
  步骤序列，保证 `distiller.py` 现有的 trace-replay 兜底路径在阶段二正式
  接入 `script_source` 校验分支之前不会失去素材来源（这份"最佳努力"
  实现的局限见下方"已知限制"）。
- `tools/capability_call.py` 改为从全局 `TaskManager` 单例（`get_task_manager()`）
  取 `base_cfg`/`session_id`/`session_dir`/`shared_tool_cache` 传给
  `build_subagent_explorer()`；取不到 `TaskManager` 时如实报错，不静默退化。
- 旧的 `build_llm_explorer()`（手写决策循环）保留，标记为遗留实现，供已有
  调用方/测试继续工作；`build_stub_explorer()` 不变。

**已知限制（留给阶段二/后续观察）**:
1. `_extract_steps_from_agent()` 记录的是探索用 `Agent`**自己的工具调用**
   （可能是 bash、也可能是桥接的领域原语），trace-replay 路径重放时用的是
   `tool_runtime.get_tool_executor()`（`distiller.py` 沙箱自测/生产重放时
   注入的执行器）。二者对同名工具（如 `browser_navigate`）的实现如果不是
   同一份，重放可能对不上——这正是方案文档 3.2 节要解决的问题（"事后猜
   trace 形状"不可靠），阶段二接入 `script_source` 后，trace-replay 会
   降级为真正的"兜底"而不是主路径，届时这个限制的影响面会自然收窄。
2. `max_seconds` 预算已按方案移除，若探索子agent在单次工具调用内部耗时
   过长（如 `browser_navigate` 卡住），目前只能等到该次工具调用返回/超时
   由工具自身的超时机制兜底，不再有引擎层面的"剩余时间提醒"（阶段十九的
   那个机制）。如果后续实测发现这是真实问题，可以考虑在 `Task` 层面补一个
   通用的 wall-clock 预算字段，而不是在 explorer_runtime 里再造一次。

**测试**: `tests/test_explorer_runtime_subagent.py`（10 个用例，覆盖 finish
成功+script_source 透传、report_failure 失败、预算耗尽不伪造成功、领域工具
桥接转发/未注入 tool_executor 时不桥接、`_resolve_domain_tool_names()` 兼容
三种历史写法），与既有 `test_generative_capability_engine.py` /
`test_generative_capability_real_tools.py` / `test_orchestrator.py` /
`test_subagent_inheritance.py` 合并运行共 131 个用例全部通过，无回归。

### 阶段二~五 —— 待实施
