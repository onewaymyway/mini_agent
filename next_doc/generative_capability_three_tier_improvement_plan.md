# generative-capability：三档 member 机制（script→skill→explore）后续改进方案

## 0. 背景

`next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md` 第3节
已经把 script→skill→explore 三档机制的双向流转（降级产出 playbook 兜底、
升级蒸馏为 script.py）落地到 `browser-site-scraper` 试点领域，并在
3.3b/3.4 节明确列出了"整体委托给 `HybridExecutor.run()`"这一项范围最大、
风险最高、尚未开始的重构，以及若干"刻意收窄范围"的已知限制。

本文档不重复上述背景，只做一件事：把审查代码后发现的、值得推进但尚未有
独立文档跟踪的**改进方向**收敛成一份可分阶段实施的清单，每个阶段完成后
在本文档追加实施记录（与项目里其它 `next_doc/*_plan.md` 的记录习惯一致）。

## 1. 改进方向清单（按优先级/风险排序）

| # | 方向 | 现状问题 | 风险/工作量 |
|---|---|---|---|
| 1 | ~~整体合并到 `HybridExecutor.run()`~~ **[已否决，见第6.1节]** | `registry.json`（script 状态机）与 `playbooks/<id>/meta.json`（playbook 成败统计）是两套独立计数，`degraded` 判定只看 script 一档 | 最大——涉及 `ScriptRepository` 字段映射；已评估后决定不做，长期保留两套独立实现 |
| 2 | `meta.json`/`registry.json` 补 `available_tiers` 字段 | 无法从 registry 一眼看出某 member 当前实际具备 script/skill 哪几档手段 | 小——纯信息性字段，只读计算 + 写入，不改变任何决策逻辑 |
| 3 | skill 升级尝试加节流 | `_maybe_upgrade_skill_to_script` 每次 `_try_skill` 成功都会重新检查，失败没有"已尝试过"标记，可能重复触发 LLM 调用（raw_result_and_hybrid_merge_plan.md 3.3e 节已知限制） | 小——独立函数内部加一个时间/次数节流 |
| 4 | `call()` 支持调用方声明 `allow_tiers` | 明知道 script 必然失败（比如刚被标记 degraded）时仍会先跑一遍脚本再降级，浪费一次注定失败的执行 | 中——需要给 `call()`/`execute()` 加可选参数，且不能改变未传参调用方的既有行为 |
| 5 | `doc-template-generation` 等其它 skill 接入三档机制验证泛化性 | 三档机制目前只有 `browser-site-scraper` 一个试点，尚未验证跨领域可复用性 | 中——依赖第1项完成后统一接线更划算，暂不单独排期 |

第1项和第5项范围大、且都依赖"评估试点效果"这个前置判断（raw_result_and_
hybrid_merge_plan.md 3.4 节原文即持此立场），本文档不在此展开设计，仍以
该文档 3.4 节为准；本文档聚焦第2/3/4项——三个自成一体、不改变现有决策语义、
可以独立落地验证的小步改进。

## 2. 各阶段设计

### 阶段一：`available_tiers` 信息性字段（对应改进方向 #2）

**目标**：`registry.json` 每个 member 条目新增只读的 `available_tiers`
字段，记录该 member 当前实际具备哪些执行手段，供人工排查/未来 `health_
patrol.py` 报告使用。**不参与任何现有决策逻辑**（`_apply_lifecycle`/
`_try_skill`/`explore` 触发条件均不读取这个字段），纯粹是"让状态可见"，
避免与"改进方向 #1 整体合并"里提到的真正统一状态机混淆。

**计算规则**（`_compute_available_tiers(member_id)`）：
- `"script"`：`members/<member_id>/script.py` 存在。
- `"skill"`：`self.playbook_repo` 已注入，且
  `playbook_repo.get_active_playbook(member_id)` 不为 `None`。
- 无脚本也无 active playbook 时为空列表 `[]`（该 member 目前只能靠
  `explore()` 重新探索才能再次使用，这本身就是有诊断价值的信息）。

**写入时机**：`_record_execution()`（script 执行后）与 `_try_skill()`
成功/失败路径末尾都调用一次 `_refresh_available_tiers(member_id)`，
与 `_save_registry()` 在同一次落盘中一起写入，不新增单独的 I/O。

### 阶段二：skill 升级尝试节流（对应改进方向 #3）

**目标**：`_maybe_upgrade_skill_to_script` 增加一个轻量节流，避免升级
持续失败时每次 `_try_skill` 成功都重新触发一次 LLM 调用。

**规则**：
- `capability.yaml -> lifecycle` 新增可选配置
  `skill_upgrade_retry_cooldown_seconds`（默认 3600，即失败后至少一小时
  才允许再次尝试；数值可由具体 skill 覆盖）。
- 升级失败（`attempt_skill_upgrade` 返回 `False`）时，在 playbook 的
  `meta.json` 里记录 `last_upgrade_attempt_at`（不影响 `success_count`/
  `fail_count` 等既有成败统计字段）。
- 下次触发升级检查时，若距上次失败尝试未超过冷却时间，直接跳过，不调用
  LLM。冷却时间过后允许再次尝试——不是"只失败一次就永久放弃"，因为
  `PlaybookRunner` 的执行样例每次都不同，后续样例有可能升级成功。
- 升级成功、或从未尝试过升级时，不受冷却时间影响。

### 阶段三：`call()` 支持调用方声明 `allow_tiers`（对应改进方向 #4）

**目标**：`CapabilityEngine.call(request, allow_tiers=None)` 新增可选
参数，值为 `{"script", "skill", "explore"}` 的子集。默认 `None` 时行为与
现在完全一致（三档全部按既有顺序尝试）。显式传入时，`call()` 内部按
声明跳过对应档位：
- `"script"` 不在集合中 → `resolve()` 命中后直接跳过 `execute()`，视同
  该 member 已失败，直接进入 `_try_skill()`。
- `"skill"` 不在集合中 → 跳过 `_try_skill()` 调用。
- `"explore"` 不在集合中 → 命中候选全部失败（或都被跳过）后，不再调用
  `explore()`，直接返回 `not_implemented`（附带跳过原因，不伪造成功）。

这是纯粹的"调用方主动跳过"，不改变 `resolve()` 的检索逻辑本身，也不影响
`registry.json`/`playbook` 的成败统计——被跳过的档位不产生任何执行尝试，
自然也不计入成败计数。

## 3. 实施记录

### 阶段一 —— 已完成

- `capability_engine.py` 新增 `_compute_available_tiers(member_id)` 与
  `_refresh_available_tiers(member_id)`，在 `_record_execution()`（script
  执行后）与 `_try_skill()` 的成功/失败路径末尾调用，`registry.json` 每
  个 member 条目新增 `available_tiers` 字段（如 `["script"]`、
  `["skill"]`、`["script", "skill"]`、`[]`）。
- 纯信息性字段，未修改 `_apply_lifecycle`/`_try_skill`/`explore` 的任何
  触发条件——`available_tiers` 只被写入，不被现有决策逻辑读取。
- `tests/test_generative_capability_available_tiers.py`（新增，4 用例）：
  只有 script 时为 `["script"]`；只有 active playbook 时为 `["skill"]`；
  两者都有时为 `["script", "skill"]`；两者都没有（script 缺失且
  playbook_repo 未注入或无 active playbook）时为 `[]`。全部通过。
- 回归：`test_generative_capability_engine.py`、
  `test_generative_capability_skill_tier.py`、
  `test_generative_capability_skill_upgrade.py` 全量重跑，无新增失败。

### 阶段二 —— 已完成

- `capability_engine.py::_maybe_upgrade_skill_to_script` 新增冷却期判断：
  升级失败时在 `active_pb`（`PlaybookRecord`）关联的 `meta.json` 里记录
  `last_upgrade_attempt_at`（通过新增的
  `playbook_repo.record_upgrade_attempt(member_id, version)` 方法写入，
  与 `record_success`/`record_failure` 同构，不影响 playbook 自身的
  成败统计字段）；下次检查时若距上次失败尝试未超过
  `capability.yaml -> lifecycle -> skill_upgrade_retry_cooldown_seconds`
  （默认 3600 秒，可覆盖），直接跳过、不调用 LLM。
- `hybrid_exec/playbook_repository.py::PlaybookRepository` 新增
  `record_upgrade_attempt()` 方法与 `PlaybookRecord.last_upgrade_attempt_at`
  字段，与既有 `record_success`/`record_failure` 风格一致（原子写入
  `meta.json`）。
- `tests/test_generative_capability_skill_upgrade.py` 新增 2 用例：冷却期
  内第二次调用不再触发 LLM（复用第一次失败留下的 `last_upgrade_attempt_at`）；
  冷却期过后（mock 时间）允许再次尝试。既有 3 用例连同新增共 5 用例全部
  通过，`test_hybrid_exec_playbook_repository.py` 同步新增 1 用例验证
  `record_upgrade_attempt()` 的原子写入行为，全量重跑无回归。

### 阶段三 —— 已完成

- `capability_engine.py::CapabilityEngine.call()` 新增可选参数
  `allow_tiers: Optional[set[str]] = None`。默认 `None` 时行为与此前完全
  一致（不改变任何既有调用方）。显式传入时：
  - `"script" not in allow_tiers` → `resolve()` 命中的候选不再逐个
    `execute()`，直接视为"已失败"进入 `_try_skill()`（跳过原因记录进
    `resolve_reason` 的诊断信息里，不静默丢弃）。
  - `"skill" not in allow_tiers` → 跳过 `_try_skill()` 调用（等价于此前
    `playbook_repo`/`skill_runner` 未注入时的行为，不新增分支复杂度）。
  - `"explore" not in allow_tiers` → 到达"命中候选与 SKILL 档都已尝试
    完毕"这一步后，不调用 `explore()`，直接返回
    `status="not_implemented"`，`error` 里说明是被 `allow_tiers` 主动
    跳过而非能力缺失。
- `tests/test_generative_capability_engine.py` 新增 4 用例：
  `allow_tiers=None` 与省略参数行为一致（向后兼容）；
  `allow_tiers={"skill", "explore"}` 时命中的脚本不被执行、直接走
  skill 档；`allow_tiers={"script"}` 时 skill/explore 均被跳过，脚本
  失败直接返回 `not_implemented` 且 error 说明原因；
  `allow_tiers={"script", "skill"}` 时脚本和 skill 都失败后不触发
  `explore_runner`（用一个"如果被调用就 fail 测试"的 mock 验证未被调用）。
  全部通过，既有全部 `generative_capability`/`hybrid_exec` 相关测试
  文件回归重跑无新增失败。

## 4. 未纳入本次范围

改进方向 #1（整体合并到 `HybridExecutor.run()`）**已在第6.1节评估并
否决**，不再留待后续评估——长期保留两套独立实现。改进方向 #5（第二个
generative-capability skill 接入验证泛化性）仍留待后续评估，不在本文档
展开设计或实施。

## 5. 阶段四（补充修复）：`tools/capability_call.py` 未注入 `playbook_repo`/`skill_runner` 的接线缺口

**背景**：阶段一~三上线后经复核发现，`skill_tier.build_skill_runner()`
的 `max_turns` 此前**没有默认值、必须显式传入**（见第 4 节"未纳入本次
范围"上方 raw_result_and_hybrid_merge_plan.md 第5节"开放问题"里的原文
判断：`skill` 档工具集/回合预算"暂不预设具体数值，留到实施 `PlaybookRunner`
时结合真实场景一次性定下"）。这导致 agent 对话唯一会调用的入口
`tools/capability_call.py` 构造 `CapabilityEngine` 时，从未传入
`playbook_repo`/`skill_runner`/`enable_skill_upgrade` 三个参数——它们在
`CapabilityEngine.__init__` 里全部落回默认值 `None`/`None`/`False`。

实际后果：`_try_skill()` 里 `self.playbook_repo is None or self.
skill_runner is None` 恒真，SKILL 档在**真实对话场景里从未被触发过**，
即便某个 member 目录下确实存在 `playbooks/<id>/` 下的 active playbook；
`distill()` 传给 `_maybe_upgrade_skill_to_script` 的 `playbook_repo` 同样
是 `None`，探索失败兜底产出 `playbook.md`（3.3d 节）与 SKILL 档证明可靠后
升级为 `script.py`（3.3e 节）这两条"双向流转"路径也从未在真实对话里跑通
过——**上面 3.3c/3.3d/3.3e 三节描述的行为，此前只在单元测试的桩环境里
成立，不是真人对话场景下的实际表现**，本节订正这一点。

**修复方式**：不再要求每个 skill 显式声明才能用上 SKILL 档，改为"没配置
就用合理默认值，而不是不开启"：

- 新增全局配置块 `AppConfig.generative_capability`
  （`src/mini_agent/config/models.py::GenerativeCapabilityConfig`），
  `skill_tier_max_turns` 默认 **40**（与 `capability.yaml -> explorer.
  max_turns` 常见配置量级一致），`skill_upgrade_enabled` 默认 **True**
  （此前 3.3e 节 `enable_skill_upgrade` 参数的既有默认值 `False` 已随本次
  修复一并调整），`skill_upgrade_success_threshold` 默认 3，
  `skill_upgrade_retry_cooldown_seconds` 默认 3600（延续阶段二的既有值，
  未改变）。可在 `agent_config.json` 里写
  `"generative_capability": {"skill_tier_max_turns": 60, ...}` 覆盖，
  走项目通用的 `NESTED_CONFIG_BLOCKS` 加载机制
  （`config/param_registry.py`），不需要额外接线代码。
- `tools/capability_call.py` 构造 `CapabilityEngine` 前，先读取该 skill
  自己的 `capability.yaml -> skill_tier`（`max_turns`/`enable_upgrade`/
  `upgrade_success_threshold`，可选，skill 级覆盖优先于全局默认值）合并
  出最终生效值；`max_turns <= 0` 视为该 skill 显式关闭 SKILL 档（等价于
  修复前的行为，保留这条退出通道），否则默认构造
  `build_playbook_repo(skill_dir)`/`build_skill_runner(project_root,
  max_turns=..., mini_agent_config=cfg)` 并注入 `CapabilityEngine`。
- 影响范围：仅改变"SKILL 档此前完全不可达"这一点，`_try_skill()`/
  `distill()` 内部逻辑本身、`registry.json`/playbook 各自独立计数的既有
  边界（见上面"当前实现边界"一节）均未改动；`playbook_repo`/`skill_runner`
  为 `None` 时 `_try_skill()` 的静默跳过行为完全保留，只是触发这一分支的
  唯一途径从"默认状态"收窄为"用户显式配置 `max_turns<=0`"。
- 回归验证：`tests/test_generative_capability_*.py` +
  `tests/test_hybrid_exec_*.py` + `tests/test_config_*` 相关测试文件全部
  通过（既有 2-3 个失败用例核实为环境依赖缺失/文档已知的预置失败，与本次
  改动文件无引用关系，改动前后表现一致）。

**测试指南**：真人对话场景下如何验证本节描述的修复，见
[test_cases/browser-site-scraper-three-tier-testing-guide.md](../test_cases/browser-site-scraper-three-tier-testing-guide.md)，
步骤2（验证 SKILL 档确实被调用）、步骤5（验证 `skill_tier_max_turns` 默认值
生效且可配置/可显式关闭）是本节修复的直接对应测试。

## 6. 阶段五：机制全貌记录 + "不合并 HybridExecutor"决策 + 测试断言漂移订正

### 6.1 决策：不再推进"整体委托给 `HybridExecutor.run()`"

**结论（用户已确认）**：`next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md`
3.4 节原设计的"`capability_engine.py` 的 `resolve/execute` 整体委托给
`HybridExecutor.run()`"，**不再推进**。原设计文档 3.4 节内容保留作为历史
记录，但状态改为"已评估，决定不实施"，不再是"待评估的下一步"。

**理由**：`generative-capability` 这套机制后续预期会持续出现"只对这类
领域场景本身有意义"的定制修改（例如三条蒸馏路径各自的优先级/合理性检查
细节、`playbook.md` 的整理规则、探索子agent的领域原语接入方式、`skill`
档升级判据等）——这些改动天然是"贴着 generative-capability 这一层语义"
去做的，如果先把执行骨架整体迁移到 `HybridExecutor` 之上，每一次这类定制
修改都要多绕一层"这个改动该加在 `CapabilityEngine` 的领域适配层，还是
`HybridExecutor` 的通用执行层"的判断成本，且 `HybridExecutor` 同时还要
服务于其它非 generative-capability 场景的通用需求，两边的演进节奏和改动
诉求并不同步。维持两套独立实现，`generative-capability` 一侧可以按自己
的节奏自由定制，代价是两套状态机长期并存、`available_tiers` 只能停留在
信息性字段、`degraded` 判定继续只看 script 一档——这是权衡后接受的已知
限制，不是待办事项。

**后续原则**：
- `CapabilityEngine`/`skill_tier.py`/`distiller.py` 这条链路的改动，
  按 generative-capability 自身的语义直接改，不再需要为"是否要向
  `HybridExecutor` 对齐字段/接口"做额外设计约束。
- `hybrid_exec.HybridExecutor` 自己（`_try_skill`/`PlaybookRepository`/
  `PlaybookRunner` 等）继续作为独立、通用的"低成本手段优先"执行框架
  演进，服务于它自己的既有调用方（`workflow_integration.py` 等），不
  再需要为对齐 `CapabilityEngine` 的字段语义做妥协。
- 两套实现之间目前唯一共享的是设计思路（script→skill→explore 分级）和
  `PlaybookRepository`/`PlaybookRunner` 这两个具体模块的**代码复用**——
  这一层复用继续保留（不重复造轮子），但仅限于"可独立复用的基础设施"，
  不代表两套状态机会在语义上统一。

### 6.2 当前完整机制记录（据代码梳理，供后续改动前快速回顾）

本节把当前 `CapabilityEngine` 实际运行的完整机制记录一遍（不是设计稿，
是对 `capability_engine.py`/`distiller.py`/`explorer_runtime.py`/
`skill_tier.py`/`tools/capability_call.py` 现状的如实梳理），后续每次
改动应该同步更新本节，而不是任由文档与代码继续漂移。

**入口与调度**：
- `tools/capability_call.py::capability_call(skill_name, request)` 每次
  调用现构造一个 `CapabilityEngine` 实例（成本低，不做跨调用缓存），
  按 `AppConfig.generative_capability`（可被 `capability.yaml -> skill_tier`
  单 skill 覆盖）默认注入 `playbook_repo`/`skill_runner`。
- `resolve(request)`：两级检索，只判断"该由哪个/哪些 member 处理"，不
  涉及执行——第一级确定性匹配（`domain_pattern`/`keyword`，零成本），
  第二级 LLM 裁决（`llm_resolver`，规则匹配不到候选时才触发）。
- `call(request, allow_tiers=None)` 是三档调度中枢：
  1. `hit` → 依次对候选 member 跑 script 档（`execute()`）；
  2. 全部失败 → `_try_skill(last_failed_member_id, request)`（没有
     active playbook 时静默跳过，不计入任何成败统计）；
  3. script+skill 都不行 → 判断该 member 是否已 `degraded`，是则
     `reexplore_member_id=` 该 member（复用同一 member_id 重新探索），
     否则视为全新场景 → `explore()`；
  4. `no_match` → 直接 `explore()`。
  - `allow_tiers` 支持调用方主动跳过某档（跳过的档位不产生执行尝试、
    不计入成败统计），但目前完全依赖调用方显式传参，引擎自己不会根据
    "已知 degraded"这类内部状态自动决定跳过 script 档。

**explore（第三档，全新探索）**：
- `explorer_runtime.py::build_subagent_explorer()` 构造真正的 `SubAgent`
  驱动探索（复用主 Agent 执行框架，不是另起一套探索循环），默认拥有完整
  通用工具集，领域原语是"追加"而非"收窄"——来源经 `capability.yaml ->
  explorer.depends_skills` 自动派生（复用
  `real_tools.py::load_skill_local_tool_implementations()`），
  `tool_allowlist.json` 降级为可选的交集收窄声明。
- 探索子agent收敛出解法后调 `finish()`：`script_source` 结构性必填，
  要么给源码要么显式 `"SKIP"`，不允许静默留空。

**distill（蒸馏，三条路径，优先级从高到低）**：
1. `script_source`——探索子agent自己判断能参数化，直接交源码。
2. `llm_synthesized`——`script_source` 为空/`SKIP` 且注入了
   `llm_helper` 时，LLM 读完整 trace 总结出参数化脚本。
3. `trace_replay`（最后兜底）——前两条都不可用时，把 trace 里每步
   `(tool, input)` 参数化后固化重放；已知局限是分不清死胡同和关键路径，
   trace 混有失败探测调用时重放必炸。
   - 生命周期上被标记"弱信任"：`distill_source_kind == "trace_replay"`
     的 member 使用更保守的 `probation_success_threshold_override`
     （默认取领域默认门槛的两倍），需要验证更多次成功才能转正。
- 三条路径共同的假数据防护：规则预检（扰动 `input` 重跑一次，输出雷同
  则可疑）+ LLM 复核两级校验，未通过直接丢弃、不落盘。
- 三条脚本路径全部失败但探索本身成功 → 不判"无沉淀"，自动整理成
  `playbook.md` 落盘（`distill_source_kind: "playbook"`，
  `registry.json` 标记 `execution_tier: "skill_only"`，member 目录下
  没有 `script.py`），交给下次命中该 member 时的 skill 档使用。

**skill 档（第二档，playbook 驱动轻量 Agent）**：
- `_try_skill()`：无 `playbook_repo`/`skill_runner` 或无 active
  playbook → 静默跳过；有 → 调 `skill_runner(request, playbook_content)`
  （内部是 `PlaybookRunner` 拉起一次轻量 Agent，工具集/回合预算受限，
  区别于全量探索）。返回内容带 `SKILL_RETIRE_ERROR_PREFIX` 前缀
  （对应 `PlaybookInvalidError`，Agent 明确判定"这份 playbook 走不通"）
  → 直接 `retire`，不走"多次失败才退役"统计；其它失败 → `record_failure`
  走正常 `consecutive_fail` 累计；成功且通过 schema 校验 →
  `record_success`，`resolve_reason="skill_playbook"`。

**升级方向（skill 证明可靠 → 固化为 script）**：
- `_try_skill()` 每次成功后调 `_maybe_upgrade_skill_to_script()`：未
  开启升级开关（全局默认已开）、未注入 `llm_helper`、已有 `script.py`、
  或 playbook 累计成功次数未达门槛（默认 3）→ 静默跳过。
- 达标 → `attempt_skill_upgrade()`：playbook 文本 + 一次真实执行样例
  交给 LLM 合成脚本，走与 `distill()` 完全一致的自测/校验/落盘流程，
  `distill_source_kind` 记为 `"skill_upgraded"`。
- 升级失败记录 `last_upgrade_attempt_at`，配合
  `skill_upgrade_retry_cooldown_seconds`（默认 3600s）节流，避免每次
  成功都重新触发一次 LLM 调用。
- **[本次订正]** 升级落盘时 `_atomic_persist(..., is_reexplore=True,
  llm_helper=llm_helper)` 里 `is_reexplore` 被硬编码为 `True`，这会
  额外触发一次"LLM 归纳检索匹配规则"的调用（`_infer_match_rule` 那条
  仅在"重新探索既有 member"且注入了 `llm_helper` 时才生效的路径）。
  也就是说**升级一次实际消耗 2 次 LLM 调用**（脚本合成 + 匹配规则
  归纳），而不是只有脚本合成这 1 次。这一点此前只在 3.3e 节"已知限制"
  里提到"`_infer_match_rule` 会基于触发升级那一次的请求重新生成匹配
  规则"，但没有点出这会导致 `llm_helper` 调用次数变化，本次予以明确。

**至此，script ⇄ skill ⇄ explore 三档之间是双向流转**：script 失败
降级到 skill，skill 不行降级到 explore；explore 成功优先升格为
script，退而求其次落为 playbook；playbook 用得好又能升格回 script。

**全局默认值与接线**：`AppConfig.generative_capability`
（`skill_tier_max_turns=40`、`skill_upgrade_enabled=True`、
`skill_upgrade_success_threshold=3`、
`skill_upgrade_retry_cooldown_seconds=3600`）保证不配置也默认启用，
`tools/capability_call.py` 每次调用据此注入依赖，`capability.yaml ->
skill_tier` 可单 skill 覆盖，`max_turns<=0` 是显式关闭通道。

**统一视角的现状边界**：`registry.json` 每个 member 有 `available_tiers`
字段（`_compute_available_tiers`/`_refresh_available_tiers`），但只是
信息性字段，不参与 `_apply_lifecycle`/`_try_skill`/`explore` 任何触发
判断；`degraded` 判定仍只看 script 一档的成败计数，script 和 skill 是
两套独立计数，互不感知——这一点在 6.1 节的决策下将长期保留，不再是
"待合并解决"的临时状态。

### 6.3 测试断言漂移订正（据实际运行发现，已修复）

复跑相关测试文件发现两处文档记录与代码实际行为不一致，均属于**代码演进
后测试断言未同步更新**，不是本次新引入的问题。**两处均已在本节修复**：

1. **`tests/test_generative_capability_skill_upgrade.py::
   test_upgrade_succeeds_and_persists_script`**：此前实际失败
   （`self.assertEqual(llm_helper.calls, 1)` 断言不成立，实际调用 2 次，
   原因见上方 6.2 节"升级方向"最后一条订正——升级会消耗 2 次 LLM 调用：
   脚本合成 + `_atomic_persist(..., is_reexplore=True)` 触发的匹配规则
   归纳）。**已修复**：断言改为 `self.assertEqual(llm_helper.calls, 2)`，
   并加注释说明两次调用分别对应哪个环节。

2. **`tests/test_generative_capability_engine.py::
   TestCapabilityEngineResolveExecute::test_full_explore_distill_reuse_cycle`**：
   此前多处文档（3.3c/3.3d/3.3e 节验证记录）反复引用"1 个与本次改动
   无关的既有失败"，暗示失败原因始终不变。核实后发现**真正原因**并非
   之前推测的"3.3d 节 playbook 兜底路径生效"，而是：这条测试复用的
   `.claude/skills/browser-site-scraper/capability.yaml` 本身显式声明了
   `distill: {trust_trace_data: true}`（不是引擎默认值 `false`）——测试
   注释里"trust_trace_data 默认 false"这句话描述的是**引擎默认值**，但
   忽略了测试实际加载的是**已经显式覆盖过这个开关的真实 skill 配置**，
   两者不是一回事。`trust_trace_data: true` 使得重放最后一步
   `browser_navigate`（无 `data`）时，蒸馏器会把探索阶段已通过
   `intent_schema` 校验的 `trace.data` 作为兜底常量嵌入脚本
   （`distill_used_trace_data_fallback: true`），蒸馏经 `trace_replay`
   路径直接成功，而不是原断言预期的 `not_implemented`。**已修复**：
   - 第一次调用断言改为 `status == "success"`、
     `resolve_reason == "explored"`、`member_id == "some-new-ci-site"`。
   - 新增一次"同一 request 再调一次"的验证，断言直接命中已蒸馏的
     `script.py`（`resolve_reason == "domain_pattern_match"`），把"探索
     一次、蒸馏落盘、后续复用"这条闭环的复用点真正验证到，而不只是验证
     蒸馏本身成功。
   - 原测试后半段"新域名走完整探索"的场景改用第二个域名
     `another-new-ci-site.example`（避免与第一段共用同一个 member_id
     产生耦合），保留原有覆盖的"探索拿到真实 data → 蒸馏 → 立即
     可用"场景，断言不变（`status == "success"`、
     `resolve_reason == "explored"`）。

**验证**：修复后 `tests/test_generative_capability_engine.py` +
`tests/test_generative_capability_skill_tier.py` +
`tests/test_generative_capability_skill_upgrade.py` +
`tests/test_generative_capability_available_tiers.py` +
`tests/test_distiller_playbook_fallback.py` +
`tests/test_distiller_script_source.py` +
`tests/test_hybrid_exec*.py`（`test_hybrid_exec_summary_route.py` 因
本地环境缺 `fastapi` 依赖被跳过，与本次改动无关）共 121 用例全部通过，
无失败、无跳过。今后不应再有"已知无关失败"这个分类挂在
generative-capability 相关测试上——上述两条曾经被这样归类的用例已确认
并非环境问题，均是断言本身过期，修复后应当保持全绿。
