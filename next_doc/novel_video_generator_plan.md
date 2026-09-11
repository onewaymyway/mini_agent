# 小说转视频：拆分为 4 个独立 skill 的方案

> 聚焦范围：新增 4 个 `.claude/skills/` 目录——`novel-scene-planner`（场景拆分+
> 角色提取）、`novel-asset-generator`（角色/场景素材+旁白配音）、
> `novel-scene-video-generator`（单场景视频生成）、`novel-video-composer`
> （最终合成）。复用已有 `gen_image_with_text`/`gen_video_with_text`
> （Agnes API）；新增本地 TTS 依赖（CosyVoice 本地优先 + edge-tts 在线兜底）。
> 参考模板：`.claude/skills/mv-generator/`（歌词转 MV，结构高度相似，"步步
> 落盘 + 步步校验脚本"的规范全部沿用）。
>
> 触发背景：`mv-generator` 解决的是"长文本 → 分场景 → 画面生成 → 时间轴
> 对齐 → 合成"的问题，但驱动时间轴的是**已有的歌声音频**；小说没有现成
> 音频，需要自己生成旁白配音，且原文往往远超一支视频能承载的篇幅，需要
> 先做"取舍/浓缩"。这些差异决定了不能简单复制 `mv-generator`，而是拆成
> 4 个各司其职、靠磁盘文件传递数据的独立 skill——任何一步都能单独重跑/
> 断点续跑，不依赖对话上下文。

---

## 0. 结论先行

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 拆几个 skill | 4 个：场景规划、素材+配音、单场景视频生成、最终合成（各自独立，靠磁盘文件传递） |
| 2 | TTS 方案 | 本地 **CosyVoice**（默认，支持零样本音色克隆，为后续角色差异化配音留口子）；装不上/跑不通时退化到 **edge-tts**（在线，零本地模型依赖） |
| 3 | BGM | **本版不做**（ACE-Step 依赖 ≥8GB 显存，环境要求明显高于其余部分，留作后续可选升级） |
| 4 | 跨 skill 文件契约 | `novel_project.json` / `characters.json` / `locations.json` / `narration_script.yaml`（草稿）→ `scene_plan.yaml`（正式版，回填时长/asset_path）/ `clips/*.mp4` / `video.mp4`，已与用户定稿 |
| 5 | 一致性规则 | 上游文件重新生成后，下游回填字段必须清空、强制重新生成，不允许新旧数据混存（同 `mv-generator` 的"改了 lyrics_timed.json 必须重跑 scene_plan.yaml"规则） |

全部 4 个 skill 共享同一个项目目录（约定名 `novel_output/<小说名>_<timestamp>/`），
目录结构与 `mv-generator` 的 `mv_output/` 平行：

```
novel_output/小说名_20260911/
├── novel_project.json        # Skill 1 产物：全局配置（篇幅/时长/TTS方案等）
├── characters.json           # Skill 1 产物 → Skill 2 回填 asset_path
├── locations.json            # Skill 1 产物 → Skill 2 回填 asset_path
├── narration_script.yaml     # Skill 1 产物：分段旁白文案草稿
├── scene_plan.yaml           # Skill 2 产物：正式场景规划（时长来自TTS）
│                              #   → Skill 3 回填 clip 状态
├── assets/
│   ├── character_*.png       # Skill 2 产物
│   ├── location_*.png        # Skill 2 产物
│   └── cover.png             # Skill 2 产物（可选）
├── audio/
│   └── segment_*.wav         # Skill 2 产物：旁白配音
├── clips/
│   └── scene_*.mp4           # Skill 3 产物
└── video.mp4                 # Skill 4 产物：最终交付物
```

---

## 1. Skill 1：`novel-scene-planner`（场景拆分 + 角色提取）

**唯一的创造性 LLM 步骤**，不能用固定脚本，需要 Agent 结合小说内容判断。

### 输入
- 小说文本（用户粘贴或给出文件路径，长/短篇均可）
- 目标视频时长/篇幅范围（用户指定，或 Agent 主动询问一次，参考 `mv-generator`
  Step 0 的"一次性问清楚、不阻塞流程"风格）

### 流程
1. 解析章节/段落结构，通读全文理解主题、情绪基调、叙事线索；
2. **抽取全局角色**：姓名/别名归并（同一角色不同称呼要合并成一个 id）、
   外貌描述、性格、与其他角色的关系；
3. **抽取反复出现的地点**（小说独有，MV 没有对应概念——地点复用率通常
   比 MV 里的"场景"更高，值得单独定妆）；
4. 确定全曲统一美术风格（同 `mv-generator` 的 `art_style` 字段，贯穿角色/
   地点/封面）；
5. 按篇幅预算做**取舍/浓缩**：判断哪些情节要可视化展开、哪些一笔带过甚至
   跳过，把选中的内容改写成适合口播的**旁白文案**（不是原文逐句照读）；
6. 把旁白文案切成场景段落草稿，每段标注引用的角色/地点 id、大致镜头描述。
   **此时不填精确时长**——时长要等 Skill 2 的 TTS 配音结果出来才能确定。

### 产出

`novel_project.json`：
```json
{
  "source_title": "小说名",
  "scope": {"mode": "chapter", "value": "第3章"},
  "target_duration_sec": 180,
  "art_style": "...(同 mv art_style，贯穿全流程)",
  "tts": {"engine": "cosyvoice", "fallback": "edge-tts", "voice": "..."},
  "bgm_enabled": false
}
```

`characters.json`：
```json
{
  "characters": [
    {"id": "char_01", "names": ["林然", "小林"], "description_zh": "...",
     "description_en": "...", "first_appear": "第1章", "relations": ["char_02:挚友"],
     "asset_path": null, "face_reference_id": null}
  ]
}
```

`locations.json`：
```json
{
  "locations": [
    {"id": "loc_01", "name": "青石客栈", "description_zh": "...",
     "description_en": "...", "asset_path": null}
  ]
}
```

`narration_script.yaml`（草稿级，无精确时长）：
```yaml
segments:
  - id: seg_01
    text: "旁白口播文案（改写后，不是原文逐句照读）"
    source_span: "第1章 第2-5段"
    uses_characters: [char_01]
    uses_locations: [loc_01]
    visual_hint: "镜头/构图/情绪的简要描述，供 Skill 2 写 prompt 时参考"
```

### 校验脚本 `check_narration_draft.py`
- 每个 `uses_characters`/`uses_locations` 引用都能在 `characters.json`/
  `locations.json` 里找到对应 id；
- 角色/地点无重复 id、无明显同名未合并（简单启发式检查，复杂情况仍需
  Agent 人工复核）；
- 旁白文案总长度粗估（按中文口播语速估算，约 4-5 字/秒）是否落在
  `target_duration_sec` 的合理区间（±30%），超出太多需要 Agent 回去重新
  取舍浓缩。

不通过 → 回到第 5/6 步调整，不进入 Skill 2。

---

## 2. Skill 2：`novel-asset-generator`（角色/场景素材 + 旁白配音）

### 输入
`characters.json` + `locations.json` + `narration_script.yaml` +
`novel_project.json`（读取 `art_style`/`tts` 配置）

### 流程

**2.1 角色/地点定妆图**（复用 `gen_image_with_text`，同 `mv-generator`
Step 4 手法）：
- 每个角色/地点生成一张定妆图，`description_en` 末尾融入 `art_style`；
- 角色可选人脸参考照片（同 mv 的 `face_reference_id` 机制，完全可选）；
- 生成后立即回填 `characters.json`/`locations.json` 里的 `asset_path`。

**2.2 旁白配音（TTS）**：
- 依次对 `narration_script.yaml` 每个 `segment` 的 `text` 跑 TTS：
  - 默认走本地 **CosyVoice**（需要提前准备好推理环境，类似 `mv-generator`
    的 `mv_env` conda 环境约定，可复用/新建专用环境如 `novel_tts_env`）；
  - CosyVoice 装不上/跑不通时（依赖缺失、无 GPU 等），自动降级到
    **edge-tts**（在线服务，无需本地模型，`pip install edge-tts` 即可，
    降级过程需要在终端明确提示用户"已降级到 edge-tts，音质/克隆能力
    会打折扣"）；
  - 产出 `audio/segment_<id>.wav`，用 `ffprobe`/`soundfile` 读取真实时长。
- 用每段真实音频时长生成正式版 `scene_plan.yaml`：
  ```yaml
  scenes:
    - id: scene_01
      segment_id: seg_01
      narration_audio: audio/segment_seg_01.wav
      duration_sec: 6.8          # 来自 TTS 真实时长
      uses_characters: [char_01]
      uses_locations: [loc_01]
      prompt_en: "...(融合 visual_hint + art_style 写成的画面 prompt)"
      video_mode: reference       # reference / keyframe / text，规则同 mv-generator
      status: pending              # Skill 3 回写 done/failed
  ```
- **时长超限处理**：若某段音频时长 > 12 秒（`gen_video_with_text` 单
  clip 硬限），在本步骤内直接把该 `narration_script` segment 拆成两个
  `scene`（复用同一段音频按时间切割，或要求 Skill 1 重新拆分该段文案
  改口播节奏更快/内容更短）——**不允许把超限问题留到 Skill 3**；
  若 < 4 秒，允许与相邻同角色/地点场景合并。

### 产出
- `assets/character_*.png` / `assets/location_*.png` / `assets/cover.png`（可选）
- `audio/segment_*.wav`
- 正式版 `scene_plan.yaml`（含精确 `duration_sec`）
- 回填后的 `characters.json` / `locations.json`

### 校验脚本 `check_assets_and_audio.py`（合并了 mv-generator 里
`check_assets.py` 的职责 + 新增音频检查）
1. 每个 `characters`/`locations` 条目是否都已回填 `asset_path`，文件存在
   且非空；
2. 每个 `scene` 引用的 `uses_characters`/`uses_locations` 是否都能在对应
   `asset_path` 找到文件；
3. 每个 `scene` 的 `narration_audio` 文件是否存在、`duration_sec` 是否
   在 4–12 秒范围内（不通过则说明拆分/合并逻辑有遗漏）；
4. 全部 `scene.duration_sec` 之和是否落在 `target_duration_sec` 的合理
   区间内。

不通过 → 回到本 skill 内部相应环节修复，重新跑校验，直到通过才进入
Skill 3（与 `mv-generator` "步步校验通过才能进下一步"规范一致）。

---

## 3. Skill 3：`novel-scene-video-generator`（单场景视频生成）

### 输入
`scene_plan.yaml` + 一个或多个 `scene_id`（不传则处理全部 `status: pending`
的场景，支持只处理某一场景，方便单独重跑/断点续跑）

### 流程
- 复用 `gen_video_with_text`，按 `video_mode` 选择 `reference`/
  `keyframe`/`text` 模式（规则同 `mv-generator`：`reference` 需要
  `uses_assets` 对应的定妆图已生成，否则自动降级为 `text`）；
- 批量场景由脚本 `generate_scene_videos.py`（沿用 `mv-generator` 同名
  脚本的能力：串行执行、限流自动切 key、单场景失败重试 3 次、断点续跑）
  循环调用；
- 每个场景生成完成后，把 `scene_plan.yaml` 对应条目的 `status` 回写为
  `done`/`failed`。

### 产出
`clips/scene_*.mp4` + 状态已回写的 `scene_plan.yaml`

### 校验脚本 `check_clips.py`（原样复用 `mv-generator` 同名脚本的逻辑，
改读字段名）
- 所有 `status` 应为 `done` 的场景是否都有非空的 `clips/<scene_id>*.mp4`；
- 不通过则回 Skill 3 重跑缺失场景（断点续跑，已成功的自动跳过），直到
  通过才能进入 Skill 4。

---

## 4. Skill 4：`novel-video-composer`（最终合成，独立拆出）

### 输入
`clips/` 全部就绪的 `scene_plan.yaml` + `audio/segment_*.wav` +
`novel_project.json`

### 流程（脚本 `compose_novel_video.py`，改造自 `mv-generator` 的
`compose_mv.py`）
1. **逐 scene 独立缩放**对齐 `scene_plan.yaml` 规划时长（同 MV 的
   "不做整体慢放"设计，保证画面切换时刻和场景规划一致）；
2. **拼接旁白音轨**：`audio/segment_*.wav` 按 `scene_plan.yaml` 顺序拼接
   成一条完整音轨（小说场景没有原始 mp3，音轨本身就是要拼接的旁白，和
   MV"混入原始 mp3"是相反方向的操作）；
3. **烧字幕**：按 `narration_script.yaml`/`scene_plan.yaml` 里每段的
   `text` 渲染字幕 PNG，逻辑同 MV 的歌词字幕渲染（每句一张 PNG，复用
   相同文本共用一张图）；
4. 可选封面片段（复用 `assets/cover.png`，做法同 MV："替换第一个场景
   前几秒"而非"插入"，不改变总时长）；
5. **本版不接 BGM**：`novel_project.json.bgm_enabled` 恒为 `false`，
   脚本按纯"旁白音轨 + 画面 + 字幕"合成，不预留额外的音乐混音分支
   （字段先占位，后续要加 ACE-Step 时改动只在本 skill 内部，不影响
   Skill 1-3）。

### 产出
`video.mp4`（最终交付物）

### 校验/交付
同 `mv-generator` Step 7：`ffprobe` 检查总时长（应约等于旁白音轨总时长）、
比特率是否正常（> 1 Mbps，排除 overlay 步骤质量崩溃的已知坑）、分辨率
是否与配置一致，向用户展示产物路径 + 场景数量 + 已知的人物一致性漂移
提示。

---

## 5. 待明确的实施细节（写方案时先记录，实现阶段逐个落地）

- CosyVoice 的具体推理接口封装（命令行脚本 vs Python 包直接调用）、
  专用 conda 环境名（暂定 `novel_tts_env`，装 `cosyvoice`/`edge-tts`/
  `soundfile`/`pyyaml` 等）；
- 角色人脸参考照片的复用（Skill 1 是否要像 `mv-generator` Step 0.5 那样
  主动问一次"要不要提供角色照片"，还是先不做，留到后续版本）；
- 字幕样式（字号/位置）默认值，是否需要横竖屏选择（`mv-generator` 有
  这个选项，小说转视频大概率也需要，建议 Skill 1 一并问一次，写进
  `novel_project.json`）。

---

## 实施状态

- [x] Skill 1：`novel-scene-planner`（SKILL.md + `check_narration_draft.py`，已测试：通过/引用不存在id报错/篇幅超区间报错 三类用例均验证正确）
- [x] Skill 2：`novel-asset-generator`（SKILL.md + `scripts/tts_engine.py`
      + `scripts/synthesize_narration.py` + `scripts/check_assets_and_audio.py`；
      已测试：edge-tts 网络路径验证了失败时的错误处理与信息透传、
      --segment-id 定向重跑、失败时保留旧记录不丢数据、缺角色/地点素材
      报错、补全后校验通过，等用例）
- [x] Skill 3：`novel-scene-video-generator`（SKILL.md +
      `scripts/generate_scene_videos.py`[改造自mv-generator，字段改用
      duration_sec/uses_characters+uses_locations，新增--scene-id定向
      重跑，生成后回写status] + `scripts/check_clips.py`；已测试：
      单元测试clamp_seconds/resolve_asset_paths/自动降级text/空prompt
      报错，端到端跑通stub gen_video_with_text全量生成+定向重跑+断点
      续跑+status回写+check_clips报错与通过）
- [x] Skill 4：`novel-video-composer`（SKILL.md + `scripts/compose_novel_video.py`，
      改造自 mv-generator 的 compose_mv.py：拼接旁白音轨（filter_complex concat，
      不用 concat demuxer 避免不同 wav 参数不一致）替代 mv 的"原始 mp3"，
      去掉 mv 的 fill_dur/空隙填补逻辑（novel 场景首尾相接，无需处理），
      保留整体误差兜底对齐；字幕改为按 scene.text 逐场景渲染（同文本去重，
      超宽自动换行），而非 mv 的逐句歌词队列；保留封面挤压/替换效果，
      去掉 mv 特有的歌名水印；末尾内置校验交付（ffprobe 总时长/比特率/
      分辨率校验，JSON 结构化输出）；已用合成 ffmpeg testsrc 素材做端到端
      冒烟测试验证：正常全量合成、`--allow-missing-clips` 借用相邻场景
      填补、默认缺 clip 时拒绝合成 三类用例均验证正确，输出时长/分辨率/
      封面效果符合预期)
- [x] 文档：各 skill 目录下 README/依赖说明（4 个 skill 目录下各新增
      `README.md`：依赖安装命令、输入/输出文件契约速查、脚本用法示例、
      与相邻 skill 的交接条件，内容与各自 `SKILL.md` 保持一致，供快速
      查阅不需要每次通读完整 SKILL.md）

## 实施状态小结

4 个 skill 已全部实现并测试完成，"小说转视频"整条流程（场景拆分→
素材+配音→分场景视频→最终合成）端到端可用。后续如需扩展（BGM 接入、
整本小说自动分卷、角色人脸参考照片），在「5. 待明确的实施细节」和各
skill「已知限制」章节基础上单独立项，不影响现有 4 个 skill 的稳定性。
