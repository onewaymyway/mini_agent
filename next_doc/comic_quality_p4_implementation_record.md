# world_forge 漫画质量升级 · P4 实施记录

> 对应设计：`comic_quality_upgrade_design.md` 第 4 节（P4）
> 前置：P1–P2 与上限配置（P3 质检已取消）
> 测试：`python -m pytest -q tests` 全部通过（204 项，其中 P4 新增 9 项）

## 1. 做了什么

- **网页阅读器** `reader.html`：每次拼装成品时生成。引用同目录 `pages/` 的 PNG 成品页；翻页按钮、方向键（从右到左时反向）、连续滚动。
- **CBZ**：`comic.cbz`，包含各页 PNG 与 `ComicInfo.xml`。从右到左的漫画写入 `YesAndRightToLeft`，从左到右写入 `No`。
- **条漫长图**：`comic_strip.png`，成品页按顺序竖向拼接，宽 1200 像素，页间无间距。
- **编辑文字**：`edit_panel_text` 修改某格的旁白、音效、对白文字。说话人与位置不变；对白数量不可增减；对白超过 40 字或为空时报错。修改后该格标记 `edited`，只重新拼装成品，不调用图像模型。
- **看板与 CLI**：故事页新增导出按钮与下载按钮、文字编辑面板；CLI 新增 `comicexport`、`text`。原有的 `export`（导出给 novel_writer）保持不变。

## 2. 改动文件

路径均相对于 `external_projects/world_forge/`：

| 文件 | 说明 |
|---|---|
| `world_forge/engine.py` | `render_reader_html`、`export_comic_files`、`edit_panel_text`；拼装时写入 reader.html |
| `app.py` | 导出按钮、下载按钮、文字编辑面板 |
| `entrypoints/cli.py` | `comicexport`、`text` 命令 |
| `tests/test_comic_p4.py` | 新增 9 项测试 |
| `docs/README.md`、`PROJECT.md` | 使用说明与状态 |

## 3. 验证

- `pytest`：204 项通过。
- 阅读器脚本用 `node --check` 检查语法通过（阅读器本身未在浏览器中手动打开过，需要在浏览器中确认翻页与方向键）。
- 测试覆盖：阅读器列出页面与方向；CBZ 内容与 ComicInfo 标记；条漫长图尺寸；无成品页时报错；文字编辑重绘且不调用图像模型；对白数量与长度校验；未知格报错。

## 4. 待确认与已知限制

- **范围待确认**：CBZ、条漫长图、文字编辑是按设计文档实现的，你之前只确认了 PNG 导出。若不需要，可以直接删除对应函数与按钮，不影响其它功能。
- **阅读器依赖相对路径**：`reader.html` 引用 `pages/`，单独复制 HTML 会失效。
- **条漫不重排**：长图只是把成品页拼接，没有为竖向阅读重新排版。
- **看板编辑只改文字**：不能拖动气泡、调整格子大小或版式。
- **重新生成分镜会覆盖手动编辑**：编辑保存在分镜中，重新生成分镜后需要重新编辑。

## 5. 结束语

按设计文档，P1–P4 已全部实施，P3 质检按决定取消。后续若要扩展（如版式拖动、按故事累计预算、批量编辑），需要另行确认。
