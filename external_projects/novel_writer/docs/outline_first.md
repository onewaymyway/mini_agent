# 大纲先行模式（outline_first）

> 设计与分期：`next_doc/novel_writer_outline_first_plan.md`（仓库根目录 next_doc/）。
> 当前进度：**O0、O1、O2 已完成**。O3（写作接入与关键内容校验）、O4（简述修改后的已写章节的处理策略）、O5（向导开放与收尾）尚未实施。

## 已确定的规则（设计）

- 每章简述：300–600 字的场景段落式，另附结构化的关键内容（必须事件、人物状态变化、伏笔动作、章末钩子）。
- 关键内容锁定，写作不得偏离；非关键内容可发挥，但须回写简述并记录原因。
- 写作模式每本书单独选择，默认直写。
- 简述修改后已写章节的处理：`brief_change_policy` = `ask`（默认，由作者决定）或 `auto_rewrite`（自动重写，复用修订流程执行器）。

## O2 已实现

| 项目 | 位置 | 说明 |
|---|---|---|
| 版本历史 | `brief_review.py`、`大纲规划/章节简述/_history/第NNN章.vNNN.json` | 每次改动（编辑、回退、单章改写、整幕重排）前保存旧版本，附原因与时间；可回退，回退本身也记为新版本 |
| 直接编辑 | `brief_review.save_edit`、界面「章节简述」页 | 先按生成时的同一套规则校验，失败则不写盘并列出全部错误 |
| 单章反馈改写 | `brief_review.rewrite_chapter` | 作者意见 + 当前简述 + 前后章章末钩子交给 LLM 改写本章；LLM 同时给出可能受影响的章节，过滤越界与自身后展示给作者，由作者决定是否检查 |
| 整幕重排 | `brief_review.regenerate_act`、`brief.generate_act(feedback=...)` | 整幕带着作者意见重新生成；改动前每章旧版本入历史 |
| 确认检查点 | `brief_review.confirm` | 仅当 `brief_review` 处于 waiting_review 时可确认；确认前每章必须通过校验；记录每章哈希到 `_confirmed.json`，批准检查点 |
| 确认失效 | 检查点输入包含 `第*章.json` | 任何简述改动都会让检查点变为 stale（需重新运行到确认点再确认）；编辑保存和改写也会主动使已确认状态失效 |
| 冻结检测 | `brief_review.confirmation_status` | 绕过界面直接改文件时，按哈希报告哪些章节在确认后被改动 |
| 界面 | `ui/briefs.py`，导航「📚 章节简述」 | 确认状态与按钮、章节表（校验与版本数）、编辑表单、版本历史与回退、单章反馈改写、整幕重排（后台执行，实时日志，最新在下）、操作日志 |

## O1 已实现

| 项目 | 位置 | 说明 |
|---|---|---|
| 简述数据 | `大纲规划/章节简述/第NNN章.json` | 唯一事实来源：叙述简述、关键内容（must_events / must_states / must_foreshadow / must_hook）、偏离记录（O3 使用） |
| 阅读版本 | `第NNN章.md` | 由 JSON 渲染，供阅读；`_index.md` 列出全部章节 |
| 按幕生成 | 步骤 `brief_draft:<幕号>` | 每幕一个步骤，LLM 一次写出该幕全部章节；输出经确定性校验后才落盘，失败的幕带错误重试一次，仍失败则步骤失败且不写半成品 |
| 确定性校验 | `brief.validate_brief` | 章号与蓝图一致、叙述 300–600 字（非空白字符）、must_events 非空、must_states 非空且人物须在人物档案中、must_foreshadow 为 F+数字且须在伏笔账本中、must_hook 非空、新简述不含偏离记录 |
| 计划顺序 | `pipeline.build_plan` | 大纲先行时：先补全所有缺失幕的蓝图条目（plan_act / blueprint_extend）→ 结局设计 → 逐幕简述 → 确认检查点 → 章节写作。所有简述在任何章节写作之前完成 |
| 上下文衔接 | 提示词 | 每幕提示带上前一幕最后一章的章末钩子，保持衔接 |

输出的规范 JSON 结构见 `brief.py` 文件头。

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
- 大纲先行：简述生成（O1）与确认页（O2）已可用。向导仍未开放大纲先行（O3 的关键内容校验完成后再开放）。
- 大纲先行不能通过向导创建（仅开发中可通过接口设置），以免建出无法继续的书。

## 配置存放位置

与 `pause_after_ideation` 等每本书的开关一致，存放在工作目录的 `studio_settings`（即 `ws.settings()`），而不是 `项目状态/workspace_config.json`。计划文档已同步。

## 测试

`tests/test_outline_first_o2.py`（14 例）：编辑与版本、非法编辑不写盘、角色校验、回退、确认失效、确认门禁、确认冻结与外部改动检测、单章改写（版本与越界过滤）、改写失败不写盘、整幕重排（快照与意见）、整幕重排使确认失效、页面按钮状态与确认。

`tests/test_outline_first_o1.py`（20 例）：校验边界（字数、关键内容、人物与伏笔）、整幕解析（章节集合、JSON 错误）、按幕生成（落盘、索引、重试、两次失败不写半成品、蓝图缺条目报错、缺执行器报错、跨幕衔接）、计划顺序（简述先于章节、幕间依赖链、补全条目先于简述）。

`tests/test_outline_first_o0.py`（8 例）：默认值与校验；直写计划无简述步骤；大纲先行计划的插入位置与依赖；确认检查点在自动驾驶挡下仍为人工审批；简述生成步骤尚未注册处理器；创建时持久化模式；开始运行后拒绝切换；非法模式被拒绝。
