---
name: novel-video-composer
description: 小说转视频流程的最后一步（最终合成）。v1（四段流程）读取 novel-scene-video-generator 产出的全部 clips/*.mp4 + scene_plan.yaml + audio/segment_*.wav；v2（六段流程）改为读取每个大场景已合成好的 macro_scene_XX/macro_scene_XX.mp4 + macro_scenes.yaml，按 transition_mode（cut硬切/fade黑场淡入淡出）拼接。两版都拼接、可选叠加封面、合成最终 video.mp4。当所有分场景/大场景视频已就绪、需要合成最终成片时使用。
triggers: 最终合成, 小说转视频合成, compose_novel_video, compose_final_video_v2, 小说视频拼接, 生成最终视频, 大场景转场, transition_mode
---

# 小说转视频最终合成 (Novel Video Composer)

## 概述

本 skill 是"小说转视频"流程的最后一步：

- **v1（四段流程，已归档）**：`novel-scene-planner` →
  `novel-asset-generator` → `novel-scene-video-generator` →
  `novel-video-composer`（本 skill），脚本
  `scripts/compose_novel_video.py`，把分场景视频、旁白配音、字幕文案
  拼接成一支完整的 `video.mp4`。方案文档：
  `next_doc/novel_video_generator_plan.md` 第 4 节。
- **v2（六段流程，当前实施依据）**：`novel-entity-extractor` →
  `novel-macro-scene-planner` → `novel-scene-detail-planner` →
  `novel-asset-generator` → `novel-scene-video-generator` →
  `novel-video-composer`（本 skill），脚本
  `scripts/compose_final_video_v2.py`，输入变成每个大场景已经合成好
  （音轨+字幕齐全）的 `macro_scene_XX/macro_scene_XX.mp4`，本 skill
  只做"大场景之间"的拼接与转场，不再处理逐场景音轨/字幕。方案文档：
  `next_doc/novel_video_generator_plan_v2.md` 第 7 节。

**新项目请直接使用 v2（`compose_final_video_v2.py`）**，v1 的内容保留
在下方 "v1 版本" 一节仅供历史参考/排障对照。

---

## v2 版本：`compose_final_video_v2.py`（当前使用）

### 输入

`macro_scenes.yaml`（所有 `status: done` 的记录）+ 对应
`macro_scene_XX/macro_scene_XX.mp4`（`novel-scene-video-generator` 的
`compose_macro_scene.py` 产出）+ `novel_project.json`
（`aspect_ratio`/`transition_mode`/`transition_duration_sec`）+ 可选
`global/assets/cover.png`。

### 前置检查

进入本 skill 前，确认所有大场景都已经用
`novel-scene-video-generator` 的 `compose_macro_scene.py` 合成出
`macro_scene_XX.mp4`、且 `macro_scenes.yaml` 对应条目 `status` 已经
回写为 `done`（可用 `check_clips_v2.py` 辅助确认小场景 clip 齐全）。
脚本自身也会检查每个大场景是否有对应的已合成视频文件，缺了默认直接
拒绝合成并报错退出。

### Step 1：合成

```bash
python .claude/skills/novel-video-composer/scripts/compose_final_video_v2.py \
  <output_dir>
```

- 默认输出到 `<output_dir>/video.mp4`，可用 `--output` 指定其它路径；
- **转场模式** `--transition-mode cut|fade`：不传则读取
  `novel_project.json.transition_mode`（默认 `cut`）；
  - `cut`：大场景之间直接硬切拼接，总时长 = 各大场景视频时长之和；
  - `fade`：每两个大场景之间插入一段可配置时长的黑场（前一个大场景
    末尾淡出到黑、后一个大场景开头从黑淡入），转场时长用
    `--transition-duration`（不传则读 `novel_project.json.
    transition_duration_sec`，默认 0.5 秒），**会增加总时长**：
    总时长 = 各大场景视频时长之和 + `(大场景数 - 1) × transition_duration`；
- 若 `<output_dir>/global/assets/cover.png` 存在，默认自动叠加封面
  效果（挤压/替换第一个大场景视频的前几秒，做法与 v1 完全一致，不
  改变总时长）；不需要时加 `--no-cover`；封面时长用 `--cover-duration`
  （默认 3 秒，会被 clamp 到第一个大场景视频时长的 50% 以内）；
- 合成前会先把每个大场景视频统一分辨率/帧率/采样率（`normalize_clip`），
  避免不同批次生成的大场景视频参数不完全一致导致拼接失败；
- 本版仍不接 BGM（`novel_project.json.bgm_enabled` 恒为 `false`）；
- 末尾**自动校验交付**：`ffprobe` 检查总时长（应约等于"各大场景时长
  之和 [+ 转场时长]"，差异 > 2 秒记为错误）、比特率（< 1 Mbps 记为
  警告）、分辨率是否与 `aspect_ratio` 推导出的目标一致，结果以结构化
  JSON 打印到 stdout，退出码非 0 说明未通过。

⚠️ **调用 bash 工具执行本命令时，建议 `timeout` 传较大值（如 `600`
或 `-1`）**：大场景数多、视频分辨率高时统一规格 + 拼接的本地 ffmpeg
计算耗时可能超过默认超时。

### Step 2：交付确认

同 v1（见下方"v1 Step 2"），校验 JSON 里 `"ok": true` 且 `errors` 为
空即可向用户展示最终产物；`"ok": false` 时按 `errors` 逐条排查，常见
原因和排查方法与 v1 一致（时长/分辨率/比特率三类）。

**产物**：`video.mp4`（最终交付物，"小说转视频"六段流程到此结束）。

### v2 常见问题

1. **合成前直接报错"缺少已合成视频的大场景"**：说明还有大场景没有
   跑完 `novel-scene-video-generator` 的 `compose_macro_scene.py`，
   或者跑了但校验没通过导致 `macro_scenes.yaml` 里 `status` 还停留在
   `planned`。回上游补齐，不要用 `--allow-missing-macro-scenes` 绕过
   ——该参数只用于明确知情并接受成片缺某个大场景的特殊场景。
2. **`fade` 模式下总时长比预期略有出入**：ffmpeg 帧对齐会带来
   零点几秒级别的误差，校验容差是 2 秒，属于正常范围；如果差异明显
   超过转场时长的整数倍，检查是否有大场景视频本身时长和
   `scene_detail.yaml` 里的 `duration_sec` 之和不一致（回
   `novel-scene-video-generator` 用 `check_clips_v2.py` 排查）。
3. **封面没有生效**：确认 `<output_dir>/global/assets/cover.png` 是否
   存在（`novel-asset-generator` 里封面是可选产物），确认没有误加
   `--no-cover`。

---

## v1 版本：`compose_novel_video.py`（已归档，仅供历史参考）

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
3. **中文字体渲染异常（方框/乱码）**：检查默认字体路径（`FONT_PATH`
   常量，Windows 下为 `C:\Windows\Fonts\msyh.ttc`）指向的文件是否存在、
   是否支持中文字形；非 Windows 环境不需要改源码，直接加
   `--font-path <本地字体路径>` 覆盖即可（例如 Linux 上的
   `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）。
4. **封面没有生效**：确认 `<output_dir>/assets/cover.png` 是否存在
   （`novel-asset-generator` 里封面是可选产物，没生成属于正常情况，
   脚本会打印提示后正常跳过，不算错误）；确认没有误加 `--no-cover`。
