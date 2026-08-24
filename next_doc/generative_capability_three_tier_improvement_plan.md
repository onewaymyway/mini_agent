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
| 1 | 整体合并到 `HybridExecutor.run()` | `registry.json`（script 状态机）与 `playbooks/<id>/meta.json`（playbook 成败统计）是两套独立计数，`degraded` 判定只看 script 一档 | 最大——涉及 `ScriptRepository` 字段映射，是 raw_result_and_hybrid_merge_plan.md 3.4 节已规划但未实施的项 |
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

改进方向 #1（整体合并到 `HybridExecutor.run()`）与 #5（第二个
generative-capability skill 接入验证泛化性）仍按
`generative_capability_raw_result_and_hybrid_merge_plan.md` 3.4 节的
既有判断留待后续评估，不在本文档展开设计或实施。

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
