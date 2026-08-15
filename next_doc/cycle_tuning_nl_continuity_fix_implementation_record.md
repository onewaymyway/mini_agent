# 修复：调优自然语言解析层丢失历史内容（实施记录）

## 问题

`/agent goals tune <goal_id> "<自然语言意见>"` 走 Stage 3 LLM 解析层时，用户
在第一次意见 confirm+apply 之后，第二次再提自然语言意见（尤其是"再加一条/
补充一下"这类追加型表述），生成的草案会把 `task_template` 整个替换掉，之前
已经确认过的内容丢失。

## 根因

`CycleDiagnosticsReport`（`perception/cycle_diagnostics.py`）此前完全没有携带
`priority` / `task_template` 这两个白名单参数的**当前值**。`parse_nl_request_
to_changes()` 拼给 LLM 的 prompt context 里只有 `goal_title`/`schedule`/
`execution_phase_mode`/`cron_health`，唯独漏了这两个——尤其 `task_template`
是唯一的自由文本参数，LLM 完全不知道"当前生效的模板长什么样"，只能凭用户这
一句话从零编一份新文本，等于每次都是"重写"而不是"追加"。

`priority`/`task_template` 本身在系统里是有单一数据源、随时能读到最新值的
（`node.priority`、`CronJob.task_template`），根因不是"没有历史记录"，而是
"报告聚合的时候压根没把这两个字段带出来"。

## 修复

1. `CycleDiagnosticsReport` 新增 `priority: int = 0` / `task_template:
   Optional[str] = None` 两个字段。
2. `build_cycle_diagnostics()`：`report.priority = node.priority`；
   `_cron_job_for_goal()` 返回的 dict 里补上 `task_template`，`report.
   task_template` 从中取值。两者都是从当前系统状态实时读取，天然就是"上一次
   apply 之后的最新值"，不需要额外维护一份"调优历史"。
3. `parse_nl_request_to_changes()` 的 prompt：
   - context 里加入 `priority` / `task_template` 当前值；
   - 明确要求 LLM——task_template 如果是追加型意见，输出的 `to` 必须是
     "保留旧内容 + 融合新要求"的完整新文本；只有用户明确说"替换/改成"才
     整段重写；不确定倾向时按追加处理（更保守，不丢用户已确认的内容）。

## 影响范围

只改了 prompt 拼装的输入数据和报告结构，不改变白名单校验、draft→confirm→
apply 状态机、REST/CLI 接口签名。`build_tuning_proposal()`/
`apply_tuning_proposal()` 均未改动。

## 测试

`tests/test_cycle_tuning.py` 新增：

- `TestParseNLRequestToChanges::test_prompt_includes_current_priority_and_
  task_template` — 验证 prompt 里确实包含当前值和"合并"措辞
- `TestParseNLRequestToChanges::test_context_default_priority_and_task_
  template_absent` — 没有历史值时字段仍然出现（值为 0/null），不会整个漏掉
- `TestBuildCycleDiagnosticsCurrentValues`（新增测试类，3 个用例）——
  `build_cycle_diagnostics()` 正确带出 priority/task_template，且
  `test_task_template_reflects_previously_applied_change` 直接复现了原始
  bug 报告的场景（第一次 apply 之后，报告里的值必须是 apply 后的最新值）

全部通过；另外跑了 `test_cycle_diagnostics.py` / `test_cycle_diagnostics_
tuning_routes.py` / `test_kanban_growth_dragdrop.py` / `test_growth_advisor.py`
共 253 个用例确认无回归。

## 文档

`docs/goal-cycle-tuning-guide.md` "Stage 3" 一节新增"多轮意见的连续性"小节，
说明现在的行为和一个示例。
