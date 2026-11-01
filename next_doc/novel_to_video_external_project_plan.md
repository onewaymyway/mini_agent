# 小说转短剧外部项目设计方案（novel_to_video）

> 状态：**Phase 0、Phase 1 已完成（2026-11-01），按 Phase 2→5 继续实施**。路线、章节粒度、人设来源、剧本来源、外部依赖、审核粒度、封面与转场等决策点已由用户确认（见第 2 节）；第 13 节未决小问题按推荐项采用。实施记录见第 14 节。
> 落地位置：`external_projects/novel_to_video/`（代码与项目说明）；本文件位于 `next_doc/novel_to_video_external_project_plan.md`。
> 依据：`external_projects/novel_writer`（导出格式、外部项目结构、挡位 G0–G5、Streamlit 界面范式）、`.claude/skills/novel-video-studio`（短剧生产流水线、外部 API key 池机制、校验脚本范式）、`mini_agent.workflow` 的 `skill_agent` 调用模式。

---

## 0. 一页结论

1. **输入**：`novel_writer` 导出的目录（`小说_YYYYMMDD_HHMMSS/`）。以它的 `设定/人物/角色/char_NNN.md`、`设定/世界观/地点详情/*.md` 作为**权威的全书级设定**，不二次抽取角色/地点。
2. **粒度三层**：
   - **全书级（一次）**：角色定妆图、地点定妆图、可选封面背景等公共素材；
   - **章节级（循环）**：每章先剧本化，再切分为若干小场景，逐小场景生成小视频，最后合成为该章的章节视频；
   - **全书级（收尾）**：所有章节完成后，统一拼接为全书视频；片头封面与章节间转场只在这一步处理。
3. **完全独立实现**，不以 skill 脚本为运行时依赖。LLM 创作步骤沿用 `skill_agent` / `FrameworkInvoker` 模式（与 novel_writer 一致）；图像/视频/TTS 的 key 池机制沿用 novel-video-studio 的做法，代码以独立模块重写。
4. **审核粒度**：界面上以**小场景**为确认单位（逐镜确认）。章节视频与全书视频作为汇总查看与最终确认。
5. **执行形态**：后台 worker + 工作目录内状态文件（可暂停/恢复/崩溃自愈），Streamlit 只展示与下指令。挡位 G0–G5 语义与 novel_writer 一致。
6. **默认保守**：G0 手动挡，逐小场景确认；全自动挡必须设置预算上限。

---

## 1. 背景与问题

- `novel_writer` 已能稳定产出长篇小说，导出为结构化目录（全书合稿 + 逐章正文 + 章节总结 + 设定库）。下一步的自然需求是把成书改编为短剧视频。
- `.claude/skills/novel-video-studio` 已形成一套"小说→剧本→角色/地点→定妆图→大场景→小场景→配音→视频→合成"的流水线，但它的定位是**单次在一个 skill 会话里跑完一本书**，缺少：
  - 面向多本书、多章节的**工作台式管理**；
  - 章节粒度的**可控循环**与逐小场景的人工确认；
  - 与 novel_writer 导出目录的**直接衔接**；
  - 后台长任务、断点续跑、挡位控制的**工程化承载**。
- 现有 skill 的强项（API key 池、限流切换、校验把关、一致性语义核查的思路、字段契约）值得吸收，但运行形态需要由外部项目重新组织。

**问题定义**：如何把 novel_writer 导出的成书，稳定地、可逐镜审核地、按章节循环地转换为一部短剧视频，并且在任何中间环节都能看到产物、提出修改、局部重跑。

---

## 2. 已确认的决策

| 议题 | 确认结果 |
|---|---|
| 路线 | 完全独立实现；LLM 步骤沿用 `skill_agent` / `FrameworkInvoker` 模式，参考 novel_writer |
| 章节粒度 | 一章 = 一个章节视频；全书最后拼接。章内大场景数量不固定，由切分阶段决定 |
| 公共设定范围 | 角色设定及各类公共设定资源以**全书为单位**，一次性准备 |
| 视频生成粒度 | 以章节为单位组织；每章切分为多个小场景，先生成小视频，再合成章节完整视频 |
| 收尾 | 所有章节完成后，提供专门的"全书合成"功能 |
| 章节范围 | 默认处理全部章节，章节数按导入目录实际数量确定（不预设为 50），允许勾选部分章节 |
| 角色/地点来源 | 沿用 novel_writer 导出的人设卡与地点文件作为权威输入；**实际创建视频时遇到库中不存在的角色/地点，再现场补建并补充素材** |
| 剧本来源 | 由 Agent 从章节正文生成剧本；章节总结不作为剧本输入 |
| 外部依赖配置 | 图像/视频/TTS 沿用 novel-video-studio 的 key 池机制（多 key 轮换、限流自动切换） |
| 审核粒度 | **每个小场景确认**（逐镜） |
| 封面与转场 | 统一放到最后的全书合成功能，不在章节流程中处理 |

---

## 3. 核心理念

1. **全书一次，章节循环，小场景为审核单位。** 公共素材（角色/地点定妆）跨章复用，保证形象一致；章节是生产与交付的节奏单位；小场景是人工把关的最小单位。
2. **导入即权威，不二次篡改设定。** 人设卡与地点文件直接来自 novel_writer，系统只做映射、补充缺失的视觉/声音字段，不重新改写角色性格与背景。
3. **先剧本，后分镜，再素材，最后视频。** 剧本是后续一切的契约，旁白/对话/说话人在剧本阶段一次性区分清楚，下游不再现场解析原文。
4. **确定性归代码，创造性归 Agent。** 落盘、校验、key 轮换、TTS、拼接、状态机由代码完成；剧本转换、分镜切分、提示词撰写、一致性语义核查由 Agent 完成，并以结构化结论文件交付，由代码做机械把关。
5. **每一层都可见、可改、可回退。** 任何产物落盘后即可在界面查看；修改意见必须关联到具体层级，并按依赖关系清理下游后局部重跑，不整章推倒。
6. **增量补素材而不是预先穷举。** 环境/角色素材允许在章节生成过程中按需补充，但补充必须走同一套生成与校验流程，并回写到全书级素材库。

---

## 4. 整体流程

### 4.1 全书级阶段（一次性，项目初始化时完成）

| 阶段 | 名称 | 执行者 | 产物 | 确认点 |
|---|---|---|---|---|
| G-0 | 导入与配置 | 代码 | `project.json`、章节清单 | 确认章节范围、目标时长/单章时长、横竖屏、TTS 方案 |
| G-1 | 人设与地点导入 | 代码 | `global/characters.json`、`global/locations.json`（由人设卡/地点文件映射生成） | 确认映射结果、缺失字段清单 |
| G-2 | 视觉与声音补全 | Agent | 为每个角色/地点补 `visual_anchor_en`、`voice_profile` | 逐条确认（可批量通过） |
| G-3 | 公共素材生成 | 图像 API | `global/assets/character_*.png`、`location_*.png` | 画廊式逐条通过/重生成 |

### 4.2 章节级阶段（循环，以章为单位，章内以小场景为审核单位）

对每一个章节 `chapter_NN`，依次执行：

| 步骤 | 名称 | 执行者 | 产物 | 审核点 |
|---|---|---|---|---|
| C-1 | 剧本转换 | Agent | `chapters/NN/script.md` | 章级确认（可选） |
| C-2 | 剧本校验 | Agent + 代码 | `chapters/NN/script_review.yaml` | 校验不过则回 C-1 |
| C-3 | 章节切分（大场景） | Agent + 代码 | `chapters/NN/scenes.yaml`（大场景清单，弹性时长） | 章级确认（可选） |
| C-4 | 小场景规划 | Agent + 代码 | `chapters/NN/shots.yaml`（每个小场景：画面提示、content_blocks、时长 4–12 秒） | **逐小场景确认前的必备步骤** |
| C-5 | 素材补全 | 代码 + 图像 API | 新增角色/地点定妆图、外观变体图 | 新增素材逐条确认 |
| C-6 | 配音 | TTS | `chapters/NN/audio/*.wav` | 随小场景确认一并展示 |
| C-7 | 提示词与一致性核查 | Agent + 代码 | `chapters/NN/prompts.yaml`、`consistency_report.yaml` | 核查不过则回 C-7 修正 |
| C-8 | 小场景视频生成 | 视频 API | `chapters/NN/clips/shot_NN.mp4` | **逐小场景确认（通过 / 重生成 / 修改意见）** |
| C-9 | 章节合成 | 代码（ffmpeg） | `chapters/NN/chapter_NN.mp4` | 章节级预览确认 |

章节内 C-4 到 C-8 按**小场景**推进：每个小场景完成视频后即进入确认，确认通过再进入下一个小场景；全部小场景通过后才执行 C-9。

### 4.3 全书收尾阶段

| 步骤 | 名称 | 产物 |
|---|---|---|
| F-1 | 章节检查 | 确认所有选中章节均已 `chapter_done` |
| F-2 | 封面（可选） | `global/assets/cover_bg.png`、`cover.png`（沿用 v7 方案：背景图与标题叠加分离） |
| F-3 | 全书合成 | `video.mp4`：`[封面片段]` + 各章节视频按顺序拼接，章节间可选转场 |
| F-4 | 成片校验与交付 | 时长、分辨率、音画同步的机械校验 |

### 4.4 补建角色/地点的分支

章节生成到 C-1/C-4 时，若剧本里出现的角色或地点在全书库中不存在：

1. 代码检测出缺失实体，列入"待补建"清单；
2. Agent 根据章节正文与相近章节的人设信息，**起草**一条新实体记录（不新增人设卡内容的主观性格，只补外观与声音所需字段），标记 `source: auto_added`；
3. 在界面上以"新增实体"卡片提示，用户确认后才写入全书库；未确认前该章节不得进入 C-5；
4. 写入后补生成定妆图，走 G-3 同一套校验；
5. 全书库的增量变更写入 `PROGRESS.md` 决策日志。

---

## 5. 目录结构

```
novel_output/小说名_20261101/           # 项目目录（独立于 external_projects 代码）
├── project.json                        # 全局配置：源目录、章节清单、时长、横竖屏、TTS、挡位、预算
├── PROGRESS.md                         # 跨 session 决策与反馈日志（追加型）
├── source/                             # 对 novel_writer 导出目录的只读引用/快照（路径+指纹）
├── global/
│   ├── characters.json                 # 角色库（映射自人设卡 + 补全字段）
│   ├── locations.json                  # 地点库
│   ├── assets/                         # character_*.png / location_*.png / cover_bg.png / cover.png
│   └── review.json                     # 全书级素材的审核状态
├── chapters/
│   ├── index.yaml                      # 章节清单与状态（pending / planned / in_review / done）
│   └── NNN/                            # 三位补零（Phase 0 偏差，见第 14 节）
│       ├── script.md                   # 章节剧本
│       ├── script_review.yaml          # 剧本校验结论
│       ├── scenes.yaml                 # 大场景清单（弹性时长）
│       ├── shots.yaml                  # 小场景规划（shot_id、画面、台词块、时长、状态）
│       ├── prompts.yaml                # 每个小场景的 prompt_en 与 video_mode
│       ├── consistency_report.yaml     # 一致性核查结论
│       ├── audio/                      # 小场景配音
│       ├── clips/                      # shot_NN.mp4
│       ├── review.json                 # 每个小场景的确认记录与修改意见
│       └── chapter_NN.mp4              # 章节合成结果
├── exports/
│   └── video.mp4                       # 全书成片
└── logs/                               # 运行日志与 API 调用记录
```

---

## 6. 代码结构（外部项目）

```
external_projects/novel_to_video/
├── project.yaml                        # daemon 契约：入口、资源声明、挡位默认值
├── PROJECT.md                          # 目标、里程碑、已知限制（与 novel_writer 同构）
├── app.py                              # Streamlit 入口
├── requirements.txt
├── novel_to_video/
│   ├── source_import.py                # 读取 novel_writer 导出目录，解析人设卡/地点/章节正文，生成映射与指纹
│   ├── project_store.py                # 项目目录、状态文件读写、原子写入、版本号
│   ├── pipeline.py                     # 全书级与章节级阶段定义、依赖图、状态推进
│   ├── gates.py                        # 挡位与检查点（G0–G5）
│   ├── budget.py                       # 调用次数/时长预算
│   ├── invoker.py                      # skill_agent 调用封装（同 novel_writer 的 FrameworkInvoker 模式）
│   ├── steps/
│   │   ├── global_import.py            # G-0 / G-1
│   │   ├── global_visual.py            # G-2 / G-3
│   │   ├── chapter_script.py           # C-1 / C-2
│   │   ├── chapter_scenes.py           # C-3 / C-4
│   │   ├── chapter_assets.py           # C-5（含补建角色/地点）
│   │   ├── chapter_audio.py            # C-6
│   │   ├── chapter_prompts.py          # C-7（Agent 语义核查 + 代码机械把关）
│   │   ├── chapter_clips.py            # C-8（逐小场景）
│   │   ├── chapter_compose.py          # C-9（ffmpeg 章节合成）
│   │   └── final_compose.py            # F-1 至 F-4
│   ├── providers/
│   │   ├── keypool.py                  # 多 key 轮换、429 自动切换、冷却（沿用 novel-video-studio 机制）
│   │   ├── image.py                    # gen_image_with_text 调用封装
│   │   ├── video.py                    # gen_video_with_text 调用封装
│   │   └── tts.py                      # CosyVoice 优先 + edge-tts 兜底
│   ├── media/
│   │   ├── ffmpeg.py                   # 拼接、转场、时长对齐、慢放/快放
│   │   └── cover.py                    # 封面背景叠字（PIL）
│   ├── revision.py                     # 修改意见→影响层级→清理下游（invalidate 逻辑）
│   ├── runner.py / worker.py           # 后台执行、暂停/停止、崩溃恢复
│   └── tool_api.py                     # UI/CLI/daemon 统一接口
├── ui/                                 # Streamlit 页面（见第 7 节）
├── entrypoints/                        # create / list / run / health / compose_all
├── workflows/                          # skill_agent 步骤的 yaml 模板
├── docs/
└── tests/
```

---

## 7. Streamlit 界面

| 页面 | 核心功能 | 可控与可见性 |
|---|---|---|
| 书架 | 项目列表、从 novel_writer 导出目录新建、整体进度 | 一眼看到每本书的全书级与章节级状态 |
| 导入与配置 | 选择导出目录、章节范围勾选（默认全选）、单章目标时长、横竖屏、TTS 方案、挡位与预算 | 导入后展示映射报告（角色数、地点数、缺失字段） |
| 全书素材 | 角色/地点库编辑；`visual_anchor_en`、`voice_profile` 编辑；定妆图画廊逐条通过/重生成/替换 | 每个实体可见其来源（人设卡原文 / 补建） |
| 补建审核 | 章节生成过程中产生的"新增角色/地点"卡片，确认或驳回 | 未确认前对应章节暂停，不会静默写入 |
| 章节列表 | 所有章节的阶段矩阵、进度、耗时、失败原因 | 可选中章节、批量排队、单章暂停 |
| 章节工作台 | 剧本（编辑/重校验）→ 大场景 → 小场景列表；每个小场景展示画面提示、台词、配音试听、视频预览、一致性结论 | **每个小场景独立的通过 / 修改意见 / 重生成 / 回退到规划** |
| 小场景确认 | 逐镜顺序审核的快捷视图（上一镜/下一镜、键盘友好） | 核心审核入口 |
| 章节预览 | 章节合成视频预览，章节级通过或退回 | 退回时按依赖清理下游 |
| 总控台 | 运行/暂停/停止、当前步骤、日志流、API 调用计数、预算余量 | 长任务可观测 |
| 全书合成 | 章节选择与顺序确认、封面设置（可选）、转场设置（可选）、生成与预览 `video.mp4` | 合成前校验所有选中章节已通过 |
| 反馈与决策日志 | 所有修改意见、补建决定、回退记录，可按章节/层级筛选 | 对应 `PROGRESS.md` 与各层 `review.json` |

**逐镜审核的关键交互**：
- 小场景确认是唯一必须逐个处理的审核；全书素材与章节剧本可在 G1/G2 挡位下批量通过；
- 每个小场景的"修改意见"必须选定影响层级：仅视频重生成 / 改提示词并重生成 / 改台词与配音 / 改分镜规划。选择后系统只清理对应下游，不整章重跑。

---

## 8. 挡位设计（与 novel_writer 语义一致）

| 挡位 | 全书级素材 | 章节剧本/分镜 | 小场景视频 | 章节合成 | 补建角色/地点 |
|---|---|---|---|---|---|
| G0 手动 | 逐条确认 | 逐章确认 | 逐小场景确认 | 逐章确认 | 逐条确认 |
| G1 引导 | 逐条确认 | 逐章确认 | 逐小场景确认 | 自动 | 逐条确认 |
| G2 半自动 | 批量确认 | 自动 | 逐小场景确认 | 自动 | 逐条确认 |
| G3 全自动 | 自动（校验失败才停） | 自动 | 自动（校验失败才停） | 自动 | 自动补建，完成后汇总通知 |
| G4 托管 | 自动 | 自动 | 自动 | 自动 | 自动，失败时自动换策略 |
| G5 不停机 | 自动 | 自动 | 自动 | 自动 | 自动，失败自动重置重试 |

- 默认 **G0**，保守。G3 及以上必须配置预算上限（视频调用次数、图像调用次数、总耗时）；
- 无论何种挡位，**全书合成前必须有一次人工确认**（F-1 检查点不可跳过）；
- 挡位可按阶段覆盖（如只让小场景视频自动，其余保持手动）。

---

## 9. 外部依赖与 key 池

- **图像**（`gen_image_with_text`）、**视频**（`gen_video_with_text`）：沿用 novel-video-studio 的 key 池机制：
  - 支持多 key 配置（环境变量列表或本地配置文件，与 skill 同一约定）；
  - 遇 429/rate limit 自动切换到下一把可用 key 立即重试，所有 key 冷却时指数退避；
  - 遇 400/鉴权类等非限流错误不重试同一请求，直接记录结构化错误，由 Agent 或用户修正参数后定向重跑单条；
  - 调用次数、耗时、key 使用情况写入 `logs/`，计入预算。
- **TTS**：CosyVoice 本地优先，不可用或推理异常时自动降级 edge-tts。
- **LLM**（剧本、分镜、语义核查、补建实体）：沿用 `skill_agent` 步骤与 `load_llm_cfg()`，不新增 provider 体系。
- 密钥只从环境变量或本机未入库配置读取，不写入项目文件、不进入 Git。

---

## 10. 关键设计细节

### 10.1 人设与地点映射（G-1）
- 角色：解析 `设定/人物/角色/char_NNN.md`，映射到 `characters.json` 的 `id`、`names`、`age_range`、`gender`、`description_zh`、`relations`、`first_appear`；人设卡（`人设卡/*.md`）中的外观描述作为 `description_zh` 的补充来源；
- 地点：解析 `设定/世界观/地点详情/*.md`，映射 `name`、`location_type`、`description_zh`；
- 缺失字段（如英文外观锚点、声音设定）由 G-2 补全，补全内容标记 `source: generated`，与原始人设字段区分；
- 若人设卡与角色索引不一致，报告为导入警告，由用户裁定，不静默选择。
- **（Phase 0 实测）导出包没有 `gender` 字段**：人设卡与角色文件中均不含性别信息，真实导出的 21 个角色全部为 null。导入以 `MISSING_GENDER` 警告提示，性别补全归入 G-2，并标记 `source: generated`。配音的音色选择依赖性别，因此 G-2 必须先于 C-6 完成。
- **（Phase 0 实测）年龄段阈值**：<13 child，<18 teen，<35 youth，<60 middle_aged，其余 elderly。阈值由 Phase 0 测试校准，原草案中青年上限 25 会把 28 岁主角误判为中年，已调整。

### 10.2 剧本（C-1 / C-2）
- 剧本按"场次"组织，格式：场次标题 `### 场次 N｜地点·时间（内景/外景）`，行格式 `旁白：…` 或 `角色名：台词`；
- 旁白允许适度口播化改写，**不得新增情节**；对话必须逐字来自正文；
- C-2 由 Agent 逐场次核对剧情覆盖、对话覆盖、说话人归属、无臆造，结论写入 `script_review.yaml`；代码只做格式与字段机械检查。

### 10.3 大场景与小场景（C-3 / C-4）
- 大场景弹性时长：默认建议 ≤120 秒，剧情连续性强时可放宽至 ≤240 秒；
- 小场景时长 4–12 秒（视频接口限制），每个小场景引用的台词块 `content_blocks` 必须能被配音时长覆盖，超限则代码强制拆分并回到 C-4；
- 小场景状态：`pending → planned → generated → approved`（或 `rejected`）。

### 10.4 提示词与一致性（C-7）
- `prompt_en` 由 Agent 手写，必须嵌入引用角色/地点的 `visual_anchor_en`；
- 一致性核查由 Agent 逐条语义核查（角色外观、地点外观、同章横向漂移、画面与情节是否一致），结论写入 `consistency_report.yaml`；
- 代码机械检查：覆盖完整、全部通过、`prompt_en` 哈希未在核查后被改动（防过期）。语义判断不交给脚本。

### 10.5 视频生成与合成（C-8 / C-9）
- 视频模式默认 `reference`，缺少定妆图时**硬性拦截**，不静默降级为 `text`；
- 小场景 clip 生成后即进入确认队列；修改不触发整章重生成；
- 章节合成时按小场景规划时长对 clip 做慢放/快放对齐，音轨与画面按小场景对齐，章节内转场默认硬切。

### 10.6 修改与回退
- 修改意见按影响层级分四类，对应清理范围：
  - **仅重生视频**：清理该 `clip`，保留提示词与音频；
  - **改提示词**：清理 `prompts.yaml` 对应条目、`consistency_report.yaml` 相关条目、`clip`；
  - **改台词**：清理该小场景音频、`prompts.yaml`、核查结论、`clip`，并重新校验 `script.md` 对应场次；
  - **改分镜**：清理该章 `shots.yaml` 之后的所有小场景产物，回到 C-4；
- 章节合成之后的任何修改，自动把 `chapter_NN.mp4` 标记为 `stale`，需重新合成；
- 全书合成之后的修改，把 `video.mp4` 标记为 `stale`。

### 10.7 补建角色/地点的落盘规则
- 补建实体写入 `global/characters.json` 时带 `source: auto_added` 与来源章节号；
- 补建不改动人设卡原文；
- 补建素材（定妆图）与全书素材走同一目录与校验流程。

### 10.8 状态与恢复
- 所有状态文件采用原子写入（临时文件 + rename），带版本号，防并发覆盖；
- worker 崩溃后，启动时扫描 `in_progress` 状态并回退到最近的稳定检查点；
- `PROGRESS.md` 只追加，记录决策、用户反馈、补建、回退与已知坑。

---

## 11. 可执行实施规划

分期原则：每期结束都能端到端跑通一个**可演示的最小闭环**，不以"模块写完"为期结束标准。

### Phase 0：脚手架与导入（约 1 期）
- 目录骨架、`project.yaml`、`PROJECT.md`、`app.py` 空壳、`entrypoints/health.py`；
- `source_import.py`：读取 novel_writer 导出目录，输出章节清单、角色映射、地点映射、导入报告；
- `project_store.py`：原子写入、版本号、目录初始化；
- 验收：对本次上传的 `小说_20261101_101309` 导出目录导入成功，章节数自动识别为 50，角色 21 个、地点 4 个，导入报告无阻断错误。

### Phase 1：全书级素材（约 1 期）
- G-2 视觉/声音补全（`invoker.py` + `skill_agent` 工作流模板）；
- G-3 定妆图生成（`providers/image.py` + `keypool.py`）；
- 全书素材画廊与逐条审核的 UI 页面；
- 验收：全部角色与地点获得 `visual_anchor_en`、`voice_profile`，定妆图生成并可逐条通过。

### Phase 2：单章端到端（约 2 期）
- C-1/C-2 剧本转换与校验；C-3/C-4 大场景与小场景规划；
- C-5 素材补全（含补建分支）；C-6 配音（TTS）；
- C-7 提示词与一致性核查；C-8 小场景视频生成；C-9 章节合成；
- 章节工作台页面 + 小场景确认页；
- 验收：第 1 章从剧本到 `chapter_01.mp4` 全流程跑通，每个小场景可独立确认、修改、重生成。

### Phase 3：多章循环与挡位（约 1 期）
- 章节列表与批量排队；挡位 G0–G3 实现；预算模块；
- 修改意见的分级回退（10.6）与 `stale` 传播；
- 补建审核页面；
- 验收：连续处理 3 章，G2 下章节间自动推进，小场景仍逐个确认；修改第 2 章某小场景不影响第 1、3 章产物。

### Phase 4：全书合成与收尾（约 1 期）
- F-1 至 F-4：章节检查、封面（可选，沿用 v7 的背景图与叠字分离方案）、全书拼接、转场（可选）、成片校验；
- 反馈与决策日志页面；
- G4/G5 托管与不停机挡；
- 验收：全部章节完成后一键生成 `video.mp4`，时长与章节之和一致。

### Phase 5：加固（约 1 期）
- 单元测试与桩测试（不依赖真实 API 的全链路）；
- 真实 API 冒烟脚本（小规模，单章前 2 个小场景）；
- 崩溃恢复演练、key 池切换演练、`stale` 传播演练；
- 文档与 `docs/README.md`。

### 里程碑与依赖

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
 导入        全书素材     单章闭环     多章+挡位    全书合成      加固
```

Phase 2 是风险最高的一期（涉及外部视频 API、一致性核查、音画对齐）。建议 Phase 2 开始前先用一个 4–6 秒的小场景做一次真实 API 冒烟。

---

## 12. 风险与已知限制

| 风险 | 影响 | 缓解 |
|---|---|---|
| 外部视频/图像 API 限流或不稳定 | 批量生成变慢或失败 | key 池轮换、退避、单条定向重跑、预算限制 |
| 视频模型对同一角色外观不稳定 | 跨小场景漂移 | `reference` 模式强制参考定妆图；C-7 语义核查；逐镜确认兜底 |
| 人设卡与正文设定冲突 | 剧本与人物不一致 | 导入报告列出冲突；由用户裁定，不静默选择 |
| 补建角色质量不稳定 | 新角色外观与既有印象不符 | 补建需用户确认；补建不改写人设卡原文 |
| 章节数量差异大（如 10 章与 200 章） | 单次任务时长失控 | 按章排队、预算上限、支持分批执行 |
| 音画时长对齐误差 | 字幕/口型与画面错位 | 小场景时长由配音时长反推并约束在 4–12 秒，合成前校验 |
| 小场景确认工作量大 | 审核疲劳 | 快捷键、上一镜/下一镜、通过后自动推进、G2 以上仅小场景仍逐个确认 |

---

## 13. 待确认事项（已按推荐项采用）

用户在确认实施时未另外指定，以下四项均按推荐项采用，如需调整可随时修改：

1. 小场景确认**不提供**"批量通过本章全部小场景"快捷操作（默认关闭，G1/G2 的批量能力仅限全书素材与章节剧本）。
2. 单章目标时长默认 **90 秒**（可在导入时用 `--duration` / 界面修改），不按字数自动推算，由大场景弹性时长约束决定实际分段。
3. 全书合成默认**硬切**，淡入淡出作为可选项留给 Phase 4 的全书合成设置。
4. 补建角色的 `source: auto_added` 实体在全书素材页单独标记，支持批量确认（Phase 2 实现）。

---

## 14. 实施记录

### 14.1 Phase 0：脚手架与导入（已完成，2026-11-01）

**交付物**（位于 `external_projects/novel_to_video/`）：

| 文件 | 作用 |
|---|---|
| `novel_to_video/source_import.py` | 只读导入导出目录，产出章节/角色/地点/问题清单/指纹 |
| `novel_to_video/project_store.py` | 项目目录创建、原子写入、章节范围解析、PROGRESS.md 追加 |
| `novel_to_video/tool_api.py` | UI/CLI/daemon 统一接口，返回 `{ok, data, error}` |
| `entrypoints/import_novel.py` `list_projects.py` `health.py` | 命令行入口，JSON 输出 |
| `app.py` `ui/import_page.py` `ui/projects_page.py` | Streamlit 导入页与项目页 |
| `project.yaml` `PROJECT.md` `docs/README.md` `requirements.txt` `.gitignore` | 项目契约与文档 |
| `tests/` | 40 项测试（含 Streamlit AppTest 冒烟与入口脚本测试），1 项真实导出冒烟（需环境变量开启） |

**验收结果**：

- 对 `小说_20261101_101309` 导出目录 dry-run：50 章、21 个角色、4 个地点，阻断错误 0，提示 5 条（21 个角色缺性别、4 条导出清洗提示）。满足计划验收标准。
- `project.yaml` 已通过 mini_agent 的 `manifest.load_manifest` 校验，入口 `import_novel`、`list_projects` 正常注册。
- 全部测试通过（`python -m pytest -q tests`）。

**相对原方案的偏差（已同步到代码与文档）**：

1. **章节目录三位补零**（`chapters/001/`，原方案为 `NN`）。原因：计划明确要支持超过 99 章，两位数会在第 100 章起冲突。
2. **年龄段青年上限 25 → 35**：原阈值把 28 岁主角归为中年，不符合常识，由测试发现后调整。
3. **导出包无性别字段**：计划假设人设卡可提供性别，实际没有。改为导入警告 + G-2 补全，并已写入第 10.1 节。
4. **书名无法从导出包获取**：合稿与 README 都不含书名，默认取目录名去掉时间戳，导入时可改。
5. **默认项目存放目录**为 `external_projects/novel_to_video/novel_output/`（已加入 .gitignore），便于随项目整体移动。
6. **角色关系 `relations` 暂为空**：人设卡中的关系信息格式尚未核实，留到 G-2 处理。

**未在 Phase 0 实现（明确留给后续阶段）**：视频/图像/TTS 调用、LLM 剧本与分镜、文件锁、G0–G5 挡位、修改回退。

**Phase 0 发现、需要后续阶段处理的导出数据问题**（已在导入报告中以警告呈现，不做自动修正）：

- 第 8、43、47 章残留工具调用标签或截断提示，需确认章节完整性；
- 第 21 章在写作备注之后有 14 行疑似重复的结尾，需人工决定是否删除，删除前不应进入剧本转换。

### 14.2 Phase 1：全书级素材（已完成，2026-11-01，离线验证）

**交付物**（位于 `external_projects/novel_to_video/novel_to_video/`）：

| 文件 | 作用 |
|---|---|
| `providers/keypool.py` | 多 key 池：429 冷却并切换 key（不消耗重试次数），400/401/403/404/422 不重试；key 只读环境变量 |
| `providers/image.py` | `AgnesImageProvider`（与 gen_image_with_text skill 同接口，b64 返回）与 `StubImageProvider` |
| `providers/text.py` | `MiniAgentTextLLM`（复用 mini_agent `LLMHelper`）与 `RuleTextLLM`（离线兜底，输出标记 rules） |
| `providers/factory.py` | 按名称构造 provider；`NOVEL_TO_VIDEO_PROVIDERS=stub` 强制离线 |
| `steps/global_visual.py` | G-2 `visual_text_pass`（逐实体、断点续跑、失败隔离）；G-3 `portrait_pass`（版本化文件名、非重试错误停机）；`update_entity` 人工编辑；`visual_status` 门禁 |
| `review.py` | 实体审核状态 `global/review.json` |
| `prompts.py` | G-2/G-3 提示词集中管理 |
| `call_log.py` | `logs/api_calls.jsonl` 外部调用留痕，不记录 key |
| `tool_api.py`（扩展） | `run_visual_text` / `run_portraits` / `get_visual_status` / `review_asset` / `edit_asset` |
| `entrypoints/build_assets.py` | 命令行入口（`--stage text|portrait|all`、`--ids`、`--regenerate`） |
| `ui/assets_page.py` | 全书素材页：执行、画廊审核、字段编辑、门禁提示 |

**验收结果**：

- 真实导出包（50 章中选取前 3 章的项目）在 stub 模式下完成 G-2 与 G-3：25 个实体（21 角色 + 4 地点）全部生成文本设定与定妆图，失败 0。
- 定妆图视觉检查通过（占位图内容与提示词一致）。
- 测试 65 项通过（新增 provider 层、G-2/G-3 流程、界面冒烟、入口脚本）。
- `project.yaml` 通过 manifest 校验，新增入口 `build_assets`。
- 门禁：stub 模式下性别缺失，`ready=false`，符合设计预期（rules 不推断性别）。

**相对原方案的偏差**：

1. **key 池与生图接口为独立实现**，而非调用 skill 脚本，但策略与接口与 gen_image_with_text 保持一致（计划要求“沿用机制”）。
2. **G-2 不通过 skill_agent 工作流执行**，而是直接调用 mini_agent 的 `LLMHelper.ask`。原因：G-2 是单实体、结构化 JSON 输出的短调用，不需要 agent 工具循环；直接调用更简单，错误也更容易定位。若后续需要 agent 式检索人设，再切换到 `skill_agent`。
3. **新增 `*_source` 来源标记与 `review.json` 版本号**：原方案只写了“标记 generated”，实施时细化为 `miniagent` / `rules` / `manual` 三类，便于后续区分可信度。
4. **门禁增加缺失字段检查**：`ready` 不仅要求审核通过，还要求 visual_anchor_en、voice_profile、gender 均已存在，避免性别缺失的角色进入章节生产。
5. **G-3 发生非重试类错误时立即停机**（`stopped` 字段），避免对每个实体重复发送同样错误的请求。
6. **未在真实 key 下运行**：Agnes 接口的请求结构依据 skill 实现，真实调用需本机冒烟后确认。

**未实现（留给后续阶段）**：视频与 TTS（Phase 2）、补建角色分支（Phase 2 C-5）、挡位自动化（Phase 3）、定妆图替换上传（当前只支持重生成）。

**需要本机冒烟的事项**：

- 设置 `AGNES_API_KEYS` 后执行 `build_assets.py <项目> --stage portrait --ids char_001`，确认返回图片可解码、保存成功；
- 配置好 mini_agent 的 LLM 后执行 `--stage text --ids char_001`，检查 `visual_anchor_en` 是否与人设卡一致，并确认 `gender_source` 为 `miniagent`。

### 14.3 后续阶段

Phase 2 起按本文件第 11 节推进。每完成一期，在本节追加对应的实施记录（交付物、验收结果、相对原方案的偏差、未实现项）。

*本文件为设计方案，实施前以本文件为准；实施过程中的偏差记入 `PROJECT.md` 的里程碑进度与已知限制。*
