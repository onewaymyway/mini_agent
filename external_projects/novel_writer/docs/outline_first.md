# 大纲先行模式（outline_first）

> 设计与分期：`next_doc/novel_writer_outline_first_plan.md`（仓库根目录 next_doc/）。
> 当前进度：**O0 已完成**（模式开关与流水线接入）。O1（简述生成）、O2（简述确认页）、O3（写作接入与关键内容校验）、O4（简述修改后的已写章节）、O5（界面收尾）尚未实施。

## 已确定的规则（设计）

- 每章简述：300–600 字的场景段落式，另附结构化的关键内容（必须事件、人物状态变化、伏笔动作、章末钩子）。
- 关键内容锁定，写作不得偏离；非关键内容可发挥，但须回写简述并记录原因。
- 写作模式每本书单独选择，默认直写。
- 简述修改后已写章节的处理：`brief_change_policy` = `ask`（默认，由作者决定）或 `auto_rewrite`（自动重写，复用修订流程执行器）。

## O0 已实现

| 项目 | 位置 | 说明 |
|---|---|---|
| 写作模式与处理策略 | `settings.py` | 新增 `writing_mode`（`direct` / `outline_first`，默认 `direct`）、`brief_change_policy`（`ask` / `auto_rewrite`，默认 `ask`）；非法值报 `SettingsError` |
| 步骤定义 | `brief.py` | `brief_draft`（简述生成，O1）与 `brief_review`（确认检查点，O2）的结构定义 |
| 计划接入 | `pipeline.py` | 仅 `outline_first` 时，在构思完成之后、章节写作之前插入两步；第一章的首步依赖 `brief_review`。`direct` 下计划不变 |
| 闸门 | `gates.py` | `brief_review` 强制为人工审批，不受挡位与托管影响，只允许显式跳过 |
| 工具接口 | `tool_api.py` | `create_novel(writing_mode=...)`；`set_writing_mode(workspace, mode)`，书已开始运行后拒绝切换 |
| 向导 | `ui/wizard.py` | 增加“写作模式”选项。O0 阶段选择大纲先行会提示“开发中”，仍按直写创建 |

## 现阶段的行为

- 直写：与之前完全一致（测试守住计划不变）。
- 大纲先行：`brief_draft` 尚未注册处理器，运行器会把它置为 `blocked`，书停在简述生成处，不会误写正文。`brief_review` 本身是检查点，在 O2 之前不会有内容。
- 大纲先行不能通过向导创建（仅开发中可通过接口设置），以免建出无法继续的书。

## 配置存放位置

与 `pause_after_ideation` 等每本书的开关一致，存放在工作目录的 `studio_settings`（即 `ws.settings()`），而不是 `项目状态/workspace_config.json`。计划文档已同步。

## 测试

`tests/test_outline_first_o0.py`（8 例）：默认值与校验；直写计划无简述步骤；大纲先行计划的插入位置与依赖；确认检查点在自动驾驶挡下仍为人工审批；简述生成步骤尚未注册处理器；创建时持久化模式；开始运行后拒绝切换；非法模式被拒绝。
