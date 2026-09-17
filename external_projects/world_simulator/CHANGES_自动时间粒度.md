# world_simulator 改动说明：自动时间粒度

本次改动涉及的文件（覆盖到原项目对应路径即可）：

```
mini_agent-master/external_projects/world_simulator/
├── app.py                                   # 修改（在上次改动基础上）
├── workflows/advance_step.yaml               # 修改
├── workflows/generate_scenario.yaml          # 修改
├── skills/life-sim-template/SKILL.md         # 修改
├── skills/group-evolution-template/SKILL.md  # 修改
├── world_simulator/state_model.py            # 修改
├── world_simulator/spec_generator.py         # 修改
├── world_simulator/engine.py                 # 修改
└── tests/test_spec_and_engine.py             # 修改（新增用例）
```

## 需求

同一次模拟里，不同情境需要不同的时间粒度才能更真实地模拟现实（比如
日常按年推进，进入谈判/危机时应该收窄到按轮次/周推进），系统应该能
在需要的时候自动切换粒度，而不是一次模拟从头到尾只有一种固定粒度。

## 方案

### 1. 三种粒度模式（`settings.time_granularity_mode`）

- **`fixed`**（原有行为）：全实例固定用同一个粒度，行为完全不变。
- **`auto`**（新默认值，对应原来"留空"的隐式行为，现在显式化）：完全
  交给 AI 按情境判断，同一次模拟允许出现多种粒度。
- **`guided`**（新增）：用户给一段偏好/基准引导语（`time_granularity_guide`，
  比如"日常按季度推进，遇到谈判时切到按轮次"），AI 仍自主判断，但会
  参考这个方向——这是最常用的"有基准节奏 + 特殊情境临时切换"场景。

### 2. 数据模型：每一步单独记录实际用的粒度

`SimState` 新增：
- `time_granularity`：这一步实际使用的粒度（如"1 个月"），与累计的
  `time_label`（如"第 3 年"）是两个维度，一个答"现在几点"，一个答
  "刚才这一步跨了多久"。
- `granularity_changed`：与上一步是否不同，由 `engine.py` 自己比较
  算出（不信任 LLM 自报，避免"值没变但自称变了"的不一致）。
- `granularity_reason`：仅当发生变化时，LLM 给出的切换理由。

`state0`（初始状态）由生成草稿时给出这次模拟的**起始基准粒度**
（`ScenarioDraft.time_granularity`），之后每一步默认延续上一步的值。

### 3. Prompt / Skill 层：延续性约束，避免"乱跳"

`advance_step.yaml` 新增 `{current_time_granularity}` 输入（上一步
用的粒度），并要求 AI：
- 默认延续上一步的粒度，只有情境明显需要（比如从平稳进入密集博弈，
  或反过来）才切换；
- 切换时必须输出一两句话的 `granularity_reason`，没有切换则不需要
  这个字段——既约束 AI 别乱跳，也给用户提供透明度。

两个模板的 `SKILL.md` 同步更新了三种模式的判断规则、`generate_scenario`/
`advance_step` 两个阶段各自的输出字段说明（`time_granularity`/
`next_time_granularity`/`granularity_reason`）。

### 4. UI（`app.py`）

- 创建向导 & 详情页「模拟设置」里的"时间粒度"从一个文本框改成三选一
  的模式选择器（自动 / 引导 / 固定），选中对应模式才出现相应输入项。
- 时间线卡片（历史列表、当前状态、游戏化章节视图三处）新增"本步
  粒度：X"展示；粒度发生切换的节点会高亮显示，并附上切换理由，方便
  用户直观看到"这里节奏变了、为什么变"。

## 向后兼容

- 老实例没有 `time_granularity_mode` 字段时，默认按 `"auto"`
  处理（与原来"留空=自动"的语义一致）。
- 老实例的历史状态没有 `time_granularity`/`granularity_changed`
  字段时，`from_dict` 按默认值（空字符串 / `False`）处理，展示层不
  强行补一个假值，只是不显示这一行。
- `fixed` 模式下行为与改动前完全一致，一次模拟从头到尾只有一种粒度，
  不会出现"切换"。

## 测试

`tests/test_spec_and_engine.py` 新增用例覆盖：默认延续上一步粒度、
记录切换与理由、忽略"值未变但给了理由"的脏数据、`resolve_hints()`
三种模式的文案内容、`create`/`advance` 两种 stage 的占位符差异。
运行 `pytest tests/` 全部通过（64 个用例）。
