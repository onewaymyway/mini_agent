# 阶段8：最终拼接与转场

**依赖脚本**：`compose_final_video_v2.py`。

## 输入

`macro_scenes.yaml`（所有 `status: done` 的记录）+ 对应
`macro_scene_XX/macro_scene_XX.mp4` + `novel_project.json`
（`aspect_ratio`/`transition_mode`/`transition_duration_sec`/`cover`）
+ 若 `cover.enabled == true`，还有 `global/assets/cover.png`。

## 前置检查

确认所有大场景都已经用阶段7合成出 `macro_scene_XX.mp4`、且
`macro_scenes.yaml` 对应条目 `status` 已回写为 `done`（可用
`check_project_state.py` 确认）。脚本自身也会检查每个大场景是否有对应
的已合成视频文件，缺了默认直接拒绝合成并报错退出。

## Step 1：合成

```bash
python .claude/skills/novel-video-studio/scripts/compose_final_video_v2.py \
  <output_dir>
```

- 默认输出到 `<output_dir>/video.mp4`，可用 `--output` 指定其它路径；
- **转场模式** `--transition-mode cut|fade`：不传则读取
  `novel_project.json.transition_mode`（默认 `cut`）：
  - `cut`：硬切拼接，总时长 = 各大场景视频时长之和；
  - `fade`：每两个大场景之间插入黑场淡入淡出，时长用
    `--transition-duration`（不传读 `transition_duration_sec`，默认
    0.5 秒），会增加总时长：总时长 = 各大场景时长之和 +
    `(大场景数-1) × transition_duration`；
- **仅当** `novel_project.json.cover.enabled == true` **且**
  `global/assets/cover.png` 存在时，才在最前面**新增一段独立的封面
  片段**（不是替换第一个大场景的画面）——拼接顺序是
  `[封面片段] + 大场景1 + 大场景2 + ...`，**总时长会增加**封面片段
  的时长，音轨用静音（不借用第一个大场景的对白/旁白音轨，避免封面
  画面撞上台词声音）；临时不需要封面效果可以加 `--no-cover`（覆盖
  `cover.enabled`，只影响这一次调用）；封面时长优先读
  `novel_project.json.cover.duration_sec`，`--cover-duration` 可
  覆盖（默认 3 秒，**不再** clamp 到大场景时长的比例——新逻辑下封面
  片段和大场景时长无耦合关系）；
- 合成前会先统一每个大场景视频的分辨率/帧率/采样率，避免不同批次生成
  的大场景视频参数不一致导致拼接失败；
- 本版不接 BGM（`bgm_enabled` 恒为 `false`）；
- 末尾**自动校验交付**：`ffprobe` 检查总时长（应约等于"各大场景时长
  之和 [+转场时长] [+封面时长，若启用]"，差异 > 2 秒记为错误）、
  比特率（< 1 Mbps 记为警告）、分辨率是否与 `aspect_ratio` 推导出的
  目标一致，结果以结构化 JSON 打印到 stdout，退出码非 0 说明未通过。

⚠️ **调用 bash 工具执行本命令时，建议 `timeout` 传较大值（如 `600`
或 `-1`）**：大场景数多、分辨率高时统一规格+拼接的本地 ffmpeg 计算
耗时可能超过默认超时。

**调用示例（system-prompt 模式工具调用格式，直接照抄，只替换
`<output_dir>`）**：

```
<tool_use>
{"name": "bash", "input": {"command": "python .claude/skills/novel-video-studio/scripts/compose_final_video_v2.py <output_dir>", "timeout": -1}}
</tool_use>
```

**关于时长对齐**：本步骤只是把各大场景已经合成好的
`macro_scene_XX.mp4` 硬切/淡入淡出拼接起来，**不做任何慢放/快放**——
真正解决"单个 clip 实际生成时长与规划不一致"的缩放动作已经在阶段7
`compose_macro_scene.py` 里逐 `micro_scene` 完成（见
`06_scene_video_generation.md` Step 4），到本步骤时每个大场景视频的
时长应该已经等于其内部所有 `micro_scene.duration_sec` 之和。如果这里
校验出总时长对不上，说明问题出在阶段7（某个大场景合成时缩放/拼接有
误差），要回阶段7用 `check_clips_v2.py` 排查，而不是尝试在本步骤加
整体缩放去补偿——那样会让本步骤新引入的时间轴误差和阶段7已经处理过
的误差混在一起，问题定位会更难。

## Step 2：交付确认

校验 JSON 里 `"ok": true` 且 `errors` 为空 → 向用户展示最终产物路径、
总时长、大场景数量；若 `warnings` 非空（如比特率偏低），如实告知，
不隐瞒。`"ok": false` → 按 `errors` 逐条排查：
- 总时长对不上：多半是某大场景视频本身时长和 `scene_detail.yaml` 里
  `duration_sec` 之和不一致，回阶段7用 `check_clips_v2.py` 排查；
- 分辨率不一致：检查 `aspect_ratio` 字段是否被误改；
- 比特率过低：检查合成过程的编码参数。

**产物**：`video.mp4`（最终交付物，流程到此结束）。

## 常见问题

1. **合成前直接报错"缺少已合成视频的大场景"**：说明还有大场景没跑完
   阶段7的 `compose_macro_scene.py`，或跑了但校验没通过导致
   `macro_scenes.yaml` 里 `status` 还停留在 `planned`。回上游补齐，
   不要用 `--allow-missing-macro-scenes` 绕过——该参数只用于明确知情
   并接受成片缺某个大场景的特殊场景；
2. **`fade` 模式下总时长比预期略有出入**：ffmpeg 帧对齐会带来零点几秒
   误差，校验容差是 2 秒，属正常范围；差异明显超过转场时长整数倍时，
   回阶段7排查；
3. **封面没有生效**：确认 `novel_project.json.cover.enabled` 是否为
   `true`、`global/assets/cover.png` 是否存在，确认没有误加
   `--no-cover`；
4. **总时长比预期多了几秒**：先看是不是启用了封面——封面片段现在是
   "新增"而不是"替换"，总时长必然会比"各大场景时长之和"多出封面
   时长，这是预期行为，不是 bug；
5. **只想加封面，不想动已经做完的成片其它部分**：不需要重跑本阶段
   之前的任何流程，见 `references/revision_and_rollback.md`
   §事后补建封面。

## 完成之后

`video.mp4` 交付后，如果用户又提出修改意见（哪怕只是想调个别镜头/
台词），不要直接在 `video.mp4` 上做局部编辑，按
`references/revision_and_rollback.md` 的流程回退到对应阶段重新生成，
再重新走一次阶段8合成全新的 `video.mp4`。
