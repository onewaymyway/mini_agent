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
