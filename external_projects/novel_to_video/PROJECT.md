# novel_to_video — 小说转短剧外部项目

> 遵循 `external_projects` 标准结构，自包含，可独立于 daemon 运行。
> 完整设计方案：`next_doc/novel_to_video_external_project_plan.md`（实施进度与偏差记录见其第 14 节）。
> 使用说明：[`docs/README.md`](./docs/README.md)。

## 目标

把 `novel_writer` 导出的成书，按「全书级公共素材 → 章节级小场景视频（逐镜确认）→ 章节合成 → 全书合成」的流程，转换为一部竖屏短剧视频。

- 全书级设定（角色、地点、公共素材）一次准备、跨章复用；
- 章节是生产与交付的节奏单位，章内以小场景为审核单位；
- 片头封面与转场统一放在最后的全书合成阶段处理；
- 完全独立实现，LLM 步骤沿用 mini_agent 的 `skill_agent` 调用模式，图像/视频/TTS 沿用 novel-video-studio 的 key 池机制。

## 里程碑进度

| 期 | 状态 | 内容 |
|---|---|---|
| Phase 0 脚手架与导入 | **已完成（2026-11-01）** | 只读导入 novel_writer 导出目录（章节/角色/地点/问题清单/指纹）；原子写入的项目目录创建；章节范围选择；dry-run 检查；项目列表与导出变更检测；Streamlit 导入页与项目页；入口 `import_novel` / `list_projects` / `health`；测试 40 项通过 |
| Phase 1 全书级素材 | **已完成（2026-11-01，离线 stub 验证；真实 API 待本机 key 冒烟）** | G-2 视觉锚点/声音设定/缺失性别补全（逐实体、可断点续跑）；G-3 定妆图生成（Agnes 生图，多 key 池）；逐条审核、驳回重生成、人工编辑（改视觉字段自动标记定妆图过期）；全部通过后 `ready` 门禁；调用留痕 |
| Phase 2 单章端到端 | **已完成（2026-11-01，离线 stub 全流程验证；真实视频/TTS/文本模型待本机冒烟）** | C-1…C-9：剧本压缩与校验、镜头规划、补建角色门禁、配音、提示词与机械一致性、小场景视频、逐镜审核、章节合成；可重入流水线与停止点 |
| Phase 3 多章循环与挡位 | **已完成（2026-11-01，离线 stub 验证）** | 章节队列（单章停止不影响其他章）；G0–G3 挡位与检查点策略；G3 预算必填与超限停止；修改四级回退（clip_only / prompt / dialogue / shot_plan）与章节/全书 stale 传播；补建审核页；测试 106 项通过 |
| Phase 4 全书合成与收尾 | **已完成（2026-11-01，离线 stub 验证）** | F-1 章节检查（未完成或过期即拒绝）；F-2 封面（背景图与叠字分离，三种布局）；F-3 全书拼接（封面硬切前置，章节间硬切或淡化）；F-4 成片校验（时长、分辨率、帧率、音轨）；人工确认任何挡位都不可跳过；G4/G5 有限恢复；测试 127 项通过 |
| 修补：立即停止（2026-11-02） | **已完成（离线测试验证）** | 停止信号可中断等待中的模型/图像/视频/配音请求与 ffmpeg 子进程；章节停在断点（待运行，不记失败）；队列不再开始后续章节；远端视频任务无法取消 |
| 修补：视频网络开关（2026-11-02） | **已完成（离线测试验证）** | 视频创建、查询、下载默认直连（不读取系统代理与注册表代理），开关可在挡位页切换；修复下载 403（系统代理不允许访问下载域名） |
| Phase 5 加固 | **已完成（2026-11-01）；真实 API 冒烟待本机执行** | 项目运行锁与遗留回收（SIGKILL 子进程演练）；key 池切换演练并修复 3 处真实缺陷；真实 API 冒烟脚本；测试 267 项通过（1 项需真实导出环境变量跳过）；章节各阶段（文本模型、逐镜视频、配音）的过程日志；G-3/视频的 skill 输出（重试、失败、轮询）同步进入界面日志；驾驶舱；实时日志；图像/视频改为调用 mini_agent 的 Agnes skill，key 从 providers.json 解析|

## 当前能力（Phase 0–5）

- `source_import.py`：读取导出目录，产出章节清单（章号、章名、字数、总结文件）、角色库（映射自 `设定/人物/角色/char_*.md`，补充人设卡「外在呈现」）、地点库（映射自 `设定/世界观/地点详情/*.md`）、问题清单（error / warning / info）与目录指纹。**不写任何文件。**
- `project_store.py`：项目目录 `<书名>_<时间戳>`，原子写入 JSON/YAML，章节范围解析（`all`、`1-10`、`20-`、`3,7,9-12`），PROGRESS.md 只追加。
- `tool_api.py`：UI/CLI/daemon 统一接口，返回 `{ok, data, error}`。
- 入口：`entrypoints/import_novel.py`、`list_projects.py`、`health.py`、`build_assets.py`、`run_chapter.py`、`run_queue.py`、`compose_book.py`。
- 界面：`streamlit run app.py`，导入页（检查 → 确认报告 → 创建）、项目页（章节表、导出变更提示、进度日志）、全书素材页（G-2/G-3 执行、画廊审核、字段编辑）、章节生产页、设置与队列页、补建角色审核页。
- Phase 1 新增：
  - `providers/keypool.py`：多 key 池，遇 429 冷却并切 key，400/401/403/404/422 不重试；key 只从环境变量 `AGNES_API_KEYS` / `AGNES_API_KEY` 读取。
  - `providers/image.py`：Agnes 生图（b64 返回）与离线 `StubImageProvider`。
  - `providers/text.py`：`MiniAgentTextLLM`（复用 mini_agent 的 `LLMHelper`）与离线 `RuleTextLLM`（规则兜底，结果标记为 rules 并需人工审核）。
  - `steps/global_visual.py`：G-2（补全 visual_anchor_en、voice_profile，仅在缺失时补 gender）与 G-3（定妆图，版本化文件名）。
  - `review.py`：每个实体的文本/定妆图审核状态，存于 `global/review.json`。
  - `call_log.py`：外部调用留痕 `logs/api_calls.jsonl`（不含 key）。
  - `merge_character.py`：补建角色合并到已有角色（别名、改写镜头引用、清理已生成的下游产物）。
  - `llm_trace.py`：文本模型调用的完整输入/输出留痕 `logs/llm_trace.jsonl`（C-1/C-3/C-5/G-2），界面「模型调用记录」读取它。
- Phase 2 新增：
  - `steps/chapter_script.py`：C-1 剧本转换（按目标口播字数压缩）与 C-2 机械校验（对白必须逐字来自原文、场次连续、字数超标即失败）。
  - `steps/chapter_shots.py`：C-3/C-4 镜头规划；对白逐字校验，超长口播按标点切分，单镜头估算不超过 10 秒。
  - `steps/chapter_assets.py`：C-5 补建角色（LLM 起草 → 等待文本确认 → 生成定妆图 → 等待定妆图确认），定妆图缺失时硬性拦截 C-8；缺失地点与缺失道具（阶段 2/3）走同一门槛，写入 `global/locations.json` / `global/props.json`。
  - `steps/chapter_audio.py`：C-6 配音（按块缓存，镜头时长 clamp 到 4–12 秒）。
  - `steps/chapter_clips.py`：C-7 提示词（代码拼装）与一致性机械检查；C-8 小场景视频（参考图 + 音频，clip_key 缓存）。
  - `steps/chapter_compose.py`：C-9 章节合成，要求全部镜头审核通过。
  - `chapter_pipeline.py`：可重入的单章流水线，停止点为 waiting_assets / waiting_review / failed / done。
  - `shot_review.py`：小场景审核（通过、驳回重生成、编辑；内容变化后已通过的审核自动变为过期）。
  - `media/ffmpeg.py`：统一输出 720×1280、24fps、H.264+AAC；所有片段都带音轨。
  - `providers/video.py`：Agnes 视频接口（create → poll → download，多 key 池）与 `StubVideoProvider`（ffmpeg 静帧推近）。
  - `providers/tts.py`：`EdgeTTS`（需安装 edge-tts）与 `SilenceTTS`（按字数估算时长的静音，离线用）。

- Phase 3 新增：
  - `gates.py`：G0–G3 检查点策略表；`final_compose` 在任何挡位都为手动；G3 必须同时设置视频/图像调用上限与耗时上限。
  - `batch.py`：多章队列。每章前检查预算，超限则停止后续章节并返回原因；G3 下对机械合格的镜头自动通过，记入 review.json（`auto: true`）与 PROGRESS.md。
  - `revision.py`：修改回退四级（仅重生视频 / 改提示词 / 改台词 / 改分镜）。只清理目标镜头（改分镜则截断其后的镜头）；章节成片与全书成片标记为 stale，不删除文件；其他章节的文件与状态保持不变。
  - `tool_api.py`：`set_gear`、`gear_policy`、`run_queue`、`invalidate_shot`、`final_status`、`auto_added_queue`。
- Phase 4 新增：
  - `steps/final_compose.py`：F-1 检查（所有选中章节 done 且未 stale）；人工确认绑定章节成片快照，成片变化后失效；合成成功后清除确认。
  - `media/cover.py`：封面叠字（PIL），布局 `center` / `bottom_bar` / `top_classic`，中文字体可用 `NOVEL_TO_VIDEO_FONT` 指定。
  - `media/concat_final.py`：全书拼接；封面与正片之间硬切；章节间硬切或 0.5 秒淡化。
  - `batch.py`：G4 失败后收紧口播块并重规划（最多 1 次）；G5 失败后重置失败阶段重试（最多 3 次）；G3 不恢复。
  - `gates.py`：挡位扩展至 G0–G5，`final_compose` 在任何挡位都为手动。
  - `entrypoints/compose_book.py`：`--check` / `--confirm` / 合成。
- Phase 5 新增：
  - `runlock.py`：项目运行锁（`.runlock`）。队列运行时持有；遗留锁（持有进程已退出或心跳超过 5 分钟）可回收；`recover_running_chapters` 把遗留的 running 章节回退到稳定状态。
  - `providers/video.py` 修复：轮询切换 key、全部冷却时等待、创建路径不会无限空转。
  - `entrypoints/smoke_real_api.py`：真实 API 冒烟（一张图 + 一个 4 秒镜头），不打印 key。

- 缺失素材（阶段 3，2026-11-02）：镜头输出 `prop_names`（仅关键道具，每镜头 ≤3），库中无则 C-5 起草入 `global/props.json`，确认流程与地点一致；道具定妆图 1:1；视频参考图有余量时才带道具。
- 角色多形象（阶段 4，2026-11-02，**默认关闭**，挡位页开启）：角色可选带 `looks`；镜头用 `character_looks` 指定形象，缺失形象由 C-5 起草并走与补建角色相同的确认流程；提示词与参考图使用形象，形象无定妆图时拦截 C-8。模块 `steps/character_looks.py`。
- 地点细化 L1（2026-11-02，**默认关闭**，挡位页开启，项目选项 `location_spots_enabled`）：镜头规划逐镜头判断「现有地点是否真的是戏发生的场所」，不是则规划新场景点（`new_location_specs`：所属区域/内外景/理由）；`steps/location_spots.py` 用剧本场次标题做确定性核对并把问题反馈给模型修正（次数用尽只警告）；C-5 把场景点写入全局库（`level=spot`/`parent_id`/`spot_kind`，继承区域风格），定妆图提示词区分内/外景。测试 `tests/test_location_spots.py`（10 例）。后续 L2/L3 见 `next_doc/novel_to_video_location_spots_plan.md`。
- 地点细化 L2（2026-11-02，同样默认关闭）：C-1 剧本规则要求场次标题写到「区域·场所·时间」；C-2 对只写区域名的场次给 warning（不判失败）；开关切换后剧本缓存自动重算。关闭时提示词与校验逐字节不变。测试 `tests/test_location_spots_l2.py`（9 项）。L3 已完成（同日）：C-7 场景点提示词注明所属区域；同区域近似名提示；全书素材页按区域分组。测试 `tests/test_location_spots_l3.py`（5 项）。L4 已完成（默认开启，项目选项 `location_presplit_enabled`，全书素材页可关闭）：对区域级地点提议场景点候选，用户勾选确认后才写入地点库；全书素材页提供「提议场景点 / 确认写入」操作入口。测试 `tests/test_location_presplit.py`（9 项）与 `tests/test_location_presplit_ui.py`（2 项）。
- 默认开关调整（2026-11-02）：角色多形象 `character_looks_enabled`、地点细化 `location_spots_enabled`、区域预拆解 `location_presplit_enabled` 默认开启；挡位页可关闭。视频网络的系统代理 `video.use_system_proxy` 是网络方式，仍默认关闭。修复：默认开启地点细化后，章节页会遇到只有 message 没有拆分字段的新提示，原代码因此报错，现已直接显示这些提示。相关测试已更新（显式关闭或断言默认值），新增 `test_chapter_page_shows_new_plan_warnings_without_crashing`。
- 图像网络开关（2026-11-02）：生图请求与图片下载新增“使用系统代理”，项目配置 `image.use_system_proxy`，默认 False（直连，不读取 HTTP_PROXY/HTTPS_PROXY 与系统代理）；挡位页“图像网络”区域可修改。开启后，常规重试仍失败时才额外尝试一次绕过代理直连。修复：图片下载原先直接调用 `requests.get`，不受 session 控制，代理残留时要多次超时后才兜底。`gen_image_with_text` skill 的 `AgnesImageClient` 同步增加 `use_system_proxy` 参数，默认 False；命令行 `gen_image.py` 也因此默认直连，需要代理时传入该参数。测试 `tests/test_image_proxy_switch.py`（6 项）。
- 标签改名（2026-11-02）：「🚦 挡位与队列」改为「🛠️ 设置与队列」，页面标题改为「设置与批量队列」，因为该页主要承载各项设置与批量队列。

## 已知限制

- **图像与视频依赖 mini_agent 的 skill**：项目必须位于 mini_agent 仓库内，或设置 `NOVEL_TO_VIDEO_SKILLS_DIR`。key 来自 mini_agent 根目录的 providers.json（或环境变量）。
- **模型调用记录只覆盖文本模型**：C-1/C-3/C-5/G-2 的完整输入输出可在界面查看；图像、视频、配音的请求参数仍只在 `logs/api_calls.jsonl` 与实时日志中。`llm_trace.jsonl` 只追加不轮转，长期运行后可手动清理。
- **自动修正有次数上限**：剧本与镜头规划校验失败时，会自动把问题反馈给模型修正，默认各最多 2 次（`script_max_retries` / `shots_max_retries`，「设置与队列」页可改，0 为关闭）；次数用尽仍未通过才失败。每次修正多一次文本模型调用。
- **镜头规划质量取决于模型**：模型可能把一整场戏写成一个镜头，代码只能按时长拆开，拆出的镜头共用画面描述；界面会提示并提供「重新规划本章镜头」，但不会自动重试。
- **旧的直接 HTTP provider 仍保留**（`AgnesImageProvider`、`AgnesVideoProvider`），仅供测试引用，未在生产流程中使用。
- **驾驶舱的后台运行是同进程线程**：界面进程退出即停止运行，遗留锁由下次运行前回收。不适合独立监控。
- **立即停止的边界**：停止会中断本地等待（模型、图像、视频、配音、ffmpeg），但**远端已创建的视频任务无法取消**，服务端可能继续生成并计费。被中断的请求线程会在网络返回后丢弃结果。详见 docs/README.md 第 19 节。
- **性别需人工确认**：正文线索不作为判定依据（代词与称谓常指向他人）。本导出包的人设卡不含性别字段，人类角色的建议均为 unknown，需在「⚧ 性别确认」页逐一确认。
- **转义导出**：目录名/文件名被转义（#U…）的导出包会在创建项目时落盘规范化副本（`source/normalized/`），项目体积会增加约 1MB。
- **真实 API 冒烟尚未执行**：需要在本机设置 key 后运行 `entrypoints/smoke_real_api.py`，结果反馈后才能确认请求结构。
- **没有独立后台 worker**：运行由界面或命令行驱动，崩溃后靠锁与回收恢复，不会自动续跑。
- **锁只防同一台机器**：不适用于跨机器共享项目目录。
- **语义一致性核查未实现**：C-7 仍只做机械检查。
- **Phase 4 只做了离线 stub 验证**：封面背景图的真实生成、转场在真实素材上的观感均未验证。
- **转场只有硬切与淡化**：没有其他样式。
- **G4/G5 的全书合成仍需人工确认**：与计划表中“全自动”的字面不同，依据第 8 节 F-1 不可跳过的规则。
- **G4 的策略切换只有一种**：收紧口播块上限，不会改变剧本内容。

- **Phase 2 只做了离线 stub 验证**：Agnes 视频接口、edge-tts、真实文本模型均未在真实环境下调用。接口结构依据 gen_video_with_text skill 与 edge-tts 文档实现，需本机冒烟后确认。
- **离线剧本是截断式**：`rules` 模式只把原文压成旁白，没有对白，不具备制作质量，仅用于验证流程。
- **一致性只做机械检查**：C-7 只确认提示词包含出镜角色、地点与道具的视觉锚点，不做“画面是否符合情节”的语义核查（未实现，见已知限制）。
- **没有大场景（macro_scene）层**：镜头直接按场次（scene_no）分组，原方案 C-3 的大场景弹性时长未实现。
- **镜头规划由模型一次给出**：超长或时长不合适的镜头只能人工编辑后重跑，尚未自动回到规划阶段重新拆分。
- **CosyVoice 未接入**：TTS 目前为 edge-tts 与静音两种。
- **章节成片的音画时长以镜头为准**：镜头 4–12 秒取整，章节总时长可能比目标多出几秒（实测 90 秒目标得到 98 秒）。
- **修改回退的“改分镜”是截断式**：删除目标镜头及其后的镜头记录并交由 C-4 重新规划，不保留前半段并重排编号。
- **G2 的章节间推进由调用方驱动**：`run_queue` 一次运行后返回，没有后台循环；后台 worker 留给 Phase 5。
- **预算耗时只计队列运行时间**：不含界面等待与人工确认的时间。
- **导出包没有性别字段**：21 个角色的 `gender` 全部为 null。G-2 的真实文本模型会从资料推断并标记 `gender_source`；离线 `rules` 模式不推断，角色会保持“缺失性别”，无法通过 ready 门禁，需人工在界面补全。
- **真实 API 未验证**：Phase 1 在沙箱中只跑了 stub（离线图像占位 + 规则文本）与单元测试；Agnes 生图的请求/响应结构按 gen_image_with_text skill 实现，但未在真实 key 下调用，需本机冒烟（建议先用 `--ids char_001` 生成一张）。
- **G-2 产出需人工审核**：LLM 生成的 visual_anchor_en 与 voice_profile 可能与资料不符（如臆造服饰），必须逐条查看后再通过。
- **定妆图重生成不保留旧版本的审核记录**：旧文件保留在 global/assets/ 中（如 `_v1`），不会自动删除。
- **导出包没有书名**：默认取目录名去掉时间戳（如 `小说`），导入页可修改；建议每次导入都填写真实书名。
- **导出包自带清洗提示**：第 8、43、47 章残留工具标签或截断行已被删除，第 21 章在写作备注后有 14 行疑似重复结尾。这些以 `EXPORT_CLEANUP_NOTE` 警告形式保留，**需人工核对**，导入不会自动修正正文。
- **角色关系未导入**：`relations` 目前为空数组，后续从人设卡「关系」段落补充。
- **年龄段阈值**为经验值：<13 child，<18 teen，<35 youth，<60 middle_aged，其余 elderly。
- 章节目录统一三位补零（`chapters/001/`），与 `设计方案` 中的 `NN` 有偏差，原因见设计文档第 14 节。
- 目前没有文件锁，同一项目只应由一个进程写入（界面与 CLI 同时写同一项目未做保护）。
- 同名函数 `run_chapter`：`tool_api.py` 中 Phase 2 的单章入口与 Phase 3 的队列共用该名称（队列调用的是 Phase 2 版本），后续若拆分请统一。

## 目录约定

```
external_projects/novel_to_video/   # 代码（本目录）
novel_output/                       # 项目数据（默认位置，已加入 .gitignore）
└── <书名>_<YYYYmmdd_HHMMSS>/
    ├── project.json                # 全局配置（时长、横竖屏、TTS、挡位、预算）
    ├── PROGRESS.md                 # 决策与反馈日志（追加）
    ├── source/manifest.json        # 导出目录路径、指纹、导入统计与问题
    ├── global/characters.json      # 角色库（Phase 1 起含 visual_anchor_en / voice_profile / gender 及来源）
    ├── global/locations.json       # 地点库
    ├── global/props.json           # 道具库（阶段 3；旧项目没有该文件按空库处理）
    ├── global/review.json          # 全书级素材审核状态
    ├── global/assets/              # 定妆图 character_*.png / location_*.png（_vN 为重生成版本）
    ├── chapters/index.yaml         # 章节清单、选中状态与章节状态
    ├── chapters/001/               # 章节工作目录（三位补零）
    │   ├── script.md               # C-1 剧本
    │   ├── script_review.yaml      # C-2 校验结论
    │   ├── shots.yaml              # C-3/4 镜头规划（含各镜头的哈希与审核依据）
    │   ├── audio/                  # C-6 口播 wav（块级 + 镜头级）
    │   ├── clips/                  # C-8 小场景视频 sNN.mp4（raw/ 为未配音原片）
    │   ├── review.json             # 小场景审核记录
    │   ├── state.json              # 各阶段状态
    │   └── chapter_001.mp4         # C-9 章节成片
    ├── exports/                    # video.mp4 全书成片；confirm.json 人工确认记录；stale.json 表示需重新合成
    └── logs/                       # api_calls.jsonl 外部调用留痕；llm_trace.jsonl 文本模型完整输入输出；job.jsonl 实时日志；batch_runs.json 累计耗时
```

## 地点细化 L5（2026-11-03）

- 区域预拆解：一键依次执行（区域 → 场景点 → 设定图），后台任务，`presplit_status` 记录已处理区域。
- 提议依据：原始正文段落（`steps/location_context.py`）优先，剧本行补充。
- 场景设定图：`global/locations.json` 中场景点新增 `sheets`（exterior / interior / room_NN）、`markers`、`sheets_basis`；图片位于 `global/assets/sheets/`。
- 镜头：`shots.yaml` 可带 `sheet_id`；C-7/C-8 使用所选设定图的锚点与参考图；缺图由 `missing_portraits` 拦截。
- 代码：`steps/location_auto.py`（一键执行）、`steps/location_sheets.py`（设定图）、`steps/location_context.py`（上下文检索）。
- 设计与限制见 `next_doc/novel_to_video_location_spots_plan.md` 第 11 节。
- 全书素材页：地点按区域成块展示（区域 → 场景点 → 设定图），分组逻辑见 `novel_to_video/steps/location_groups.py`。

## 章节状态一致性（补建素材）

- 补建审核队列（`tool_api.auto_added_queue`）覆盖 C-5 草拟的全部实体：角色、地点、道具、角色形象，每行带 `chapter`、`kind`、`key`。
- 章节页在流水线处于 `waiting_assets` 时显示流水线报告的真实等待项，不再同时显示“没有需要确认”。
- 日志面板的“最近一次”只显示本面板作用域内的任务，避免把其它任务（如 G-3 定妆图）当作重新合成的记录。
- 测试：`tests/test_waiting_assets_queue.py`。

## 补建角色：性别自动确定

- C-5 起草补建角色时，性别由系统自动确定，不再要求用户在页面上选择：模型起草给出 male/female 则直接采用；否则把正文中提到该角色的段落交给模型二选一（`prompts.gender_infer_prompt`，不允许 unknown）。非人类实体（如盖亚）不判定。
- 章节已起草但性别仍为空的补建角色，下一次 C-5 自动补定。
- 自动推断的性别 `gender_source = "auto"`，视为已确定，可以通过设定；用户仍可在性别确认或角色卡片中人工覆盖（覆盖后为 `user`）。
- 门禁：`gender_review.CONFIRMED_SOURCES` 为 user / persona / auto。
- 测试：`tests/test_auto_gender.py`；`tests/test_script_trace.py` 中的补建流程已改为新行为。

## 补建素材一键确认与一键生成定妆图

章节页「补建角色」区和全书补建素材页都有三个批量按钮，按顺序使用：

1. **一键通过设定**：通过本章（或全部）所有待确认的设定。缺字段的项跳过并说明原因，不会误通过。
2. **一键生成定妆图**：后台为设定已通过、还没有可用定妆图的补建素材生成定妆图（角色、地点、道具、角色形象）。设定未通过的项不生成。
3. **一键通过定妆图**：通过所有已生成的定妆图。尚无图片的项跳过并说明原因。

后端：`tool_api.batch_review`、`tool_api.chapter_portrait_ids`、`tool_api.start_chapter_portraits_background`。界面：`ui/auto_added_page.render_batch_bar`。测试：`tests/test_batch_review.py`（4 项）。

## C-8 视频日志带镜头号

- 每个镜头的视频请求在 `joblog.task_label("第 N 章 C-8 镜头 sXX")` 下执行：skill 内部的排队、重试、失败、用时等信息自动加上镜头前缀，例如「第 1 章 C-8 镜头 s05：[POST 排队已满] 第 1 次，7s 后重试」。
- 视频生成的“仍在进行”心跳标签为「第 N 章 C-8 镜头 sXX 视频生成」，每个镜头单独计时；章节级的 C-8 心跳已取消，避免出现不带镜头号的“仍在进行”。
- 测试：`tests/test_shot_log_labels.py`。

## 大场景分层（C-3a）

- 一章 → 多个大场景（同一地点、连续的一段剧情）→ 每个大场景的镜头。设计见 `next_doc/novel_to_video_big_scene_plan.md`。
- C-3a 由模型划分大场景并经代码校验（连续覆盖全部场次）；失败按地点自动归并兜底。结果保存在 `chapters/NNN/big_scenes.yaml`。
- 镜头标注 `big_scene_no`，大场景汇总共用的地点、出镜角色、道具；章节页按大场景分块展示。
- 代码：`steps/chapter_scenes.py`；测试：`tests/test_big_scenes.py`。

## 镜头覆盖检查（每场至少一个镜头）

- C-3 校验新增：剧本中的每个场次都必须至少有一个镜头；镜头引用的场次号必须存在于剧本。缺失的场次作为问题反馈给模型自动修正（`chapter_shots._scene_coverage_issues`）。
- 大场景中没有镜头时，章节页显示警告。
- 测试：`tests/test_big_scenes.py` 中的覆盖用例。

## 立即停止：运行中途开始新一轮后仍能停止

- 问题：运行期间若再次调用 `cancel.begin`（替换项目的取消信号对象），仍在运行的旧线程绑定的是旧信号，停止请求只置位新信号，导致视频轮询等待无法中断，且子进程登记被清空（显示“终止子进程 0 个”）。
- 修复：`cancel.py` 为每个项目保留最近若干个信号对象，停止时全部置位；子进程登记在新一轮开始时保留，仍在运行的子进程停止时依然会被终止。
- 测试：`tests/test_cancel_replaced_signal.py`（替换后中断等待、终止替换前登记的子进程、视频 provider 的完整停止链路）。

## 镜头规划分段调用

- C-3b 改为每个大场景单独调用一次模型，每段独立校验与修正；合并后镜头重新编号。解决整章一次规划时后面场次被漏掉、修正反馈难以定位的问题。设计见 `next_doc/novel_to_video_big_scene_plan.md` 第 8 节。
- 空结果的反馈同时指出缺哪一场。
- 测试：`tests/test_big_scenes.py`（分段调用与只修正漏场的那一段）。

## 大场景显示场景参考图

- 章节视图的每个大场景带 `refs`：该大场景镜头实际使用的场景参考图（与 C-8 选图规则一致：镜头所选设定图，否则地点定妆图），去重，并标注是否已生成。
- 小场景逐镜审核中，大场景块在“共用素材”下方显示这些参考图；未生成的显示占位文字。
- 代码：`chapter_pipeline.big_scene_refs`；界面：`ui/chapter_page._render_big_scenes`；测试：`tests/test_scene_refs.py`。

## 镜头卡片显示场景参考图

- 每个镜头的视图数据带 `ref_image`（`chapter_pipeline.shot_ref_image`，与 C-8 选图规则一致），小场景逐镜卡片左侧在视频下方显示该镜头使用的参考图；未生成时显示占位文字。

## 视频接口：账号受限（403）时换 key 重试

- 视频 skill 识别“账号异常 / 访问受限”类 403：该 key 长冷却并切换到下一把，提交阶段直接换 key 重试，轮询阶段整条镜头换 key 重新提交。
- 全部 key 受限时报告“所有 API key 所在账号都已受限”，不再无意义重试。
- 修改：`.claude/skills/gen_video_with_text/agnes_tools.py`、`agnes_key_pool.py`（与图像 skill 的 key 池保持一致）；`novel_to_video/providers/skill_providers.py`（错误文本）。

## 换 key 的日志

- 每个镜头开始时打印本镜头使用的 key（编号 + 脱敏，如 `#1 sk-a…2222`）。
- 账号受限切换时打印：失败的 key → 切换到的 key（同样脱敏，只显示前 4 位和后 4 位，完整 key 不进入日志）。
- 整条镜头重新提交时打印当前使用的 key；全部受限时列出每把 key 的状态（可用 / 冷却剩余分钟数）。

## key 配置来源与每把 key 的状态（日志）

- key 解析顺序：环境变量 `AGNES_API_KEYS` → `AGNES_PROVIDERS_JSON` 指定的 providers.json（绝对路径，推荐）→ 从 skill 目录向上查找的 providers.json → 环境变量 `AGNES_API_KEY`。
- 每个镜头开始时打印：配置来源（含 providers.json 的路径）、key 总数、每把 key 的编号、脱敏值（前 4 位 + 后 4 位）和状态（可用 / 冷却剩余分钟）。完整 key 不进入日志。
- 之前的问题：skill 只从自己的目录向上找 providers.json，项目里的配置文件如果不在其祖先目录中就找不到，会退回到单个 AGNES_API_KEY，只用了 1 把 key。现在可以用 `AGNES_PROVIDERS_JSON` 明确指定文件，并且日志会显示实际读到的来源。

## 失败时打印服务端原始返回（脱敏）

- 视频接口请求失败（非 2xx）、不可重试错误、排队超时、创建任务失败、任务状态为 failed、轮询超时，都会打印服务端返回的原始内容（压缩空白，截断到 800 字符）。
- 排队已满时：每一次排队失败都打印服务端原始内容（不抽样）。
- 原始内容中的完整 key 与形如 `sk-…` 的密钥全部替换为脱敏形式。
