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

### 3.2 校验算法（新增 `engine/resource_guard.py::_check_field_ledger()`）

```
输入：current.vars（本步开始前）、next_vars（本步最终落盘值，已
      完成资源下限校验夹值）、field_ledger 原始数据、
      manifest.settings.resource_fields

1. 归一化：只保留 field/kind/amount 三者齐全、amount 可解析为非负数
   的记录；kind 只认 increase/decrease，其它值归一化为 decrease。
   完全无法解析（缺 field 或 amount 非数字）的记录直接丢弃，不计入
   展示也不计入校验（noise，不是有效审计记录）。

2. 按 field 分组，组内保持原始顺序（假定是这一步内发生的时间顺序）。

3. 对每个分组：
   a. 首笔 value_before 是否等于 current.vars 里该字段的实际值（用
      现有 `_get_nested()` 读取），容差 1e-6；不等 → start_mismatch。
   b. 逐笔检查 value_after 是否等于 value_before ± amount（容差
      1e-6）；不等 → arithmetic_mismatch。
   c. 逐笔检查本笔 value_before 是否等于上一笔 value_after（第一笔
      跳过，已在 3a 检查过）；不等 → chain_broken。
   d. 末笔 value_after 是否等于 next_vars 里该字段的最终值；不等 →
      end_mismatch_with_actual。

4. 对 manifest.settings.resource_fields 里声明过的每个字段：如果
   current.vars 与 next_vars 里的值不同（超出容差），但这个字段完全
   没有出现在归一化后的 field_ledger 分组里 → unaccounted_resource_
   change。

5. 非数值字段（value_before/value_after 无法解析为 int/float）：
   跳过算术校验，不因为"不是数字"本身报违规——field_ledger 设计上
   就是给数值型字段用的，非数值字段不适用这套机制（不强行报错，
   也不假装能校验）。

返回：(归一化后的 field_ledger 列表, ledger_violations 列表)
```

**不做的事**：
- 校验失败**不阻断这一步推进、不自动纠正 `vars` 数值**——不一致
  代表"账目和实际结果有出入"，具体是 LLM 把 `vars` 算错了还是账记
  错了，机器分不清，只能如实标记，交给用户判断。这一点和"资源
  下限校验"（会主动纠正）不同，是延续项目里 `relation_violations`
  一贯"只标记、不擅自纠正"的取舍，需要在展示层措辞上讲清楚区别
  （不要让用户误以为标红的地方系统已经处理了）。
- 不做自动重试/重新生成这一步直到账目对上——不引入这种成本高、
  且和现有单次调用架构不匹配的循环。
- 不强制非资源指标字段也必须记账——只有 `resource_fields` 声明过
  的字段变化了才要求必须有账（`unaccounted_resource_change`），
  其余数值指标记不记账随意，记了才校验准不准。

### 3.3 由谁产出、什么时候产出

- `workflows/advance_step.yaml`（不拆分模式）和 `workflows/world_
  evolve.yaml`（拆分调用模式，"世界状态类字段"归它负责）的输出
  说明里，新增一段和 `key_drivers`/`causal_links` 同样风格的提示：
  - 字段结构说明（`field`/`kind`/`amount`/`value_before`/
    `value_after`/`reason`）；
  - 明确要求：`value_before`/`value_after` 必须和这一步实际的
    `next_vars` 结果对得上，虚报或算错会被系统标记出来给用户看到，
    请认真核算，不要为了"看起来完整"而编造或凑数；
  - `resource_fields` 里声明过的字段如果这一步发生了变化，应尽量
    给出对应的记账记录（不是强制，但引擎会标记未记账的资源变化，
    影响这份模拟记录的可信度）；
  - 平淡、没有明确原因的变化不需要记账，不要为了填表而硬造理由。
- `world_simulator/engine/advance.py`：解析 `data.get("field_ledger")`，
  调用 `_check_field_ledger()`，把归一化结果和违规列表落到
  `next_state.field_ledger` / `next_state.ledger_violations`。
- `state0`（初始状态）一般不需要记账（还没有"发生的变化"），如需
  记"初始资金"等，可以复用同一字段，但不强制。

### 3.4 展示：模拟界面 + 导出网页都要加

时间线每一步新增一个"记账"小节，渲染成表格：

| 字段 | 类型 | 变化前 → 变化后 | 原因 |
|---|---|---|---|
| resources.cash | 💰 收入 +500 | 1000 → 1500 | 签下新客户，预付款到账 |
| tech.ai_capability | 📈 提升 +5 | 60 → 65 | 完成一轮模型调优 |

- 增加用绿色、减少用红色（复用 `--ws-success`/`--ws-danger`），字段
  是否属于 `resource_fields` 决定图标/措辞（💰 vs 📈），不新增第三套
  配色体系。
- 如果这一步有 `ledger_violations`，在表格下方用醒目的红色提示块
  列出每一条不一致（字段/问题类型/期望值/实际值），措辞明确写清楚
  "系统未自动纠正，仅作透明提示"，避免和"资源下限纠正"（会自动
  纠正）混淆。
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

## 4. 需要改动的文件清单

| 文件 | 改动 |
|---|---|
| `world_simulator/state_model.py` | 新增 `SimState.field_ledger`、`SimState.ledger_violations` 字段 + 序列化/反序列化 + 详细 docstring |
| `world_simulator/engine/resource_guard.py` | 新增 `_check_field_ledger()`，复用现有 `_get_nested()` |
| `world_simulator/engine/advance.py` | 解析 `data.get("field_ledger")`，调用新校验函数，落到 `next_state` |
| `workflows/advance_step.yaml` | 新增输出格式说明段落 |
| `workflows/world_evolve.yaml` | 同上（拆分调用模式） |
| `app.py` | 新增 `_field_ledger_html()` / `_ledger_violations_html()`，接入 `_render_timeline()`；`THEME_CSS` 新增记账表格 + 不一致提示样式 |
| `world_simulator/html_export.py` | `_render_state_card()` 新增对应渲染；`_PAGE_CSS` 新增同款样式 |
| `tests/test_spec_and_engine.py` | 新增：正常记账解析、连续账校验通过；`start_mismatch`/`chain_broken`/`arithmetic_mismatch`/`end_mismatch_with_actual`/`unaccounted_resource_change` 各自能正确触发；非数值字段不误报 |
| `tests/test_html_export.py` | 新增：导出网页正确展示记账表格与不一致提示，无记账/无不一致时对应小节不出现 |
| `PROJECT.md` | 变更日志 |

## 5. 待确认的设计取舍

1. 校验失败**不阻断、不自动纠正，只透明标记**——是否符合预期，
   还是需要更严格的"账不对就重试这一步"（成本会高很多）？
2. 只有 `resource_fields` 声明过的字段"变化了必须有账"，其它数值
   指标记不记账随意（记了才校验）——是否符合预期？
3. 数值容差取 `1e-6`（浮点精度误差范围）判定"相等"——是否需要
   放宽或收紧？
4. `field_ledger` 只处理数值型字段，非数值型（字符串/列表类）的
   `vars` 变更不适用这套机制——是否需要另外覆盖"状态从 A 变成 B"
   这种非数值变更的记账（形式会不同，没有加减，只有"从…变成…"）？

以上四点确认后再开始改代码。
