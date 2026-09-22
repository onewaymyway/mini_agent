# world_simulator 资源转移一致性机制根本性改进（第九轮批次一）

> **状态**：✅ 已完成并落地。

## 1. 问题背景

用户在实际使用中报告了两条典型的资源转移"不一致"提示：

```text
{"from": "resources.invested", "to": "resources.cash", "delta_from": 1000, "delta_to": -12000}
{"from": "resources.cash", "to": "health.physical_fitness", "delta_from": -12000, "delta_to": -1}
```

排查后确认这是两类不同层面的问题，不能用同一个思路修：

- **`cash → physical_fitness`**：类型用错了，量纲根本不可比。
  `resource_relations` 的 `transfer` 核对逻辑假设"两个字段变化量
  之和 ≈0"，这个假设只在 `from`/`to` 是同一种量纲时成立（现金和
  库存账面价值是同一种东西换了个记账位置）。"花掉的现金"和"体能
  指数的涨幅"根本不是同一种量纲，套用这个假设必然报错——这不是
  数值算错，是这条关系从声明阶段起就不该用 `transfer` 描述。
- **`invested → cash`**：本身同量纲，但引擎每步只对*整份* `vars`
  快照做差反推，隐含假设"这一步 `cash` 的净变化只由这一条转移产生"。
  如果这一步 `cash` 同时被别的变动影响（比如收回投资的同时又花掉
  了别的钱），净差分就不等于这条关系单独的搬运量，这是"整体状态
  差分"这种核对方式的固有局限——没有逐笔流水就没法精确拆分归因。

参考 `万能模拟器到底如何构建` 架构文档第二十七节："现实世界应该以
Event 为主要变化单位"，这类问题的根本解法方向是把状态变化改成显式
的 Event/流水驱动，而不是隐式地对前后完整状态做差再反推。但把整个
推进循环（`engine/advance.py`）改造成完整的 Event Sourcing 属于另一
个量级的工程（牵动所有模板/所有测试），不适合作为一次性修复，也和
项目一贯"新增开关 + 旧路径完全保留"的风险控制原则冲突。

## 2. 方案：两个独立的增量改进

### 2.1 扩展关系类型系统：新增 `conversion`

`resource_relations` 新增 `"conversion"` 类型，专门表达"有代价的
跨量纲转化"（花钱健身、花时间学习换技能……），和 `"transfer"`
（只用于同量纲搬运）明确区分：

- `transfer`：保留原有零和差分核对逻辑不变。
- `conversion`：**永不参与任何数值核对**，只做结构化记录（因果
  关系标注），供时间线/展示层使用。

三个模板 `SKILL.md`（life-sim/negotiation/group-evolution）同步
更新 `resource_relations` 的说明，给出 `transfer` vs `conversion`
的选择规则和一正一反两个例子，从声明源头减少类型选错的概率。

### 2.2 显式流水优先，缩小差分法的误报面

不做完整的 Event Sourcing，而是给 `transfer` 关系一个务实的折中：

- `advance_step` 阶段新增可选输出字段 `resource_transfers`：skill
  如果这一步确实触发了某条 `transfer` 关系，可以在这里如实报告
  `{"relation": "{from}->{to}", "amount": ...}`。
- 一致性检查里，**只要这条关系在这一步被显式报告过，就直接信任、
  跳过差分核对，不产生任何不一致项**——不会反过来拿净差分去验证
  这份自报数据是否"准确"。这是刻意的取舍：净差分本来就可能被没有
  声明成 `resource_relations` 的其它变动污染，继续拿它去做二次校验
  只是把"差分法不可靠"这个问题换个地方重新引入。这一点和项目里
  `major_decision`/`key_drivers` 等其它"自报"字段的既有取舍一致。
- 没有显式流水时，仍走原有的整体快照差分核对，但新增"共享字段
  自动降级"规则：如果同一个 `from`/`to` 字段被两条以上
  `transfer`/`conversion` 关系引用，说明这一步该字段很可能不止
  一个来源在变，差分法本身已知不可靠，直接跳过、不产生不一致项。

## 3. 具体改动

- `world_simulator/state_model.py`：`resource_relations`/
  `relation_violations`/`resource_transfers` 三处文档更新；`SimState`
  新增 `resource_transfers: List[Dict[str, Any]]` 字段。
- `world_simulator/engine/resource_guard.py`：
  - `_normalize_resource_relations()` 支持 `"conversion"` 类型。
  - 新增 `_normalize_resource_transfers()`、`_count_field_references()`。
  - 重写 `_check_resource_relations()`：跳过 `conversion`；`transfer`
    的核对改为"显式流水优先（信任、不反查）、共享字段自动降级、
    默认差分兜底"三级逻辑。
- `world_simulator/engine/advance.py`：读取 `data.get("resource_
  transfers")`，传入一致性检查函数，落盘到 `next_state`。
- `skills/life-sim-template/SKILL.md`、`negotiation-template/SKILL.md`、
  `group-evolution-template/SKILL.md`：更新 `resource_relations`
  （`transfer`/`conversion` 选择规则 + 正反例）与 `advance_step`
  输出说明（新增 `resource_transfers` 字段）。
- `app.py`：`_relation_violations_html()` 更新提示措辞，明确"这里
  出现的 `transfer` 不一致项都来自差分估算，仅供参考"。
- 测试：
  - `tests/test_resource_production_relation.py`、
    `tests/test_spec_and_engine.py`：更新原有 `transfer` violation
    的精确断言，补上新增的 `"checked_by": "diff"` key。
  - 新增 `tests/test_resource_relation_consistency_fix.py`，覆盖
    `conversion` 类型不产生误报、显式流水直接信任（不反查）、共享
    字段自动降级、未共享字段维持原差分核对等场景。

全量测试套件（604 个）全部通过。

## 4. 向后兼容性

- `resource_relations` 未声明、`resource_transfers` 未提供时，行为
  与改进前完全一致。
- 旧存档里的 `relation_violations` 没有 `checked_by` 这个 key，
  展示层按缺失时默认当 `"diff"` 处理。
- 不引入任何强制字段、不拒绝推进、不修改任何数值——延续项目一贯
  "只提示、不接管决策"的克制原则。

## 5. 与完整 Event-Driven 架构的关系

本次改动是在不触碰推进循环整体结构的前提下做的局部改进，不是
架构文档第二十七节设想的完整 Event Stream 实现。如果未来要往
"每一步的每个字段变化都有显式来源"这个方向继续推进，需要的是把
`advance()` 改造成先产出 Event 列表、再由 Event 驱动 `vars` 更新，
这是比本次改动大得多的工程，留给后续独立评估。
