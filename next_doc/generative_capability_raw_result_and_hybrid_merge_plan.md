# generative-capability：raw_result 落盘化 + 三档 member 执行机制（合并 hybrid_exec）改进计划

## 0. 背景

本次改进针对 `next_doc/generative-capability-skill-plan.md` 落地后暴露的两个问题：

1. **`view_raw_result` 取不到结果**：主 agent 的工具结果被截断后存入 `RawResultStore`，
   但探索子agent（`SubAgent._build_agent()`）在同一进程内构造了一个新的 `Agent` 实例，
   其 `__init__` 会调用 `configure_raw_result_store()` 覆盖 `tools/builtin.py` 里的
   **模块级全局单例**。探索结束后没有任何"恢复"逻辑，全局指针停留在探索子agent的
   store 上，主agent之后再调用 `view_raw_result(result_id=...)` 读到的是错误的 store，
   查不到自己存过的 id。
2. **蒸馏出的脚本是假数据**：探索子agent把本次抓取到的具体结果硬编码进 `script.py`
   里提交给 `finish(script_source=...)`，脚本对任意 `input` 都返回同一份静态数据，
   完全不可复用。现有沙箱自测只做 `intent_schema` 结构校验，天然无法识别"结构对但
   数据是写死的"这类语义问题。

围绕这两个问题展开分析后，进一步发现"脚本蒸馏总是很难成功"背后还有一个更根本的
架构问题：把强依赖运行时页面状态的操作（浏览器抓取）硬套进"一次性固化成确定性代码"
的单一模型，中间缺一档"比脚本鲁棒、比全量探索便宜"的手段；而这个缺口，与项目里已经
存在的 `hybrid_exec`（`next_doc/hybrid_exec_design_plan.md`）本质是同一个"低成本手段
优先、逐级降级"状态机的另一套独立实现。因此本计划一并提出把 `generative-capability`
的执行骨架合并到 `hybrid_exec` 之上，而不是两套并行发展。

---

## 1. raw_result 落盘化

**状态：已完成**（详见 `next_doc/generative-capability-skill-plan.md` 阶段
二十六实施记录）。改动与本节设计基本一致，唯一偏差：`RawResultStore.get()`
保留而非移除（便于测试直接按 id 断言），`view_raw_result` 工具改为按路径
读取，`get()` 不再是该工具的主路径。

### 1.1 目标

- 从"session 内存 LRU + 模块级全局单例传递 id"改为"落盘 + 传路径"，从根上消除
  多 Agent/SubAgent 实例互相覆盖全局指针的问题（问题2 不再需要专门打补丁修复）。
- 支持跨 task、跨 session 事后查看原始结果（当前内存 LRU 随进程释放，session 一结束
  就彻底没了）。

### 1.2 存储布局

```
<project_root>/.agent/raw_results/<session_id>/<result_id>.txt        # 原文
<project_root>/.agent/raw_results/<session_id>/<result_id>.meta.json  # {tool_name, created_at, task_id?}
```

- `result_id` 继续用内容 md5 短哈希，同一 session 内天然去重。
- `session_id` 用 Agent/SubAgent 已有的 session 标识，不新增概念。
- 写入用 tmp + rename 原子化，避免半写文件被并发读到。

### 1.3 接口变化

- `RawResultStore.put(content, tool_name)`：内部实现从"写入内存 dict"改为"写入
  上述路径"，返回值不再是纯 id，而是可直接使用的**完整文件路径**（或至少让调用方
  能拼出路径——直接返回路径更简单，减少一次拼接约定）。
- `RawResultStore.get(...)`：可以保留，也可以整体去掉——因为落盘之后，"取回原文"这个
  动作本质上和 `read_file` 没有区别。倾向于**去掉专门的 get 逻辑**，`view_raw_result`
  工具直接提示 agent 用已有的 `read_file(path=...)` 读取，减少一套重复的读取路径。
- `_trim_result()` 末尾附带的提示文案，从
  `Use view_raw_result(result_id="...") to inspect the original ...`
  改为
  `Full output saved to <path>. Use read_file to inspect it.`
  （或保留 `view_raw_result` 这个工具名字但参数从 `result_id` 换成 `path`，纯粹作为
  "同义提示"存在，视是否需要保持向后兼容的工具名而定，倾向于直接换成 `read_file`，
  没有必要维护两套读文件的工具）。

### 1.4 随之消失的问题

- 不再需要 `tools/builtin.py` 里的模块级全局 `_raw_result_store` 及
  `configure_raw_result_store()` 注入机制——每次 `put()` 只需要知道"当前 session_id
  + project_root"就能算出路径，是纯函数式的，不需要"当前活跃的是哪个 store 实例"这种
  全局可变状态。`Agent.__init__` 里也不再需要那段注入逻辑。
- 多个并发 SubAgent 各自的原始结果天然落在各自的 `<session_id>/` 子目录下，互不干扰。

### 1.5 清理策略

不再是"内存 LRU 超限即驱逐"，改为低频后台巡检（复用 `health_patrol.py` 的风格：
只读扫描 + 显式 `apply_cleanup` 开关）：

- 按 session 目录的总大小 / 总文件数 / 最后修改时间设置上限，超过阈值的旧 session
  目录才整体清理，不做单文件级别的精细驱逐。
- 默认只读扫描输出"建议清理"报告，显式传参才真正删除，与 `health_patrol` 保持
  一致的"清理需要显式确认"的原则。

### 1.6 改动文件清单（预估）

```
src/mini_agent/perception/raw_result_store.py   # 存储实现改为文件系统版本
src/mini_agent/agent/core.py                     # 去掉 configure_raw_result_store 注入
src/mini_agent/tool_executor.py                  # _trim_result 提示文案改为路径
src/mini_agent/tools/builtin.py                  # view_raw_result 改为按路径读取，
                                                   # 或直接移除，改用 read_file
tests/test_raw_result_and_smart_summary.py       # 同步改写测试
```

---

## 2. 探索脚本假数据问题：合理性校验

**状态：已完成**（详见 `next_doc/generative-capability-skill-plan.md` 阶段
二十五实施记录）。与本节设计的唯一收窄：合理性检查只施加于
`script_source`/`llm_synthesized` 两条路径，不施加于 `trace_replay`——
运行既有测试后发现 `trace_replay` 自测执行器在测试/部分生产场景下天然不
随输入变化，属于环境属性而非脚本本身硬编码的证据，纳入检查会造成误伤，
故收窄范围（不影响本节要解决的核心问题——`script_source` 路径下探索
子agent自己写的假数据脚本）。

### 2.1 现状问题

`explorer/prompt.md` 只要求"整理成不依赖具体探索过程的脚本"，没有明确禁止把本次
观察到的具体数据硬编码进去；`distiller.py` 里已经对 `llm_synthesized` 路径写了
"不要硬编码 url/query"的提示词约束，但这条约束**没有覆盖到 `script_source` 路径**
（探索子agent自己写脚本直接提交，优先级最高，恰恰跳过了这层提示）。而沙箱自测目前
只做 `intent_schema` 结构校验，假数据脚本结构完全合法，规则校验发现不了。

### 2.2 改进措施

1. **`explorer/prompt.md` 显式禁止硬编码**：在"收尾"部分明确加入——提交的
   `script_source` 禁止包含本次探索观察到的具体数据（标题/URL/数字等具体值），
   脚本必须真正使用 `input` 参数重新执行导航/提取，对不同输入要能产生不同结果。
   该约束应写进 `capability.yaml` 的公共模板说明里，因为这是所有
   `generative-capability` skill 通用的问题，不止 browser-site-scraper 一个领域。

2. **蒸馏阶段新增"脚本合理性检查"**（低成本规则预检 + LLM 复核两级，思路与
   现有 `resolve()` 的"确定性匹配优先、命中失败再上 LLM"分级触发原则一致）：
   - **规则预检（零成本）**：用两个不同的采样 `input`（例如不同 `query`）各跑一次
     `run()`，若两次输出完全相同（且业务上不应该相同），标记为可疑，直接判自测失败，
     不需要动用 LLM。
   - **LLM 复核**：规则预检通过后，再用一次独立、单一职责的 LLM 调用，把脚本源码
     + 本次 `request` 一并交给模型，只回答"该脚本是否把探索观察到的具体数据硬编码
     进了源码 / 是否真实依赖 `input` 参数执行"，不通过则和 schema 校验失败一样丢弃、
     不落盘、不污染检索池。
   - 这一检查同样适用于 `distill_source_kind: script_source` 的产物——现有实现对
     "子agent自己判断可复用而直接提交脚本"的信任没有二次核验，属于同样的漏洞。

### 2.3 改动文件清单（预估）

```
.claude/skills/browser-site-scraper/explorer/prompt.md   # 显式加入禁止硬编码的约束
.claude/skills/_engine/distiller.py                       # 新增合理性检查
                                                            # （规则预检 + LLM 复核）
                                                            # 覆盖 script_source /
                                                            # llm_synthesized 两条路径
next_doc/generative-capability-skill-plan.md              # 追加阶段实施记录
```

---

## 3. member 三档执行机制：合并 hybrid_exec

**状态：`HybridExecutor` 主循环已接入 SKILL 档；`CapabilityEngine` 已用
风险更低的方式试点接入 SKILL 档；探索失败脚本蒸馏时已能自动产出
`playbook.md` 兜底；SKILL 档被证明可靠后已能自动升级蒸馏为 `script.py`；
`capability_engine` 整体委托给 `HybridExecutor` 仍未开始**——本阶段
（3.3e 节）补上了 3.3b 遗留的最后一项：`_try_skill()` 每次成功后检查
该 playbook 的累计成功次数，达到调用方配置的门槛且开启了升级开关时，
用 LLM 阅读 playbook 文本 + 一次真实执行样例，尝试蒸馏出等价的
`script.py`（复用与 `distill()` 完全一致的沙箱自测/schema 校验/合理性
检查/原子落盘）。至此，"script → skill → explore"三档不仅有"降级"方向
（阶段三十：脚本失败退化为 playbook），也有"升级"方向（本阶段：playbook
证明可靠后固化为脚本），3.3b 节列出的三项遗留工作全部完成，只剩
`capability_engine.resolve/execute` 整体委托给 `HybridExecutor.run()`
这一项范围最大、风险最高的重构仍未开始。

**开放问题决策（用户已确认）**：
1. playbook 不复用 `ScriptRepository` 的 `<task_id>/v{n}.py` 目录布局，
   单独设计一套版本化目录——见 3.3a 节 `PlaybookRepository` 的实施说明。
2. `skill` 档"轻量 Agent 参照 playbook 执行"的工具集范围、回合预算暂不
   预设具体数值，留到接线 `PlaybookRunner` 时结合真实场景一次性定下。

### 3.1 动机

脚本蒸馏难以稳定复用的根因：浏览器抓取这类任务强依赖运行时页面状态（改版、AB测试、
分页、弹窗时机），把它硬套进"一次性固化成确定性代码"的模型天然脆弱。中间缺一档
"比脚本鲁棒、比全量探索便宜"的手段——**让 LLM/轻量 Agent 在执行时参照一份人类可读
的步骤说明（playbook）去带工具执行**，而不是逐动作重放固化的脚本。步骤说明本身
不是代码，页面细节变化时能像人看着 SOP 操作一样做局部适应，选择器变了不会直接
让整个方案报废。

这与项目里已经存在的 `hybrid_exec`（见 `next_doc/hybrid_exec_design_plan.md`）
本质是同一个"低成本手段优先、失败逐级降级"状态机的两套独立实现：

| | generative-capability（现状） | hybrid_exec（已有） |
|---|---|---|
| 手段分级 | 无中间档，只有 命中已有 member / 全新探索 两级 | `ExecutionTier`: SCRIPT → LLM → AGENT |
| 版本化存储 | `registry.json` 手写 success/fail 计数 | `ScriptRepository` + `ScriptRecord`（已有 success_count/fail_count/consecutive_fail/status） |
| 生成器 | `explorer_runtime.py`（SubAgent 驱动探索） | `Explorer`（`LLMExplorer`/`AgentExplorer`） |
| 校验 | `intent_schema` + `schema_validator` | `TaskSpec.output_validator` |

两者应当合并成一套，而不是并行演进、各自踩坑。

### 3.2 三档 member 模式设计

对应用户提出的优先级 **script > skill > explore**，与 `hybrid_exec` 的
`ExecutionTier` 对应关系如下（`skill` 档是本次新增，插在 SCRIPT 和 AGENT 之间，
语义上不同于 `hybrid_exec` 现有的 `ExecutionTier.LLM`——现有 LLM 档是"LLM 一次性
生成一份脚本草稿"，而 `skill` 档是"LLM/轻量 Agent 在每次调用时参照 playbook 执行"，
产出物、执行方式都不同，需要作为独立 tier 引入，暂命名 `ExecutionTier.SKILL`）：

| tier | 说明 | 成本 | 鲁棒性 | 产出物 |
|---|---|---|---|---|
| `script` | 确定性代码直接执行 | 最低 | 最脆，环境一变就挂 | `members/<id>/script.py` |
| `skill`（新增） | LLM/轻量 Agent 参照 playbook 执行，工具集受限、回合预算比全量探索小得多（已知大致步骤，不是从零摸索） | 中 | 中，能应对页面细节变化，步骤本身不变时有效 | `members/<id>/playbook.md` |
| `explore` | 全新自由探索（对应 `ExecutionTier.AGENT`） | 最高 | 最强泛化，能应对结构性变化 | 成功后优先蒸馏 `script.py`（形状足够参数化时），否则退化为整理 `playbook.md`（步骤说明，替代/补充现有 trace-replay 兜底路径） |

**执行顺序**（`capability_engine.execute()` 改造为按序尝试，而不是"脚本失败直接
判 member 失败进入全新 explore"）：

1. 有 `script.py` 且未处于 degraded → 先跑脚本；失败进入 2。
2. 有 `playbook.md`（或 script 缺失/已 degraded）→ 用 playbook 驱动一次轻量
   Agent 执行；产出同样必须经过 `intent_schema` 校验。若本次执行观察到"和
   playbook 描述高度一致、且可进一步参数化"，可顺手升级蒸馏为 `script.py`
   （脚本比 playbook 更便宜，值得升级）。
3. 都不行 → 进入全新 `explore`，摸索成功后按"能参数化则落 script，否则落
   playbook"的优先级落盘。

`meta.json` 增加 `available_tiers` 字段记录该 member 当前实际具备哪些手段；
`degraded` 判定按"当前实际在用的最低成本可用 tier 连续失败"计算，而非只有
script 一档的成败统计——script 挂了不必然要重新探索，先尝试 skill 档更便宜。

### 3.3a 本阶段已实施部分

- `hybrid_exec/spec.py`：`ExecutionTier` 新增 `SKILL`，插在 `LLM` 和
  `AGENT` 之间（枚举值 `"skill"`）。`TaskSpec.allow_tiers` 默认值不变
  （仍是 `SCRIPT, LLM, AGENT`），`SKILL` 档需要调用方显式传入
  `allow_tiers` 才会生效——因为 `PlaybookRunner`（真正执行"参照 playbook
  跑一次轻量 Agent"的执行器）和 `HybridExecutor` 主循环的接线还没做，
  现在把 `SKILL` 放进默认 `allow_tiers` 只会导致主循环遇到不认识的 tier。
- `hybrid_exec/playbook_repository.py`（新增）：`PlaybookRepository`，
  接口形状与 `ScriptRepository` 完全同构（`save_new_version`/
  `record_success`/`record_failure`/`retire`/`list_versions` 等），落盘
  目录改为 `<project_root>/.agent/hybrid_exec/playbooks/<task_id>/`，
  文件后缀 `.md` 而非 `.py`。与 `ScriptRepository` 各自独立管理自己的
  `meta.json`，同一个 `task_id` 可以同时有脚本版本历史和 playbook 版本
  历史，互不干扰、由调用方决定当前实际采用哪一档。
- `hybrid_exec/__init__.py`：导出 `PlaybookRepository`/`PlaybookRecord`。
- `tests/test_hybrid_exec_playbook_repository.py`（新增）：8 个用例，
  与 `tests/test_hybrid_exec.py::TestScriptRepository` 对称验证同一组
  行为（存取/新版本转正/成功重置连续失败计数/连续失败自动退役/手动退役），
  外加两个 playbook 专属用例：文件后缀确认为 `.md`、以及"同一 task_id 在
  两个仓库里的版本历史完全独立"的显式回归验证。全量通过，既有
  `test_hybrid_exec.py`/`test_hybrid_exec_p3.py`/`test_hybrid_exec_p4.py`/
  `test_hybrid_exec_summary_route.py` 无回归（共 47 + 8 = 55 用例）。
- `hybrid_exec/playbook_runner.py`（新增）：`PlaybookRunner`，与
  `explorer.py::AgentExplorer`/`FallbackExecutor.agent_direct` 同构地
  复用 `_agent.run_agent_prompt()`（"临时起一个最小 Agent 跑一次 prompt"
  的共享逻辑），给定 playbook 文本 + `TaskSpec`，拉起一次执行，返回 Agent
  最后一轮的原始文本回复（是否/如何解析交给 `TaskSpec.output_validator`，
  与 `agent_direct()` 的既有约定一致，不新增一套解析规则）。约定 Agent
  最终回复以 `PLAYBOOK_INVALID:` 开头时视为"这份 playbook 根本走不通"，
  抛出 `PlaybookInvalidError`（携带原因），供调用方区分"这次执行失败，
  值得重试"和"这份 playbook 该退役了"两种情况。
  **`max_turns` 构造参数没有默认值，必须显式传入**——对应用户已确认的
  "skill 档工具集/回合预算暂不预设数值"，不在这里偷偷塞一个默认值。
  新增 `prompts/run_playbook.md` 提示模板，风格与既有
  `prompts/explore_script_agent.md`/`prompts/fallback_agent.md` 一致。
- `tests/test_hybrid_exec_playbook_runner.py`（新增，5 用例）：mock 掉
  `_agent.run_agent_prompt`（同 `test_hybrid_exec_p2.py` 的既有模式），
  验证 prompt 拼装、`max_turns`/`session_label` 透传、`max_turns` 缺失时
  报 `TypeError`（而不是静默用某个默认值）、`PLAYBOOK_INVALID:` 前缀正确
  识别为 `PlaybookInvalidError`（含"未说明原因"兜底文案）。全量通过；
  连同前述 `playbook_repository` 测试，本阶段新增 13 用例，加上既有
  hybrid_exec 全部测试文件共 91 用例，全部通过，无回归。

### 3.3a-2 本阶段新增：HybridExecutor 主循环接入 SKILL 档

对应 3.3b 原先列出的第一项"`HybridExecutor._run()` 的主循环状态机接入
SKILL 档"，本阶段已完成、不再属于未实施范围：

- `hybrid_exec/executor.py::HybridExecutor.__init__`：新增两个可选构造参数
  `playbook_repo: Optional[PlaybookRepository]`、
  `playbook_runner: Optional[PlaybookRunner]`，均默认 `None`——不传时
  SKILL 档在 `_try_skill()` 里直接跳过，对所有既有调用方（未传新参数）
  行为零影响，与 `TaskSpec.allow_tiers` 默认不含 `SKILL` 保持一致的
  "不默认改变既有行为"原则。
- 新增私有方法 `HybridExecutor._try_skill(task, attempts)`：
  - `ExecutionTier.SKILL` 不在 `task.allow_tiers`，或
    `playbook_repo`/`playbook_runner` 未配置，或该 `task_id` 没有 active
    playbook（从未探索/落盘过）→ 直接返回 `None`（静默跳过，不产生
    SKILL 相关 attempt 记录，不影响后续降级流程判断）。
  - 有 active playbook → 调用 `PlaybookRunner.run(task, content)`：
    - 抛 `PlaybookInvalidError`（Agent 判定 playbook 根本走不通）→
      记一条失败 attempt，直接 `playbook_repo.retire(...)`（不走
      `consecutive_fail` 计数——语义上这不是"偶发失败"而是"这份 playbook
      本身该淘汰了"，与脚本档"多次失败才 retire"的判定原则不同，因为
      Agent 已经明确给出了判断，不需要靠统计再确认一次）。
    - 抛其它异常 → 记一条失败 attempt，`record_failure(...)`（走正常的
      `consecutive_fail` 累计/自动 retire 路径，与脚本档一致）。
    - 正常返回 → 用 `task.run_validator()` 校验输出：通过则
      `record_success(...)` 并把输出向上返回（外层以
      `tier_used=ExecutionTier.SKILL` 结束本次 `run()`）；不通过则视为
      失败，`record_failure(...)`，继续向下降级。
- `HybridExecutor._run()` 执行顺序调整为与
  `next_doc/generative-capability-skill-plan.md`（原设计 §3.2）描述的
  三档优先级一致：
  1. 有 active 脚本 → 先跑脚本，失败则走 `_repair_loop`（不变）；
  2. 脚本修复彻底失败，或从一开始就没有 active 脚本 → 尝试 `_try_skill`
     （新增，插在原先"脚本路径耗尽"和"进入 explore/fallback"之间）；
  3. SKILL 档也拿不到结果 → 仅当"从一开始就没有 active 脚本"时才尝试
     `_explore`（脚本存在但修复失败的情形，沿用改造前的既有行为，不进入
     `_explore`，直接下一步 Fallback——这一分支的行为在本次改造中刻意
     保持不变，只是在它前面插入了 SKILL 档，避免过度扩大改动面）；
  4. 都不行 → Fallback（不变）。
- `hybrid_exec/executor.py::default_executor()`：新增
  `enable_skill_tier: bool = False`、`skill_max_turns: Optional[int] = None`
  两个参数。默认不启用（保持与既有调用方零差异）；`enable_skill_tier=True`
  但未传 `skill_max_turns` 时显式 `raise ValueError`（SKILL 档轻量 Agent
  的回合预算没有默认值，见 3.3a 节 `PlaybookRunner` 的既有约定，这里不
  偷偷补一个默认值）；启用时会构造
  `PlaybookRepository(project_root / ".agent" / "hybrid_exec" / "playbooks", ...)`
  与 `PlaybookRunner(app_cfg, max_turns=skill_max_turns)` 并传入
  `HybridExecutor`。
- `tests/test_hybrid_exec_skill_tier.py`（新增，7 用例）：向后兼容性（不
  传 playbook 依赖 / `allow_tiers` 不含 `SKILL` 时即使配置了依赖也不生效）、
  脚本修复失败后 SKILL 顶上成功、无脚本时 SKILL 优先于 explore、SKILL
  输出未过校验后正确降级并记失败、`PlaybookInvalidError` 直接 retire、
  没有 active playbook 时静默跳过共 7 种场景。连同既有
  `test_hybrid_exec.py`/`test_hybrid_exec_p2.py`/`test_hybrid_exec_p3.py`/
  `test_hybrid_exec_p4.py`/`test_hybrid_exec_playbook_repository.py`/
  `test_hybrid_exec_playbook_runner.py` 全量运行，共 71 用例全部通过，
  无回归。

### 3.3b 仍未实施部分（下一阶段范围）

**[本次更新]** 3.3b 原先列出的三项遗留工作已全部完成：
"三档执行顺序在 `capability_engine` 里的实际调度逻辑"见 3.3c 节，
"`explore` 阶段产出 `playbook.md` 的整理规则"见 3.3d 节，
"SKILL 档执行时观察到可参数化则升级蒸馏为 script.py"见 3.3e 节。
唯一仍未开始的是范围最大、风险最高的一项：

- `capability_engine.py` 的 `resolve/execute` **整体**委托给
  `HybridExecutor.run()` 执行（见下方 3.4 节，原设计不变）——涉及现有
  `registry.json` 状态机与 `ScriptRepository`/`PlaybookRepository` 的
  字段映射，尚未开始，留待评估 3.3c/3.3d/3.3e 节的试点效果（即"不整体
  迁移、只在 `capability_engine` 现有链路里分别接入 script→skill→explore
  各个方向"这条更小风险路径本身是否已经足够）后再决定是否需要。
- `meta.json`/`registry.json` 新增 `available_tiers` 字段、`degraded`
  判定改为"按当前实际在用的最低成本可用 tier 连续失败计算"——3.3c/
  3.3d/3.3e 节的实现刻意**不**触碰 `registry.json` 里 script 那一档已有
  的状态机，playbook 的成功率统计完全独立记在单独的
  `playbooks/<member_id>/meta.json` 里，二者互不影响，
  `available_tiers`/`degraded` 这类"统一视角"的字段仍未实施。

### 3.3e 本阶段新增：SKILL 档被证明可靠后自动升级蒸馏为 `script.py`

对应 3.3b 原先列出的第三项，本阶段已实施、不再属于未实施范围。与阶段
三十"脚本失败 → 退化落 playbook"方向相反：这是"playbook 已经被反复证明
可靠 → 尝试把它固化成更便宜的 script.py"，让 3.3d 节打通的"落 playbook"
与阶段二十九的"用 playbook"之间，再补上一条"用得好就升级"的路径，三档
之间形成完整的双向流转（script ⇄ skill ⇄ explore），而不只是单向降级。

**改动内容**：

1. `distiller.py` 新增 `attempt_skill_upgrade(...)`：不是 `distill()` 的
   一条新路径（没有逐步 trace 可用——`PlaybookRunner` 驱动的是一次自由
   的 Agent 执行，不是固定工具调用序列，无法复用 `trace_replay`/
   `script_source` 两条既有蒸馏路径），而是一个独立的"事后再给一次机会"
   入口：用新增的 `_llm_synthesize_script_from_playbook()` 把 playbook
   文本 + 一次真实执行的输入/输出样例交给 LLM（系统提示词
   `_SKILL_UPGRADE_SYSTEM_PROMPT`，与 `_llm_synthesize_script()` 的
   "不要硬编码样例具体值、必须真实调用工具执行器"等硬性要求一致），产出
   的脚本经过与 `distill()` 完全一致的"沙箱自测 → `intent_schema` 校验 →
   `_check_script_plausibility()` 合理性检查 → `_atomic_persist()` 原子
   落盘"流程，`distill_source_kind` 记为 `"skill_upgraded"`
   （`SKILL_UPGRADED_HEADER_TEMPLATE`）以便审计区分于另外三种来源。任何
   一步失败都返回 `False`，playbook 本身不受任何影响、不计入其成败统计
   ——升级是"锦上添花"，不是"必须成功的一步"。
2. `capability_engine.py::CapabilityEngine.__init__` 新增两个可选参数
   `enable_skill_upgrade: bool = False`、`skill_upgrade_success_threshold:
   int = 3`——默认关闭，即使注入了 `llm_helper` 也不会尝试升级，与项目里
   "新增能力默认不生效"的一贯风格一致。
3. 新增私有方法 `_maybe_upgrade_skill_to_script(member_id, request,
   result_data, playbook_content)`：`_try_skill()` 每次成功执行并
   `record_success()` 之后调用。未开启升级开关、或未注入 `llm_helper`、
   或该 member 已有 `script.py`、或 playbook 累计成功次数未达门槛，均
   静默跳过。升级本身抛出的任何异常都被吞掉（`try/except`），不影响
   `_try_skill()` 已经拿到的成功结果——本次调用的返回值不受升级尝试
   成败影响。升级成功落盘后重新加载 `self.index`/`self.registry`，保持
   内存与磁盘一致（下一次 `execute()` 就能直接加载到新脚本）。
4. `tests/test_generative_capability_skill_upgrade.py`（新增，3 用例，
   用桩 `llm_helper`/`skill_runner`/`tool_executor`，不依赖真实 LLM/
   浏览器）：默认关闭时即使门槛达标也不升级（向后兼容）、开启后 LLM
   产出的脚本通过全部校验时正确落盘（`meta.json` 的
   `distill_source_kind`/`version` 字段、`playbooks/` 目录未受影响、
   落盘的脚本可被独立加载并正确执行）、LLM 产出内容不像 Python 源码时
   静默放弃升级且不影响本次已成功的调用结果、playbook 本身不受影响
   共 3 种场景，全部通过。连同既有全部 `hybrid_exec`/
   `generative_capability`/`distiller` 相关测试文件（同样排除需要
   `fastapi` 的 `test_hybrid_exec_summary_route.py`），共 124 用例通过、
   2 个与本次改动无关的既有环境相关失败（同阶段三十记录的
   `test_full_explore_distill_reuse_cycle`、`websocket-client` 依赖
   缺失），无新增回归。

**已知限制（留给后续阶段）**：
- 升级门槛（`skill_upgrade_success_threshold`）目前是简单的"累计成功次数
  ≥ 门槛"，每次成功都会重新检查一次——如果第一次尝试升级失败（LLM 产出
  没通过自测），后续每次成功仍会再次尝试升级（因为没有"升级已尝试过"的
  标记），这是刻意的简化：升级尝试本身成本可控（一次 LLM 调用 + 一次
  沙箱自测），且不消耗 playbook 的成败统计，暂不需要额外的"已尝试过"
  状态位；如果后续发现升级失败率高、重复尝试造成不必要的 LLM 调用开销，
  再补一个每 member 的"最近升级尝试时间/次数"节流。
- 升级产出的 `script.py` 落盘时，`_atomic_persist()` 会用
  `_infer_match_rule(request)` 基于**触发升级那一次的请求**重新生成该
  member 的检索匹配规则（`_index.json` 里的 `match` 字段），这是
  `_atomic_persist()` 本身的既有行为（`reexplore`/常规 `distill()` 产出
  脚本时同样如此），不是本阶段新引入的问题，但值得记录：如果该 member
  最初是通过 LLM 语义检索（而非确定性 `domain_pattern`/`keyword` 匹配）
  命中的，升级后的匹配规则可能与升级前不同，需要在后续评估
  `available_tiers`/统一检索规则时一并考虑。

### 3.3d 本阶段新增：`explore` 阶段产出 `playbook.md`（脚本蒸馏失败兜底）

对应 3.3b 原先列出的第二项，本阶段已实施、不再属于未实施范围：脚本蒸馏
三条路径（`script_source`/`llm_synthesized`/`trace_replay`，含各自的修复
重试）**全部失败**、但探索本身确实成功且数据已通过 `intent_schema`
校验时，`distill()` 不再直接判整次探索"无沉淀"，而是把探索过程整理成一份
`playbook.md` 落盘，交给 3.3c 节的 SKILL 档今后参照执行——这正是打通"3.3c
节 SKILL 档目前只能使用人工预先放好的 playbook"这一限制的自动化入口。

**改动内容**：

1. `distiller.py::DistillResult` 新增 `playbook_only: bool = False`
   字段：脚本蒸馏全部失败但成功落 playbook 兜底时 `success=True` 且
   `playbook_only=True`，调用方（`capability_engine.explore()`）据此得知
   这次探索的沉淀物是 playbook 而非 script，但对现有 `explore()` 主流程
   透明——`success=True` 时的处理逻辑不需要区分，行为与"蒸馏出脚本"完全
   一致（重新加载 index/registry、返回 `status="success"`）。
2. `distill()` 新增可选参数 `playbook_repo: Any = None`（通常是
   `skill_tier.build_playbook_repo(skill_dir)` 构造的
   `PlaybookRepository`）。未传时行为与此前完全一致，三条脚本路径失败
   即直接返回失败——不默认改变任何既有调用方行为。
3. 新增 `_build_playbook_markdown(trace, request, skill_name, member_id)`：
   把 trace 中非失败的步骤（跳过探测性死胡同，与 trace_replay 脚本
   "不区分死胡同和关键路径"的已知局限刻意划清界限）整理成"调用了什么
   工具、参数结构大致什么样"的步骤列表，附上探索阶段实际拿到的数据形状
   作为"预期产出形状"参考。刻意不把具体的标题/URL/数字等值写进步骤描述
   本身，与 `explorer/prompt.md` 对 `script_source` 路径"禁止硬编码
   具体数据"的既有要求保持一致的精神。
4. 新增 `_persist_playbook_member(...)`：与 `_atomic_persist()` 对称，
   但只登记 member 的检索元信息（`meta.json` + `registry.json` +
   `_index.json`），不写 `script.py`——playbook 正文由
   `playbook_repo.save_new_version()` 单独落盘（复用 3.3a 节已有的
   `PlaybookRepository` 实现，不重复一套存储逻辑）。`registry.json`
   条目新增 `execution_tier: "skill_only"` 标记（仅供人工/审计识别，不
   参与任何现有状态机判断），`meta.json` 的 `distill_source_kind` 记为
   `"playbook"`（区别于既有的 `script_source`/`llm_synthesized`/
   `trace_replay` 三种）。member 目录下没有 `script.py` 时，
   `CapabilityEngine._load_member_run()` 天然返回 `None`，`execute()`
   判"脚本加载失败"，后续 `call()` 走到 3.3c 节的 `_try_skill()`，用
   同一个 `member_id` 去 `playbook_repo` 里找 active playbook——两个
   阶段（"产出 playbook"与"使用 playbook"）通过 `member_id` 这一既有
   概念自然衔接，不需要新增映射机制。
5. 新增 `_distill_to_playbook(...)`：串联上述两步，任何异常都静默返回
   `None`（退回调用方原有的"全部路径失败"错误信息，不让这个兜底路径
   本身的异常掩盖真正的失败原因）。
6. `capability_engine.py::CapabilityEngine._distill()`：调用 `distill()`
   时新增透传 `playbook_repo=self.playbook_repo`——`self.playbook_repo`
   未注入时（既有调用方）仍是 `None`，行为不变；注入后（配合
   `skill_tier.build_playbook_repo()`）即可自动打通"脚本蒸馏失败 →
   落 playbook → 下次命中该 member 时 3.3c 节 SKILL 档自动使用"的完整
   闭环。
7. `tests/test_distiller_playbook_fallback.py`（新增，3 用例）：未注入
   `playbook_repo` 时行为不变（向后兼容）、注入后脚本蒸馏失败正确落盘
   playbook 且登记 member（`meta.json`/`registry.json`/`_index.json`
   均验证，且确认没有 `script.py`）、`playbook_repo` 自身抛异常时不掩盖
   原始的"全部路径失败"错误信息共 3 种场景，全部通过。连同既有
   `test_generative_capability_engine.py`（45 用例通过，1 个与本次改动
   无关的既有失败——`test_full_explore_distill_reuse_cycle`，与 3.3c
   节记录的同一个环境相关预置失败，非本次改动引入）及全部
   `hybrid_exec`/`generative_capability`/`distiller` 相关测试文件（排除
   需要 `fastapi` 依赖、当前环境未安装的
   `test_hybrid_exec_summary_route.py`），共 121 用例通过、2 个既有失败
   （另一个是 `test_generative_capability_real_tools.py` 里缺少
   `websocket-client` 依赖导致的环境相关失败，同样与本次改动无关），
   无新增回归。

**已知限制（留给后续阶段）**：
- "SKILL 档执行时观察到可进一步参数化则顺手升级蒸馏为 `script.py`"仍未
  实现——目前 playbook 一旦落盘，除非重新探索（`reexplore_member_id`），
  不会自动尝试再次升级为脚本。
- `meta.json`/`registry.json` 里 `execution_tier`/`available_tiers`/
  统一 `degraded` 判定仍是 3.3b 节列出的未实施范围，本阶段的
  `execution_tier: "skill_only"` 只是一个信息性标记，不是该统一视角的
  正式实现。

### 3.3c 本阶段新增：CapabilityEngine 试点接入 SKILL 档（不改动 registry.json 状态机）

对应 3.3b 原先列出的"三档执行顺序（script → skill → explore）在
`capability_engine` 里的实际调度逻辑"，本阶段用一种风险更低的方式先落地：
**不做** `capability_engine.resolve/execute` 整体委托给 `HybridExecutor`
（那需要先解决 `registry.json` 状态机与 `ScriptRepository` 字段映射这个
本身就复杂的问题），而是在 `CapabilityEngine.call()` 现有的"命中 member
执行失败 → 判断是否重新探索 → 进入 explore()"这条既有链路里，插入一次
"参照已有 playbook 跑一次轻量 Agent"的尝试，验证 script → skill → explore
优先级本身在真实调用路径里是否成立，同时完全不改变 `registry.json` 里
script 那一档的既有行为。

**改动内容**：

1. 新增 `src/mini_agent/skills/generative_capability/skill_tier.py`：
   - `build_playbook_repo(skill_dir)`：把 skill 目录下新增的 `playbooks/`
     子目录包成一个 `PlaybookRepository`（复用 `hybrid_exec` 里的同一个
     实现，不重新发明），与 `members/` 平级但互不干扰——`member_id` 本身
     直接作为 `PlaybookRepository` 的 `task_id`。
   - `build_skill_runner(project_root, max_turns=..., mini_agent_config=None)`：
     构造一个符合 member `run(request) -> {"status", "data", "error"}`
     契约的 callable，内部用 `PlaybookRunner` 执行并把返回文本 `json.loads`
     成结构化结果；解析失败（Agent 没按格式回复）与 schema 校验失败（结构
     对但语义错）在错误信息里明确区分。`PlaybookInvalidError` 被转换成
     一个专门前缀 `SKILL_RETIRE_ERROR_PREFIX = "PLAYBOOK_INVALID: "` 的
     错误字符串（不抛异常，保持 member 契约"只返回 dict"的一致性）。
2. `capability_engine.py::CapabilityEngine.__init__`：新增可选参数
   `playbook_repo`/`skill_runner`，与既有 `explore_runner`/`tool_executor`
   同样的 DI 风格，未注入时零影响。
3. 新增 `CapabilityEngine._try_skill(member_id, request)`：没有配置依赖、
   或该 member 没有 active playbook → 静默跳过（返回 `None`，不算失败
   尝试）；有 → 调用 `skill_runner`，按 `SKILL_RETIRE_ERROR_PREFIX` 前缀
   区分"直接 retire"和"记一次普通失败"，成功且通过
   `_validate_schema()`（与 `execute()` 完全复用同一份 schema 校验逻辑，
   不重写一套）才算成功。
4. `CapabilityEngine.call()`：命中的候选 member 全部执行失败后、判断是否
   "重新探索"（reexplore）之前，插入一次 `_try_skill()` 调用——成功则
   直接返回 `resolve_reason="skill_playbook"`；失败则把 SKILL 档的失败
   原因与 member 执行失败的原因合并进最终 `combined_error`，不丢弃诊断
   信息，再继续走原有的 explore 流程。
5. `src/mini_agent/skills/generative_capability/__init__.py`：导出
   `build_playbook_repo`/`build_skill_runner`/`SKILL_RETIRE_ERROR_PREFIX`。
6. `tests/test_generative_capability_skill_tier.py`（新增，6 用例，用最小
   合成 skill 目录、不依赖真实浏览器/网络）：未接线时行为不变、无 active
   playbook 时静默跳过、SKILL 档成功后不进入 explore、SKILL 档失败后错误
   信息正确合并、`PLAYBOOK_INVALID` 前缀直接 retire、SKILL 档输出未过
   schema 校验按失败处理。连同既有 `test_generative_capability_engine.py`
   （17 用例通过，1 个与本次改动无关的既有失败——`test_full_explore_
   distill_reuse_cycle` 在改动前的原始代码上跑同样报错，是环境相关的
   预置失败，非本次改动引入的回归）及全部 hybrid_exec 相关测试文件，共
   94 用例通过、1 个既有失败，无新增回归。

**已知限制（留给后续阶段）**：
- 目前没有任何自动机制往 `playbooks/<member_id>/` 写入新版本——`explore()`
  失败时不会退化整理出 playbook，需要人工/其它工具预先调用
  `PlaybookRepository.save_new_version()` 放一份 `v1.md` 才会被
  `_try_skill()` 用到。这是刻意的范围收窄，验证"用上了会不会更好"比
  "怎么自动产出"优先级更高。
- `_try_skill()` 只在"命中的 member 全部执行失败"之后触发，没有独立的
  `allow_tiers` 之类的开关来跳过 SCRIPT 档直接试 SKILL——这与
  `hybrid_exec.HybridExecutor` 里 `TaskSpec.allow_tiers` 的可裁剪设计不
  同，因为 `capability_engine` 目前没有等价于 `TaskSpec` 的调用方配置面，
  引入这类精细控制留给"是否要整体迁移到 HybridExecutor"的评估结果来决定。

### 3.4 具体合并方式（原设计，尚未实施）

`capability_engine.py` 的 `resolve/execute` 找到目标 member 后，不再自己实现
执行/重试/降级逻辑，而是构造一个 `hybrid_exec.spec.TaskSpec`
（`task_id=member_id`，`allow_tiers` 按上表映射到 `(SCRIPT, SKILL, AGENT)`，
`output_validator` 用 `intent_schema` 包一层适配函数），交给
`HybridExecutor.run()` 执行；`ScriptRepository` 复用为 member 的版本化存储
后端，取代现在手写的 `registry.json` 状态机（`ScriptRecord` 已有的
`success_count/fail_count/consecutive_fail/status` 字段与现有
`probation/trusted/degraded/dead` 语义基本对应，需要做一次字段/状态名映射，
而不是维护两份重复的计数逻辑）。

`generative-capability` 保留的是领域特有部分（`capability.yaml` 声明式配置）：
- `domain_matchers` 检索（两级过滤：确定性匹配 + LLM 裁决，这部分是
  `hybrid_exec` 没有的，是 generative-capability 独有的"先找 member 再执行"
  逻辑，予以保留）。
- `intent_schema_template`（→ 转换成 `TaskSpec.output_validator`）。
- `explorer.prompt`（现状是"从零探索"的角色设定，需要扩展出对应 `skill` 档的
  "参照 playbook 执行"角色设定，以及 `explore` 档产出 `playbook.md` 时的
  整理规则）。

### 3.5 改动文件清单（预估，尚未实施部分）

```
src/mini_agent/hybrid_exec/executor.py            # HybridExecutor._run() 接入
                                                    # SKILL 档决策分支（脚本失败/
                                                    # 缺失后先试 PlaybookRunner，
                                                    # 再降级到 explore/fallback）；
                                                    # spec.py/playbook_repository.py/
                                                    # playbook_runner.py 均已在
                                                    # 本阶段实施完成，不再列入
                                                    # 本清单
.claude/skills/_engine/capability_engine.py        # resolve() 保留，execute()
                                                    # 改为委托给 HybridExecutor
.claude/skills/_engine/distiller.py                # 落盘逻辑改为经由
                                                    # ScriptRepository/
                                                    # PlaybookRepository 统一管理
next_doc/generative-capability-skill-plan.md       # 追加阶段实施记录，标注与
                                                    # hybrid_exec 合并后的新架构
next_doc/hybrid_exec_design_plan.md                # 追加 ExecutionTier.SKILL 的
                                                    # 设计说明（本阶段已在
                                                    # spec.py 落地枚举定义，
                                                    # 设计文档本身的同步说明
                                                    # 仍待补充）
```

---

## 4. 实施顺序建议

1. **第一步：raw_result 落盘化**（第1节）——改动范围小、收益明确（顺带修掉
   `view_raw_result` 的 bug），且与后续改动无耦合，可独立先落地验证。
2. **第二步：探索脚本假数据的合理性校验**（第2节）——同样范围可控，独立于
   三档机制的大改造，先堵住"假数据脚本被固化"这个当前最直接影响可用性的漏洞。
3. **第三步：三档 member 机制 + 合并 hybrid_exec**（第3节）——范围最大，
   涉及两套现有机制的整合，建议先在 `browser-site-scraper` 单一领域试点
   （复用阶段三/阶段五验证过的桩探索器/桩执行器方式验证接线逻辑），确认
   `TaskSpec`/`ScriptRepository` 与现有 `registry.json`/`_index.json` 语义
   对得上之后，再考虑要不要让 `doc-template-generation` 等其它
   generative-capability skill 同步迁移。

## 5. 开放问题（已确认，供后续实施参照）

- **playbook 存储布局**：已确认——单独设计一套版本化目录，不复用
  `ScriptRepository` 的 `<task_id>/v{n}.py` 命名。已落地为
  `hybrid_exec/playbook_repository.py::PlaybookRepository`，见 3.3a。
- **`skill` 档工具集/回合预算**：已确认——暂不预设具体数值，留到实施
  `PlaybookRunner`（见 3.3a）时结合真实场景一次性定下；`PlaybookRunner`
  的 `max_turns` 已按此原则实现为必传参数（无默认值），`default_executor`
  的 `enable_skill_tier=True` 开关、`skill_tier.build_skill_runner()` 均
  同样要求显式传入回合预算参数。
