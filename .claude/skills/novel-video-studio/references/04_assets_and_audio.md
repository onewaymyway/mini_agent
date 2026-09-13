# 阶段4：素材 + 差异化配音生成

**依赖 skill**：`gen_image_with_text`（定妆图生成，必须）。
**依赖脚本**（本 skill `scripts/` 下）：
- `voice_mapping.py`：把角色 `voice_profile` 文字描述映射到具体 TTS
  音色（edge-tts 内置音色名 / CosyVoice 说话人名，规则式关键词匹配）；
- `synthesize_scene_audio.py`：批量给全部（或 `--macro-id` 指定的）
  大场景下所有小场景的 `content_blocks` 配音，回填 `duration_sec`；
- `check_assets_and_audio_v2.py`：校验素材+配音是否就绪，必须跑，不
  通过不能进入阶段5。

**外部依赖**：CosyVoice（可选，配 `novel_tts_env`）、edge-tts（兜底）、
ffmpeg/ffprobe、`AGNES_API_KEY`（定妆图）。API 失败处理见
`error_handling.md`。

产物路径：
```
novel_output/小说名_20260911/
├── global/
│   ├── characters.json / locations.json   # 本阶段回填 asset_path
│   └── assets/character_*.png / location_*.png
└── macro_scene_XX/
    ├── scene_detail.yaml   # 本阶段回填每个 micro_scene 的 duration_sec
    └── audio/
        ├── narration_seg_<micro_id>_<block序号>.wav
        └── dialogue_<角色id>_<micro_id>_<block序号>.wav
```

**本阶段默认按单个大场景触发，不要不带 `--macro-id` 跑全量**：跟着
SKILL.md §2.3 的循环，处理完某个大场景的阶段3规划后，配音只给这一个
大场景配（`--macro-id macro_XX`），配完直接进入阶段5生成这个大场景的
视频，而不是先把所有大场景的配音都配完再统一进入阶段5——用户要能在
看到第一个大场景成片的同时，其它大场景还没开始配音，这是正常状态，
不是"漏跑了"。

唯一例外是 Step 1 的定妆图：按 `asset_path` 是否已生成去重，处理某个
大场景时顺带给这个场景新出现的角色/地点生成一次即可，天然不需要、也
不应该攒到所有大场景一起生成。

## Step 1：角色/地点定妆图生成（按需）

扫描全部 `global/characters.json`/`locations.json`，找出 `asset_path`
仍为 `null` 的条目（新项目首次跑，或阶段3回补的新增实体），对每一项
调用：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
  --save-path <output_dir>/global/assets/character_<id>.png
```

地点同理，落到 `global/assets/location_<id>.png`。生成后**立即回填**
`global/characters.json`/`locations.json` 里对应的 `asset_path` 字段。
已有 `asset_path` 的条目跳过，除非用户明确要求重新生成某个角色定妆图
（此时用 `invalidate.py --global-entity <id>` 先清空再重新生成，见
`revision_and_rollback.md`）。

封面图（可选）逻辑同角色定妆图，落到 `global/assets/cover.png`，供
阶段6可选叠加。

## Step 2：按 content_blocks 给当前大场景配音

```bash
python .claude/skills/novel-video-studio/scripts/synthesize_scene_audio.py \
  <output_dir> --macro-id macro_01
```

- **默认带 `--macro-id` 只处理当前正在跑循环的这一个大场景**，不带
  `--macro-id` 会遍历全部 `macro_scene_*/scene_detail.yaml`——只有在
  用户明确要求"把所有场景的配音一起重新配一遍"这类全局操作时才这么用；
  正常按 SKILL.md §2.3 的逐场景循环推进时，永远只处理当前这一个大场景；
- 对该大场景下每个 `micro_scene` 的每个非空 `content_blocks[*].text`
  分别配音：
  - `narration` → 项目默认音色（`novel_project.json.tts.voice`）；
  - `dialogue` → 读取 `speaker` 对应角色的 `voice_profile`，用
    `voice_mapping.py` 解析出具体音色，不同角色自动配不同声音；
- 已存在且非空的音频默认跳过（断点续跑，读取真实时长参与求和），需要
  强制重新生成该场景全部音频时加 `--force`；
- 同一个 `micro_scene` 内全部 `content_blocks` 配音完成后，脚本自动
  把真实时长**求和**（多段音频首尾相接，不叠加），回填该 `micro_scene`
  的 `duration_sec`，覆盖写回 `scene_detail.yaml`；
- 终端会打印 `engine_usage`（多少段用了 `cosyvoice`，多少段降级到了
  `edge-tts`），一次性看清降级情况。

**时长真实性**：`duration_sec` 一律来自 `ffprobe`/`mutagen` 读出的
**真实**音频时长；两者都失败时脚本默认直接报错（该 block 记入
`errors`），不会再产出假数据静默写入 `scene_detail.yaml`（历史上这里
有过"按文件名字数估算"的 bug，已修复，见
`next_doc/novel_video_studio_fix_plan_v1.md` 问题1）。如果确实需要在
`ffprobe`/`mutagen` 都不可用的环境下先跑通流程，可显式加
`--allow-estimated-duration`，退回按**文本真实字数**（约4字/秒）估算，
返回结果里对应 block 会标记 `duration_estimated: true`，终端也会打印
提示——这是一个默认关闭的兜底开关，估算值和真实值不能混用，用完之后
应该尽快修好 `ffprobe`，用 `--force` 重新生成拿到真实时长。

**过程输出与超时**：配音涉及 edge-tts 在线请求（或 CosyVoice 本地推理），
是本 skill 里少数会耗时较久的步骤。脚本会逐 block 打印进度到 stderr
（引擎、用时、时长），命令挂着几分钟没有新日志属于异常，正常情况下应该
持续看到新行；同时两条合成路径都带了超时保护（edge-tts 默认
`--edge-tts-timeout 45` 秒，CosyVoice 默认 `--cosyvoice-timeout 60`
秒，均可调），超时会被记入 `errors`（CosyVoice 超时会先尝试降级到
edge-tts），不会导致整个脚本无限期挂起。详见
`references/error_handling.md`。

**时长超限处理**：跑完后若某个 `micro_scene.duration_sec > 12`
（`check_assets_and_audio_v2.py` 会报错），**不允许忽略**——回阶段3
把该小场景的 `content_blocks` 拆成两个 `micro_scene`（分别给新 id），
用 `invalidate.py --macro-id macro_01 --level detail` 清理旧状态后，
只重新跑受影响的这一个大场景。低于 4 秒只是 warning（建议与相邻小
场景合并，不强制）。

## Step 3：校验交付

```bash
python .claude/skills/novel-video-studio/scripts/check_assets_and_audio_v2.py \
  <output_dir> [--macro-id macro_01 ...]
```

检查：角色/地点定妆图完整性（所有被引用的都有 `asset_path`）、每个
`content_block` 是否都有对应音频文件、每个 `micro_scene.duration_sec`
是否已回填且落在 4-12 秒范围内，以及一条独立的**时长合理性**
warning——按文本量粗估理论时长，和实际 `duration_sec` 的比值明显偏离
（不足理论值40%或超过理论值250%）时提示，不依赖上面的时长真实性修复
是否生效，作为兜底防线单独发现"时长和文本对不上"这类数据异常；另外还
会自检 `ffprobe` 是否可用，不可用时给出 warning 提示时长真实性无法通过
时长本身验证。支持 `--macro-id`（可传多个，不传则查全部，参数风格对齐
`check_clips_v2.py`），方便按 §2.3 的逐场景循环定向校验当前大场景，不
必等所有大场景都配完音才能跑校验；不传时只扫描磁盘上**已经存在**的
`macro_scene_*/scene_detail.yaml`——还没跑到阶段3的大场景根本不会有
这个文件，所以校验结果天然只覆盖"已经规划到当前进度"的场景，不会因为
后面的大场景还没配音就报错。**退出码非 0 不允许交付阶段5**，按报错
逐条修复（补生成缺失定妆图、回 Step 2 用 `--macro-id`/`--force` 定向
重跑），重新跑校验直到通过；warning 不阻断，但时长合理性 warning 建议
结合上下文核实，不要习惯性忽略。

**向用户展示**：定妆图生成结果（本次新生成多少角色/地点，是否有失败
需要重试）、配音引擎使用情况（`engine_usage` 汇总，含
`estimated_duration_block_count` 如果非零）、校验通过结果 + 各大场景/
小场景总时长。

## 已知限制

- `voice_mapping.py` 目前是纯关键词规则映射，同一分类（如"青年男声"）
  下所有角色会用同一个 edge-tts 音色，无法做到"每个角色独一无二"；
  CosyVoice 零样本克隆理论上可以，但依赖参考音频采集入口（未实现）；
- 音色映射规则只覆盖性别+年龄段两个维度，"清亮/沙哑/沉稳"这类更细的
  音质描述暂不参与选择；
- CosyVoice 推理超时依赖 `ThreadPoolExecutor` 的 `result(timeout=...)`
  实现，Python 线程无法被强制中断，超时只是不再等待结果、判定为失败走
  降级，底层线程可能仍在后台跑一段时间，不是真正"杀掉"进程，正常环境
  影响很小，但如果频繁触发超时，说明本地推理环境（GPU/模型体积）需要
  排查，而不是持续依赖超时兜底。

## 下一步

进入阶段5，为每个 `micro_scene` 生成视频 clip，并在每个大场景内合成
出 `macro_scene_XX.mp4`。
