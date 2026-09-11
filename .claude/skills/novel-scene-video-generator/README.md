# novel-scene-video-generator

"小说转视频"四段流程的第三步：读取正式版 `scene_plan.yaml`，批量/定向
调用 `gen_video_with_text` 生成每个场景的视频片段。详细流程规范见
`SKILL.md`，整体架构/文件契约见 `next_doc/novel_video_generator_plan.md`。

## 依赖

**依赖 skill**：`gen_video_with_text`（必须）。

**Python 依赖**：

```bash
pip install pyyaml
```

**外部依赖**：`AGNES_API_KEY`/`AGNES_API_KEYS` 环境变量（同
`gen_video_with_text`，支持多 key 池自动限流切换）。

## 输入 / 输出

| | 内容 |
|---|---|
| 输入 | `novel-asset-generator` 产出的、已通过 `check_assets_and_audio.py` 校验的 `scene_plan.yaml` |
| 输出 | `clips/<scene_id>.mp4`（全部非空）+ 状态已回写为 `done` 的 `scene_plan.yaml` |

## 脚本

- `scripts/generate_scene_videos.py`：批量/定向生成场景视频，内置 key
  池自动切换、单场景失败重试 3 次、断点续跑，生成完成后回写
  `scene_plan.yaml` 对应场景的 `status`：
  ```bash
  python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos.py \
    <output_dir> [--scene-id scene_03 scene_07 ...] [--force]
  ```
  ⚠️ 调用 bash 工具执行本命令时 `timeout` 参数必须传 `-1`（不限时），
  单场景生成（排队+轮询+下载）常常要几分钟。
- `scripts/check_clips.py`：校验所有场景是否都有非空 clip，本 skill
  结束前必须跑：
  ```bash
  python .claude/skills/novel-scene-video-generator/scripts/check_clips.py <output_dir>
  ```

退出码 0 才能交付给 `novel-video-composer`；非 0 时 stdout 打印
`missing_scene_ids`/`empty_scene_ids`，用 `--scene-id` 定向补齐缺失场景，
重新跑校验直到通过。

## 常见问题

见 `SKILL.md` 末尾「常见问题」：prompt 触发内容审核、参考图缺失导致
自动降级为 `text` 模式、账号 key 限流/额度耗尽等。
