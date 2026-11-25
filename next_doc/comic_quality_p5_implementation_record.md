# world_forge 漫画 · P5 实施记录（叙事与镜头语言）

> 对应方案：`comic_improvement_plan_v2.md` 第 3 节 P5
> 测试：`python -m pytest -q tests` 全部通过（215 项，其中 P5 新增 11 项）

## 1. 做了什么

- **分镜字段**：每格新增 `connects_prev`（与上一格的衔接，≤40 字）、`camera`（8 种机位，缺省平视）、`gaze`（每个出场角色的视线，非法值丢弃）、`screen_order`（两人以上时画面从左到右的顺序，否则为空）、`action`（动作）、`composition`（构图与留白）。
- **越轴检查** `generate.check_axis`：跨页按顺序比较相邻出现的角色，左右顺序翻转且未标注 `反打` 即判越轴，触发重试。
- **旁白约束**：超过 30 字判为不合格。
- **提示词**：分镜指令要求写清衔接、机位、反打的使用条件、视线、构图与留白；图像提示词由 `engine._panel_prompt` 结构化拼接（镜头 → 机位 → 构图 → 动作 → 视线 → 画面左右顺序 → 人物外观 → 背景 → 配色 → 留白要求）。
- **离线 stub**：输出新字段，并把旁白截到 30 字，保证规则版也能通过检查。
- **看板**：分镜脚本显示机位（非平视时）、动作、承接与构图。

## 2. 改动文件

路径均相对于 `external_projects/world_forge/`：`world_forge/comic_layout.py`（词表与旁白上限）、`world_forge/generate.py`（字段规范化、越轴检查、指令）、`world_forge/engine.py`（结构化提示词）、`world_forge/providers/text.py`（stub）、`app.py`（显示）、`tests/test_comic_p5.py`（新增）、`docs/README.md`、`PROJECT.md`。

## 3. 验证与边界

- 测试覆盖：越轴判定（翻转、反打豁免、同向通过、单人忽略、跨页检查）、左右顺序需为在场角色、非法机位与视线的处理、旁白长度边界、提示词包含各结构字段、stub 通过检查。
- **未覆盖**：越轴只检查左右关系，不能判断视线是否真的朝向对方；对白语气、旁白是否复述画面由提示词约束，需要人工抽查。
- 真实模型的效果（画面是否真的按 gaze 与 composition 生成）需要在有 key 的环境下确认；离线 stub 只能验证结构。

## 4. 下一步

P6 版式节奏（大小格、同行三格）与 P7 角色一致性与黑白网点，见改进方案 v2。
