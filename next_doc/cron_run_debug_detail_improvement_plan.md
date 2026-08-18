# cron 任务执行记录——补充工具调用细节 + 完整 Agent 输出，可读时间线展示

- **触发背景**：用户想在看板"最近执行记录"里查看某次 cron 执行对应的
  详细历史，排查"为什么这次结果不符合预期"。追问后明确目的是**调试
  分析**，需要的是**工具调用细节**（调用了什么工具、传了什么参数、
  返回了什么）和**完整的 Agent 输出**（不是截断预览）。
- **架构现实（先厘清，决定了能做成什么样）**：cron 任务执行是刻意跟
  用户对话 Session **完全分离**的——`cron_agent_bridge.py` 里明确写了
  "避免和用户会话的 session 存储混在一起"，每次触发都是全新 Agent
  实例，靠 `progress_summary` 文本续接，不创建 `SessionManager` 意义
  上的 session。所以"打开对应的对话 session"这条路径在当前架构下不
  存在，也不打算新建一套（会导致 cron 任务历史无限增长，且与设计初衷
  相悖）。可行的路径是本方案要做的：把执行数据本身补充完整，再用一个
  专门的可读时间线视图展示，效果上等价于"看到这次执行的完整过程"，
  只是承载方式是 cron 自己的 `runs/<run_id>.jsonl` 事件流，而不是
  Session。

## 现状缺口

`runs/<run_id>.jsonl` 每步事件（`cron_job_executor.py`）目前只记录：

```python
ws.append_run_event(run_id, {
    "type": "step", "step_index": step_index,
    "text_preview": last_text[:500],   # 只有前 500 字
    "error": result.error,
})
```

- **输出被截断到 500 字**，看不到完整内容
- **完全没有工具调用轨迹**——调用了哪些工具、传了什么参数、每个工具
  返回了什么，一概没有记录。`StepResult`（`cron_job_executor.py`）
  只有 `text`/`done`/`error` 三个字段
- 看板"查看事件详情"目前是 `st.json(events)` 把原始事件列表直接甩
  出来，可读性差，几百个 tool_use/tool_result 混杂在一起要自己肉眼找

## 方案

### ① 从 `agent._hist`（HistoryManager）提取本步的工具调用轨迹

`agent.run_turn()` 内部已经把完整的 assistant 回复（含 `tool_use`
content block：`name`/`input`）和工具结果回注（`tool_result`，渲染成
`<tool_result>{"name":...,"output":...}</tool_result>` 格式的字符串）
都写进了 `agent._hist.history`。这些信息本来就存在，只是没人把它们
从 history 里"捞出来"存进 cron 的执行事件——不需要改 Agent 核心执行
逻辑，只在 `cron_agent_bridge.py::make_submit_step_fn()` 里，每次
`run_turn()` 前后记录 `len(agent._hist.history)`，对新增的这一段
history 做一次提取：

- 遍历新增片段，`_type == "assistant_reply"` 且 content 里有
  `type == "tool_use"` 的 block → 记下 `{name, input}`，按出现顺序
  放进一个"待配对"列表
- 遇到 `_type == "tool_result"` → 用正则把 `<tool_result>...
  </tool_result>` 里的 JSON 块逐个解析出来，按顺序跟"待配对"列表
  一一对应（顺序保证见 `history_manager.append_tool_results()` 内部
  `zip(tool_calls, results)` 的写入方式），组装成完整的
  `{name, input, output}` 三元组
- 纯读 history，不修改、不影响主流程；任何解析失败都静默跳过该条
  （防御性处理，不能让调试功能本身拖垮 cron 执行）

`StepResult` 新增字段 `tool_calls: list[dict] = field(default_factory=list)`。

### ② 完整输出改为分级保留：预览 + 全文（各自加大小上限）

`text_preview` 保留（500 字，向后兼容，旧调用方/看板 UI 不受影响），
新增 `full_text`（上限 `STEP_FULL_TEXT_MAX_CHARS = 8000` 字符）——
调试场景绝大多数单步输出不会超过这个量级；真的超过时至少能看到
输出的主体部分，不是无差别截断到 500 字。

`tool_calls` 里每条的 `input`/`output` 也各自加上限（分别
`TOOL_INPUT_MAX_CHARS = 2000` / `TOOL_OUTPUT_MAX_CHARS = 3000`）——
避免个别工具调用（比如整段文件内容、超长搜索结果）把单条事件记录
撑得过大，写死常量不做配置项，跟其它同类阈值一样先观察默认值是否
够用。

### ③ 看板：从 `st.json()` 原始转储升级成可读时间线

新增渲染函数，替换现有"查看事件详情"按钮的展示方式：
- `run_started`：任务名、超时/步数上限
- `step`：第几步、完整输出（`full_text`，用 `st.markdown` 渲染，
  过长时默认折叠只显示前一部分）、这一步的工具调用列表（每个工具
  一个可展开区块，显示 `input`/`output`，不是堆在一起的一大段 JSON）
- `stuck_recover` / `stuck_give_up`：卡死检测相关的标记
- `timed_out` / `max_steps_reached`：终止原因
- `run_finished`：最终状态/步数/耗时汇总

纯展示层改动，不改事件存储格式本身的向后兼容性（新增字段，不删改
旧字段），旧数据（还是只有 `text_preview`、没有 `tool_calls`）打开
时间线视图时该步的"工具调用"区块显示为空，不报错。

## 不做的事（本轮刻意不做）

- 不打通到真正的 Session/对话界面——架构上不存在对应的 Session，见
  上面"架构现实"一节，不引入新的关联存储。
- 不改变 `text_preview`/`progress_summary`（续接用的 2000 字进度摘要）
  的既有截断逻辑——那是 cron 续接机制本身依赖的字段，改动风险与本方案
  目标无关，维持原样。
- `STEP_FULL_TEXT_MAX_CHARS`/`TOOL_INPUT_MAX_CHARS`/
  `TOOL_OUTPUT_MAX_CHARS` 暂不做成配置项，先用固定阈值观察是否够用。

## 实施状态

| 内容 | 状态 | 涉及文件 |
| --- | --- | --- |
| `StepResult` 新增 `tool_calls`/`full_text` 字段 | 待实施 | `src/mini_agent/evolution/cron_job_executor.py` |
| `make_submit_step_fn()` 从 `agent._hist` 提取工具调用轨迹 | 待实施 | `src/mini_agent/evolution/cron_agent_bridge.py` |
| `run_job()` 把新字段写进 `step` 事件 | 待实施 | `src/mini_agent/evolution/cron_job_executor.py` |
| 看板可读时间线视图，替换 `st.json()` 原始转储 | 待实施 | `apps/mini_agent_kanban/app.py` |
| 单元测试 | 待实施 | 新增测试文件 |
| 文档同步 | 待实施 | 本文件「实施状态」表格 |
