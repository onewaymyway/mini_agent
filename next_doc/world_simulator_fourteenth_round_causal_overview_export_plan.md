# world_simulator 第十四轮：导出网页补齐「因果线总览」按线视图

## 1. 背景 / 问题

第十三轮（`world_simulator_thirteenth_round_html_export_plan.md`）
落地的 `html_export.export_simulation_html()`，「因果线总览」一节
只有两部分：声明的因果线列表 + `causal_graph.build_causal_graph()`
聚合出的「线到线影响关系」图。这和 `app.py` 详情页「📊 因果线总览」
标签页里真正展示的内容（`_render_causal_lines_overview()`）差距很
大——后者是按每条因果线分别展示：

- 这条线的时间点序列（`line_updates`，按 step 升序排列，例如
  "第 1 月 起步筹备 → 第 2 月 签下首单"）；
- 这条线关联的具体因果链条目（`causal_links.line_id` 过滤）；
- 这条线的「未来因果树」（`future_tree.branches`，按生命周期分组
  展示：潜在/活跃、已发生、已排除）；
- 这条线的简单历史外推（`hypothesis.project_line_futures()`）。

用户反馈："现在导出模拟结果的时候，只有时间线信息，没有因果线总览
信息，应该导出类似模拟界面那样的因果线信息和时间线信息，都是要
时间正序的，两个分开显示，不要放在一起"。

## 2. 方案

在 `world_simulator/html_export.py` 里新增一组函数，把 `app.py::
_render_causal_lines_overview()` 的按线展示逻辑移植一份到导出模块
（继续遵守第十三轮定下的"不 import `app.py`，格式化逻辑独立实现"
的取舍——导出模块要能脱离 Streamlit 运行时单独单测）：

- `_render_causal_line_row()`：单条因果线的完整卡片（时间点序列 +
  关联因果链 + 未来因果树 + 历史外推），去掉「标为已解决/已失效」
  「提修改意见」等只在可交互详情页才有意义的按钮/表单——导出网页
  是纯只读存档，这一点第十三轮已经定过（`html_export.py` 模块
  docstring）。
- `_future_tree_branches_html()` / `_line_futures_html()`：未来
  因果树分组渲染、历史外推渲染，分别独立成小函数方便单测。
- `_render_causal_lines_breakdown()`：聚合入口，处理"声明列表里没
  有、但历史里实际出现过的 line_id 也要展示"这条既有约束（对齐
  `app.py` 同一段逻辑），并调用 `hypothesis.project_line_futures()`
  取历史外推（失败时静默降级为空，不影响总览其余部分）。
- `_render_causal_overview()`：整合成"声明的因果线 → 按线聚合的
  时间点序列/未来因果树/历史外推 → 线到线影响关系图"三段，全部
  按时间正序组织。

**两个板块的独立性**：「因果线总览」和「时间线（正序）」在导出
页面里从第十三轮起就是两个独立的 `<div class="ws-card">`（各自
一节），这次改动延续这个结构，没有把两节内容混排到一起——用户
反馈里"两个分开显示，不要放在一起"这条要求在导出页面结构上一直
成立，本轮只是把"因果线总览"这一节的内容从"只有边聚合图"补充为
"和详情页对齐的按线完整视图"。

**正序**：`_render_causal_line_row()` 遍历的 `history` 由调用方
（`export_simulation_html()`）按 `store.load_history()` 的原始
（升序）顺序传入，不做反转，时间点序列天然按 step 升序排列，和
`_render_timeline()` 的正序要求一致。

**新增依赖**：`html_export.py` 新增 import `causal_tree`
（取 `canonical_status()` 归一化 6 态状态）和 `hypothesis`
（取 `project_line_futures()`），都是纯函数模块，不引入任何新的
运行时/持久化副作用。

**CSS**：`_PAGE_CSS` 里新增 `.ws-causal-line-row` 系列样式和
`.ws-uncertain-*` 系列样式，直接照搬 `app.py::THEME_CSS` 里对应的
定义（同色板/圆角/字号），保证导出页面和详情页视觉风格一致。

## 3. 明确不做的部分

- 不在导出页面里放"标为已解决/已失效""提修改意见""接受因果线
  建议"等交互入口——这些操作会改写 `manifest.settings`，导出网页
  是脱离项目运行时的静态存档，没有落盘路径可以承接这些操作。
- 不新增数据结构，不改动 `future_tree`/`line_updates` 的落盘格式。
- 「因果线建议」区块（`_render_suggested_causal_lines()`，未接受
  的候选线）不纳入导出——那是"待处理的建议"，不是"这次模拟已经
  发生的因果线信息"，纳入反而会让一份只读存档看起来像还能操作。

## 4. 验收

`tests/test_html_export.py` 新增 2 个测试（共 13 个，全部通过）：

- `test_causal_overview_breakdown_by_line_ascending_and_separate_from_timeline`：
  按线的时间点序列正序展示、未来因果树按生命周期分组正确出现、
  关联因果链正确展示、且「因果线总览」整节先于「时间线（正序）」
  出现（两节不混排）。
- `test_causal_overview_breakdown_degrades_for_undeclared_line_ids`：
  没有在 `causal_lines` 里声明、但历史 `line_updates` 里实际出现
  过的 line_id 也能退化展示。

原有 11 个测试（因果线声明+图、SVG 降级、时间线正序等）全部保持
通过，未改动其行为。
