# world_simulator 改动说明

本次改动涉及的文件（覆盖到原项目对应路径即可，目录结构与原项目一致）：

```
mini_agent-master/external_projects/world_simulator/
├── app.py                                   # 修改
├── workflows/advance_step.yaml              # 修改
├── skills/life-sim-template/SKILL.md        # 修改
├── skills/group-evolution-template/SKILL.md # 修改
├── world_simulator/store.py                 # 修改
├── world_simulator/branch_manager.py        # 修改
├── world_simulator/engine.py                # 修改
├── world_simulator/autopilot.py             # 修改
├── tests/test_branch_manager.py             # 修改（新增用例）
└── tests/test_autopilot.py                  # 修改（新增用例）
```

## 1. 分支列表信息增强

**问题**：分支列表只显示分支 id，用户无法区分分支之间的差异。

**改动**：
- `store.py` 新增 `branch_meta.json`（记录创建时间/来源分支/分叉自哪一步）。
- `branch_manager.fork_branch()` 分叉时落盘该元信息。
- 新增 `branch_manager.list_branches_detailed()`，一次性返回每条分支的：
  - 创建时间、来源分支、分叉自第几步（main 用实例创建时间，无来源）
  - 当前进度（第几步 / 历史条数）
  - 自动挡画像摘要（是否开启、风险偏好、review_mode、是否允许自选、原则条数）
- `app.py` 的「分支」区块改用这个函数渲染，每条分支现在会显示：
  `创建于 xxx，分叉自 main/第 3 步` + `进度：第 5 步（历史 6 条）` + `自动挡 · 保守 · 重大决策暂停 · 2 条原则`。

原有的 `list_branches()`（只返回 id 列表）保持不变，未破坏任何既有调用方。

## 2. 自动挡 / 手动挡都支持"候选列表之外"的选项

**问题**：系统给出的候选方向只是建议，无论是用户还是自动挡代理，都可能想到更合理的第三个选项，但原来只能从候选列表里选。

**手动挡**：
- `engine.advance()` 新增 `custom_option: {"label", "description"}` 参数，绕开"必须在 `current.options` 里"的校验，直接生成一个 `custom_` 前缀的新选项并按用户所写方向推进。
- `app.py` 推进面板新增「✏️ 都不满意？自己写一个选项」折叠区，填标题+说明，点「应用这个选项」即可，不需要先加入候选列表这道额外步骤。

**自动挡**：
- 自动挡配置新增开关 `allow_custom_options`（UI：「允许代理跳出候选列表，自己提出更合理的选项」）。
- 开启后，`autopilot._build_decision_context()` 会明确告知代理"可以跳出候选列表提出新方向"；未开启时明确告知"必须从候选列表里选"。
- `advance_step.yaml` 的 prompt 和两个模板的 `SKILL.md` 同步更新了输出格式说明：允许时代理可以输出 `custom_option_label`/`custom_option_description`/`chosen_reason`，而不是 `chosen_option_id`。
- `engine.advance()` 相应识别并落盘这种情况，`chosen_by` 记为 `"autopilot"`。

默认关闭 `allow_custom_options`，不影响任何已有实例的行为。

## 3. 多策略自动挡对比实验

**新增能力**：一次性生成多份自动挡策略画像，从同一个历史节点各自分叉出一条分支，依次跑相同步数，跑完后可以直接进对比视图查看"同样起点、不同策略"的结果差异。

- `autopilot.run_comparison_experiment(cfg, workspace_root, data_dir, sim_id, source_branch=..., from_step=..., steps=..., profiles=[...])`：
  - 每份 `profile` 支持独立的 `principles`/`risk_preference`/`review_mode`/`allow_custom_options`。
  - 每份策略各自跑在独立分支上，互不干扰；单条策略出错/被 `pause_on_major_decision` 提前结束，不影响其它策略继续跑。
  - 实验结束后会把实例的"当前活跃分支"恢复为实验开始前用户正在看的那条，不会把用户导航到最后一条策略分支上。
- `app.py` 新增「🧪 对比实验」页面（侧边栏新增入口）：选实例/起点分支/分叉步数/每条推进步数，可配置 2~6 份策略画像，运行后展示每条分支的完成情况，支持一键"加入对比"跳转到对比视图。

## 测试

`tests/test_branch_manager.py` / `tests/test_autopilot.py` 新增了针对上述三部分能力的单元测试（分支元信息、自定义选项手动/自动两种路径、对比实验分支隔离与活跃分支恢复）。运行 `pytest tests/` 全部通过（需要环境装有 `fastapi`/`mini_agent` 等既有依赖，与本次改动无关的既有约束一致）。
