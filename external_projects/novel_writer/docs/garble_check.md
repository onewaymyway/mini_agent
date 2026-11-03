# 关键文件乱码检测（garble_check）

> 对应方案：`next_doc/novel_writer_garble_post_step_check_plan.md`
> 当前进度：**G1 已完成**（配置 + 注册表 + 只读体检 + 驾驶舱入口 + report 模式记录）。G2（步骤内重试环）、G3（统一守卫 + 归因重跑 + 计数）尚未实现。

## 1. 为什么需要

正文乱码已有 `garble_guard` 守卫，但章节总结、全书章节索引、人物档案、伏笔账本等由 agent 顺手写出的文件没有检测。乱码内容会被下游步骤当作输入继续使用（例如第 13 章总结乱码，写到第 14 章才被发现）。

乱码无法转码还原，唯一修复方式是让产出它的步骤重新生成，因此本机制的核心是：**检测 → 找到产出者 → 重跑产出者**。

## 2. 检测什么

关键文件注册表 `WATCH_GLOBS`（`novel_writer/garble_watch.py`）：

| 类别 | 匹配 |
|---|---|
| 章节总结、全书章节索引 | `章节总结/*.md` |
| 记忆摘要、上下文包 | `项目状态/memory/ch_*.json`、`项目状态/memory/context_ch*.md` |
| 蓝图 | `大纲规划/蓝图/_index.md`、`大纲规划/蓝图/*.md` |
| 人物档案 | `大纲规划/人物设定/角色索引.md`、`大纲规划/人物设定/角色/*.md` |
| 伏笔账本 | `大纲规划/线索设定/伏笔/_index.md`、`伏笔/*.md`、`伏笔注册表.md`、`故事线状态.md` |
| 世界观与规划文档 | `world_bible.json`、`主题设计.md`、`节奏规划.md`、`前三章开篇设计.md`、`结局设计.md` |

排除：`_studio/`、`导出/`、`_drafts/`、`*.bak`，以及非 `.md` / `.json` / `.txt` 后缀的文件。

判定沿用 `garble.is_garbled`（替换符 / 私有区 / 控制字符、汉字旁半角 `?`、生僻字比例），阈值不变。读取一律使用 `repair=False`，检测过程**不改写任何文件**。

## 3. 配置

`_studio/settings.json`：

```json
"garble_check": {
  "mode": "off",
  "max_reruns": 2,
  "watch_extra": []
}
```

| 字段 | 说明 |
|---|---|
| `mode` | `off`（默认，与旧行为逐字节一致）；`report`：步骤结束后检测本步骤改动过的关键文件，只写运行日志与 `_studio/inbox/garble_<步骤>.json`；`rerun`：G2/G3 实现前按 `report` 处理，并在运行日志中注明 |
| `max_reruns` | 同一「步骤 + 文件」自动重跑上限（G3 起生效，当前仅校验） |
| `watch_extra` | 额外守卫的相对路径或 glob，如 `["自定义/*.md"]` |

也可在 **审校页 → 🧪 乱码体检** 中切换模式。

## 4. 只读体检

- 接口：`tool_api.scan_garble(workspace)`，返回 `files_checked`、`garbled_count` 与每个命中文件的 `reasons`、`writer`（最后写入者）、`writer_kind`（declared / agent / author）、`auto_rerunnable`、`note`。
- 归因规则：最后写入者是已知流水线步骤 → 可自动重跑；作者手改或查不到写入记录 → 只提示，不替作者决定重生成。
- 驾驶舱：审校页「🧪 乱码体检（关键文件）」→「开始体检」。对可自动重跑的命中项提供「重跑产出者」按钮，它只是把步骤重置为待执行（等同于总控台的重跑），之后到总控台运行。

## 5. report 模式的执行位置

`runner.run_step` 中：

1. 步骤开始前，`garble_watch.snapshot()` 记录关键文件指纹（`mode=off` 时直接返回空，零开销）；
2. 步骤将要置为 `passed` / `waiting_review` 时，`report_step()` 比较前后指纹，并把步骤声明的产出一并纳入检测；
3. 命中则写运行日志（`⚠ 乱码检测：…`）与收件箱记录。

该过程**不改变步骤状态**，任何异常都被吞掉并记入返回值，不影响步骤结果。

## 6. 已知限制（G1）

- `rerun` 模式尚未自动重跑，仅记录；
- 误报风险：生僻字阈值来自少量样本，集中出现人名/古字的规划文档可能误判，建议先用 `report` 观察；
- 收件箱记录目前只落盘为 JSON，尚未接入收件箱页面的汇总展示；
- 本机制不解决乱码产生的根因（例如 Windows 下的写入编码），需另行排查。

## 7. 后续阶段

| 阶段 | 内容 |
|---|---|
| G2 | skill 步骤内重试环并入乱码检测（`rerun`）：命中后备份到 `_studio/backup/garbled/`，带 hint 让同一步骤重做，最多 `MAX_RECEIPT_ATTEMPTS` 次 |
| G3 | `run_step` 统一守卫 + 归因 + 当前/上游步骤重置 + `garble_reruns` 计数 + 用尽后挂起问作者（`GARBLE_OPTIONS`） |
| G4 | 补齐测试与本文档的 G2/G3 章节 |
