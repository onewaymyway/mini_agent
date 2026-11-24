# 架空世界工坊 · 使用说明

## 启动看板

```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8509
```

## 典型流程

0. **世界观简介**：在「总览」生成一篇背景介绍，快速了解当前世界观；世界推进后可重新生成。
1. **新建**：输入一句概念，生成宪章草案与首批设定。
2. **设定与提案**：「按要求修改设定」可选中对象修改，也可不选，由系统判断涉及哪些对象；「自动补全实体关系」为关系较少的实体建立连接（目标关系数可调），提示中会说明仍有几个实体未达标；页面底部的「关系图」可拖动、缩放、聚焦查看；批准宪章（`set_charter`）。可用「自动完善世界观」一键补全缺口并扩展；或在「按要求扩展」中用一句话提出需求（如「北方增加一个冰原部族」）。查看待确认提案：可勾选后批量批准或拒绝；「全部」按钮需先勾选确认。批量处理按列表顺序逐条检查，单条失败不影响其它条目。
3. **推进**：选择步数，并可设定每步时长与时间单位（分钟、小时、天、星期、月、年）；默认取「设置」中的值，也可只为本次推进临时调整。推进过程的每个阶段（收集状态、模型调用、解析、逐条写入、时钟推进）会实时显示，并保存到世界的运行日志中，可在「运行日志」中回看历史运行。
4. **时间线**：在任一事件之后开新分支（即回退），可在分支间切换与对比。
5. **故事与漫画**：漫画按页排版：分镜决定每格的镜头（远景/中景/近景/特写）、宽度（整行或半行）和对白位置；生成每格时统一画风，并带上出场角色的外观描述和上一格的画面作为参考，以保持人物与画面的连贯；每个操作（生成小故事线、成文、分镜、漫画图、导出）都会在状态框中实时显示运行日志，并保存到「运行日志（故事与漫画）」中回看；选择事件生成小故事线，成文短篇或直接创作；生成分镜、生成漫画图；导出给 novel_writer。

## 命令行

```bash
python entrypoints/cli.py health
python entrypoints/cli.py init --concept "蒸汽与符文并存的海上帝国"
python entrypoints/cli.py approve --world <wid> --proposal <pid>
python entrypoints/cli.py advance --world <wid> --steps 2 --amount 3 --unit day   # 运行日志实时输出到 stderr
python entrypoints/cli.py fill --world <wid>
python entrypoints/cli.py expand --world <wid> --request "北方增加一个冰原部族"
python entrypoints/cli.py relations --world <wid>
python entrypoints/cli.py modify --world <wid> --request "把北港改为冬季封冻"
python entrypoints/cli.py enrich --world <wid>
python entrypoints/cli.py overview --world <wid>
python entrypoints/cli.py arc --world <wid> --from 0 --to 30
python entrypoints/cli.py short --world <wid> --arc <sid>
python entrypoints/cli.py board --world <wid> --story <sid>
python entrypoints/cli.py comic --world <wid> --story <sid>
python entrypoints/cli.py export --world <wid> --story <sid>
```

## 环境变量

| 变量 | 作用 |
|---|---|
| `WORLD_FORGE_PROVIDERS=stub` | 全部离线（规则文本 + 占位图），不消耗额度 |
| `WORLD_FORGE_DATA_DIR` | 数据目录，默认 `data/` |
| `MINI_AGENT_ROOT` | mini_agent 仓库根，用于找到 LLMHelper 与 gen_image_with_text skill |
| `WORLD_FORGE_SKILLS_DIR` | 手动指定 `.claude/skills` 目录 |
| `AGNES_API_KEYS` / `AGNES_API_KEY` | 图像模型 key（与 skill 规则相同） |

## 测试

```bash
python -m pytest -q tests
```

## 时间与历法

- 时钟以分钟为基本单位，推进步长 = 数值 × 单位。
- 架空历法：1 小时 = 60 分钟，1 天 = 24 小时，1 星期 = 7 天（周一至周日），1 月 = 30 天，1 年 = 12 月。
- 显示格式：`第 Y 年 M 月 D 日 周X HH:MM`，年、月、日从 0/1 起编号。
- 旧版本的世界（按年推进）会自动转换，无需手动处理。

## 角色与人设图

- 「角色」页每张卡片左侧显示人设图，并可单独「重新生成人设图」，历史版本可在卡片中展开查看。
- 「角色」页的「一键生成所有人设图」会为存活且还没有人设图的角色批量生成；勾选「覆盖已有人设图」则全部重新生成（旧图保留在 history/）。
- 批量生成在后台运行，逐个角色显示进度，可随时停止，已生成的人设图保留。

## 停止与重新生成

- **停止**：推进、生成小故事线/短篇/分镜/漫画图、世界观简介、立绘等耗时操作在后台运行。页面顶部的任务条实时显示日志，点击「停止」后，操作在下一个安全点结束（每次模型调用前、每一步或每一格之间）。当前正在进行的模型调用会完成，已写入的内容全部保留；漫画会拼装已完成的格子。同一世界同一时间只能有一个后台任务。
- **重新生成**：
  - 小故事线：沿用原来的事件重新生成，故事 id 不变。
  - 短篇：沿用创建时的原始输入重新成文。
  - 分镜：重新生成后，漫画图需要重新生成。
  - 漫画：可以重新生成全部格子，也可以只重新生成某一格，其它格保留原图。
  - 立绘、世界观简介：直接覆盖。
- **旧版本**：重新生成前，旧文件会保存到对应目录的 `history/` 下（带时间戳），故事页的「旧版本」里可以查看。
- **暂不支持**：推进历史的"重新推进"（需要用分支回退实现）；扩展世界观、关系补全、按要求修改设定仍为同步操作，暂无停止按钮。
