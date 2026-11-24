# world_forge 漫画质量升级 · P2 实施记录

> 对应设计：`comic_quality_upgrade_design.md` 第 4 节
> 前置：P1（`comic_quality_p1_implementation_record.md`）
> 范围：风格指南、每幕配色、角色表、场景设定图、每格候选与人工选用、图像调用上限
> 测试：`python -m pytest -q tests` 全部通过（185 项，其中 P2 新增 20 项）

## 1. 本阶段做了什么

### 1.1 风格指南（全世界一份）
- 新增文本任务 `style`，输出中文简述 `summary` 与英文 `line`（线条技法）、`palette`（全书主色）、`lighting`（光照）。
- 保存在世界元数据 `style_guide` 中；看板「风格与设定图」或 CLI `style` 生成。
- 每次出图的统一风格提示词会带上 `line`、`lighting`，彩色模式额外带上 `palette`；黑白模式不带配色。

### 1.2 每幕配色
- 节拍表（beats）每页新增 `palette`（英文配色短语）。分镜生成后写入每页 `palette`。
- 渲染时若该页有配色且为彩色模式，提示词追加 `Color palette: …`。

### 1.3 角色表
- 为本故事分镜中出场、且已有立绘的角色生成四张图：`side`（侧面）、`angry`、`sad`、`surprised`；平静沿用立绘。
- 文件保存在 `cast/sheets/{角色id}_{键}.png`；以立绘为参考图生成。
- 已存在的跳过；`overwrite` 时旧图归档到 `history/` 后覆盖。
- 没有立绘的角色跳过并提示，不会生成无参考的表情图。

### 1.4 场景设定图
- 分镜格新增 `location` 字段，必须取自地理类设定（分镜上下文提供 `locations`）。
- `make_scene_images` 对分镜中用到的地点：先由新增文本任务 `scene` 生成英文场景描述（保存在设定条目的 `attrs.visual_en`，之后不再重复生成），再出图。
- 文件保存在 `scenes/{设定id}.png`。

### 1.5 参考图优先级与情绪映射
- 参考图顺序：上一格 → 主角表情图（无则立绘）→ 地点场景图 → 其它出场角色立绘；仍然最多 3 张。
- 情绪映射（`comic_layout.expression_key`）：含「愤/怒/生气/恼」→ 愤怒；「悲/哭/伤/难过/绝望」→ 悲伤；「惊/震/错愕」→ 惊讶；其它返回 None（沿用立绘）。

### 1.6 每格候选与人工选用
- 渲染新增 `candidates`（1–3，默认 1）。N>1 时每格生成 N 张：`panels/pXX_YY_cK.png`，并把第一张成功的复制为正式图。
- 格子字段：`candidates`（成功的候选路径列表）、`chosen`（当前采用的序号）。
- `choose_candidate(wid, sid, page, panel, k)`：复制所选候选为正式图，重新拼装 PNG 与网页预览，不调用图像模型。
- 看板：在「单格重新生成」区域展示候选图并选用；CLI `choose`。

### 1.7 图像调用上限
- 每次操作（漫画渲染、角色表、场景图）的图像调用总数上限，默认 80，可用 `WORLD_FORGE_IMAGE_BUDGET` 调整。
- 超限即停止：已完成的格子保留，返回 `budget_exhausted=True` 与 `image_calls`，日志给出提示。
- 候选 N 张会计入 N 次调用，因此 3 候选的整页渲染会以 3 倍调用计入上限。

### 1.8 渲染流程整理
- 成品拼装（PNG、zip、网页预览）抽为 `_assemble_outputs`，渲染与候选选用共用。
- 渲染参数：`reading`、`color`、`candidates`；单格重新生成同样支持这些参数。

## 2. 改动文件

路径均相对于 `external_projects/world_forge/`：

| 文件 | 类型 | 说明 |
|---|---|---|
| `world_forge/comic_layout.py` | 修改 | 候选上限、角色表键与提示、情绪映射 |
| `world_forge/repo.py` | 修改 | 角色表、场景图的路径与解析 |
| `world_forge/generate.py` | 修改 | `style`、`scene` 任务与校验；分镜 `location`；节拍 `palette` |
| `world_forge/engine.py` | 修改 | 风格提示词、参考图优先级、候选与调用上限、角色表、场景图、风格指南、候选选用、成品拼装抽取 |
| `world_forge/providers/text.py` | 修改 | 离线 stub 的 `style`、`scene` 任务；节拍配色与分镜地点 |
| `app.py` | 修改 | 风格与设定图按钮、候选数设置、候选图选用、新增运行日志类型 |
| `entrypoints/cli.py` | 修改 | 新增 `style`、`sheets`、`scenes`、`choose`；`comic` 增加 `--candidates` |
| `tests/test_comic_p2.py` | 新增 | P2 专项测试（20 项） |
| `docs/README.md` | 修改 | 使用说明、CLI、环境变量 |
| `PROJECT.md` | 修改 | 分期状态与已知限制 |

next_doc 下：新增本记录；更新设计文档状态与索引。

## 3. 验证

- `pytest`：185 项通过（P1 之后为 165 项，P2 新增 20 项）。
- CLI 冒烟：`comic --candidates 2` 产出 16 次图像调用（8 格 × 2 张），`choose --k 2` 成功切换；`sheets`、`scenes`、`style` 在已有故事上正常执行（无需新调用的项目按设计跳过）。
- 测试覆盖：风格指南进入提示词；每页配色；黑白模式去配色；角色表生成、跳过与覆盖；愤怒格参考愤怒表情图；场景图生成并作为参考；候选保存与选用；候选上限为 3；调用上限中止渲染与角色表。

## 4. 已知限制

- 离线 stub 的图像内容相同，无法从视觉上验证表情与场景是否真的被模型采用，需要在有 key 的环境中确认。
- 情绪映射靠关键词，「百感交集」之类的表述会落到立绘。
- 候选之间没有自动排序，全部依赖人工选用（符合 P2 范围；质检属于 P3）。
- 场景图的英文描述由文本模型生成，地点名称与描述需要与设定一致，不一致时需要人工修正 `attrs.visual_en`。
- 调用上限是单次操作的上限，不是按天或按故事累计的预算。

## 5. 下一步

进入 P3：多模态质检与自动重试。需要先确认：质检使用的模型（同一图像/文本模型的视觉能力，或单独配置）、自动重试的次数上限，以及质检不通过时是保留候选还是直接重试。
