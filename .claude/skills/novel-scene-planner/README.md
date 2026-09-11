# novel-scene-planner

"小说转视频"四段流程的第一步：场景拆分 + 角色/地点提取。详细流程规范见
`SKILL.md`，整体架构/文件契约见 `next_doc/novel_video_generator_plan.md`。

## 依赖

- **无外部 API/模型依赖**，纯 Agent 推理 + 文件写入；
- Python 依赖：`pyyaml`（`scripts/check_narration_draft.py` 读写
  `narration_script.yaml`）。

```bash
pip install pyyaml
```

## 输入 / 输出

| | 内容 |
|---|---|
| 输入 | 一段小说文本（用户粘贴或指定文件路径），目标视频时长 |
| 输出 | `novel_project.json` / `characters.json` / `locations.json` / `narration_script.yaml` |

产物目录：`novel_output/<小说名>_<timestamp>/`（后续三个 skill 共用同一目录）。

## 快速使用

Agent 按 `SKILL.md` 的 Step 0–4 依次生成并落盘四个文件后，跑校验：

```bash
python .claude/skills/novel-scene-planner/scripts/check_narration_draft.py <output_dir>
```

退出码 0 才能交付给 `novel-asset-generator`；非 0 时 stdout 打印结构化
错误清单，按提示回 Step 3/4 修复后重跑，直到通过。

## 已知限制

见 `SKILL.md` 末尾「已知限制」：本版只支持单次处理一段文本（不支持整本
小说自动分卷循环跑）；角色人脸参考照片本版未实现。
