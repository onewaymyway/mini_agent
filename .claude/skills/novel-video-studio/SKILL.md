---
name: novel-video-studio
description: 把一篇小说自动转换成一支配旁白/角色对话配音的成片视频。一体化流程：全局角色地点抽取（含声音设定）→大场景切分→单大场景详细规划（旁白/对话拆分）→素材与差异化配音生成→小场景视频生成与大场景内合成→最终拼接转场。每个阶段的具体操作步骤放在 resources/ 下，进入某阶段前按需加载对应文件，完成后可从上下文中卸载。支持断点续跑、失败重试与 API Key 自动切换、以及用户对已生成内容提反馈后的定向回退重跑。当用户说"把这本小说做成视频"、"小说转视频"、"生成小说解说视频"时使用本 skill。
triggers: 小说转视频, 小说做视频, 小说解说视频, novel to video, 角色配音视频, 小说视频生成
---

# 小说转视频一体化 Skill (Novel Video Studio)

## 0. 这个 skill 怎么用

本 skill 把"小说转视频"的完整流程放在一个 skill 里，但**不要求你一次性
读完所有细节**。主文件（本文件）只讲两件事：

1. 整体流程长什么样、各阶段产物/状态的契约是什么、阶段之间怎么衔接；
2. 通用规则：什么时候必须跑校验脚本、失败了怎么办、用户要改内容时怎么
   联动重跑、API 报错怎么处理。

**每个阶段具体"这一步要做什么、跑哪个脚本、参数怎么传"的操作细节，
放在 `resources/` 目录下对应文件里，进入该阶段时才读，读完执行完这
一阶段就可以把这份细节从上下文里放下**（不需要的时候不用一直带着，
下次要用再读一次即可，文件不会变）：

| 阶段 | 说明 | 何时加载 |
|---|---|---|
| `resources/01_entity_extraction.md` | 全局角色/地点抽取 | 开始阶段 1，或阶段 3 触发单点补抽取时 |
| `resources/02_macro_scene_split.md` | 大场景切分 | 阶段 1 完成后，进入阶段 2 时 |
| `resources/03_scene_detail_planning.md` | 单大场景详细规划（小场景+对话拆分） | 处理某个 `status: pending` 的大场景时 |
| `resources/04_assets_and_audio.md` | 定妆图 + 差异化配音 | 大场景规划完（`status: planned`）后 |
| `resources/05_scene_video_generation.md` | 小场景视频生成 + 大场景内合成 | 配音回填 `duration_sec` 后 |
| `resources/06_final_compose.md` | 最终拼接与转场 | 所有大场景 `status: done` 后 |
| `resources/error_handling.md` | API 失败/限流/参数错误的处理规范 | 任何一次调用 `gen_image_with_text`/`gen_video_with_text`/配音脚本报错时 |
| `resources/revision_and_rollback.md` | 用户反馈修改内容后的联动重跑规则 | 用户对已生成内容提出修改意见时 |

不要在还没进入某个阶段之前就把该阶段的 resource 文件通读一遍——这样
会造成上下文浪费；也不要凭记忆猜测某阶段的脚本参数，进入该阶段前先
读对应文件。

配套脚本统一放在 `scripts/`（不分子目录，直接按文件名调用）：

```
.claude/skills/novel-video-studio/scripts/
├── check_entities.py             # 阶段1校验
├── check_macro_scenes.py         # 阶段2校验
├── check_scene_detail.py         # 阶段3校验
├── voice_mapping.py / tts_engine.py / synthesize_scene_audio.py
│                                  # 阶段4配音（synthesize_scene_audio.py 是入口）
├── check_assets_and_audio_v2.py  # 阶段4校验
├── generate_scene_videos_v2.py   # 阶段5生成小场景视频
├── compose_macro_scene.py        # 阶段5大场景内合成
├── check_clips_v2.py             # 阶段5校验
├── compose_final_video_v2.py     # 阶段6最终合成
├── check_project_state.py        # 通用：查看项目当前进度到哪一步、下一步做什么
└── invalidate.py                 # 通用：用户改内容后，联动清理下游产物+回退状态
```

角色/地点定妆图依赖 `gen_image_with_text` skill，视频生成依赖
`gen_video_with_text` skill，两者都是本 skill 的外部依赖（不重复实现，
按需调用即可）。

## 1. 整体流程与产物契约

```
阶段1 novel-entity-extractor（角色/地点抽取，含 voice_profile）
  → 阶段2 大场景切分（macro_scene，按剧情/时间地点切）
  → 阶段3 单大场景详细规划（micro_scene，旁白/对话拆分，逐场景处理）
  → 阶段4 素材+差异化配音（按需触发，逐场景/逐实体可增量）
  → 阶段5 小场景视频生成 + 大场景内合成
  → 阶段6 最终拼接转场
```

目录结构：

```
novel_output/小说名_20260911/
├── novel_project.json          # 全局配置（时长/画风/TTS方案/转场模式）
├── global/
│   ├── characters.json         # 角色库，含 voice_profile、asset_path
│   ├── locations.json          # 地点库，含 asset_path
│   └── assets/                 # character_*.png / location_*.png / cover.png
├── macro_scenes.yaml           # 大场景清单，status: pending→planned→done
├── macro_scene_01/
│   ├── scene_detail.yaml       # 本大场景的小场景规划
│   ├── audio/                  # narration_seg_*.wav / dialogue_<char>_*.wav
│   ├── clips/                  # micro_scene_*.mp4
│   └── macro_scene_01.mp4      # 本大场景合成结果
├── macro_scene_02/ ...
└── video.mp4                   # 最终产物
```

状态字段是阶段推进和"该不该回退"的唯一依据：
- `macro_scenes.yaml` 每条大场景：`pending`（未详细规划）→`planned`
  （规划完成、待生成/待合成）→`done`（大场景视频已合成且校验通过）；
- 每个大场景 `scene_detail.yaml` 里每个 `micro_scene`：`pending`→
  `done`/`failed`（视频 clip 生成结果）；
- 顶层 `video.mp4` 是否存在，代表整个项目是否已交付。

任何时候想知道"现在进度到哪、下一步该干什么"，跑：

```bash
python .claude/skills/novel-video-studio/scripts/check_project_state.py <output_dir>
```

输出里的 `overall_stage`/`next_actions`/`warnings` 直接告诉你该读哪个
`resources/*.md`、该处理哪些大场景，`warnings` 里如果出现"状态和磁盘
不一致"，先处理这个再继续往下走（通常是上次执行中途被打断导致的）。

## 2. 通用规则（贯穿所有阶段）

### 2.1 先落盘，再校验，通不过不进入下一阶段

每个阶段的产物写完立刻落盘（不等用户确认），然后跑该阶段对应的
`check_*.py` 脚本。**校验脚本退出码非 0，绝对不允许进入下一阶段**，
也不允许把状态字段推进（比如 `pending`→`planned`）。不通过时回到本阶段
内部修正，重新写文件、重新校验，直到通过。这是保证整条流水线不带着
"看似完成实则有缺陷"的产物往下传的唯一手段。

### 2.2 阶段间的"回补"是正常流程，不是异常

阶段3（详细规划）在处理某个大场景时，如果发现引用了阶段1没抽取到的
角色/地点，**不是报错终止**，而是：

1. 加载 `resources/01_entity_extraction.md` 的"单点补抽取"模式，只处理
   当前这段原文，把新角色/地点并入 `global/characters.json`/
   `locations.json`；
2. 加载 `resources/04_assets_and_audio.md`，只给这几个新增实体生成定妆图；
3. 回到阶段3继续规划，重新校验。

这个回补循环必须在阶段3内部闭环完成，不能把"引用了不存在的资源"这种
问题遗留到阶段4/5才发现——那样定位问题的成本高很多。类似地，阶段4如果
发现某个 micro_scene 配音后时长超过 12 秒硬限，也不是丢给阶段5硬着头皮
生成，而是回阶段3把这个小场景拆成两个，再重新走阶段4。

**判断"该往前回退几步"的通用原则**：定位到"是哪个阶段的产物不满足
下游契约"，就回到那个阶段修正，而不是在下游阶段里硬编码补丁绕过去。

### 2.3 长篇/多大场景：逐个处理，不要求一次性通关

阶段3-5 都是"以大场景为单位"处理的：`macro_scenes.yaml` 里有几个大场景
就要走几轮阶段3-5（阶段4/5支持 `--macro-id` 只处理指定大场景）。不需要
把所有大场景都推到同一阶段才继续，允许有的大场景已经 `done`、有的还
`pending`，用 `check_project_state.py` 随时查看整体进度。这样任何一个
大场景中途失败、需要重新调整，都不影响其它已完成的大场景。

### 2.4 API 调用失败

跑到 `synthesize_scene_audio.py`（配音）、`generate_scene_videos_v2.py`
（视频生成）、或直接调用 `gen_image_with_text`（定妆图）这几步时，
不可避免会遇到 API 报错（限流/参数错误/网络问题）。处理规范见
`resources/error_handling.md`，核心原则一句话说完：**限流类错误脚本会
自动切换 key 重试，不需要人工介入；非限流的参数类错误脚本重试几次后
会打印结构化错误直接放弃该条目并继续处理其它条目，需要 Agent 读懂错误
信息、去修对应的配置字段，然后只对失败的条目定向重跑，不要整体重跑，
也不要绕过错误直接忽略失败项**。

### 2.5 用户反馈要求修改内容

用户看了中间产物或成片后要求"这句台词改一下"/"这个角色形象换一个"/
"这段画面重新生成"，处理规范见 `resources/revision_and_rollback.md`。
核心原则一句话说完：**先改源头内容，再跑 `invalidate.py` 联动清空所有
依赖它的下游产物和状态，然后从被清空的那一层重新往后走，不允许只改
源头文件却不清理下游——那样下游文件会和新内容对不上（比如台词改了但
字幕、配音还是旧的）**。

## 3. 项目启动

新项目从"进入阶段1"开始，向用户确认目标时长/横竖屏后立即写
`novel_project.json`（细节见 `resources/01_entity_extraction.md`
Step 0），随后按上面的阶段顺序推进。用户如果已有跑了一半的项目目录，
先跑 `check_project_state.py` 定位断点，从 `next_actions` 指向的阶段
继续，不要重新从头开始。
