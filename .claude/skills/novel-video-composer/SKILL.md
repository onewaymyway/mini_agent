---
name: novel-video-composer
description: 小说转视频流程的第四步（最终合成）。读取 novel-scene-video-generator 产出的全部 clips/*.mp4 + scene_plan.yaml + audio/segment_*.wav，拼接旁白音轨、逐场景缩放对齐、烧字幕、可选叠加封面，合成最终 video.mp4。当分场景视频已全部生成（check_clips.py 已通过）、需要合成最终成片时使用。
triggers: 最终合成, 小说转视频合成, compose_novel_video, 小说视频拼接, 生成最终视频
---

# 小说转视频最终合成 (Novel Video Composer)

## 概述

本 skill 是"小说转视频"四段流程（`novel-scene-planner` →
`novel-asset-generator` → `novel-scene-video-generator` →
`novel-video-composer`）的第四步，也是最后一步：把前三步产出的分场景
视频、旁白配音、字幕文案拼接成一支完整的 `video.mp4`。

方案文档：`next_doc/novel_video_generator_plan.md` 第 4 节。

**依赖脚本**（本 skill 目录下，改造自 `mv-generator` 的
`compose_mv.py`，核心差异见脚本头部注释）：
- `scripts/compose_novel_video.py`：一站式完成拼接旁白音轨、逐场景
  独立缩放、整体对齐误差兜底、烧字幕、可选封面、混音、校验交付。

**外部依赖**：系统需安装 `ffmpeg`/`ffprobe`（同 `mv-generator`）；
需要能正常渲染中文的字体（脚本内置路径为 Windows 的
`C:\Windows\Fonts\msyh.ttc`，与 `mv-generator/compose_mv.py` 完全一致，
非 Windows 环境需要自行改脚本里的 `FONT_PATH` 常量）。

**与 mv-generator 的关键差异**（不要照抄 mv 的合成心智模型）：
- mv 版本的时间轴由"已有的歌声 mp3"驱动，画面反过来对齐音频，存在
  场景间/首尾空隙需要 `fill_dur` 填补；novel 版本**没有现成音频**，
  音轨就是要拼接的旁白 wav，`scene_plan.yaml` 里的 scene 天然首尾
  相接（每个 scene 的 `duration_sec` 就是这段旁白的真实时长），因此
  不存在空隙问题，只保留"累积误差兜底对齐"这一层；
- 字幕来源是每个 scene 自带的 `text` 字段（一个 scene 一整句/一段
  旁白），不是 mv 那种"逐句歌词、句间可能有停顿"的结构；
- 本版不接 BGM、不做歌名水印，只有"旁白音轨 + 画面 + 字幕（+ 可选
  封面）"。

## 前置检查

进入本 skill 前，确认 `novel-scene-video-generator` 的
`check_clips.py` 已经通过（退出码 0）——`compose_novel_video.py` 自己
也会在合成前再确认一遍每个场景是否都有 `clips/<scene_id>.mp4` 和
`audio/segment_*.wav`，缺了默认直接拒绝合成并报错退出（不会静默产出
画面/音频缺失的成片），所以正常流程下这里是双保险，不会有意外。

## 流程规范

### Step 1：合成

```bash
python .claude/skills/novel-video-composer/scripts/compose_novel_video.py \
  <output_dir>
```

- 默认输出到 `<output_dir>/video.mp4`，可用 `--output` 指定其它路径；
- `--target-fps`（默认 24）/`--font-size`（默认 28）/
  `--overlay-y-offset`（默认 80，字幕距画面底部的像素距离）可按需调整，
  竖屏（`aspect_ratio: 9:16`）建议参考 `mv-generator` 的竖屏经验值
  适当调小字号、调大 `overlay-y-offset`；
- 目标分辨率**自动从 `novel_project.json` 的 `aspect_ratio` 推导**
  （`16:9` → `1280:720`，`9:16` → `720:1280`），不需要手动传
  `--target-size`；
- 若 `<output_dir>/assets/cover.png` 存在，默认自动叠加封面效果
  （挤压/替换第一个场景 clip 的前几秒，做法与 `mv-generator` 完全
  一致，不改变总时长）；不需要封面效果时加 `--no-cover`；封面时长用
  `--cover-duration`（默认 3 秒，会被 clamp 到第一个场景规划时长的
  50% 以内）。

⚠️ **调用 bash 工具执行本命令时，建议 `timeout` 传较大值（如
`600` 或 `-1`）**：多场景视频的逐场景缩放 + 拼接 + 字幕渲染是纯本地
ffmpeg 计算，场景数多、clip 分辨率高时耗时可能超过默认超时。

脚本内部固定按顺序完成：
1. 拼接 `audio/segment_*.wav`（用 `filter_complex concat` 重新编码，
   不用 `concat` demuxer 直接拼接，避免不同 wav 参数不一致导致失败）
   得到权威的旁白总时长；
2. 每个场景独立 `setpts` 缩放到自己的 `duration_sec`（短则慢放、长则
   快放），保证画面切换时刻和 `scene_plan.yaml` 规划严格一致；
3. 若拼接后总时长和旁白总时长有残留误差（ffmpeg 帧对齐等原因），
   强制整体 `setpts` 对齐，误差不设阈值，一律纠正到底；
4. 按每个 scene 的 `text` 渲染字幕 PNG（同文本复用同一张图），按
   （对齐后的）scene 时长窗口显示；
5. 混入拼接后的旁白音轨，输出最终 `video.mp4`；
6. **自动校验交付**：`ffprobe` 检查总时长（应约等于旁白总时长，
   差异 > 2 秒记为错误）、视频比特率（< 1 Mbps 记为警告，提示可能是
   overlay 步骤质量崩溃）、分辨率是否与 `aspect_ratio` 推导出的目标
   一致（不一致记为错误），结果以结构化 JSON 打印到 stdout，退出码
   非 0 说明校验未通过。

### Step 2：交付确认

- 校验 JSON 里 `"ok": true` 且 `errors` 为空 → 向用户展示最终产物
  路径、总时长、场景数量；若 `warnings` 非空（如比特率偏低），如实
  告知用户，不隐瞒；
- `"ok": false` → 按 `errors` 逐条排查：
  - 总时长对不上：多半是 `novel-asset-generator` 阶段某些场景的
    `duration_sec` 和实际 `clips/*.mp4` 严重不匹配，或本次运行有场景
    走了 `--allow-missing-clips` 的借用填补分支，回上游确认后重跑；
  - 分辨率不一致：检查 `novel_project.json` 的 `aspect_ratio` 字段
    是否被误改，或本地 ffmpeg 版本对 `pad` 滤镜支持异常；
  - 比特率过低：参考 `mv-generator` 的已知坑——通常是 overlay 相关
    filter 链没有正确指定编码参数，检查是否误改了脚本里的
    `-preset`/`-crf` 参数。

**产物**：`video.mp4`（最终交付物，"小说转视频"四段流程到此结束）。

## 常见问题

1. **合成前直接报错"缺少 clip/旁白音频文件"**：说明前置检查没有
   通过，回 `novel-scene-video-generator`（补 clip）或
   `novel-asset-generator`（补配音），完成后重新执行本命令，不要用
   `--allow-missing-clips` 绕过——该参数只用于用户明确知情并接受
   成片有画面重复的特殊场景。
2. **字幕文字被裁切/挤出画面**：单个 scene 的 `text` 过长时脚本会
   按画面宽度 90% 自动换行，但极长文案换行后仍可能超出画面高度；
   建议回 `novel-scene-planner`/`novel-asset-generator` 阶段把过长的
   旁白文案拆得更短（同时也有利于把 `duration_sec` 控制在
   `gen_video_with_text` 的 4–12 秒范围内）。
3. **中文字体渲染异常（方框/乱码）**：检查 `FONT_PATH` 常量指向的
   字体文件是否存在、是否支持中文字形；非 Windows 环境需要把
   `FONT_PATH` 改成本地实际可用的中文字体路径。
4. **封面没有生效**：确认 `<output_dir>/assets/cover.png` 是否存在
   （`novel-asset-generator` 里封面是可选产物，没生成属于正常情况，
   脚本会打印提示后正常跳过，不算错误）；确认没有误加 `--no-cover`。
