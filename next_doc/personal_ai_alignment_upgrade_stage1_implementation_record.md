# Personal AI 架构对齐升级 —— 阶段一实施记录

> 对应方案：`next_doc/personal_ai_alignment_upgrade_plan.md` §4.1 / §6 阶段一
> （Personal Model 证据分级扩展）。

## 1. 做了什么

### 1.1 抽取共享的"证据→归纳→矛盾不覆盖只降权"合并算法

新增 `src/mini_agent/evolution/evidence_pattern.py`，把原本写死在
`agent_value_profile_builder._apply_contradiction()` 里的合并算法抽成一个
不依赖具体调用方数据结构的纯函数 `merge_evidence_patterns()`：

- 输入输出都是 `{"pattern", "evidence_refs", "confidence", "first_observed",
  "last_reinforced", "contradicted_by"}` 形状的 dict。
- 同一 `pattern` 文本再次出现：证据取并集，若证据数量确实增加则按
  `+0.1 * 新增证据数`（封顶 1.0）强化置信度、刷新 `last_reinforced`；
  证据没有变化则不动置信度，也不刷新 `last_reinforced`。
- 不同 `pattern` 文本一律新增，不覆盖旧记录。
- 新模式的初始置信度为 `min(0.6, 0.15 * 证据数)`——仅凭初次归纳最多拿到
  0.6，更高置信度只能靠后续被反复印证累积。

`agent_value_profile_builder.py` 的 `_apply_contradiction()` 改为调用该
共享函数，只做 `AgentValuePattern` dataclass ↔ 通用 dict 的转换，对外
行为、返回类型、落盘格式完全不变（已用原有 10 个测试验证无回归）。

### 1.2 `profile.py`：`values` / `risk_preference` / `constraints` 三个新维度

在 `UserProfile.derived` 现有的 `tech_stack`/`habits` 之外，新增三个
命名空间，复用相同的存储结构范式并扩展两个字段：

```
{"text": "...", "last_confirmed_at": 1700000000.0,
 "source": "user_stated" | "ai_observation" | "ai_inference",
 "confidence": 0.0~1.0,
 "evidence_refs": ["...", ...]}
```

- `source` 三态取值语义：
  - `user_stated`：用户话里明确说的（当前仅 `constraints` 走这条）。
  - `ai_observation`：从行为直接观察到、无需推测（预留，阶段一未落地
    独立的 observation 类证据源，见 §2 已知限制）。
  - `ai_inference`：AI 基于观察推测出的模式（当前 `values`/
    `risk_preference` 走这条）。
- 新增 `_migrate_evidence_items()`：加载时把缺失/非法的 `source` 一律
  回退为最谨慎的 `ai_inference`，`confidence` 回退为 0.0，`evidence_refs`
  回退为空列表——不让脏数据/旧数据被误当成用户原话或直接观察。
- 新增 `USER_SIGNAL_KEYS = ("values", "risk_preference", "constraints")`，
  **不**加入 `PROFILE_GENERATED_KEYS`——`UserProfileManager.generate()`
  不会触碰、也不会清空这三个字段，与 `growth_advisor` 写
  `growth_focus_areas` 是同一条既有约定（已用测试
  `test_generate_does_not_touch_constraints` 验证）。

### 1.3 `constraints`：用户显式声明，不经过 LLM

新增 `UserProfileManager.add_constraint(text)` /
`remove_constraint(text)` / `list_constraints()`：

- `add_constraint`：按 `_normalize_text_key` 归一化匹配 upsert，
  `source` 固定 `user_stated`、`confidence` 固定 `1.0`——用户自己说的话
  不需要"置信度打折"。
- `remove_constraint`：按归一化文本匹配移除，返回是否命中。
- 这一维度按方案定义"必须是用户明确说过的"，因此**不**提供 LLM 归纳
  入口，只能由调用方（CLI/未来的 API）在用户明确表达约束时显式调用。

### 1.4 `values` / `risk_preference`：新写一个面向用户信号源的归纳器

新增 `src/mini_agent/evolution/user_signal_profile_builder.py`：

- 证据源：`evolution/suggestion_feedback_ledger.py` 已经在维护的
  "建议采纳/拒绝累积账本"（覆盖 `soft_goal_deriver`/
  `improvement_backlog_merge` 等多路建议来源），**不新增采集点**。
- 归纳流程：读取全部有过 accepted/rejected 记录的 category → 组装成
  `{category, accepted, rejected}` 列表 → 要求 LLM 分别归纳 `values`
  （决策取向）与 `risk_preference`（风险偏好）两组模式，每条模式必须
  引用 ≥3 个不同 category 作为证据（`MIN_EVIDENCE_COUNT = 3`，与
  `agent_value_profile_builder` 一致）→ 用 `merge_evidence_patterns()`
  与上一版结果合并 → 转成 §1.2 的存储结构，`source` 固定
  `ai_inference`，写入 `UserProfile.derived`。
- 未被本轮证据触及的旧条目保留原有 `last_confirmed_at`，只有本轮确实
  新建或被再次印证的条目才刷新时间戳——否则每次重新归纳都会无差别
  刷新所有条目，"多久没被再次印证"这个判断就失去意义。
- `llm_helper=None` 或证据不足（<3 个 category）时直接返回 `None`，
  不落盘、不清空已有数据，对齐 `agent_value_profile_builder` 的同一
  取舍。

### 1.5 CLI

新增 `src/mini_agent/cli/commands/user_signal_profile_cmd.py`，注册为
`/user_signal_profile` 命令（`repl.py`/`parser.py` 已更新帮助文本）：

```
/user_signal_profile                          展示 values/risk_preference/constraints
/user_signal_profile update                   触发一次 values/risk_preference 归纳（需要 LLM）
/user_signal_profile constraint add <text>    记录一条用户约束
/user_signal_profile constraint remove <text> 移除一条约束
/user_signal_profile constraint list          只列出 constraints
```

展示时 `ai_inference` 类记录带【推测】角标、`ai_observation` 带
【观察】角标、`user_stated` 带【用户明确表示】角标，对齐方案 §4.1
"三者中 `ai_inference` 类记录展示时必须带角标区分"的要求。

## 2. 已知限制（如实记录，未来阶段可能需要处理）

- **`risk_preference` 与 `values` 目前共用同一份账本证据**：
  `suggestion_feedback_ledger` 的 `category` 是调用方自定义的粗粒度
  字符串（dedupe_key 或 "source:kind"），账本本身没有风险等级字段，
  无法可靠区分"用户对高风险操作的确认/拒绝"与"普通建议的采纳/拒绝"。
  留待相关模块（`soft_goal_deriver`/`improvement_backlog_merge`）补上
  风险等级标注后再细化两个维度各自独立的证据源，本阶段不臆造区分。
- **`ai_observation` 这一 source 分类尚无独立落地的证据源**：阶段一
  只落地了 `constraints`（`user_stated`）与 `values`/`risk_preference`
  （`ai_inference`，LLM 语义归纳出的模式）。`ai_observation`（"从行为
  直接观察到、无需推测"）在 schema/迁移/展示逻辑里已经预留，但暂无
  写入方——例如"用户对高风险操作确认/拒绝的次数统计"这类原始计数
  本身可以算 `ai_observation`，留待后续阶段结合 §4.2 State 快照或
  Context Pack 的实际需要再决定是否要落地独立展示。
- **矛盾检测仍是"不同文本即不覆盖"，不做语义级别判断**：
  `merge_evidence_patterns()` 与原 `agent_value_profile_builder` 的
  既有行为一致，`contradicted_by` 字段预留但未自动填充；如果 LLM
  两轮归纳出的模式文本字面不同但语义矛盾（如"我倾向于稳妥" vs
  "我倾向于激进"），当前会被当成两条独立记录并存展示，不会互相标注
  矛盾。这是延续既有范式的已知行为，不是本阶段引入的新问题。

## 3. 改动文件清单

```
src/mini_agent/evolution/evidence_pattern.py                新增
src/mini_agent/evolution/agent_value_profile_builder.py     修改（_apply_contradiction 改为委托）
src/mini_agent/evolution/user_signal_profile_builder.py     新增
src/mini_agent/profile.py                                   修改（新增三个 derived 维度 + constraint 方法）
src/mini_agent/cli/commands/user_signal_profile_cmd.py      新增
src/mini_agent/cli/repl.py                                  修改（注册 /user_signal_profile）
src/mini_agent/cli/parser.py                                修改（帮助文本）
tests/test_evidence_pattern.py                               新增
tests/test_user_signal_profile_builder.py                    新增
tests/test_profile.py                                        修改（新增 TestUserStatedConstraints）
next_doc/personal_ai_alignment_upgrade_plan.md               修改（标注阶段一已完成）
next_doc/personal_ai_alignment_upgrade_stage1_implementation_record.md  新增（本文档）
```

## 4. 测试情况

```
tests/test_profile.py                       — 37 项（含新增 6 项 constraints 用例）
tests/test_evidence_pattern.py              — 5 项（新增）
tests/test_user_signal_profile_builder.py   — 8 项（新增）
tests/test_agent_value_profile_builder.py   — 10 项（既有，验证重构无回归）
```

本地执行 `python -m pytest tests/test_profile.py tests/test_evidence_pattern.py
tests/test_user_signal_profile_builder.py tests/test_agent_value_profile_builder.py`
全部通过（需要 `fastapi` 依赖以完整跑通 `test_profile.py` 里涉及
`generate()` 的用例；缺少该依赖时这几项会因无关的 import 错误失败，与
本次改动无关）。

## 5. 阶段二预告（尚未开始）

方案 §4.2 的 `perception/personal_state_snapshot.py` 依赖本阶段的
`constraints` 字段（快照要摘要"当前标记为 active 的约束"）——阶段一
已就绪，可以直接开始阶段二。
