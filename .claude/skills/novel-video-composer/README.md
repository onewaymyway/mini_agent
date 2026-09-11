# novel-video-composer

"小说转视频"四段流程的第四步（最终合成）：拼接旁白音轨、逐场景独立
缩放对齐、烧字幕、可选叠加封面，合成最终 `video.mp4`。详细流程规范见
`SKILL.md`，整体架构/文件契约见 `next_doc/novel_video_generator_plan.md`。

## 依赖

**Python 依赖**：

```bash
pip install pyyaml pillow
pip install imageio-ffmpeg   # 可选：自动探测 ffmpeg/ffprobe 路径
```

**外部依赖**：
- `ffmpeg`/`ffprobe`（系统命令；探测逻辑优先系统 `PATH`，其次
  `imageio_ffmpeg`，最后回退 Windows 专用路径，`mv-generator` 同款）；
- 能正常渲染中文的字体。默认路径为 Windows 的
  `C:\Windows\Fonts\msyh.ttc`，非 Windows 环境用 `--font-path` 参数
  指定本地实际可用的中文字体路径即可，不需要改源码（例如 Linux 上的
  `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）。

## 输入 / 输出

| | 内容 |
|---|---|
| 输入 | `novel-scene-video-generator` 产出的、已通过 `check_clips.py` 校验的 `clips/*.mp4` + `scene_plan.yaml` + `audio/segment_*.wav`（可选 `assets/cover.png`） |
| 输出 | `video.mp4`（最终交付物） |

## 快速使用

```bash
python .claude/skills/novel-video-composer/scripts/compose_novel_video.py <output_dir>
```

常用可选参数：`--output`（默认 `<output_dir>/video.mp4`）、
`--target-fps`（默认 24）、`--font-size`（默认 28）、
`--overlay-y-offset`（默认 80）、`--cover-duration`（默认 3，仅在
`assets/cover.png` 存在时生效）、`--no-cover`（跳过封面效果）、
`--allow-missing-clips`（默认关闭，仅用于明确知情接受画面重复的场景）。

目标分辨率自动从 `novel_project.json` 的 `aspect_ratio` 推导
（`16:9`→`1280:720`，`9:16`→`720:1280`），不需要手动指定。

脚本末尾内置校验交付：`ffprobe` 检查总时长/比特率/分辨率，以 JSON
打印到 stdout，退出码非 0 表示未通过，需要排查。

## 与 mv-generator 的关键差异

- 时间轴由旁白 wav 拼接驱动（无现成音频），不存在 mv 里的场景间空隙，
  因此省去了 `fill_dur` 填补逻辑，只保留累积误差兜底对齐；
- 字幕来源是每个 scene 自带的 `text` 字段（一整句/一段），不是逐句
  歌词队列；
- 不接 BGM、不做歌名水印，只有"旁白音轨 + 画面 + 字幕（+ 可选封面）"。

详见 `scripts/compose_novel_video.py` 文件头注释和 `SKILL.md`。
