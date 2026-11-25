# world_forge 漫画 · 分镜素材可选实施记录

> 问题：漫画分镜的输入是什么？能否选择？
> 测试：`python -m pytest -q tests` 全部通过（255 项，本次新增 7 项）。

## 1. 改动前的输入（说明）

- 有短篇正文（`story.md`）：用正文全文。
- 没有短篇正文：用小故事线的标题、梗概、各幕、结局，加上它的来源事件（`source_event_ids`）。
- 世界中的原始事件只有通过小故事线的来源事件进入，没有直接选项。
- 角色、地点、主体视角、类型与基调始终作为上下文提供。

这个规则不能由用户选择，也不能看出实际用的是哪种素材。

## 2. 改动

- **素材选项**：`auto`（默认）、`short`（短篇正文全文）、`arc`（小故事线与来源事件，忽略正文）、`events`（原始事件全文）。
- **引擎**：`engine._coverage_source(wid, sid, source)` 按选项取素材；选项不满足（如没有正文却选「短篇正文」）时报出明确错误。`make_storyboard(..., source=...)` 记录所选来源，并写入分镜 `source` 与故事的 `comic_opts.source`。
- **缓存**：拆解结果的哈希包含来源名称，切换来源会重新拆解；`coverage.json` 记录来源。
- **分块统一**：所有来源都经过分块，长文本不截断（修复了一处只对正文分块的问题）。
- **界面与 CLI**：看板「漫画设置」新增「分镜素材」选择；CLI `board --source auto|short|arc|events`。

## 3. 改动文件

路径均相对于 `external_projects/world_forge/`：`world_forge/comic_layout.py`（选项与说明）、`world_forge/engine.py`（素材选择、缓存、分块）、`app.py`、`entrypoints/cli.py`、`tests/test_comic_source.py`（新增，7 项）、`tests/test_comic_p1.py`（设置记录新增 source 字段）、`docs/README.md`、`PROJECT.md`。

## 4. 限制

- 「原始事件」是故事来源的事件，不是整个世界的所有事件。若需要整个世界的事件，需要在故事中选入它们。
- 素材越长，拆解的模型调用越多（每个分块一次），选择时请留意成本。
