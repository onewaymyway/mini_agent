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

### 3.3 具体合并方式

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

### 3.4 改动文件清单（预估，本阶段先设计不动代码）

```
src/mini_agent/hybrid_exec/spec.py                # 新增 ExecutionTier.SKILL
src/mini_agent/hybrid_exec/repository.py          # ScriptRecord 是否需要扩展
                                                    # 支持 playbook 产物的版本记录
                                                    # （目前是纯 .py 版本文件布局，
                                                    # 需要评估是否要扩展为
                                                    # <task_id>/v{n}.py 或 .md 两种后缀）
src/mini_agent/hybrid_exec/explorer.py            # 新增 SkillPlaybookExplorer /
                                                    # PlaybookRunner 抽象
                                                    # （对应"参照 playbook 执行"）
.claude/skills/_engine/capability_engine.py        # resolve() 保留，execute()
                                                    # 改为委托给 HybridExecutor
.claude/skills/_engine/distiller.py                # 落盘逻辑改为经由
                                                    # ScriptRepository 统一管理
next_doc/generative-capability-skill-plan.md       # 追加阶段实施记录，标注与
                                                    # hybrid_exec 合并后的新架构
next_doc/hybrid_exec_design_plan.md                # 追加 ExecutionTier.SKILL 的
                                                    # 设计说明
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

## 5. 待确认的开放问题

- `ScriptRepository` 现有目录布局是按纯脚本版本设计的（`v1.py`/`v2.py`），
  引入 `playbook.md` 这种非代码产物后，版本存储布局需要扩展；是否要为
  playbook 单独设计一套版本化目录，还是复用同一套 `<task_id>/v{n}.{py|md}`
  命名，需要在第三步动手前定下来。
- `skill` 档执行时"轻量 Agent 参照 playbook"具体收多窄的工具集、多小的
  回合预算，需要参考现有 `explorer.max_steps`/`max_seconds` 配置项，给
  `skill` 档单独设一套更小的默认值（比如 explore 档 40 步/180 秒，skill
  档可以定在 10-15 步/60 秒量级），具体数值建议第三步实施时结合真实场景
  一次性定下，不在本计划里预设死数字。
