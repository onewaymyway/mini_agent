# novel-video-studio

小说转视频一体化 skill（取代原先 6 个独立 skill：
`novel-entity-extractor` / `novel-macro-scene-planner` /
`novel-scene-detail-planner` / `novel-asset-generator` /
`novel-scene-video-generator` / `novel-video-composer`）。

- 主流程说明、阶段契约、通用规则见 `SKILL.md`；
- 每个阶段的具体操作步骤在 `references/*.md`，按需加载；
- 所有可执行脚本在 `scripts/`（不分子目录）；
- `scripts/check_project_state.py`：随时查看项目进度/断点；
- `scripts/invalidate.py`：用户要求修改内容后，联动清理下游产物+回退状态。

旧的 6 个 skill 目录暂时保留，待本 skill 验证无误后由用户手动删除。

## 变更记录

- **新增阶段0"小说→剧本转换"+阶段0.5"剧本校验"，下游全部基于剧本
  而非小说原文**：解决过去大场景/小场景切分反复出现"不合适"的根因——
  在小说原文（非结构化，引号嵌套动作描写）上现场识别旁白/对话/说话人
  一致性差。现在先把小说一次性转换成结构化剧本 `script.md`
  （`旁白：...` / `角色名：台词`），经 Agent 核对剧情覆盖/对话覆盖/
  说话人归属/无臆造（写入 `script_review.yaml`，`scripts/check_script.py`
  机械把关）通过后，阶段1-3 都读剧本而不是原文，阶段3的旁白/对话拆分
  简化为"按剧本行直接映射"，不再需要现场抠引号/识别动作描写混入。
  详见 `references/00_script_conversion.md`、
  `next_doc/novel_video_studio_fix_plan_v5.md`。
- **大场景时长约束弹性化**：原先 ≤1000字/≤120秒是硬性上限，超限必须
  强行二次细分，容易破坏连续剧情的戏剧张力。现改为弹性建议值——默认
  目标 ≤120秒，剧情连续性强时允许放宽到 ≤240秒硬上限（超过240秒才是
  `check_macro_scenes.py` 的 error，120-240秒之间只是 warning）。小
  场景（micro_scene）4-12秒的硬限制不变，因为这是视频生成接口本身
  的限制。
- **角色/地点外观变体（`appearance_variants`）**：解决"人设图/锁定
  锚点只有一套，但原文里角色确实换装/变装、地点确实昼夜天气变化"的
  问题。`characters.json`/`locations.json` 新增可选字段
  `appearance_variants`，`scene_detail.yaml` 的 `micro_scene` 新增可选
  字段 `character_variant_overrides`/`location_variant_overrides`
  标注某场景生效哪个变体。详见 `references/01_entity_extraction.md`
  Step 2.6、`references/03_scene_detail_planning.md` Step 4.5。
- **阶段5一致性校验改为完全 Agent 驱动 + 结构化报告把关**：原
  `scripts/check_character_consistency.py`（关键词级别的语义校验，
  既会漏检同义词改写也会误报合法用词）已删除，替换为
  `scripts/check_consistency_report.py`——不做任何语义判断，只机械
  校验 Agent 在阶段5 Step 0 写出的 `macro_scene_XX/consistency_report.yaml`
  是否覆盖全部待生成场景、是否全部 `pass`、有没有在核查通过后
  `prompt_en` 又被改动却未更新报告（过期检测）。四项语义核查维度
  （角色外观一致性、地点外观一致性、大场景内横向漂移、**prompt_en
  与 visual_hint/content_blocks 情节内容是否一致**，且核查时角色/
  地点档案信息与情节原文要放在一起同时对照，不能分开单独看）完全由
  Agent 完成，详见 `references/05_scene_video_generation.md` Step 0/
  Step 0.5。
