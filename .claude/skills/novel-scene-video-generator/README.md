# novel-scene-video-generator

"小说转视频"流程的第五步：生成小场景（micro_scene）视频 + 大场景内
合成。**新项目请使用 v2**；v1（读取 `scene_plan.yaml` 生成分场景视频，
无大场景概念）保留仅供历史参考。详细流程规范见 `SKILL.md`，整体架构/
文件契约见 `next_doc/novel_video_generator_plan_v2.md`（v1 见
`novel_video_generator_plan.md`）。

## 依赖

**依赖 skill**：`gen_video_with_text`（必须）。

**Python 依赖**：

```bash
pip install pyyaml pillow
```
（`pillow` 只有 v2 的 `compose_macro_scene.py` 渲染字幕时需要。）

**外部依赖**：`AGNES_API_KEY`/`AGNES_API_KEYS` 环境变量（同
`gen_video_with_text`）；v2 的 `compose_macro_scene.py` 额外需要
`ffmpeg`/`ffprobe` 和一个能渲染中文的字体（`--font-path` 指定，非
Windows 环境必填）。

## v2（六段流程，当前使用）

| | 内容 |
|---|---|
| 输入 | 各 `macro_scene_XX/scene_detail.yaml`（已通过 `check_scene_detail.py` 校验，`novel-asset-generator` 已回填 `duration_sec`；Agent 需先手写 `prompt_en`/`video_mode`，见 `SKILL.md`） |
| 输出 | 各 `macro_scene_XX/clips/<micro_id>.mp4` + `macro_scene_XX/macro_scene_XX.mp4` + `macro_scenes.yaml` 全部 `status: done` |

脚本：
- `scripts/generate_scene_videos_v2.py`：批量/定向生成小场景视频，
  支持 `--macro-id`/`--micro-id` 双重过滤：
  ```bash
  python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos_v2.py \
    <output_dir> [--macro-id macro_01 ...] [--micro-id micro_03 ...] [--force]
  ```
  ⚠️ `timeout` 必须传 `-1`。
- `scripts/check_clips_v2.py`：校验小场景 clip 完整性 + 大场景合成
  视频（若已存在）的时长一致性：
  ```bash
  python .claude/skills/novel-scene-video-generator/scripts/check_clips_v2.py \
    <output_dir> [--macro-id macro_01 ...]
  ```
- `scripts/compose_macro_scene.py`：单个大场景内合成（clip 拼接 +
  配音混合 + 字幕），成功后自动回写 `macro_scenes.yaml` 对应大场景
  `status: done`：
  ```bash
  python .claude/skills/novel-scene-video-generator/scripts/compose_macro_scene.py \
    <output_dir> macro_01 --font-path <本地中文字体路径>
  ```

对每个大场景重复"生成小场景视频 → 校验 → 合成大场景"，直到
`macro_scenes.yaml` 里全部大场景 `status: done`，再进入
`novel-video-composer`。

---

## v1（四段流程，已归档）

| | 内容 |
|---|---|
| 输入 | `novel-asset-generator` 产出的、已通过 `check_assets_and_audio.py` 校验的 `scene_plan.yaml` |
| 输出 | `clips/<scene_id>.mp4`（全部非空）+ 状态已回写为 `done` 的 `scene_plan.yaml` |

- `scripts/generate_scene_videos.py` + `scripts/check_clips.py`，用法
  与 v2 同款脚本类似（`--scene-id` 替代 `--macro-id`/`--micro-id`），
  详见 `SKILL.md` 「v1 版本」一节。

## 常见问题

见 `SKILL.md` 对应版本的「常见问题」小节：prompt 触发内容审核、参考图
缺失导致自动降级为 `text` 模式、账号 key 限流/额度耗尽、（v2 新增）
大场景合成时缺配音等。
