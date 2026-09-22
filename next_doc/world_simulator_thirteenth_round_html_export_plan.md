# world_simulator 改进计划：模拟结果导出为静态网页
# （第十三轮）

> **状态**：待实施。

## 0. 这份文档是什么

用户要求新增一个功能：把一次模拟（某个 `sim_id` 在某条 `branch` 下）
导出成一个**自包含、可下载、脱离项目也能打开**的 HTML 网页，内容包括
模拟输入/背景、因果线总览、时间线（**正序**展示，区别于详情页现有的
倒序展示）、以及这条分支生成过的复盘报告（如果有）。

这是一个纯新增的展示层功能，不改变任何已有数据结构、不影响任何
推进/校验逻辑——`SimState`/`SimManifest`/`causal_graph.py`/
`retrospective.py` 现有字段已经足够支撑，不需要新增任何持久化
字段。这一点和第十二轮第 3～6 节"复用既有扩展模式"的取舍一致，
但本轮连字段扩展都不需要，纯粹是"读现有数据、换一种排版方式"。

---

## 1. 为什么要单独写正序渲染，而不是复用详情页现有的时间线代码

`app.py` 详情页的时间线是"倒序 + 折叠卡片 + 交互控件"，是为**持续
操作**场景优化的（让用户第一眼看到最新进展，历史步骤默认折叠，
减少滚动）。导出的 HTML 是**一次性通读的静态报告**，倒序反而要
来回对照因果顺序，所以必须正序、全展开、无交互。

两者**共享格式化逻辑**（`capabilities_gained` 图标映射、
`_confidence_label()`、`_causal_graph_edges_to_dot()` 等纯函数），
但**不共享布局代码**——这是两种消费场景，硬合并成一份反而让两边
都变复杂，这个取舍延续项目一贯的"渲染函数按场景拆分、格式化逻辑
按数据类型复用"的风格。

---

## 2. 模块设计

新增 `world_simulator/html_export.py`，对外只暴露一个函数：

```python
def export_simulation_html(
    data_dir: Path,
    sim_id: str,
    branch: str = "main",
) -> str:
    """返回一个完整的、自包含的 HTML 字符串。不写任何文件、不产生
    任何副作用——纯函数，调用方（app.py）自己决定怎么处理返回值。
    """
```

**自包含**是硬要求：不引用任何 CDN，所有 CSS 内联在 `<style>` 里，
因果图渲染成内嵌 SVG（见第 4 节），不依赖任何客户端 JS 运行时。
导出的文件双击用浏览器打开就能看，脱离 Streamlit/Python 环境。

`app.py` 详情页新增一个"📤 导出为网页"按钮：调用
`export_simulation_html()` 拿到字符串，直接喂给
`st.download_button(data=html_str, file_name=f"{sim_id}_{branch}.html",
mime="text/html")`——不落盘中间文件，和看板现有"生成即下载"的一贯
风格一致（对照 `retrospective.py` 的复盘报告是落盘的，两者不矛盾：
复盘报告要长期保留供后续复盘引用，导出的 HTML 是一次性产物，用户
自己决定存在哪）。

---

## 3. 内容结构与数据来源

```text
1. 页头
   - manifest.title / sim_id / branch（如非 main，注明从
     <parent_branch> 第 <fork_step> 步分叉而来，取自
     branch_manager 的分支元信息）
   - manifest.status 状态徽章、导出生成时间戳

2. 模拟输入 / 背景
   - manifest.intent（原始一句话意图）、manifest.template、
     manifest.created_at
   - 初始状态 state_history[0].vars（格式化展示，不是原始 JSON dump）
   - manifest.settings.desired_state（conditions/constraints/
     assumptions，含 per_entity，多主体模式下按主体分组展示）
   - manifest.settings 里对读者有意义的部分：objectives、
     resource_fields、time_granularity_mode（内部实现细节如
     resource_relations 的 tolerance 数值不展示，只展示"声明了
     哪些资源/关系"这个事实）

3. 因果线总览
   - manifest.settings.causal_lines（声明了哪些因果线，各自的
     一句话描述）
   - causal_graph.build_causal_graph(history) 算出的线到线邻接
     关系，渲染成内嵌 SVG（见第 4 节）；无因果线数据时这一节展示
     "本次模拟未声明因果线"占位文字，不是空白

4. 时间线（正序，step 0 → 最新）
   - 每个 SimState 一张卡片，按 step 升序排列：
     - time_label + time_granularity（有变化时标注
       granularity_changed 的切换理由）
     - summary / narrative
     - "如何走到这一步"：上一个状态的 chosen_option_id 对应的
       选项文本 + chosen_by（user/autopilot）+ chosen_reason
       （state0 没有这一段）
     - key_drivers / causal_links（有则展示，含 relation_type/
       delay_steps/magnitude）
     - capabilities_gained（复用 app.py 的图标映射：
       first_occurrence ⭐、capability_kind 🔧/🏢/📜）
     - major_decision == True 时整张卡片加醒目边框
     - resource_violations / relation_violations（有则标注"系统
       纠正/不一致提示"，不隐藏这类透明性信息）

5. 复盘报告（可选，取决于这条分支是否生成过）
   - retrospective.load_for_branch(data_dir, sim_id, branch) 取
     全部记录，按生成时间**倒序**列出（复盘是"回头看"的产物，多次
     复盘之间不要求正序，用户更关心"最近一次复盘怎么说"）
   - 每条记录：生成时间、覆盖的 step 范围、五个板块
     （turning_points / what_went_well / what_to_reflect_on /
     lessons / caveats）
   - 这条分支一次复盘都没生成过时，整节不渲染（不留"暂无复盘"的
     空标题——参考 `_render_first_occurrence_milestones()` 等既有
     折叠区"没有记录就不占版面"的一贯处理）
```

---

## 4. 因果线图的静态化方案

现有 `st.graphviz_chart()` 依赖 Streamlit 运行时调用 Graphviz 的
`dot` 二进制实时渲染，导出的 HTML 脱离 Streamlit 进程后没法这样做。

**方案**：`export_simulation_html()` 在还处于 Streamlit 进程内、
`dot` 命令还可用的那一刻，直接调用
`graphviz.Source(dot_str).pipe(format="svg")` 把 DOT 字符串渲染成
SVG 字节，解码后把 SVG 原文本**内嵌**进 HTML（`<div class="causal-graph">
<svg>...</svg></div>`）。这样导出的网页里因果图是一段纯 SVG 标签，
静态、可缩放、不需要任何 JS 运行时。

**降级路径**：如果这一步渲染失败（导出运行环境没装 `dot` 二进制、
或 DOT 字符串本身有问题），捕获异常后降级为
`causal_graph.format_edges_for_display()` 现成的纯文字列表版本，
不让整个导出因为图渲染失败而失败——这个降级和 `resource_guard.py`
"校验失败不拒绝推进，只留痕"是同一种"部分失败不影响整体可用性"的
取舍。

---

## 5. 边界情况

- **多分支**：导出按调用时传入的 `branch` 为准，**不在一次导出里
  塞进所有分支**——和"对比视图是独立页面、不混进详情页"的既有取舍
  一致。要导出多条分支对比，用户分别导出多次（后续如果有真实需求
  再考虑要不要做"对比导出"，本轮不做）。
- **`state0` 没有 `chosen_*` 字段**：时间线第一张卡片不渲染"如何
  走到这一步"这一段，不是留空展示，是整段不出现。
- **多主体模式**：`desired_state.per_entity` 存在时按主体分组展示；
  不存在时展示全局 `conditions`/`constraints`/`assumptions`；两者
  可以同时出现（`state_model.py` 里两者本来就不互斥）。
- **超长模拟（几十上百步）**：不做分页/懒加载——导出的初衷就是
  "留一份完整存档"，单文件几百 KB～几 MB 级别的 HTML 是可接受的；
  如果真实使用中发现文件大到浏览器打开卡顿，作为后续观察项处理，
  本轮不预先加复杂度。

---

## 6. 明确不做的范围

- 不支持导出为 PDF/Word——HTML 本身可以用浏览器"打印为 PDF"，
  重复实现一条导出通道收益不明确
- 不支持一次导出打包多个实例/多条分支
- 不在导出的网页里嵌入任何"继续推进"之类的交互能力——纯只读存档，
  不是迷你版看板
- 不做导出历史记录/导出结果去重缓存——每次点击都是一次全新生成
- 不做"导出后自动上传/分享链接"——`world_simulator` 项目本身不
  依赖任何外部托管服务，导出产物完全交给用户自己处理

---

## 7. 落地步骤与验收

1. `world_simulator/html_export.py`：
   - `export_simulation_html()` 主函数
   - 内部拆出 `_render_header()` / `_render_input_and_background()` /
     `_render_causal_overview()` / `_render_timeline()` /
     `_render_retrospectives()` 几个子函数，每个返回一段 HTML 片段
     字符串，主函数负责拼接——便于单独测试每一节的输出
   - CSS 沿用项目已有"夜航日志"深靛蓝底 + 灯笼金点缀主题，保持
     导出网页和看板视觉一致
2. `app.py`：详情页新增"📤 导出为网页"按钮（放在现有"分支"区块
   附近，同一处操作面板）
3. `tests/test_html_export.py`：
   - 意图/背景/因果线（含降级路径）/时间线正序/复盘报告存在与
     不存在两种情况分别断言关键字段出现在输出字符串里
   - `state0` 单独一步（没有 `chosen_*`）不报错
   - 多主体 `per_entity` 分组展示正确
   - graphviz 渲染抛异常时能正确降级为文字列表，不影响其余内容
     渲染

---

## 8. 参考资料

- `world_simulator/state_model.py`——`SimState`/`SimManifest` 全部
  字段定义，本文档第 3 节内容结构的直接依据。
- `world_simulator/causal_graph.py`——`build_causal_graph()`/
  `format_edges_for_display()`，第 4 节因果图渲染与降级方案的
  复用基础。
- `world_simulator/retrospective.py`——`load_for_branch()`/
  `RetrospectiveReport`，第 3 节第 5 部分的数据来源。
- `world_simulator/branch_manager.py`——分支元信息读取，第 3 节
  页头"从哪条分支分叉而来"的数据来源。
- `app.py`——现有 `st.graphviz_chart()`/`_causal_graph_edges_to_dot()`/
  `_confidence_label()` 等可复用的纯函数，以及"夜航日志"CSS 主题。
- `next_doc/world_simulator_twelfth_round_exploration_and_deferred_
  directions_plan.md`——本文档延续的编号（第十三轮）及"范围克制、
  明确记录不做的部分"的写作惯例。
