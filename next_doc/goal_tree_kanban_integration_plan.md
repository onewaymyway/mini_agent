# 目标树看板集成方案（Stage 6：树级报告 / 节点详情 / Wiki 浏览 / 反馈闭环接入看板）

- **状态**：设计中，未实施。
- **前置文档**：`next_doc/goal_tree_visibility_wiki_and_report_plan.md`
  （本文档是它 §6 开放问题"看板集成延后"的落地方案，Stage 编号紧接
  该文档的 Stage 1-4，记为 **Stage 6**——避免跟该文档自己的 Stage 5
  （LLM 总结层，未实施）编号冲突）。
- **触发背景**：用户在实际使用目标树系统时反馈"现在感觉只能编辑树形
  目标，但是无法方便地查看每个目标的进度、产出内容"，并提出三个具体
  诉求：① 产出应该有个类似人生目标 wiki 目录的东西方便浏览管理；
  ② 应该有个能输出整个目标树状态的报告机制；③ 反馈交互方式也需要更
  合理的设计。经代码核实，这三项能力在 `perception/goal_tree_report.py`
  / `perception/goal_node_page.py` / `evolution/goal_wiki.py` /
  `goal_backlog.py::add_user_feedback()` 里**均已实现并有 CLI/REST 入口
  和回归测试**（见前置文档 Stage 1-4），唯独没有接入看板
  （`apps/mini_agent_kanban/app.py`）——这正是用户实际感知到"缺失"的
  原因：用户是看板用户，看不到 CLI 命令行为，自然感觉不到这些能力
  存在。

---

## 0. 现状盘点：这次要接的是什么

前置文档 Stage 1-4 已经把后端能力做完，逐一列一下现成可以直接调用的
东西，本文档不重新设计任何聚合逻辑，只做"看板怎么把这些数据摆出来、
交互怎么接"：

| 能力 | 后端函数 | CLI | REST | 数据结构 |
|---|---|---|---|---|
| 树级汇总报告 | `goal_tree_report.build_goal_tree_report()` | `goals report [root_id]` | `GET /v1/goals/tree_report?root_id=...` | `GoalTreeReport`：`by_phase`/`by_status`/`stuck_or_alerted`/`cron_unhealthy`/4 类 `pending_*` 待办列表 + `pending_feedback`/`recent_outputs_digest` |
| 节点详情页 | `goal_node_page.build_goal_node_page()` | `goals show <id>` | `GET /v1/goals/{goal_id}/page` | `GoalNodePage`：面包屑/进度/产出扫描/子节点导航/待处理项/反馈历史 |
| 产出 Wiki 落盘 | `goal_wiki.build_goal_wiki_tree()` | `goals wiki build [root_id]` | `POST /v1/goals/wiki/build?root_id=...` | 落盘到 `.agent/daemon_run_outputs/goals_wiki/<id>/index.md`，返回 `{root_id, rendered_goal_ids, rendered_count}` |
| 反馈（可关联待办项） | `goal_backlog.add_user_feedback()` | `goals feedback <id> <text> [--about ...]` | `POST /v1/goals/{goal_id}/feedback`（body 新增 `about`） | 反馈条目 `{text, at, status, about?}`，`status` 会随关联待办被处理自动变 `addressed` |

看板目前对目标树相关能力的覆盖情况（`apps/mini_agent_kanban/app.py`
第 5020 行起"目标树"区块）：

- ✅ 树形结构渲染、展开/收起（`goal_tree_collapse_plan.md`）
- ✅ 分解候选 采纳/忽略（`_render_goal_tree_candidates`）
- ✅ 焦点 pin/unpin、改父节点、研究建议区块
- ❌ **没有**报告入口、详情面板、wiki 浏览、反馈状态展示——`client.py`
  里甚至还没有 `goal_tree_report()`/`goal_node_page()`/
  `build_goal_wiki()` 这几个方法的封装，`add_goal_feedback()` 也还
  不支持传 `about`。

## 1. 设计原则（延续前置文档）

- **只读展示优先，不新增判定逻辑**：看板只负责调用现成的 REST 端点、
  排版展示、把用户点击转发成已有的写操作（`accept_candidate`/
  `confirm_tuning_proposal`/`add_user_feedback` 等），不在前端或本次
  改动里新增任何"综合评分"或自动化判断。
- **复用现有交互原语**：折叠区用 `st.expander`，弹窗式详情用
  `st.dialog`（看板其它模块已有先例，见下），列表用
  `st.dataframe`/循环 `st.container`，不引入新的 UI 库或自定义组件。
- **不做"看板专属"的新后端字段**：前端要展示的所有信息，`GoalTreeReport.
  to_dict()` / `GoalNodePage.to_dict()` 已经覆盖；如果实施过程中发现
  某个信息展示不了，优先检查是不是现有字段没读全，而不是急着加后端
  字段——这条原则是为了防止"看板改造"演变成"后端能力返工"，两件事
  分开推进。
- **改动集中在"目标树"这一个子页**：`_render_goal_tree_view()` 已经是
  看板"📌 目标看板" tab 内部按钮切换出来的一个视图（`_goal_view_mode_
  radio`），不是独立顶层 tab，本次新增的三块内容（报告/详情/wiki）都
  挂在这个子页下，不新开顶层 tab，避免 tab 数量进一步膨胀（现在已经
  22 个）。

## 2. 信息架构

目标树子页从现在的"列表/看板视图 + 🌳 目标树"二选一 `st.radio`，改成
在 🌳 目标树视图内部再切一层：

```
📌 目标看板
 └─ 视图选择（现有 st.radio）：📋 列表/看板视图 | 🌳 目标树
      └─ 🌳 目标树（现有树形渲染，本次基础上追加）
           ├─ 顶部新增一排按钮/expander：
           │    📊 全局报告   📖 产出 Wiki   🔄 重建 Wiki
           ├─ 树形结构渲染（现状不变，`_render_goal_tree_node()`）
           │    └─ 每个节点标题旁新增一个 "📄 详情" 按钮
           │         → 点击展开 st.dialog（或同位置展开面板）展示
           │           GoalNodePage 内容 + 反馈输入框
           └─ （原有的候选/焦点/研究建议区块，位置不变）
```

选择"顶部追加 + 节点旁加按钮"而不是整体重做树形渲染，是因为
`_render_goal_tree_node()` 递归渲染逻辑本身跑得稳定（`goal_tree_
collapse_plan.md` 刚优化过折叠性能），本次不动它，只在其基础上叠加。

## 3. 分项设计

### 3.1 `client.py` 新增方法（前置，三块功能共用）

```python
class AgentClient:
    def goal_tree_report(self, root_id: str | None = None):
        params = {"root_id": root_id} if root_id else {}
        return self._get("/goals/tree_report", params=params)

    def goal_node_page(self, goal_id: str):
        return self._get(f"/goals/{goal_id}/page")

    def build_goal_wiki(self, root_id: str | None = None):
        params = {"root_id": root_id} if root_id else {}
        return self._post("/goals/wiki/build", params=params)  # 注意：query 参数，不是 body

    def add_goal_feedback(self, goal_id: str, text: str, about: str | None = None):
        """[Stage 6] 补上 `about` 可选参数，向后兼容——原有调用点不传
        `about` 时行为不变。"""
        body = {"text": text}
        if about:
            body["about"] = about
        return self._post(f"/goals/{goal_id}/feedback", body)
```

`add_goal_feedback()` 是**修改**现有方法签名（加一个可选参数），不是
新增方法——`client.py` 第 790 行已有的调用点（如果有）不传 `about`
时行为完全不变，需要在实施时搜一遍现有调用点确认没有位置参数调用
方式会因为加参数而错位（Python 关键字参数默认值不影响已有的位置调用，
但仍要过一遍确认）。

### 3.2 能力 B 接入：📊 全局报告

新增 `_render_goal_tree_report_panel(client, root_id=None)`，挂在
`_render_goal_tree_view()` 顶部，默认收起的 `st.expander("📊 全局报告",
expanded=False)`：

- 展开时调用 `client.goal_tree_report(root_id)`（`root_id` 取当前树形
  视图的根，全局视图传 `None`），拿到 `tree_report` dict。
- 展示内容（对应 `GoalTreeReport` 字段，按信息价值从高到低排列）：
  1. **顶部一行摘要**：`node_count` 个节点，按 `by_status` 统计的
     active/completed/... 数量（`st.metric` 横排几个）。
  2. **待办清单**（价值最高的部分，前置文档已强调）：`pending_
     decompose_candidates` / `pending_focus_confirmation` /
     `pending_tuning_proposals` / `pending_execution_specs` /
     `pending_feedback` 五类分别用一个小标题 + 列表展示，每一项都是
     `{id, title, ...}` 形状，渲染成"`<title>` [跳转到详情]"，跳转
     方式见 §3.5。
  3. **健康告警**：`stuck_or_alerted` / `cron_unhealthy` 两个列表，
     每项标红/加警告图标展示 `message`。
  4. **产出速览**：`recent_outputs_digest`，每个活跃节点一行"`<title>`：
     `<task_summary>`"。
- **不做**：Stage 5 的 LLM 自然语言总述——`report.llm_summary` 字段
  当前后端默认不生成（前置文档已说明"可选、视效果再定"），看板这里
  只要字段非空就展示，为空不显示，不主动触发生成。

### 3.3 能力 A 接入：节点详情面板

`_render_goal_tree_node_body()` 现有的节点行（标题 + 状态 + 操作按钮）
旁边加一个 `📄` 按钮，点击后用 `st.dialog` 弹出详情（看板其它模块如果
已有 `st.dialog` 用法则复用同一套装饰器写法，没有的话这是本项目第一次
引入，需要确认 Streamlit 版本支持——`st.dialog` 是 1.31+ 特性，实施
前先确认 `requirements.txt` 里 `streamlit>=1.30.0` 是否需要一并上调
下限，或改用"同位置展开的 `st.expander`"作为兼容降级方案）。

弹窗/展开区内容（`_render_goal_node_detail_panel(client, goal_id)`）：

1. **面包屑**：`path_from_root` 拼成 `根 > ... > 当前` 一行文字。
2. **进度**：`execution_phase_mode`、`recent_cycle_summaries`（最近几条
   摘要）、`progress_notes_tail`（原文本框展示，只读）。
3. **产出**：`output_readme_text` 直接用 `st.markdown()` 渲染（这段
   文本本身就是 `render_output_readme()` 生成的 Markdown，不需要额外
   加工）；`output_structure` 里如果有子目录/文件列表，配合已有的
   `_render_goal_output_manifests()`（第 3249 行，已经存在的产出清单
   渲染函数）看是否可以直接复用展示逻辑，避免重复造一套文件列表 UI。
4. **子节点导航**：`children` 列表，每项一个按钮"查看 `<title>`"，
   点击后 `st.session_state` 记录目标 `goal_id` 并 `st.rerun()`，
   弹窗内容随之切换（同一个 dialog 组件展示不同 goal_id 的数据，不用
   每个子节点开一个新弹窗）。
5. **待处理项** + **反馈历史**：反馈历史每条展示 `text` + 时间 +
   状态图标（`pending` 用 ⏳，`addressed` 用 ✅，参照前置文档 Stage 4
   "展示层"约定的图标）；下方一个文本输入框 + "提交反馈"按钮，调用
   `client.add_goal_feedback(goal_id, text)`（先只做"笼统反馈"，不在
   Stage 6 首个版本里做"关联到某条具体待办项"的下拉选择，见 §5 开放
   问题）。

### 3.4 能力 A 后半接入：📖 产出 Wiki 浏览

新增 `_render_goal_wiki_panel(client, root_id=None)`，挂在报告面板
旁边，同样是默认收起的 `st.expander("📖 产出 Wiki", expanded=False)`：

- 顶部一个"🔄 重建 Wiki"按钮，调用 `client.build_goal_wiki(root_id)`，
  成功后 `st.success(f"已刷新 {rendered_count} 个节点")`。
- 浏览方式：**不需要新增导航逻辑**——直接复用 §3.3 已经渲染出来的
  树形结构作为导航入口，每个节点旁再加一个"📖"按钮，点击后用
  `client.fs_read(f".agent/daemon_run_outputs/goals_wiki/{goal_id}/
  index.md")`（`fs_read` 是看板已有的通用文件读取方法，第 1490 行
  `client.py`）拿到 Markdown 原文，`st.markdown()` 直接渲染展示。
  这样不用维护一套独立的"wiki 树浏览器"，复用目标树本身的结构作为
  wiki 的导航——两者结构本来就是同构的（`goal_wiki.py` 渲染时子节点
  链接就是按目标树的父子关系生成的）。
- 找不到对应文件时（该节点还没生成过 wiki 页）提示"该节点尚未生成
  Wiki 页，点击上方「重建 Wiki」生成"，不报错。

这样"📄 详情"和"📖 wiki"其实是同一份 `GoalNodePage` 数据的两种呈现——
详情面板是"实时聚合的当前状态"，wiki 页是"上一次落盘的静态快照"，
两者内容通常一致（wiki 页就是详情页的 Markdown 渲染），差异只在于
wiki 页可能不是最新的（要等 tidy 阶段触发或手动重建）。这一点在
UI 上需要有一行小字提示，避免用户误以为两者是两套不同的信息。

### 3.5 待办清单的"跳转到详情"交互

§3.2 报告面板里的每一条待办（`pending_decompose_candidates` 等）本身
携带 `id`（候选 id / 提案 id / spec 版本号等）和所属节点的 `goal_id`
（需要确认 `GoalTreeReport` 里每个 `pending_*` 条目的 dict 是否已经
带了 `goal_id` 字段——如果没有，这是唯一可能需要回头找后端补一个字段
的地方，其余全部复用现成结构）。跳转方式：

```python
if st.button(f"→ {item['title']}", key=f"jump_{item['id']}"):
    st.session_state["_goal_tree_detail_target"] = item["goal_id"]
    st.rerun()
```

`_render_goal_tree_view()` 读取 `_goal_tree_detail_target`，如果非空
就直接展开对应节点的详情面板（而不是要求用户先在树里找到这个节点再
点开）——这是"点击待办直接跳转到对应节点"这个前置文档 §3.1 已经设想
过的交互，本次落地。

## 4. 实施步骤（建议顺序）

按"风险从低到高、独立性从强到弱"排序，每步都可以单独提交 + 跑通：

1. **`client.py` 四个方法**（§3.1）——零 UI 改动，先写单测（mock
   HTTP 层，校验请求路径/参数正确）再接入界面。
2. **📊 全局报告面板**（§3.2）——纯展示，不涉及交互跳转，风险最低，
   独立验证"数据到不到位、字段展示对不对"。
3. **📖 产出 Wiki 浏览**（§3.4）——同样纯展示 + 一个刷新按钮，复用
   `fs_read`，不涉及新交互模式。
4. **📄 节点详情面板**（§3.3）——涉及 `st.dialog`（或降级方案）和
   子节点切换的状态管理，复杂度最高，放在数据展示两块验证完之后做。
5. **待办跳转联动**（§3.5）——依赖 1-4 全部就位，最后做。

## 5. 测试计划

- `tests/test_kanban_client_goal_tree_extras.py`（新建）：覆盖
  `client.py` 新增/修改的四个方法，mock HTTP 响应，校验请求方法/
  路径/参数（`root_id` 省略时不传 query 参数、`about` 省略时 body
  里不出现该 key，保证向后兼容）。
- 看板 UI 部分（`_render_goal_tree_report_panel` 等渲染函数）参照
  仓库里其它 Streamlit 渲染函数的既有测试方式（如果有——需要实施时
  查一下 `tests/` 下是否有对 `app.py` 渲染函数的既有测试模式，比如
  `test_kanban_growth_dragdrop.py` 的做法）：优先测"给定 mock 的
  `client` 返回值，渲染函数不抛异常、关键文案出现"，不测 Streamlit
  的像素级渲染。
- 手动验收清单：
  - 全局报告展开后，待办数量与 CLI `goals report` 输出一致
  - 点击某条待办能跳转到对应节点详情
  - wiki 浏览能看到跟 `goals wiki build` 手动生成的文件内容一致
  - 详情面板提交反馈后，`goals show <id>` 能看到这条新反馈

## 6. 开放问题

- **`st.dialog` 的版本依赖**：需要实施前确认当前 `requirements.txt`
  锁定的 Streamlit 版本是否支持；如果看板已经在别处用过 `st.dialog`
  可以直接对齐写法，否则要么升级最低版本要求，要么改用同位置展开的
  `st.expander` 作为详情面板的呈现方式（牺牲"弹窗"体验，换取不动
  依赖版本）。
- **待办条目是否携带 `goal_id` 字段**：§3.5 的跳转交互依赖这一点，
  需要实施时先读一遍 `goal_tree_report.py` 里几个 `pending_*` 列表的
  组装代码确认，如果缺失，是本方案里唯一可能需要回头改一行后端代码
  的地方（补充字段，不改变现有语义）。
- **反馈"关联到具体待办项"的下拉选择未纳入 Stage 6 首个版本**：
  §3.3 第 5 点提到，首版详情面板的反馈输入只做"笼统反馈"（不传
  `about`），因为要在 UI 上把"当前节点有哪些未处理待办项"列出来让
  用户选择关联哪一条，需要待办清单和详情面板两块数据打通，复杂度
  较高；建议 Stage 6 先跑通"看得见"这一半（报告/详情/wiki），"关联
  反馈到具体待办"作为 Stage 7 的独立小需求再做，不在本次范围内搞大。
- **wiki 浏览是否需要独立于目标树结构的"仪表盘视图"**（按状态/更新
  时间筛选排序，而不是必须按树形结构点击下钻）：这是此前讨论中提到
  的一个想法，本方案 §3.4 为了控制改动范围，暂时只做"复用树形结构
  导航"这一种方式；如果用户实际用起来之后觉得树太深、找起来还是麻烦，
  再考虑加一个独立的列表/筛选视图，不在 Stage 6 首版做。
- **"选中多条待办统一批量处理"**（此前讨论提到的批量 accept 想法）：
  同样不在 Stage 6 范围内——批量操作涉及到给每一类待办分别设计批量
  接口（现有 `accept_candidate`/`confirm_tuning_proposal` 等都是单条
  处理的端点），是一个独立量级的需求，留到 Stage 1-4/Stage 6 使用
  一段时间、验证"全局待办清单"确实好用之后再评估是否需要。
