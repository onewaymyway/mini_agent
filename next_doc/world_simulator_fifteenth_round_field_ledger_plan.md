# world_simulator 第十五轮：字段变化台账（`field_ledger`）——
审计级记账机制

## 1. 背景 / 用户需求

用户要求：模拟推进的时候（导出结果时也要有），时间线的每一个阶段都能
输出一份"资源变化表格"，类似记账账本：每条记录要有类型（支出/收入）、
具体数值、发生的原因/目的；每个阶段都要能展示出这一阶段对应的记账
变化表格。

在方案讨论过程中，用户进一步明确了两点关键要求，把范围和严格度都
提高了：

1. **不只是普通资源**：类似"技术指标"这类非货币量，也应该有同样
   结构的记录——"因为做了什么，导致指标增加或减少了多少"。
2. **必须能对上账，不能有矛盾**：这些记账信息要和实际发生的总资源
   变化能够核对一致，至少不能自相矛盾；而且不能只记"变化量"，还要
   记"变化后的值"；整体要完全符合审计要求。

## 2. 现状：已有哪些相关机制，为什么都不满足

项目里已有三类和"资源变化"相关的机制，逐一对照：

| 机制 | 字段 | 能表达什么 | 缺什么 |
|---|---|---|---|
| `resource_fields` 下限校验 | `SimState.resource_violations` | 某资源字段被算成负数时，系统纠正到 0 的记录 | 只在越界出错时才有，不是每步正常收支流水；没有"原因" |
| `resource_relations`（`transfer`/`conversion`/`production`） | `SimState.resource_transfers` + `relation_violations` | 两个字段之间"搬运/转化/产出"关系的一致性核对；`resource_transfers` 是可选的"这一步实际搬运了多少"流水，但只服务于 `transfer` 关系核对 | 必须先声明 `resource_relations` 才能报账；只有 `relation`+`amount` 两个键，没有方向/原因/变化后的值；展示层从未渲染过它 |
| `key_drivers` / `causal_links` | `SimState.key_drivers`、`causal_links` | 一句话驱动因素、`driver → effect` 因果链 | 纯叙事性描述，没有结构化数值、没有挂在具体字段上 |

`resource_transfers` 的设计文档（`state_model.py` 里 `resource_relations`
字段说明）明确写道"报了就直接信任、不会反过来拿差值验证"——这是刻意
的取舍，但和本轮用户"必须对得上、不能有矛盾"的要求正好相反，不能
直接沿用这套"只信任不校验"的思路，需要新的、更严格的机制。

## 3. 方案

### 3.1 数据结构

#### 3.1.1 `SimState.field_ledger`（字段变化台账）

命名从最初讨论的 `resource_ledger` 改为 **`field_ledger`**：字段名不
再限定于"资源"，语义是"`vars` 里任意数值型字段的变化流水"，覆盖
货币类资源，也覆盖技术指标、声望值、健康度等非货币数值指标。

```python
field_ledger: List[Dict[str, Any]] = field(default_factory=list)
```

每条记录：

```json
{
  "field": "resources.cash",
  "kind": "increase",
  "amount": 500,
  "value_before": 1000,
  "value_after": 1500,
  "reason": "签下新客户，预付款到账"
}
```

- `field`：必填，`vars` 路径（支持一层嵌套，写法同 `resource_fields`），
  不要求该字段提前声明为 `resource_fields`——任意数值型字段都可以记账。
- `kind`：必填，`"increase"`（增加）| `"decrease"`（减少）二选一。用
  "增加/减少"而不是"收入/支出"，是因为要覆盖非货币指标（"技能熟练度
  提升了 5 点"用"收入"表达会很别扭）；不认识的取值按现有"未知取值
  退化为默认档"的一贯规则处理（归一化为 `"decrease"`，不报错）。
- `amount`：必填，非负数，本笔变动的幅度，方向由 `kind` 决定。
- `value_before` / `value_after`：必填，这笔记录发生前/后该字段的值
  ——这是本轮新增的审计要素，让每一笔记录本身自带"能否对上账"的
  验证依据，而不是只有一个孤立的变化量。
- `reason`：必填，一句话说明具体做了什么/为什么发生这笔变化。
- 一步可以有 0～N 条记录（不要求每步都记账，和 `key_drivers` 一样
  "稀疏声明"，平淡的一步可以完全没有）。

渲染层按字段是否属于 `manifest.settings.resource_fields` 决定展示
措辞（资源类显示"💰 收入/支出"，非资源指标显示"📈 提升/📉 下降"），
但底层数据结构统一，不重复定义两套字段。

#### 3.1.2 `SimState.ledger_violations`（记账不一致提示）

```python
ledger_violations: List[Dict[str, Any]] = field(default_factory=list)
```

由引擎在 `advance()` 落盘前算出，形如：

```json
{"field": "resources.cash", "issue": "end_mismatch_with_actual", "expected": 1500, "actual": 1400}
```

`issue` 取值：
- `start_mismatch`：某字段第一笔记录的 `value_before` 与这一步开始
  前 `current.vars` 里的实际值对不上。
- `chain_broken`：同一字段内，后一笔的 `value_before` 与前一笔的
  `value_after` 对不上（账目不连续）。
- `arithmetic_mismatch`：某一笔自己的 `value_before ± amount` 算不出
  声明的 `value_after`。
- `end_mismatch_with_actual`：某字段最后一笔的 `value_after` 与这一步
  最终落盘的实际值（`next_vars` 里、经过资源下限校验夹值*之后*的
  最终生效值）对不上。
- `unaccounted_resource_change`：`resource_fields` 声明过的字段这一步
  实际发生了变化，但完全没有对应的 `field_ledger` 记录。

### 3.2 动态追踪字段集合（`manifest.settings.tracked_ledger_fields`）

用户明确要求"不局限于一开始在 `resource_fields` 声明的，只要变更过
就应该开始记录追踪"，所以"哪些字段必须有账"不能是创建模拟时就
固定死的静态集合，需要能随模拟推进动态扩展。

做法参照项目里已有的 `_auto_register_causal_lines()`（因果线自动
登记，不要求提前声明）：新增 `manifest.settings.tracked_ledger_
fields`（字符串数组，持久化在 `settings` 里），规则是——

- 初始集合 = `manifest.settings.resource_fields` 里声明的字段名；
- 每次 `advance()` 落盘之后，`field_ledger` 里实际出现过的字段
  （只要出现过一次记账记录，不管是不是资源）自动并入这个集合
  （`_auto_register_ledger_fields()`，和因果线自动登记的写法一致）；
- 一旦某个字段进了这个集合，**从下一步开始**它就属于"变了必须有
  账"的范围——本轮首次出现记账的这一步不追溯要求（不能要求 LLM
  在还没决定要追踪这个字段之前就已经记了账），但从此以后这个字段
  再变化、又没有对应记账记录，就会触发 `unaccounted_resource_
  change`。
- 这个集合只增不减（因果线登记同样如此），符合"审计范围只会越
  来越完整，不会中途缩小"的直觉；不提供手动移出的入口（如果确实
  需要，属于后续迭代范围，不在本轮做）。

`_check_field_ledger()` 的"未记账变化"检查（下面 3.3 节第 4 步）
改为扫描 `tracked_ledger_fields`（而不是只扫描 `resource_fields`）。

### 3.3 校验算法（新增 `engine/resource_guard.py::_check_field_ledger()`）

```
输入：current.vars（本步开始前）、next_vars（本步最终落盘值，已
      完成资源下限校验夹值）、field_ledger 原始数据、
      tracked_ledger_fields（见 3.2，含 resource_fields 并集）

1. 归一化：只保留 field/kind/amount 三者齐全、amount 可解析为非负数
   的记录；kind 只认 increase/decrease，其它值归一化为 decrease。
   完全无法解析（缺 field 或 amount 非数字）的记录直接丢弃，不计入
   展示也不计入校验（noise，不是有效审计记录）。

2. 按 field 分组，组内保持原始顺序（假定是这一步内发生的时间顺序）。

3. 对每个分组（容差见下方"容差"小节）：
   a. 首笔 value_before 是否等于 current.vars 里该字段的实际值（用
      现有 `_get_nested()` 读取）；不等 → start_mismatch。
   b. 逐笔检查 value_after 是否等于 value_before ± amount；不等 →
      arithmetic_mismatch。
   c. 逐笔检查本笔 value_before 是否等于上一笔 value_after（第一笔
      跳过，已在 3a 检查过）；不等 → chain_broken。
   d. 末笔 value_after 是否等于 next_vars 里该字段的最终值；不等 →
      end_mismatch_with_actual。

4. 对 tracked_ledger_fields 里的每个字段：如果 current.vars 与
   next_vars 里的值不同（超出容差），但这个字段完全没有出现在
   归一化后的 field_ledger 分组里 → unaccounted_resource_change。

5. 非数值字段（value_before/value_after 无法解析为 int/float）：
   跳过算术校验，不因为"不是数字"本身报违规——当前版本的
   `field_ledger` 只处理数值型字段（见 3.6 扩展性说明），非数值
   字段不适用这套机制（不强行报错，也不假装能校验）。

返回：(归一化后的 field_ledger 列表, ledger_violations 列表)
```

**容差**：用户要求"可以放宽，只要不产生大幅度的误差和冲突即可"，
改用相对容差而不是固定的绝对值——判定"相等"的阈值取
`max(1e-6, abs(期望值) * 0.01)`（1% 相对容差 + 一个极小的绝对值
下限防止两个数都接近 0 时除零/失真）。这样数值越大，允许的绝对
误差也越大，能容忍 LLM 正常的估算/取整误差，但percent级别以上
的明显算错、方向搞反这类"大幅度矛盾"仍然会被抓出来。

**校验失败后的处理：反馈修正循环（而不是只标记）**——见 3.4 节。

### 3.4 反馈修正循环（新增）

用户明确要求："失败应该提供反馈信息，让模拟器对当前步骤进行重新
修正，不是完全重新跑，而是根据提供的反馈信息进行修正。"

设计为**一次、有限范围的修正调用**，而不是无限重试或者整步重新
生成：

1. `advance()` 正常完成一次 LLM 调用、解析出 `next_vars`/
   `field_ledger` 之后，先跑一遍 `_check_field_ledger()`。
2. 如果 `ledger_violations` 非空，触发一次**修正调用**：新增
   `workflows/ledger_correction.yaml`（单步骤 workflow），prompt
   只包含："这一步原始的 `next_summary`/`narrative`（供上下文，不
   要求重写）+ 原始 `next_vars` 里涉及问题字段的部分 + 原始
   `field_ledger` + 逐条 `ledger_violations`（字段/问题类型/期望值/
   实际值）"，要求模型**只修正有问题的字段和对应的记账记录**，
   其余字段/叙事/选项保持不变，返回一个精简的补丁：
   `{"next_vars_patch": {...}, "field_ledger_patch": [...]}`
   （只包含被修正的字段，不要求把整个 `next_vars`/`field_ledger`
   重新吐一遍）。
3. 引擎把补丁合并回原始结果（`next_vars_patch` 按现有一层嵌套
   路径规则合并进 `next_vars`；`field_ledger_patch` 里出现的字段
   整体替换掉原来那个字段的记账分组，未出现在补丁里的字段保持
   原样），然后**重新跑一遍 `_check_field_ledger()`**。
4. **只修正一次**：不管修正后是否还有 `ledger_violations`，都不再
   发起第二次修正调用——修正调用本身也是有成本的 LLM 调用，无限
   重试在工程上不可控（也可能出现"改一个字段、连带另一个字段又
   不平"的震荡）。修正一次之后剩余的 `ledger_violations`（如果
   有）就按原方案"只标记、不阻断"处理，转化成时间线上的透明提示
   ——这是"尽力修正 + 兜底透明标记"的组合，不是纯标记也不是无限
   重试，符合用户"根据反馈信息修正"但不是"完全重新跑"的要求。
5. 修正调用失败（workflow 报错/解析失败等基础设施问题，不是"账
   还是不平"）时，直接跳过修正、保留修正前的结果和违规列表，不
   让一次可选的修正调用失败拖垮整个推进流程（沿用项目一贯的
   "旁路失败不影响主流程"取舍，类似 `_safe_suggest_knowledge()`
   这类 `_safe_*` 包装函数的处理方式）。
6. 修正调用只在“不拆分模式”和“拆分模式”下都统一触发（拆分模式
   下 `field_ledger`/`next_vars` 由 `world_evolve` 产出，修正调用
   同样只针对这部分，不涉及 `decision_generate` 负责的 `options`
   等字段）。

这一步实现成本比"纯标记不修正"高（新增一个 workflow + 一次条件
触发的额外 LLM 调用），但只在真正出现不一致时才会触发（大多数
步骤账目本来就是对的，不会有任何额外调用/延迟），符合"有问题才
补救、没问题不加成本"的取舍。

### 3.5 由谁产出、什么时候产出

- `workflows/advance_step.yaml`（不拆分模式）和 `workflows/world_
  evolve.yaml`（拆分调用模式，"世界状态类字段"归它负责）的输出
  说明里，新增一段和 `key_drivers`/`causal_links` 同样风格的提示：
  - 字段结构说明（`field`/`kind`/`amount`/`value_before`/
    `value_after`/`reason`）；
  - 明确要求：`value_before`/`value_after` 必须和这一步实际的
    `next_vars` 结果对得上，虚报或算错会被系统标记出来、并可能触发
    一次修正请求，请认真核算，不要为了"看起来完整"而编造或凑数；
  - 一个字段一旦被记过账（不限于 `resource_fields`），之后每一步
    它再变化就应该继续记账（见 3.2 动态追踪集合）——这是持续性
    要求，不是"记一次就完了"；
  - 平淡、没有明确原因的变化不需要记账，不要为了填表而硬造理由。
- `world_simulator/engine/advance.py`：解析 `data.get("field_ledger")`，
  调用 `_check_field_ledger()`；有违规时按 3.4 节触发一次修正调用
  并合并结果、再校验一次；把最终的归一化列表和（可能仍剩余的）
  违规列表落到 `next_state.field_ledger` / `next_state.ledger_
  violations`；调用 `_auto_register_ledger_fields()` 更新
  `manifest.settings.tracked_ledger_fields`。
- `state0`（初始状态）一般不需要记账（还没有"发生的变化"），如需
  记"初始资金"等，可以复用同一字段，但不强制。

### 3.6 展示：模拟界面 + 导出网页都要加

时间线每一步新增一个"记账"小节，渲染成表格：

| 字段 | 类型 | 变化前 → 变化后 | 原因 |
|---|---|---|---|
| resources.cash | 💰 收入 +500 | 1000 → 1500 | 签下新客户，预付款到账 |
| tech.ai_capability | 📈 提升 +5 | 60 → 65 | 完成一轮模型调优 |

- 增加用绿色、减少用红色（复用 `--ws-success`/`--ws-danger`），字段
  是否属于 `resource_fields` 决定图标/措辞（💰 vs 📈），不新增第三套
  配色体系。
- 如果修正调用之后仍然剩余 `ledger_violations`，在表格下方用醒目
  的红色提示块列出每一条不一致（字段/问题类型/期望值/实际值），
  措辞写清楚"系统已尝试自动修正一次，以下是修正后仍未解决的问题，
  不代表系统还会继续纠正"，避免用户误以为这是"未处理"或者"还会
  自动再改"。
- 两个地方都要渲染，且逻辑要一致：
  - `app.py::_render_timeline()` 每个 `ws-chapter` 节点新增
    `_field_ledger_html(state)` / `_ledger_violations_html(state)`
    两个 helper，直接展示（不放进 expander，这是用户明确要的核心
    信息）；
  - `world_simulator/html_export.py::_render_state_card()` 对应新增
    静态渲染（延续"导出模块独立实现一份格式化逻辑、不 import
    app.py"的既有约定）。
- 没有记账记录、也没有不一致提示的步骤，这两个小节都不出现（不留
  空标题），和 `key_drivers`/`causal_links` 现有处理方式一致。

### 3.7 扩展性：为未来支持非数值变更留好基础

用户要求"可以先这样，但是也要为未来支持其他形式做好扩展基础"。
本轮只实现 `kind ∈ {increase, decrease}` 的数值型记账，但设计上
预留了非数值扩展的空间，不需要推倒重来：

- 记录结构本身（`field`/`kind`/`value_before`/`value_after`/
  `reason`）已经是通用的"某字段从一个值变成另一个值、为什么变"，
  并不依赖"加减"这个语义——未来要支持"状态从 A 变成 B"这种非数值
  变更，只需要新增 `kind: "transition"`（`value_before`/
  `value_after` 是字符串而不是数字，没有 `amount`），是新增一种
  `kind` 分支，不用改字段结构。
- 校验算法第 5 步已经写好"非数值字段跳过算术校验、不报错"的
  兜底路径——未来给 `transition` 类型加校验逻辑（比如"从 A 变成 B
  之后，下一笔的 value_before 必须是 B"这类连续性检查）可以复用
  同一个分组/连续性框架，只是不做加减算术这一层。
- 渲染层（表格）按 `kind` 分支决定"显示 ±amount 数字"还是"显示
  `A → B`"，两种渲染共用同一张表格的行结构，不需要为将来的类型
  另起一张表。
- 动态追踪字段集合（3.2 节）和"未记账变化"检查天然不区分数值/
  非数值，未来加入 `transition` 类型后可以直接复用，不用改。

本轮实现范围明确限定为数值型 `increase`/`decrease`，`transition`
等非数值类型留到有实际需求时再单独立项实现，这里只保证现在的
设计不会挡路。

## 4. 需要改动的文件清单

| 文件 | 改动 |
|---|---|
| `world_simulator/state_model.py` | 新增 `SimState.field_ledger`、`SimState.ledger_violations` 字段 + 序列化/反序列化 + 详细 docstring；`SimManifest.settings` 新增 `tracked_ledger_fields` 的字段说明 |
| `world_simulator/engine/resource_guard.py` | 新增 `_check_field_ledger()`（含相对容差）、`_auto_register_ledger_fields()`，复用现有 `_get_nested()` |
| `world_simulator/engine/advance.py` | 解析 `data.get("field_ledger")`，调用校验函数；违规非空时加载 `ledger_correction` workflow 发起一次修正调用、合并补丁、重新校验一次；更新 `tracked_ledger_fields` |
| `workflows/advance_step.yaml` | 新增输出格式说明段落 |
| `workflows/world_evolve.yaml` | 同上（拆分调用模式） |
| `workflows/ledger_correction.yaml` | 新增：单步骤修正 workflow，输入原始违规+相关字段，输出补丁 |
| `app.py` | 新增 `_field_ledger_html()` / `_ledger_violations_html()`，接入 `_render_timeline()`；`THEME_CSS` 新增记账表格 + 不一致提示样式 |
| `world_simulator/html_export.py` | `_render_state_card()` 新增对应渲染；`_PAGE_CSS` 新增同款样式 |
| `tests/test_spec_and_engine.py` | 新增：正常记账解析、连续账校验通过；五种 `issue` 各自能正确触发；相对容差生效（小误差不报、大误差报）；动态追踪集合正确随 `field_ledger` 扩展；修正调用触发后违规减少/清零、合并补丁正确、修正调用本身失败时优雅降级 |
| `tests/test_html_export.py` | 新增：导出网页正确展示记账表格与（修正后仍剩余的）不一致提示，无记账/无不一致时对应小节不出现 |
| `next_doc/world_simulator_fifteenth_round_field_ledger_plan.md` | 本文档 |
| `PROJECT.md` | 变更日志 |

## 5. 设计取舍已确认

1. 校验失败先尝试**一次反馈修正调用**（3.4 节），修正后仍有问题
   才降级为"透明标记、不再重试"——不是纯标记，也不是无限重试。
2. **动态追踪任意记过账的字段**（3.2 节），不局限于创建时声明的
   `resource_fields`，追踪集合随模拟推进自动扩展、只增不减。
3. 数值容差改为**相对容差**：`max(1e-6, abs(期望值) * 1%)`，允许
   合理的估算误差，但拦住大幅度的矛盾。
4. 本轮只实现数值型 `increase`/`decrease`，但数据结构和校验框架
   已经为未来的非数值 `transition` 类型留好扩展点（3.7 节）。

确认无误，下一步开始改代码。