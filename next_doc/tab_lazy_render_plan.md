# Streamlit 看板：Tab 按需渲染改造（tab_lazy_render_plan）

## 背景问题

`apps/mini_agent_kanban/app.py` 的 `main()` 原来用 `st.tabs([...])` 组织
20 个功能 Tab。`st.tabs()` 只是把内容分组到不同的 CSS 容器里做视觉切换，
Streamlit 脚本本身还是从头到尾整体重跑一遍——不管触发 rerun 的交互发生
在哪个 tab 里（哪怕只是"💬 对话"里发了一条消息、侧边栏点了个下拉框），
20 个 `render_*_tab()` 全部会被无条件调用一遍，各自内部该发的 HTTP
请求也都会发出去。

更进一步：部分 tab 内部挂着 `@st.fragment(run_every="Ns")` 的后台轮询
板块（"💬 对话"的消息流/事件流，"🗂️ 会话管理"的变更横幅/置顶面板）。
`st.fragment(run_every=...)` 语义是"在服务端按固定周期自己重跑"，同样
不感知浏览器里这个 tab 是否可见——只要它所在的 `render_*_tab()` 被调用
过一次（把它"挂载"上了），就会在服务端持续按周期轮询，跟用户有没有
点开那个 tab 毫无关系。也就是说，从看板打开的那一刻起，这几个 fragment
就在后台常驻轮询，不管用户是不是一直停在别的 tab 上。

## 目标

只渲染用户当前选中的那一个 tab，避免没打开过（或已经切走）的 tab 产生
不必要的后台请求 / fragment 轮询。

## 方案：用"假 tabs"替换 `st.tabs()`

`st.tabs()` 本身没有懒加载能力，也没有官方 API 支持"程序化切到第 N 个
tab"或"判断用户当前停在哪个 tab"。唯一的解法是不用它做真正的内容
分发，改成：

1. 用 `st.session_state["_active_tab"]` 持久记录当前选中的 tab（key，
   非展示 label），默认 `"chat"`（💬 对话）。
2. 顶部渲染一排 `st.button` 模拟"假 tab"外观：选中态用
   `type="primary"`（实心红底），未选中用 `type="secondary"`（描边）。
3. 点击某个按钮时，把 `_active_tab` 设成对应 key 并 `st.rerun()`——
   `st.button` 点击本身就会触发一次全量 rerun（不像 `st.tabs()` 切换是
   纯前端 CSS 行为）；rerun 之后只有 `_active_tab` 对应的一个分支会被
   执行，其余 19 个 `render_*_tab()` 根本不会被调用，包括它们内部挂载
   的 `@st.fragment(run_every=...)` 也不会被创建。

`TAB_DEFS`（`app.py`，紧邻 `main()` 定义）是唯一的 tab 清单来源：
`(key, label, render_fn)` 三元组列表。`render_tab_nav()` 渲染按钮行并
返回当前选中的 key；`main()` 里只需要：

```python
active_key = render_tab_nav()
for key, _label, render_fn in TAB_DEFS:
    if key == active_key:
        render_fn(client)
        break
```

不需要动 20 个 `render_*_tab()` 函数内部的任何代码——函数签名和实现
完全不变，只是"什么时候被调用"这一层逻辑变了。

## 顺带解决的问题

- **后台轮询**：原来"💬 对话"的两个 2s fragment、"🗂️ 会话管理"的
  5s/3s fragment，只要看板页面开着就一直在服务端轮询。改成按需渲染后，
  这些 fragment 只有在对应 tab 被选中时才会挂载，切到别的 tab 之后，
  原来的 fragment 停止执行（fragment 生命周期跟着触发它的代码路径走，
  路径不执行了，轮询自然停止）。
- **顶栏跳转逻辑变简单**：原来的 `_inject_tab_switch_script()` 是靠 JS
  在 DOM 里找 `button[data-baseweb="tab"]` 按可见文本模拟点击，属于
  "绕过 Streamlit 拿不到 tab 控制权"的 hack。改成 `_active_tab` 驱动后，
  `st.session_state["_active_tab"] = "kanban"; st.rerun()` 一行就能实现
  同样的跳转，不再需要 JS 注入、也不再依赖 tab 文案不变（原来的实现是
  按钮文本"包含匹配"，理论上以后改文案就可能失效；现在直接用稳定的
  key，不存在这个问题）。`_inject_tab_switch_script()` 函数已删除。

## 兼容性处理（已落地）

1. **原 3 处 `_pending_tab_switch` 调用点**（顶栏"🔍 查看并控制"按钮跳到
   目标看板/Cron任务/工作流，共 3 处；另有 1 处"外部项目"tab 的
   "复制到对话框"跳转到"💬 对话"）——全部改成直接设置 `_active_tab` +
   `st.rerun()`。
2. **`?manifest_id=`/`?session_id=` 深链接**——`apply_deep_link_query_params()`
   原来只把 query param 写进 `artifacts_open_id`/`artifacts_session_filter`，
   本身不跳 tab；按需渲染改造后，如果不顺带跳 tab，"产出预览" tab 根本
   不会被渲染，用户点开链接后还要自己再点一下才能看到效果。这次改造
   顺带把这个补上：命中 `manifest_id`/`session_id` 时，同时把
   `_active_tab` 设成 `"artifacts_preview"`，深链接现在能真正做到"打开
   链接直接停在产出预览 tab"。
3. **切 tab 的体感**：从"纯前端瞬切"变成"点击 → 服务端 rerun → 重新
   渲染"，会有一次网络往返（通常几十到一两百毫秒级别），不再是零延迟
   切换。这是这个方案本质上的代价，用瞬时切换体验换取减少无效请求。
4. **样式**：不再需要原来给 `st.tabs()` 做"换行 + 隐藏原生滑动高亮条"
   的 CSS 补丁（这段 CSS 已删除）——`st.button(type=...)` 原生的
   实心/描边样式已经足够区分选中态，`st.columns` 窄屏下也会自动换行。

## 影响范围

- `apps/mini_agent_kanban/app.py`：
  - 删除 `_inject_tab_switch_script()` 函数；
  - 删除针对 `st.tabs()` 的 CSS 补丁（`inject_css()` 内）；
  - `init_state()`：`_pending_tab_switch` 替换为持久的 `_active_tab`
    （默认 `"chat"`）；
  - `apply_deep_link_query_params()`：命中深链接参数时顺带设置
    `_active_tab = "artifacts_preview"`；
  - 新增 `TAB_DEFS` 常量与 `render_tab_nav()` 函数（紧邻 `main()`）；
  - `main()` 末尾原来约 60 行的 `st.tabs()` + 20 个 `with tabs[i]:`
    分发代码，替换成 `render_tab_nav()` + 单分支渲染；
  - 4 处 `st.session_state["_pending_tab_switch"] = "<label>"` 替换为
    `st.session_state["_active_tab"] = "<key>"`。
- `docs/kanban-dashboard-guide.md`：
  - "⚙️ daemon 正在执行 N 项任务"一节里关于跳转实现方式的描述更新；
  - 新增"Tab 导航与按需渲染"一节，说明机制、`TAB_DEFS`、顶栏跳转、
    深链接兼容、已知取舍；
  - "开发规范：新增/修改板块务必用 `@st.fragment` 做局部刷新"一节补充
    说明：按需渲染改造后，"重跑整个脚本"实际效果收窄为"重新渲染当前
    选中的这一个 tab"，`@st.fragment` 现在解决的是"同一个 tab 内部
    板块之间互相牵连"的问题，"不同 tab 之间互相牵连"已经被按需渲染
    从根上解决，两者分工不同、都还需要。

未改动 20 个 `render_*_tab()` 函数内部的任何代码。

## 状态

已完成并验证（`python -m py_compile app.py` 通过）。
