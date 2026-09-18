# world_simulator 阶段二十六：因果线的"未来因果树"

> **状态**：**已完成**。对应用户原话："现在因果线只有已进行的，应该
> 还能显示未来可能发展的不同可能线条的显示。而且现在还没推进的看不到
> 因果线，这也不符合预期。当创建模拟的时候，就应该创建出主要的核心的
> 因果线了，每个因果线都应该有未来的发展因果树。这些因果树，也可以在
> 模拟过程中不断地修正优化。"
> **前置依赖**：阶段二十二（4.13 节，多尺度因果线，`SimState.
> line_updates`/`SimManifest.settings.causal_lines`）、阶段二十五
> （4.17 节，因果线 UI，`app.py::_render_causal_lines_overview()`）。
> 均已完成，本阶段是在两者之上补一层"未来"，不改动它们已有的行为。
> **上游文档**：`next_doc/world_simulator_toward_universal_simulator_plan.md`
> 第 4.17 节交付记录的"已知限制"已经预告了这个后续需求（"如果需要
> 更严谨的'多个可能未来'……后续如果验证下来用户确实想要这个联动，
> 可以再单独接入"）。

## 1. 现状分析（阶段二十五遗留的缺口）

阶段二十二/二十五落地的因果线机制，看似完成了"因果线"这个概念，
但仔细检查后有三个和用户预期不符的地方：

1. **因果线是"事后归纳"出来的，不是"创建时规划"出来的**。创建向导
   里因果线是可选的手填 JSON，默认留空；`engine.
   _auto_register_causal_lines()` 只在某一步的 `line_updates`/
   `causal_links.line_id` 里**出现了新 id 之后**才补登记一条。结果：
   `state0`（初始状态）阶段因果线列表几乎总是空的——"没推进的看不到
   因果线"。
2. **"未来"只有一种退化实现：单路径的确定性外推，且要求历史已经
   存在**。`hypothesis.project_line_futures()` 是唯一的"展望"函数，
   逻辑是从**已经发生**的 `causal_links` 里找该线最近几条
   `driver→effect`，直接说"如果这条因果关系延续会怎样"——每条线
   只给一条延续路径，不是分叉树；而且完全依赖 `history` 已经有
   `causal_links` 记录，`state0` 阶段给不出任何东西。
3. **UI 上因果线总览只展示"过去时间点序列 + 尾巴上一句猜测"**，
   视觉上不是"树"。

结论：现状不符合"创建时就有核心因果线 + 每条线自带面向未来的因果树，
并在推进中持续修正"的预期，是设计层面的缺口，不是小 bug。

## 2. 改进目标

1. **创建模拟时**：除了生成初始 `vars`，还要规划出这次模拟的若干条
   核心因果线，并且**每条线自带一棵初始的"未来因果树"**（几个可能
   的分叉方向，而不是一条延续路径），且这个保证要落到代码层面（不
   完全依赖 LLM 是否配合），保证"创建模拟就有因果线和未来树"。
2. **推进过程中**：允许（不强制）对相关因果线的树做修正——推进结果
   印证了某个分支就标注"已实现"，出现新可能性就长出新分支，过时
   分支可以被裁剪或标记为"已排除"，但不做自动删除（保留痕迹）。
3. **UI**：能同时看到"已经走过的实际路径"（历史，来自 `line_updates`）
   和"从当前节点出发的若干可能分支"（未来树），而不是过去的一条
   直线 + 结尾一句话；用户也可以直接手动标记分支状态，不需要等
   `advance_step` 输出。

## 3. 数据结构设计

### 3.1 `future_tree`：挂在每条因果线下（而不是全局字段）

`SimManifest.settings.causal_lines` 的每一项新增可选字段
`future_tree`：

```json
{
  "id": "tech_line", "label": "技术线", "time_granularity": "年",
  "future_tree": {
    "as_of_step": 3,
    "branches": [
      {
        "id": "tech_fast",
        "description": "AI 成本快速下降，两年内降至当前 1/3",
        "likelihood": "medium",
        "status": "confirmed",
        "children": []
      },
      {
        "id": "tech_slow",
        "description": "成本仅缓慢下降",
        "likelihood": "medium",
        "status": "pruned",
        "children": []
      }
    ]
  }
}
```

- `status` 只分四档：`open`（仍然开放）、`confirmed`（后续推进印证）、
  `diverged`（实际走向偏离但不主动判断"不可能"）、`pruned`（主动
  排除）。不做严格互斥——同一棵树上可以同时有多个 `confirmed` 分支
  （"渐进式"和"局部突变"并不矛盾），不追求概率归一化（同项目里
  `confidence: high/medium/low` 一以贯之的克制风格）。
- `children` 可选，支持多层分叉，但不强制、不做深度限制之外的额外
  约束。

### 3.2 创建时机：`materialize_simulation()` 落盘前统一兜底

新增模块 `world_simulator/causal_tree.py`，核心函数
`ensure_future_trees(causal_lines, as_of_step=0)`：

- 因果线列表非空但某条线缺 `future_tree`（或形状不合法）：用通用
  兜底模板（`build_default_future_tree()`，"延续现状/加速好转/遇阻
  受挫"三个维度）补全。
- 因果线列表整体为空：兜底生成一条 `main_line`（"主线"），保证
  "创建模拟就有核心因果线"这个承诺不依赖用户/skill 是否配合声明。

`engine.materialize_simulation()` 在 `store.save_manifest()` 之前
统一调用这个函数，**不论调用方是独立看板创建向导还是 CLI/entrypoint
一步到位创建**，行为完全一致，不需要每个调用方各自记得调用。

同时更新 `spec_generator._resolve_causal_lines_hint(stage="create")`
的 prompt：明确要求 skill 规划 2~4 条核心因果线，且每条线必须给出
初始 `future_tree`（2~3 个有区分度的分支）。就算 skill 没有认真
遵守，代码层面的兜底仍然生效，但认真的 prompt 能让第一次生成的树
更贴合具体情境，而不是全部退化成通用模板。

### 3.3 推进时机：`advance_step` 可选输出 `tree_updates`

```json
{
  "tree_updates": [
    {
      "line_id": "tech_line",
      "confirmed_branch": "tech_fast",
      "pruned_branches": ["tech_slow"],
      "new_branches": [{"description": "政策突变导致成本骤降", "likelihood": "low"}]
    }
  ]
}
```

三个子字段都可选，只在真正发生时输出。`engine._apply_tree_updates()`
调用 `causal_tree.apply_tree_updates()` 纯合并（不做判断，判断交给
skill），并把生效的修改摘要记入新增的 `SimState.tree_updates`
字段（供时间线展示"这一步修正了未来树"，不重复存储分支全文）。

调用顺序：`_auto_register_causal_lines()`（新线登记，同样带默认
未来树）→ `_apply_tree_updates()`（合并树修正）→ `store.
append_state()`（把 `tree_updates` 审计摘要一起落盘）。

### 3.4 手动修正：`causal_tree.set_branch_status()`

因果线本身的风险量级和 `_auto_register_causal_lines()` 一致（远
低于 `structural_change` 那种会改写 `vars` schema 的操作），因此
不需要"提议→人工确认"两步——用户可以在详情页直接点击"标为已印证/
标为已排除"，通过既有的 `update_settings()` 直接写回
`manifest.settings.causal_lines`，同 `structural_change` 的
"系统只发现和展示，人工确认才生效"的保守思路是互补的另一半：
一个是"系统提议，人工确认"，一个是"人工可以直接动手"，取决于
操作本身的风险。

### 3.5 向后兼容

`future_tree`/`tree_updates` 全程可选：旧实例、旧数据的
`from_dict()` 解析完全不受影响（默认空列表/空字典）；
`_auto_register_causal_lines()` 兜底逻辑保留，万一 skill 没规划出
线仍然靠事后自动登记；`hypothesis.project_line_futures()`（单路径
历史外推）原样保留，作为 UI 上的补充参考，不是"未来"的唯一来源。

## 4. UI 展示方案

技术栈是 Streamlit + 自定义 CSS 卡片，没有专门的图可视化库，选择
**低成本方案**：因果线总览视图里，每条线下方新增"🌳 未来因果树"
子区块，用缩进列表 + 状态图标表达树（●已印证 / ○开放 / ◐已偏离 /
✕已排除），每个分支旁边给"标为已印证/标为已排除"按钮，点击直接
调用 `causal_tree.set_branch_status()` + `update_settings()` 写回。
不引入力导向图/桑基图/Mermaid，纯 HTML/CSS 列表实现，风险最低——
如果后续验证下来列表不够直观，可以再考虑 Mermaid 树状图，但本次
不在必做范围内。

原有的"🔮 简单历史外推"区块（`project_line_futures`）保留在未来树
下方，重新定位为"辅助参考"，不再是"未来"的唯一展示。

## 5. 涉及文件

- `world_simulator/causal_tree.py`（新增）：`ensure_future_trees()`、
  `normalize_future_tree()`、`build_default_future_tree()`、
  `auto_register_lines()`、`apply_tree_updates()`、
  `set_branch_status()`。
- `world_simulator/state_model.py`：`SimState` 新增 `tree_updates`
  字段；`SimManifest.settings.causal_lines` 文档补充 `future_tree`
  格式说明。
- `world_simulator/spec_generator.py`：`_resolve_causal_lines_hint()`
  按 `stage` 区分创建/推进阶段的要求。
- `world_simulator/engine.py`：`materialize_simulation()` 落盘前
  兜底；`_auto_register_causal_lines()` 委托给 `causal_tree`；新增
  `_apply_tree_updates()` 并接入 `advance()`。
- `app.py`：因果线总览新增未来因果树渲染区块（含手动确认/排除
  按钮）；创建向导默认展示含 `future_tree` 的因果线 JSON；时间线
  新增 `tree_updates` 审计提示；详情页设置面板文案同步更新。
- `skills/life-sim-template/SKILL.md`、
  `skills/negotiation-template/SKILL.md`、
  `skills/group-evolution-template/SKILL.md`：补充 `future_tree`
  （创建时）、`tree_updates`（推进时）的输出格式说明。
- `tests/test_causal_tree.py`（新增）：7 个单元测试，覆盖兜底、
  规整、自动登记、合并 `tree_updates`、手动覆盖。
- `tests/test_spec_and_engine.py`：更新 3 个既有用例以匹配新行为
  （创建即有 `main_line`、`causal_lines_hint` 创建阶段文案变化、
  已声明因果线的字段级断言放宽为不逐字段比对新增的 `future_tree`）。

## 6. 风险/克制点（如实记录，供实施与验收参考）

- 创建阶段要求"必须规划因果线+树"增加了 prompt 的复杂度和输出
  长度，如果 LLM 为了凑树而编造牵强分支，代码层面无法识别，只能
  靠后续实际使用时的质量观察；不合规输出仍会被 `normalize_future_
  tree()` 挡掉形状不对的部分，但内容质量本身不做校验。
- 树的"合理修正"完全依赖 LLM 在 `advance_step` 里输出
  `tree_updates`，和 `structural_change` 一样是"有多大概率被认真
  输出"的已知不确定性；`engine.py` 只负责合并落盘，不做正确性
  判断。
- 不做分支概率归一化，保持和项目里 `confidence: high/medium/low`
  一致的克制风格，不追求伪精确。
- 手动修正分支状态（`set_branch_status()`）没有"确认"环节，是
  比 `structural_change` 更宽松的风险处理——如果后续发现用户误操作
  较多，可以考虑加一个"撤销"入口（历史仍然保留在 `tree_updates`
  审计里，理论上可以回溯，但目前没有做撤销 UI）。
- 因果线 UI 仍然是纯列表展示，不是真正的图形化树；如果后续验证
  下来用户需要更直观的可视化，需要额外评估引入图形库或 Mermaid
  的成本。

## 7. 验收标准

- 新建任意模拟（任意模板），未推进任何一步时，「因果线总览」标签页
  应该已经能看到至少一条因果线，且每条线下方有"🌳 未来因果树"区块，
  展示 2 个以上分支。
- 推进一步之后，如果 skill 输出了 `tree_updates`，对应分支状态应该
  发生变化（`confirmed`/`pruned`），且时间线对应步骤下方出现
  "🌳 未来树更新" 提示。
- 点击某个分支的"标为已印证"/"标为已排除"按钮后，刷新页面应该
  看到状态图标同步更新。
- 全部测试通过：`python3 -m pytest tests/ -q`（累计 169 个）。
