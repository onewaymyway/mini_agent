# 成长顾问：理想形态对照与改进方向 实施记录

对应计划：`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`
（第 7 节给出的优先级顺序：方向 1 → 方向 4 → 方向 5 → 方向 3 →
方向 2 → 方向 6（不排期））。本记录按落地顺序追加，每个方向落地后
在此补一节，不新开文件。

## 已完成

### 方向 1：素材参与度信号

对应方案文档第 1 节。目标：让系统知道"用户到底有没有在看"某个正在
自主推进方向的素材，作为方向 2/4/5 判断的数据地基。

- `evolution/growth_advisor.py` 新增两个函数，紧跟在 `get_pursuit_
  saturation()` 之后，复用同样的存储位置约定：
  - `record_pursuit_material_view(paths, goal_id, cycle_count)`：
    覆盖式写入 `growth_state.json` 新增的 `pursuit_material_views`
    子字典（跟 `pursuit_saturation` 平行，不新开文件），只存
    `{"last_viewed_cycle", "viewed_at"}`，不记录停留时长——看板技术上
    拿不到，也没必要为这一个信号引入额外的前端埋点体系。
  - `get_pursuit_material_engagement(paths, goal_id, current_cycle)`：
    只读查询，返回 `{"last_viewed_cycle", "current_cycle",
    "cycles_since_last_view"}`。从未查看过时 `last_viewed_cycle` 为
    `None`，`cycles_since_last_view` 等于 `current_cycle`（视为"从头
    到现在都没看过"）；`current_cycle` 小于 `last_viewed_cycle` 时
    （理论上不该发生，轮次只增不减）防御式钳制为 0，不返回负数。
- `api/routes.py`：
  - `GET /growth/pursuits` 响应每条方向新增 `engagement` 字段，调用
    `get_pursuit_material_engagement()` 拼装，纯只读聚合，不产生新的
    持久化。
  - 新增 `POST /growth/pursuits/{goal_id}/view_material`：供看板
    "📄 素材"按钮点击时调用。当前轮次由后端从 `GoalBacklog` 读取（不
    信任前端传来的轮次，也省去前端拼请求体），Goal 不存在时返回 404。
- `apps/mini_agent_kanban/client.py` 新增 `growth_pursuit_view_
  material(goal_id)`，对应新端点。
- `apps/mini_agent_kanban/app.py::_render_growth_pursuits()`：
  - "📄 素材"按钮点击时先调用 `client.growth_pursuit_view_material()`
    记一次埋点，失败不阻塞打开素材本身（`try/except` 静默吞掉）。
  - 每条方向标题下方新增一行纯展示 caption：从未查看过时提示"你还
    没查看过这份素材（已有 N 轮内容）"；查看过但又有新内容时提示
    "距你上次查看已经过了 N 轮新内容"；`cycles_since_last_view == 0`
    时不展示（刚看过，没必要提示）。不做警告样式（跟 `saturation`
    的 `st.warning` 区分），不做任何阻断，用户自己判断要不要点进去。
- 新增测试 `tests/test_growth_advisor_material_engagement.py`（5 个
  用例）：从未查看/记录后查询/二次查看覆盖式更新/跨 goal_id 隔离/
  轮次倒退时钳制为 0。
- 成本核对：零新增 LLM 调用，零新增持久化文件，`GET /growth/
  pursuits` 每条方向多一次 `_load_growth_state()` 读取（跟
  `get_pursuit_saturation()` 同一次 IO 量级），符合方案文档"成本
  极低"的预期。这条信号本身不触发任何自动决策（比如自动降频），
  下一步用途留给方向 4（跨方向全局视角）判断，对齐方案文档"先有
  数据、再谈决策"的克制顺序。

## 待推进（按方案文档第 7 节优先级）

1. 方向 4：跨方向全局视角摘要（依赖本方向的参与度信号 + 已有的
   饱和度信号）。
2. 方向 5：学习效果自测环节。
3. 方向 3：Goal 执行内容反哺信号扫描。
4. 方向 2：反馈模式统计展示（第一步纯统计，第二步 LLM 归纳暂不
   排期）。
5. 方向 6：主题类型分化的调研/呈现风格——方案文档明确本轮不建议
   排期，留待后续视情况重新评估。
