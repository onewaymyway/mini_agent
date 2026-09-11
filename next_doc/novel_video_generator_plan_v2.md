# 小说转视频 v2：长篇支持 + 大场景分层 + 多角色配音

> 本文档是对 `novel_video_generator_plan.md`（v1，已实现且测试通过的
> 4-skill 版本）的**结构性升级**，取代 v1 作为后续实施依据。v1 文档保留
> 作为历史参考（其中 `mv-generator` 复用手法、校验脚本"步步落盘+步步
> 校验"的规范，v2 全部沿用，不再重复说明）。
>
> 触发需求：v1 只支持短篇、单角色旁白、扁平场景列表，无法支撑长篇小说、
> 无法区分角色对话音色、单个"场景"粒度太粗不便于分工生成。v2 引入
> 三层结构（全局资源 → 大场景 → 小场景）解决这些问题。

---

## 0. 结论先行

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 整体结构 | 三层：全局角色/地点库（跨大场景持续追加）→ 大场景（按剧情切分，`macro_scene`）→ 小场景（`micro_scene`，对应一个实际生成的视频 clip，≤12 秒硬限） |
| 2 | Skill 数量 | 6 个（v1 的 4 个基础上拆出 2 个）：`novel-entity-extractor` / `novel-macro-scene-planner` / `novel-scene-detail-planner` / `novel-asset-generator` / `novel-scene-video-generator` / `novel-video-composer` |
| 3 | 大场景切分粒度 | Agent 按"剧情转折/时间地点跳变"自动切；硬约束：单个大场景原文 **≤1000 字** 且预估口播 **≤2 分钟**，超限强制在场景内部找转折点继续细分；切分结果（含原文片段本身）落盘 `macro_scenes.yaml`，供下游直接使用 |
| 4 | 对话文案来源 | **严格从原文摘录**，不允许 Agent 改写/补写台词；校验脚本对每条对话文本做子串匹配，匹配不到原文判定"疑似臆造"，直接打回 |
| 5 | 角色配音 | 角色抽取时新增 `voice_profile`（音色描述），TTS 阶段按角色分配不同音色（CosyVoice 零样本克隆 / edge-tts 按风格描述映射内置音色） |
| 6 | 大场景间转场 | `novel_project.json.transition_mode`：`cut`（默认，硬切）/ `fade`（黑场淡入淡出，时长可配，默认 0.5s）；仅作用于大场景之间，大场景内部小场景拼接固定硬切 |
| 7 | 资源回补校验 | Skill3（detail planner）生成每个大场景的小场景规划后，立即校验角色/地点引用是否都在全局库里存在且已有素材；缺失则触发 Skill1/Skill4 补抽取/补生成，再重新校验，不通过不进入 Skill5 |

---

## 1. 目录结构

```
novel_output/小说名_20260911/
├── novel_project.json              # 全局配置：篇幅/时长/TTS方案/transition_mode等
├── global/                         # 全局资源，跨大场景共享，只有一份
│   ├── characters.json             #   角色库，含 voice_profile 字段
│   ├── locations.json              #   地点库
│   └── assets/
│       ├── character_*.png
│       └── location_*.png
├── macro_scenes.yaml               # 大场景清单：见 §2，Skill2 产物 → Skill3 逐条回写 status
├── macro_scene_01/                 # 每个大场景一个独立工作目录，互不干扰，可独立重跑
│   ├── scene_detail.yaml           #   本大场景的小场景规划（旁白+对话文案、引用校验）
│   ├── audio/
│   │   ├── narration_seg_*.wav     #   旁白音轨
│   │   └── dialogue_<char>_*.wav   #   角色对话音轨，文件名含角色 id
│   ├── clips/
│   │   └── micro_scene_*.mp4       #   每个小场景独立生成的视频片段
│   └── macro_scene_01.mp4          #   本大场景合成结果（clips拼接+音轨混合）
├── macro_scene_02/
│   └── ...（结构同上）
└── video.mp4                       # 最终产物：所有 macro_scene_*.mp4 按 transition_mode 拼接
```

原则：
- 全局资源发现缺失时只回写 `global/`，不允许各大场景目录建私有副本；
- 大场景目录自包含：重新配音/重新合成某个大场景不影响其他大场景已完成产物；
- 两级合成：小场景 clip → 大场景视频（本级处理音轨混合，含旁白+对话拼接）→ 最终视频（大场景间拼接/转场，不再处理音轨细节）。

---

## 2. Skill 1：`novel-entity-extractor`（全局角色/地点抽取，支持长篇）

### 输入
小说全文（长/短篇均可）、目标总时长/篇幅范围。

### 流程
- 长篇按章节/固定长度分块喂给 LLM 通读；
- 每块抽取角色（含 `voice_profile` 音色描述：性别/年龄段/音色特点，如"中年男声，低沉沙哑"）与地点；
- 与已有 `global/characters.json`/`locations.json` **增量合并去重**（同名/别名归并到同一 id，逻辑沿用 v1 已实现的合并规则，扩展成多轮增量而非一次性）；
- 后续 Skill3 发现某大场景引用了未抽取的角色/地点时，本 skill 支持**单点补抽取**模式（只处理指定原文片段，追加到全局库，不重跑全文）。

### 产出
`global/characters.json`（新增 `voice_profile` 字段）、`global/locations.json`。

### 校验
沿用 v1 `check_narration_draft.py` 里角色/地点去重与 id 校验部分，新增：每个角色是否都有非空 `voice_profile`。

---

## 3. Skill 2：`novel-macro-scene-planner`（大场景切分）

### 输入
小说全文 + `global/characters.json`/`locations.json`（供标注引用）。

### 流程
1. 按"剧情转折/时间地点跳变"识别切分点；
2. 每个大场景校验：原文字数 ≤1000、预估口播时长（字数/4.5）≤120 秒，超限则在该大场景内部继续找转折点二次切分；
3. 落盘 `macro_scenes.yaml`，**原文片段全文保留**（不是引用范围），供 Skill3 直接使用。

### 产出
`macro_scenes.yaml`：
```yaml
macro_scenes:
  - id: macro_01
    title: "青石客栈初遇"
    source_span: "第1章 第1-3段"
    raw_text: "...(逐字保留)"
    summary: "一两句话梗概"
    estimated_duration_sec: 95
    char_count: 480
    uses_characters: [char_01, char_02]
    uses_locations: [loc_01]
    status: pending        # Skill3 处理完回写 planned
```

### 校验脚本 `check_macro_scenes.py`
- 每条 `char_count≤1000`、`estimated_duration_sec≤120`；
- `uses_characters`/`uses_locations` 均能在全局库中找到；
- 所有 `raw_text` 拼接起来应覆盖原文（无遗漏、无重叠，允许过渡性文字轻微取舍但需要总体覆盖率校验，阈值待实现时定）。

不通过 → 回到步骤 1/2 调整，不进入 Skill3。

---

## 4. Skill 3：`novel-scene-detail-planner`（单大场景详细规划）

### 输入
单个 `macro_scene`（从 `macro_scenes.yaml` 按 id 取一条）+ 全局角色/地点库。

### 流程
1. 把该大场景的 `raw_text` 切成小场景（micro_scene，对应最终一个视频 clip，控制在 4-12 秒口播时长，规则同 v1）；
2. 每个小场景用 `content_blocks` 区分旁白/对话：
   ```yaml
   content_blocks:
     - type: narration
       text: "..."
       speaker: null
     - type: dialogue
       text: "..."          # 必须是原文逐字摘录
       speaker: char_01
   ```
3. 标注每个小场景引用的角色/地点、镜头描述；
4. **引用完整性校验**：所有引用是否在全局库中存在且已有 `asset_path`；缺失 → 调用 Skill1 补抽取该片段涉及的新角色/地点 → 调用 Skill4 补生成对应素材 → 重新执行本步骤；
5. **对话真实性校验**：每条 `dialogue.text`（去标点空白后）必须能在本大场景 `raw_text` 中找到子串匹配，找不到判定失败，要求 Agent 重新从原文摘录（不允许改写）。

### 产出
`macro_scene_XX/scene_detail.yaml`，`macro_scenes.yaml` 对应条目 `status` 回写 `planned`。

### 校验脚本 `check_scene_detail.py`
- 引用完整性（角色/地点 id + asset_path 是否存在）；
- 对话子串匹配；
- 小场景时长估算是否在 4-12 秒区间（超限走 v1 已有的拆分/合并规则）。

不通过 → 不进入 Skill4/5。

---

## 5. Skill 4：`novel-asset-generator`（素材 + 配音，按需/增量执行）

在 v1 基础上扩展：
- 角色/地点定妆图生成逻辑不变（复用 `gen_image_with_text`），改为**按需触发**（支持只补生成 Skill3 回补出的新增角色/地点，不用每次全量重跑）；
- TTS 部分按 `content_blocks` 逐条生成：
  - `type: narration` → 走 `novel_project.json.tts` 配置的默认音色（旁白音）；
  - `type: dialogue` → 按 `speaker` 对应角色的 `voice_profile` 选择音色（CosyVoice 零样本克隆时可用参考音频；edge-tts 兜底时维护一张"风格描述 → edge-tts voice 名"映射表）；
- 产出音频文件落到对应 `macro_scene_XX/audio/` 下，文件名区分 `narration_seg_*.wav` / `dialogue_<char_id>_*.wav`；
- 每个小场景内 `content_blocks` 对应音频按顺序生成后，还需计算**该小场景拼接后的总时长**（多段音频首尾相接，不叠加），回填 `scene_detail.yaml` 对应小场景的 `duration_sec`（真实值，供 Skill5 生成视频用）。

### 校验脚本 `check_assets_and_audio_v2.py`
在 v1 `check_assets_and_audio.py` 基础上新增：
- 每个小场景的 `content_blocks` 是否都生成了对应音频文件；
- 该小场景 `duration_sec`（拼接后）是否已回填且非空。

---

## 6. Skill 5：`novel-scene-video-generator`（小场景视频生成 + 大场景内合成）

### 流程（在 v1 Skill3 基础上加一层）
1. 沿用 v1 逻辑，对 `scene_detail.yaml` 里 `status: pending` 的小场景逐个生成 `clips/micro_scene_*.mp4`；
2. 单个大场景内全部小场景 clip 就绪后，**在本 skill 内先合成"大场景视频"**：clips 按顺序硬切拼接 + 音轨（旁白/对话已按小场景拼接好）对齐混合 → 产出 `macro_scene_XX/macro_scene_XX.mp4`；
3. `macro_scenes.yaml` 对应大场景 `status` 回写为 `done`。

### 校验脚本
沿用 v1 `check_clips.py` 逻辑校验小场景 clip 完整性；新增一项校验大场景合成视频的时长是否约等于该大场景所有小场景时长之和。

---

## 7. Skill 6：`novel-video-composer`（最终合并）

### 输入
所有 `status: done` 的 `macro_scene_XX/macro_scene_XX.mp4` + `novel_project.json`。

### 流程
- 按 `macro_scenes.yaml` 顺序拼接所有大场景视频；
- 按 `transition_mode`：
  - `cut`：直接拼接（同 v1 硬切逻辑）；
  - `fade`：每两个大场景之间插入可配置时长（默认 0.5s）的黑场淡入淡出；
- 可选封面片段逻辑沿用 v1（替换第一帧前几秒，不改变总时长）；
- 本版仍不接 BGM（`bgm_enabled` 恒 false，理由同 v1）。

### 产出与校验
`video.mp4`，校验逻辑沿用 v1 Skill4（`ffprobe` 总时长/比特率/分辨率校验），新增：总时长应约等于"各大场景视频时长之和 + 转场时长"（若 `transition_mode=fade`）。

---

## 8. 实施状态

- [x] Skill 1：`novel-entity-extractor`（从 v1 `novel-scene-planner` 中拆出抽取部分，支持长篇分块+增量合并+单点补抽取；SKILL.md + `scripts/check_entities.py` + README.md；已测试：id重复/字段缺失报错、修复后通过 两类用例均验证正确）
- [x] Skill 2：`novel-macro-scene-planner`（新增；SKILL.md + `scripts/check_macro_scenes.py` + README.md；已测试：引用不存在地点id报错/修复后通过、覆盖率检查 均验证正确）
- [x] Skill 3：`novel-scene-detail-planner`（新增，含引用回补循环 + 对话子串校验；SKILL.md + `scripts/check_scene_detail.py` + README.md；已测试：素材缺失报错→修复后通过、臆造对话子串匹配报错 三类用例均验证正确）
- [x] Skill 4：`novel-asset-generator` v2（在 v1 基础上扩展按需触发 + 角色差异化配音 + content_blocks 拼接；新增 `scripts/voice_mapping.py` + `scripts/synthesize_scene_audio.py` + `scripts/check_assets_and_audio_v2.py`，v1 脚本保留仅供历史参考；已测试：voice_mapping 关键词映射、check_assets_and_audio_v2 缺配音文件报错→修复后通过）
- [x] Skill 5：`novel-scene-video-generator` v2（在 v1 基础上新增大场景内合成层；
      新增 `scripts/generate_scene_videos_v2.py`[遍历 macro_scene_*/scene_detail.yaml，
      按 --macro-id/--micro-id 双重过滤批量/定向生成 micro_scene clip，逐大场景
      独立计轮次重试，完成后回写各 micro_scene 的 status] + `scripts/compose_macro_scene.py`
      [新增：单大场景内把 content_blocks 对应的旁白/对话 wav 按序拼接、micro clip
      按 duration_sec 独立缩放拼接、按 micro_scene 拼出的字幕文案（对话用「」包裹）
      渲染字幕，合成 macro_scene_XX.mp4，成功后回写 macro_scenes.yaml 对应大场景
      status=done，校验不通过则不回写] + `scripts/check_clips_v2.py`[校验 micro_scene
      clip 完整性 + 已合成大场景视频时长一致性]；v1 脚本保留仅供历史参考；
      已测试：用合成 ffmpeg testsrc 素材端到端验证 compose_macro_scene 正常合成
      /status 回写/check_clips_v2 通过、cut 模式总时长校验、fade 模式转场时长
      校验、--allow-missing-macro-scenes 跳过逻辑，共 13 项断言全部通过。
      另需在 SKILL.md 中新增一步「Agent 手写 prompt_en/video_mode」——
      novel-scene-detail-planner 产出的 scene_detail.yaml 不含画面 prompt 字段，
      需要 Agent 在跑生成脚本前结合 visual_hint+art_style 手写好，脚本本身遇到
      空 prompt_en 会直接报错，不代为生成）
- [x] Skill 6：`novel-video-composer` v2（输入改为大场景视频 + 转场选项；新增
      `scripts/compose_final_video_v2.py`：统一规格（分辨率/帧率/采样率）后按
      transition_mode 拼接所有 status=done 的 macro_scene_XX.mp4——cut 直接拼接，
      fade 在每两个大场景间插入可配置时长的黑场淡入淡出（前一个大场景尾部淡出、
      后一个头部淡入），总时长按预期增加 (大场景数-1)×transition_duration；封面
      效果沿用 v1（挤压/替换首个大场景视频前几秒，音轨保留不受影响）；末尾内置
      ffprobe 时长/比特率/分辨率校验；v1 脚本保留仅供历史参考；已测试：cut/fade
      两种转场时长校验、封面应用、--allow-missing-macro-scenes 跳过未就绪大场景
      均验证正确，与 Skill5 共用同一份冒烟测试脚本全部通过）
- [x] 各 skill 目录 README 同步更新（`novel-scene-video-generator`/
      `novel-video-composer` 的 `SKILL.md`/`README.md` 均已改为"v2 当前使用 /
      v1 已归档仅供参考"的结构，与 `novel-asset-generator` 的既有写法一致）
- [x] 测试用例（`test_cases/`）新增 `novel_video_v2_composer_smoke_test.py`：
      不需要 `AGNES_API_KEY` 的离线冒烟测试，覆盖 v2 Skill5（大场景内合成+
      校验）与 Skill6（cut/fade 转场、封面、缺失大场景跳过）共 13 项断言，
      本地全部跑通。**尚未覆盖**：Skill1-4（实体抽取/大场景切分/详细规划/
      多角色配音）的长篇端到端真实流程测试，需要真实 LLM 推理 + `AGNES_API_KEY`，
      留待后续按 `novel_video_generator_testing_guide.md` 的思路补一份 v2 专用
      测试指南（含长篇/多大场景/多角色对话配音场景）时一并覆盖。

## 9. 实施状态小结

六个 skill（`novel-entity-extractor` / `novel-macro-scene-planner` /
`novel-scene-detail-planner` / `novel-asset-generator` /
`novel-scene-video-generator` / `novel-video-composer`）已全部实现，
"小说转视频 v2"整条流程（全局实体抽取→大场景切分→单大场景详细规划→
素材+差异化配音→小场景视频生成+大场景内合成→最终合成+转场）端到端
可用，脚本层面的合成/拼接/转场逻辑已通过离线冒烟测试验证。

后续如需扩展，建议按优先级：
1. 补一份覆盖长篇小说、多大场景、多角色对话配音的 v2 专用端到端测试
   指南（真实调用 `AGNES_API_KEY`/CosyVoice/edge-tts），类似 v1 的
   `novel_video_generator_testing_guide.md`；
2. `voice_mapping.py` 的音色映射精细化（目前只按性别+年龄段两个维度，
   见 `novel-asset-generator` SKILL.md「已知限制」）；
3. BGM 接入（ACE-Step，显存要求较高，独立评估）；
4. 角色人脸参考照片复用入口（CosyVoice 零样本克隆理论上可做到"每个
   角色独一无二"的音色，目前未实现参考音频采集入口）。

> 本文档记录整体方案与目录/文件契约；具体实施每完成一个 skill，会在此
> 状态表打勾，并同步更新对应 skill 目录下的 `SKILL.md`/`README.md`。
