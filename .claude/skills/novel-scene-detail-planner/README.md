# novel-scene-detail-planner

小说转视频 v2 流程第 3 步：针对单个大场景做详细的小场景规划（旁白+
角色对话拆分），并做引用完整性回补闭环。详细规范见 `SKILL.md`；整体
架构见 `next_doc/novel_video_generator_plan_v2.md`。

## 依赖

无外部 API/模型依赖，纯 Agent 推理 + 文件读写；引用缺失时会调用
`novel-entity-extractor`（模式B）和 `novel-asset-generator` 补齐资源。
校验脚本依赖 `pyyaml`。

## 输入 / 输出速查

| | 路径 | 说明 |
|---|---|---|
| 输入 | `<output_dir>/macro_scenes.yaml` 里 `status:pending` 的一条记录 | 由 `novel-macro-scene-planner` 产出 |
| 输入 | `<output_dir>/global/characters.json` / `locations.json` | 引用校验依据 |
| 输出 | `<output_dir>/macro_scene_<后缀>/scene_detail.yaml` | 本大场景的小场景规划 |
| 副作用 | `macro_scenes.yaml` 对应条目 `status` 改为 `planned`；可能追加更新 `global/` 下的角色/地点库 | |

## 关键规则

- **每次只处理一个大场景**，不是一次性处理全部；
- `content_blocks` 区分 `narration`（可改写）/`dialogue`（**必须原文逐字
  摘录**，不允许改写或补写）；
- 发现引用了全局库里没有的角色/地点时，先回调 `novel-entity-extractor`
  补抽取，再回调 `novel-asset-generator` 补生成素材，形成闭环，不允许
  把问题遗留给下游 skill。

## 脚本用法

```bash
python .claude/skills/novel-scene-detail-planner/scripts/check_scene_detail.py \
  <output_dir> <macro_id>
```

校验：角色/地点引用完整性+asset_path非空、dialogue.speaker 合法性、
对话原文子串匹配（防臆造）、content_blocks 非空、micro_scene id 项目内
全局唯一。退出码 0 = 通过，1 = 有问题。

## 与相邻 skill 的交接条件

- 上游：`novel-macro-scene-planner`（提供 `macro_scenes.yaml`）、
  `novel-entity-extractor`/`novel-asset-generator`（回补调用）；
- 下游：`novel-asset-generator`，在**所有**大场景都变成 `status: planned`
  后，才整体进入 TTS 配音阶段（回填每个小场景的真实 `duration_sec`）。
