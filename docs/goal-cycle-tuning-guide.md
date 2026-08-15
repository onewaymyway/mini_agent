# Goal 交互式调优（Cycle Tuning）指南

> Stage 2（规则+结构化调优 draft/confirm/apply/reject）/ Stage 3（可选的
> LLM 自然语言意见解析，默认关闭）均已实现，见
> `next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`。
> 依赖 [诊断报告](goal-cycle-diagnostics-guide.md) 的数据，但属于独立能力
> （能力 B）。

## 解决什么问题

看完[跨轮次诊断报告](goal-cycle-diagnostics-guide.md)之后，你可能想调整
这个 Goal 的调度频率、优先级、执行阶段等参数——但不希望自己去翻配置文件，
也不希望改动直接生效、没有一个"看一眼再确认"的环节。交互式调优提供
**草案（draft）→ 确认（confirm）→ 应用（apply）** 三步流程，改动本身经过
明确确认才会真正生效。

## 安全边界：白名单参数

调优机制**只能**修改以下五个参数，每个参数都复用已有的、独立测试覆盖的
修改入口，不允许通过这个机制修改任意代码/配置文件，也不会执行任意工具
调用：

| 参数 | 说明 | 复用的既有入口 |
|---|---|---|
| `schedule` | cron 调度频率，如 `interval:3600` / `cron:0 9 * * 1` | `make_goal_recurring()` |
| `priority` | Goal 优先级 | `GoalBacklog.update_fields()` |
| `execution_phase` | 手动切换执行阶段（`explore`/`converge`/`stable`/`tidy`/`auto`） | `execution_phase.set_mode()` |
| `task_template` | cron 触发时注入的任务描述模板 | `CronScheduler.update_task_template()` |
| `regenerate_spec` | 重新生成一份执行规范草稿（只生成，不自动确认） | `GoalExecutionSpecBuilder.build_draft()` |

**明确不支持**：修改 Goal 的 title/description 本体、产出目录结构/命名
规则、白名单之外的任何字段。扩大白名单需要单独评审。

## 命令

```
/agent goals tune <goal_id> <param>=<value> [<param2>=<value2> ...] [--reason <text>]
                                  — 直接生成结构化草案
/agent goals tune <goal_id> "<自然语言改进意见>"
                                  — Stage 3（可选，需开启配置）：不含 '=' 时
                                    按自然语言意见处理，尝试用 LLM 解析成
                                    白名单参数改动
/agent goals tune suggest <goal_id>
                                  — 基于诊断报告规则触发的候选草案
/agent goals tune list <goal_id>
                                  — 列出历史草案（含状态）
/agent goals tune confirm <goal_id> <proposal_id>
                                  — 确认草案（仍未生效）
/agent goals tune apply <goal_id> <proposal_id>
                                  — 应用已确认的草案
/agent goals tune reject <goal_id> <proposal_id> [reason...]
                                  — 拒绝草案，作废
```

示例：

```
/agent goals tune goal_abc123 priority=8 --reason "最近产出质量不错，提高优先级"
/agent goals tune confirm goal_abc123 tuning_xxxxxx
/agent goals tune apply goal_abc123 tuning_xxxxxx
```

## REST

```
POST   /v1/goals/{goal_id}/tuning_proposals          Body: {"changes": [...], "source"?: str}
                                                       或 Body: {"nl_text": str}（Stage 3，可选，
                                                       需开启配置，可能返回 proposal=null）
POST   /v1/goals/{goal_id}/tuning_proposals/suggest   规则触发建议（可能返回 proposal=null）
GET    /v1/goals/{goal_id}/tuning_proposals           列出历史草案
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/confirm
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/apply
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/reject   Body（可选）: {"reason": str}
```

## 四个状态：draft → confirmed → applied | rejected

- **draft**：草案已生成，尚未确认，不影响任何实际状态。
- **confirmed**：用户确认了"这份草案本身"，**仍未生效**——与
  `GoalExecutionSpec` 的确认语义一致，确认不代表立即执行。
- **applied**：真正调用了白名单参数对应的修改入口。每一项改动的成败在
  `apply_results` 里逐条列出（`{"param", "to", "ok", "detail"}`），某一项
  失败不影响其它项已经成功应用的部分，不会静默吞掉失败。应用完成后会
  自动追加一条 `progress_notes`（"根据诊断报告调优：... "）留痕。
- **rejected**：草案作废，不产生任何实际改动，同样会追加一条
  `progress_notes` 记录"提出过但被拒绝"，避免下次规则建议又提出同样的
  内容而你不记得已经考虑过。

## 规则触发的建议（`tune suggest`）

不调用 LLM，基于诊断报告里已经算出的信号直接生成候选草案：

- **cron 连续跳过达到阈值**（默认 5 次）且 `schedule` 是
  `interval:<秒>` 格式 → 建议把间隔翻倍。`cron:` 表达式格式没有一种
  确定性的"放宽"方式，不会为这种格式生成建议。
- **长期卡在 explore 阶段未收敛**（且处于 `auto` 模式、未被手动锁定）→
  建议重新生成一份执行规范草稿，供你对比是否要用新草案替换现状（这一步
  只生成草稿，不会自动确认生效，仍需 `/agent goals spec confirm`）。

两个信号都没命中时返回"当前没有基于诊断报告规则触发的调优建议"，不是
错误。

## `regenerate_spec` 的额外依赖

应用 `regenerate_spec` 改动需要 `AppConfig` 来构造
`GoalExecutionSpecBuilder`（与生成执行规范草稿走同一条路径）。CLI/REST
会尝试自动加载配置；如果这一项失败并提示"未提供 AppConfig"，可以改用
`/agent goals spec generate <goal_id>` 手动生成。

## Stage 3（可选）：自然语言意见解析

Stage 1/2 已经能覆盖"命令行/接口直接传结构化 `param=value`"和"规则触发
建议"两种场景。如果想直接说一句人话（比如"这个任务最近老是被跳过，帮我
放宽一下触发间隔"）让系统自动映射到白名单参数，可以打开这一层可选增强：

1. 在 `agent_config.json` 里设置：

   ```json
   { "cycle_tuning": { "tuning_llm_parse_enabled": true } }
   ```

2. CLI：命令里不含任何 `param=value`（即没有 `=`）时，整段文本按自然语言
   处理：

   ```
   /agent goals tune goal_abc123 这个任务最近老是被跳过，帮我放宽一下触发间隔
   ```

   REST：

   ```json
   POST /v1/goals/{goal_id}/tuning_proposals
   { "nl_text": "这个任务最近老是被跳过，帮我放宽一下触发间隔" }
   ```

3. 解析出的改动会先生成 `status="draft"` 的草案（`source="user_request"`，
   虽然经过了 LLM 转译，改动意图仍然来自用户），**不会自动生效**——仍然
   要走正常的 `confirm` → `apply` 两步，请在确认前仔细核对 diff，确认
   LLM 理解的映射符合你的本意。

**边界与失败回退**（见
`perception/cycle_tuning.py::parse_nl_request_to_changes()`）：

- 只能映射到 `WHITELIST_PARAMS` 里的五个参数；LLM 即使编出一个不存在的
  参数名，也会在解析阶段被丢弃，不会进入草案（双重校验：`build_tuning_
  proposal()` 本身仍然会对最终结果再做一次白名单校验）。
- 开关未开启、没有可用的 LLM、LLM 输出无法解析成合法 JSON、或判断"这条
  意见无法映射到任何白名单参数"（比如"暂停一阵子"——这应该走
  `/agent goals unrecur`，不是调优参数改动）：都会静默返回"未能生成
  草案"，CLI/REST 会提示改用具体的 `param=value` 命令，不会报错中断，也
  不会强行猜一个可能有害的改动。

**多轮意见的连续性**：每次自然语言解析都会把 `priority`/`task_template`
的**当前值**（上一次 apply 之后的最新状态，由 `build_cycle_diagnostics()`
实时读取）带进 prompt 作为基线，并明确要求 LLM——如果用户这次是"追加/
再加一条"，输出的 `to` 必须是合并了旧内容的完整新文本，不能只输出这次
新提的那一小段。也就是说：

```
第 1 次："检查完 A 之后，再检查一下日志有没有报错"
  → task_template.to = "检查 A；检查日志是否有报错"
  → confirm → apply（这份内容现在是"当前值"）

第 2 次："再加一条，顺便看看磁盘空间"
  → LLM 这次能在 prompt 里看到上面这句"当前值"，输出的 to 会是
    "检查 A；检查日志是否有报错；检查磁盘空间"，而不是只有"检查磁盘空间"
```

如果确实想整段推倒重来，明确说"改成/替换成/不要之前那些了"，LLM 会按
整段重写处理，不会强行拼接。`priority` 同理，"当前值"用于理解"加倍/
再高一点"这类相对表述。

## 看板（Streamlit）里的交互方式

CLI/REST 之外，`apps/mini_agent_kanban` 也提供了同一套能力的图形化入口，
在每张 Goal 卡片（非 Objective）上追加一个 `🩺 诊断与调优` 折叠区，跟
`🧭 执行阶段` 徽章是同级的常驻入口，不需要先绑定周期性才能看到。

**设计取舍**（对应"看板里怎么操作比较合理"这个问题）：

1. **复用已有的"徽章 + 默认折叠"范式**，不新开一个独立 Tab/页面。诊断
   和调优都是围绕单个 Goal 的操作，跟着卡片走，用户不用在"看板"和某个
   独立的"诊断中心"之间来回切换、重新定位到同一个 Goal。折叠区标题上的
   🟢/🟡/🔴 徽章直接反映健康状态，不需要展开就能"扫一眼知道要不要管"——
   这是诊断报告最核心的价值，如果还要点开才能看到红绿灯，价值会打折扣。
2. **诊断报告本身随卡片渲染就取一次**（跟执行阶段徽章、产出目录折叠区
   同一策略）——纯本地文件聚合，成本可控，不需要额外的"加载"按钮。
   **LLM 自然语言摘要不会跟着自动生成**：那是要真正花一次 LLM 调用的，
   看板上可能同时渲染几十张卡片，不应该因为卡片数量触发一堆后台 LLM
   请求，所以做成独立的"🤖 生成自然语言摘要"按钮，用户主动点了才算一次。
3. **调优草案完整复用已有的 draft → confirmed → applied 状态机和 REST
   接口**，看板侧不重新发明一套逻辑；每张草案卡片按当前状态只展示对应
   能做的操作（draft → 确认/拒绝，confirmed → 应用/拒绝），已应用/已拒绝
   的进历史折叠区，不占主要空间。
4. **手动生成草案时按参数类型给对应控件**（`execution_phase` 是下拉框、
   `priority` 是滑块、`task_template` 是文本域），而不是让用户填自由文本
   `param=value`——CLI 场景下打错参数名/值格式只是重新敲一遍命令，看板
   表单提交出错却不容易定位到底错在哪，所以从交互层面把"参数名拼错"
   这类问题排除掉，而不是提交后才靠后端报错。自然语言意见输入框依然
   保留（Stage 3），是否真的生效取决于服务端配置开关，看板不重复维护
   一份"是否已开启"的判断逻辑，未开启时原样展示后端返回的错误信息。
5. **只在 Goal 层级渲染**，不出现在 Objective 卡片上——调优的白名单参数
   全部是挂在 Goal 上的概念，Objective 没有对应语义。

对应的看板代码：`apps/mini_agent_kanban/app.py::_render_goal_cycle_
diagnostics_widget()` / `_render_goal_tuning_widget()`，`client.py` 里
`get_cycle_diagnostics` / `list_tuning_proposals` / `suggest_tuning_
proposal` / `create_tuning_proposal` / `confirm_tuning_proposal` /
`apply_tuning_proposal` / `reject_tuning_proposal` 这一组方法，均直接
调用上面列出的 REST 接口，没有引入新的后端逻辑。
