# novel-video-studio 改进方案 v7：片头封面（剧情向背景图 + 标题文字后期叠加）

## 目标

在最终成片 `video.mp4` 最前面新增一段独立的封面片段（默认3秒），封面
背景图根据整体小说剧情生成、小说名字以后期文字叠加的方式加到封面上。
要求：

1. 新功能默认关闭（保守 opt-in），不影响任何现有项目/现有流程；
2. 支持"事后补建"——即成片已经全部完成（`video.mp4` 已存在）之后，
   用户才决定要加封面，不需要重跑任何大场景相关流程；
3. 标题文字默认居中，同时提供几种可选布局供用户选择。

## 现状问题（v6 之前遗留）

1. `references/02_global_assets.md` 里封面只有一句话："封面图（可选）
   逻辑相同"——即完全复用单条角色/地点定妆图的生成方式，**没有"基于
   整体剧情"的 prompt 构造步骤**，也没有征询用户是否需要封面；
2. 完全没有"标题文字叠加"这一步，`gen_image_with_text` 生成的图里即使
   带文字也大概率是乱码/不准确的英文，不能指望模型把小说名字画对；
3. `scripts/compose_final_video_v2.py` 的 `apply_cover()` 语义是
   **"用封面画面替换第一个大场景视频最开头几秒，音轨仍用原声，总时长
   不变"**——这与需求（新增3秒、增加总时长、这3秒不应该混用第一句
   台词的音轨）正好相反，`validate_output()` 的 `expected_dur` 也没把
   封面时长算进去；
4. 触发条件是"文件存在就自动套用"（`--no-cover` 才关闭），属于新功能
   默认开启，与一贯要求的"新功能保守 opt-in 默认"方向相反；
5. 没有"事后补建"路径——现在只能在阶段3（角色/地点定妆图生成）那个
   时间窗口里做封面，成片做完之后想加封面，没有文档描述该怎么办，
   容易被误当成要走一遍 `revision_and_rollback.md` 的完整回退重跑。

## 修复方案

### A. `novel_project.json` 新增 `cover` 配置块

```json
"cover": {
  "enabled": false,          // 默认 false；用户明确要才置 true
  "title_text": null,        // null 时用 source_title；用户可指定展示用短标题（比如全名太长时用简称）
  "duration_sec": 3.0,
  "layout": "center"         // "center" | "bottom_bar" | "top_classic"，见下方 C 节
}
```

- **询问时机**：
  - **新项目**：阶段2（`references/01_entity_extraction.md` Step 0，
    问目标时长/横竖屏的同一步）一起问"要不要生成片头封面、标题文字
    放哪个位置"，答案直接写进 `cover` 块。用户不主动提，`enabled`
    维持 `false`，不多问、不阻塞主流程；
  - **已完成项目事后追加**：见 D 节，走独立的补建流程，不需要回到
    阶段2。
- `check_project_state.py` 增加一条弱提示（`warnings` 而非 `errors`）：
  若 `cover.enabled == true` 但 `global/assets/cover.png` 不存在，
  提示"封面配置已开启但尚未生成，可在阶段3或事后补建流程里生成"——
  帮跨 session 接手时不遗漏。

### B. 阶段3新增"剧情向封面生成"步骤（改 `references/02_global_assets.md`）

仅当 `cover.enabled == true` 时执行，放在角色/地点定妆图**全部生成
完成之后**（需要完整的 `visual_anchor_en` 清单和 `art_style` 作为
素材）：

1. Agent 读 `script.md` 全文，提炼能代表全书的核心视觉意象（整体基调
   + 核心冲突 + 1个最具代表性的画面，例如主角剪影 + 核心地点/核心
   矛盾道具的组合），**不是某个具体场景的截取**，也不是逐场景拼接；
2. 结合 `novel_project.json.art_style` 和相关角色的 `visual_anchor_en`，
   写成一句英文 prompt，并显式加入 `no text, no letters, no
   typography, no watermark` 一类约束（避免生图模型自己画一堆和后面
   烧上去的标题字冲突的乱码文字）；
3. 调用 `gen_image_with_text` 生成**不含文字**的纯背景图，存
   `global/assets/cover_bg.png`（注意：**不直接叫 `cover.png`**，见
   C 节说明原因）；
4. 校验：`cover_bg.png` 存在即算完成，失败按
   `references/error_handling.md` 常规重试规则处理。

### C. 新增独立的标题文字叠加脚本 `scripts/render_cover_title.py`

输入 `cover_bg.png` + `title_text` + `layout`，输出烧好标题文字的
`global/assets/cover.png`。复用 `common.py` 的 `resolve_font_path()`
和 `compose_macro_scene.py` 里 `_render_text_png()` 已有的 PIL 画中文
字逻辑（无需新写字体探测代码）。

**故意把"生成背景图"（B节，调 API，有成本、较慢）和"叠字"（C节，
本地 PIL/ffmpeg，免费、秒级）拆成两个独立产物**，好处：
- 用户只想换标题文字/字体/布局，不需要重新生成背景图，直接重跑
  `render_cover_title.py`，成本几乎为零；
- 对 D 节"事后补建"和 `revision_and_rollback.md` 的联动更精确：
  "改标题文字"只重跑 C，"换封面画面"才连 B 一起重跑。

**布局选项**（`layout` 字段，三选一，默认 `center`）：

| layout | 效果 | 适用场景 |
|---|---|---|
| `center`（默认） | 标题大字居中偏上1/3处，半透明深色蒙层衬底保证可读性，类似经典电影海报主标题 | 通用默认，不确定选哪个就用这个 |
| `bottom_bar` | 底部一条半透明色块横条，标题文字放在条内左对齐或居中，类似短视频/公众号封面惯用样式 | 背景图本身画面饱满、不想遮挡主体时 |
| `top_classic` | 标题文字放画面上方1/6处，无蒙层，字体加描边保证可读性 | 背景图上方留白充足、构图偏中下时 |

三种布局共用同一套字号/描边/自动换行逻辑（沿用
`_render_text_png()` 现有的自动换行+描边实现），只是文字锚点位置和
是否加蒙层不同，实现成本低。用户询问环节给出这三个选项供选择（可以
配截图示例说明，也可以先用默认 `center` 生成一版，用户看了不满意再
换布局重跑，不强制事前反复确认）。

### D. `scripts/compose_final_video_v2.py`：从"替换"改成"前置新增3秒"

1. `apply_cover()` 重写为 `build_intro_clip()`：只生成一段独立的
   `cover.duration_sec` 秒封面片段（画面沿用现有 zoompan 缓慢缩放
   效果），**不再动第一个大场景视频本身**；音轨用
   `anullsrc`（静音）而不是借用原视频音轨——封面阶段不应该撞上
   第一句台词/旁白的声音；
2. 拼接顺序：`[封面片段] + [大场景1] + [大场景2] + ...`；
3. 触发条件改为 **`novel_project.json.cover.enabled == true` 且
   `global/assets/cover.png` 存在**才应用（不再是"文件存在就自动
   套用"），`--no-cover` 保留作为单次手动关闭的兜底；
4. 去掉现在 `min(clip_dur*0.5, clip_dur-0.2)` 这种"clamp 到第一个
   大场景一半时长以内"的限制——新逻辑下封面时长和大场景时长无耦合
   关系，直接用 `cover.duration_sec`（CLI `--cover-duration` 可覆盖）；
5. `validate_output()` 的 `expected_dur` 计算加上封面时长
   （`enabled` 时 `expected_dur += cover_dur`），否则新增的3秒会被
   总时长校验误判为"对不上"。

### E. 事后补建流程（成片已完成后才决定要加封面）

新增到 `references/revision_and_rollback.md`，作为一条**独立于**
常规"改内容→invalidate→回退重跑"链路的轻量路径——因为封面片段只
影响阶段8的最终拼接，不影响任何大场景内部产物，不需要清空/回退
任何已有状态：

```
用户提出"帮我这个已完成的视频加个封面"
  → 检查 novel_project.json 是否已有 cover 块：
      没有 → 按 A 节问 title_text/layout，写入 cover.enabled=true
      有但 enabled=false → 确认要不要开启，问 title_text/layout（若之前没问过）
  → 检查 global/assets/cover_bg.png 是否存在：
      不存在 → 走 B 节生成（此时 characters.json/locations.json/
                script.md 全部还在，不受影响，随时可以补跑）
  → 检查 global/assets/cover.png 是否存在（或 title_text/layout 有变化）：
      需要生成/更新 → 跑 C 节 render_cover_title.py
  → 直接重跑阶段8 compose_final_video_v2.py：
      macro_scenes.yaml 里各大场景仍是 done、macro_scene_XX.mp4 都还在
      磁盘上，脚本本身就是"读现成大场景视频+当前封面配置→重新合成"，
      天然支持覆盖重跑，不需要 invalidate 任何东西
  → 覆盖旧 video.mp4，向用户展示新总时长（应为旧总时长+封面时长）
```

**唯一需要提醒用户的一点**：旧的 `video.mp4` 会被覆盖，如果用户想保
留旧版本对比，生成前建议提示"是否需要先把当前 video.mp4 另存
一份"，而不是默认静默覆盖。

同理，"只换标题文字/布局，封面画面不用换"、"标题文字不变，只想换一张
背景图"这两种更轻量的场景，都可以照这个流程走，只是跳过其中不需要
重新生成的那一步（背景图不用重生成就跳过B直接走C；反过来标题不变就
跳过C，B生成完新 `cover_bg.png` 后 `cover.png` 也要用现有 title_text
重跑一遍C，因为C的输入`cover_bg.png`变了）。

## 需要同步更新的文档/文件清单

| 文件 | 改动 |
|---|---|
| `references/01_entity_extraction.md` | Step 0 增加"是否需要片头封面+标题文字+布局"的确认项 |
| `references/02_global_assets.md` | 把"封面图（可选）逻辑相同"展开为完整的剧情向 prompt 构造步骤（B节） |
| `references/07_final_compose.md` | 封面语义从"替换/不改变总时长"改为"前置新增/总时长增加"，更新总时长预期公式 |
| `references/revision_and_rollback.md` | 新增 E 节"事后补建封面"独立轻量路径 |
| `SKILL.md` | 目录结构示例补 `cover_bg.png`/`cover.png`；§1.1 字段速查表补 `novel_project.json.cover.*` |
| `scripts/compose_final_video_v2.py` | `apply_cover→build_intro_clip`，静音轨、前置拼接、`validate_output` 时长公式 |
| `scripts/render_cover_title.py`（新增） | 标题文字叠加，三种 `layout`，复用 `common.py`/`compose_macro_scene.py` 已有字体逻辑 |
| `scripts/check_project_state.py` | 新增弱提示：`cover.enabled=true` 但 `cover.png` 缺失时警告 |

## 兼容性说明

- 老项目 `novel_project.json` 里没有 `cover` 字段 → 视为
  `{"enabled": false}`，行为与改动前完全一致；
- 新项目默认也是 `enabled: false`，不主动打断/拖慢现有确认流程，
  只是多一个可以顺带回答、也可以直接跳过的问题；
- 现有的 `--no-cover` CLI 参数继续保留语义（临时关闭），不删除。

## 验证计划

1. **全新项目，开启封面**：走完整流程，确认 `cover_bg.png`→
   `cover.png` 依次生成，最终 `video.mp4` 总时长 = 各大场景时长之和
   + 封面时长，且校验 `ok: true`；
2. **全新项目，不开启封面**：确认全程无 `cover` 相关文件生成，
   `video.mp4` 行为和改动前完全一致；
3. **事后补建**：用一个已经 `video.mp4` 生成完毕的老项目（无 `cover`
   字段）模拟"用户事后要求加封面"，确认不触发任何大场景重跑，只新增
   `cover_bg.png`/`cover.png`，重跑阶段8后总时长正确增加；
4. **只换标题文字**：对已有封面的项目只改 `title_text`，确认只重跑
   `render_cover_title.py` 和阶段8，不重新调用图片生成 API；
5. **三种 layout 各跑一遍**，人工检查文字可读性（描边/蒙层是否够，
   不同画面背景下标题是否清晰）。
