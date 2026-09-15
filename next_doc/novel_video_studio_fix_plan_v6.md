# novel-video-studio 改进方案 v6：阶段5生成前硬性检查角色/地点定妆图

## 问题

在还没有生成任何角色/地点定妆图（阶段4 Step1）的情况下，`generate_scene_
videos_v2.py` 就已经开始生成大场景的小场景视频片段。

## 根因（两层叠加）

1. **文档层面纵容**：`references/05_scene_video_generation.md` 原先写
   "有可用参考图时优先 `reference`，无参考图时用 `text`"，把"要不要先
   生成定妆图"变成了 Agent 的临时判断，而不是强制前置条件。
2. **代码层面没有硬性拦截**：`generate_one_scene()` 里 `video_mode ==
   "reference"` 但找不到参考图时，只打印一行提示后自动降级为 `text`
   继续跑；`main()` 在开始生成任何 clip 之前也没有做任何资产完整性检查；
   真正该拦这件事的 `check_assets_and_audio_v2.py` 是完全独立的脚本，
   没有任何机制强制 Agent 在调用生成脚本之前一定跑过它、一定通过。
   另外 `video_mode` 未设置时的默认值是 `"text"`（不是 `"reference"`），
   进一步放大了"默认不需要一致性"的倾向。

两层叠加的结果：Agent 可以在完全没有生成任何定妆图的情况下，直接进入
阶段5、全部按 `text` 模式生成，脚本不会报错，产物"看起来生成成功"，但
本 skill 的核心卖点（跨大场景角色/地点外观一致）被静默放弃。

## 修复方案

### A. 文档修正

- `references/05_scene_video_generation.md`：删除"有图用 reference 无图
  用 text"的被动措辞，改为——**`video_mode` 默认就是 `reference`**，只
  有用户/Agent 明确不追求一致性时才主动设 `text`；发现定妆图缺失时
  必须先回阶段4 Step1 补生成，不允许用改 `video_mode` 的方式绕过。
- `references/04_assets_and_audio.md`：明确 Step1（定妆图生成）是阶段5
  的强制前置条件，不是可选优化项。

### B. 代码修正（`generate_scene_videos_v2.py`）

1. `video_mode` 未设置时的默认值从 `"text"` 改为 `"reference"`；
2. 新增 `find_missing_assets()`：对本次目标 `micro_scene`（应用
   `--macro-id`/`--micro-id` 过滤后）聚合检查引用到的角色/地点是否都
   已有 `asset_path`（跳过显式 `video_mode: text` 的场景）；
3. `main()` 在开始生成任何 clip **之前**调用上述检查，缺失时整体 fatal
   退出（exit code 2），打印结构化缺失清单 + `action_required`，不生成
   任何 clip；
4. `generate_one_scene()` 里 `reference` 模式找不到图时的静默降级行为
   改为硬错误（`non_retryable: True`）——双保险，防止运行中途角色库被
   改动导致漏检；
5. 新增 `--allow-missing-assets` 显式开关，仅用于用户明确要求跳过一致
   性、快速预览等特殊场景，正常流程不使用。

## 验证

用测试项目（`永生协议_test`，`macro_03`）验证：
- 角色 `asset_path` 为空时直接跑生成脚本 → `exit 2`，fatal，缺失清单
  正确列出涉及的 `micro_scene`/角色，未生成任何 clip；
- 补齐 `asset_path` 后 → 正常通过前置检查（后续因沙盒无 API key 在别处
  失败，属预期）；
- 加 `--allow-missing-assets` → 正确跳过检查；
- 显式设置 `video_mode: text` → 不受该检查约束，正确放行。
