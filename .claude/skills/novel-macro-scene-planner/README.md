# novel-macro-scene-planner

小说转视频 v2 流程第 2 步：按剧情把全文切分成大场景（macro_scene）。
详细规范见 `SKILL.md`；整体架构见 `next_doc/novel_video_generator_plan_v2.md`。

## 依赖

无外部 API/模型依赖，纯 Agent 推理 + 文件读写；校验脚本依赖 `pyyaml`
（`pip install pyyaml`）。

## 输入 / 输出速查

| | 路径 | 说明 |
|---|---|---|
| 输入 | `<output_dir>/novel_project.json` / `global/characters.json` / `global/locations.json` | 由 `novel-entity-extractor` 产出 |
| 输出 | `<output_dir>/macro_scenes.yaml` | 大场景清单，含原文全文 `raw_text` |

## 硬约束

单个大场景：原文字数 ≤1000、预估口播时长 ≤120 秒（按 4.5 字/秒估算）。
超限强制继续细分。

## 脚本用法

```bash
python .claude/skills/novel-macro-scene-planner/scripts/check_macro_scenes.py \
  <output_dir> [--novel-text-file <原文路径>]
```

`--novel-text-file` 可选，传了会额外做"原文覆盖率"检查（总摘录字数 /
原文总字数是否在 ±15% 区间）。退出码 0 = 通过，1 = 有问题。

## 与相邻 skill 的交接条件

- 上游：`novel-entity-extractor`，需要 `global/characters.json`/
  `global/locations.json` 已存在且通过 `check_entities.py`；
- 下游：`novel-scene-detail-planner`，只有 `check_macro_scenes.py` 通过
  后才能交接；下游按 `macro_scenes.yaml` 里 `status: pending` 逐条处理，
  不是一次性处理全部大场景。
