# article_garden P7 实施记录（闭环、挡位、预算与导出）

- **关联方案**：`next_doc/article_garden_external_project_plan.md`（§9 挡位与预算、§3 一轮 cycle 的固定顺序、§14 P7、§19.2 顶栏预算、§19.3 首页与待办收件箱、Q13 导出、Q17 多库串行与日预算总闸）
- **完成日期**：2026-11-23
- **状态**：已完成。**六期（P0–P7）全部完成**，等待你确认。

## 1. 验收对照（方案 §14 P7）

| 验收项 | 结果 |
|---|---|
| G0→G2 逐级可开 | 挡位 + 开关共同决定每一项自动行为（`gears.py`）。G0 拒绝运行闭环；G1 只整理与选题（选题仍需放行，`test_g1_cycle_proposes_topics_without_approving_and_is_idempotent`）；G2 只有打开自动放行 / 调研 / 成文后才执行，每轮成文数受限（`test_g2_cycle_auto_approves_researches_and_drafts_within_limits`）；G3 自动到已审阅但不发布，除非打开自动发布（`test_g3_reviews_and_publishes_only_when_allowed`） |
| 预算超限正确停止 | 本库与全局每日上限强制执行，超限即抛出 `BudgetStop`，整轮停止并在报告中写明；超限那次调用不计数；当日用尽后下一轮立即停止、零调用（`test_daily_budget_stops_cycle_and_next_run_makes_no_calls`、`test_global_budget_is_a_separate_hard_cap`） |

## 2. 新增 / 修改的文件（相对项目根目录）

| 路径 | 说明 |
|---|---|
| `article_garden/budget.py` | 预算与停止：`GuardedLLM`（停止检查、单步超时、记账、辅助线程执行）、`charge`（本库与全局两级上限）、`request_stop / stop_requested / clear_stop`（`logs/stop.flag`）、`usage` |
| `article_garden/gears.py` | 挡位闸门：`allowed(eff, action)` 对 cycle、digest、wiki、tree、topics、auto_approve、research、write、review、publish、scheduled 给出允许与原因 |
| `article_garden/cycle.py` | 一轮闭环：固定七步、单步隔离、预算/停止整轮结束、无变化跳过（摘要签名、提案检查）、知识树提案不自动应用、报告 `runs/last_cycle.json` |
| `article_garden/export.py` | 导出：小型 Markdown→HTML 转换器、静态站（仅已发布文章与 ok 概念）、已发布文章 zip（含 manifest） |
| `article_garden/overview.py` | 流水线状态改为库内真实数据；待办收件箱改为真实事项（待放行、待审、失败、树提案）。P0 占位已移除 |
| `entrypoints/cycle.py` | 一轮闭环入口：`--scheduled`、`--stop`；先检查挡位，再检查模型 |
| `entrypoints/export_site.py` | 导出入口：默认静态站，`--zip` 打包 |
| `entrypoints/{digest,build_wiki,build_tree,plan_topics,research,write_article,draft_charter}.py` | 模型调用改为经 `budget.guard(...)` 包装（超时取自设置） |
| `project.yaml` | 登记 `cycle`（managed，4 小时）与 `export_site`（unmanaged）；仍不登记 cron |
| `ui/home_page.py` | 「▶ 运行一轮（后台）」按挡位启用；「导出静态站」；今日 LLM 用量显示 |
| `ui/runs_page.py` | 「■ 请求停止正在运行的任务」 |
| `tests/test_cycle.py` | 17 项测试（全部假模型、假搜索、假网络）：闸门、G1/G2/G3 闭环、幂等、提案不自动应用、预算（本库/全局）、停止（前后两种时机）、单步超时、异常类型、导出（仅已发布、草稿不导出、zip 内容）、Markdown 转换、入口脚本、首页 |
| `PROJECT.md` `docs/USAGE.md` `docs/README.md` | 文档更新 |

## 3. 关键实现与决定

| 事项 | 做法 | 理由 |
|---|---|---|
| 超限与超时的异常类型 | `BaseException` 子类 | 流水线多处 `except Exception` 会把单篇失败吞掉；超限必须整轮停止 |
| 0 的含义 | 上限为 0 表示不允许调用 | 比“0 = 不限”更保守；设置页说明文字已写明 |
| 停止的生效点 | 每次模型调用前检查，等待期间每 0.2 秒检查 | 方案 §16 “立即停止”的可实现边界；未发出的调用不会再发出 |
| 单步超时 | 调用在辅助线程执行，超时即返回 | 没有这一层，超时只能在调用边界检查，无法限制一次长时间的等待 |
| 停止信号的位置 | `logs/stop.flag`，每轮开始清除 | 看板与命令行是不同进程，需要文件信号（P1 记录中留待 P7 的事项） |
| 闭环顺序 | 与方案 §3 一致：摘要 → 概念 → 提案 → 选题 → 调研 → 成文 → 审阅 | 每一步的输入都是前一步的输出 |
| 无变化不调模型 | 摘要：内容哈希；概念与关系：`wiki/last_build.json` 摘要签名；提案：没有未挂载概念时不调用；选题：已在队列的缺口不再判重 | 方案 §15 成本与“重复跑一次零 LLM 调用”的验收 |
| 树提案 | 只生成提案，已有待确认的提案时不再生成 | 方案 §5.3 与 §19.1 原则 1：先预览、后应用 |
| 单步失败 | 记录并继续后续步骤；超限、停止结束整轮 | 方案 §9 失败策略：单个失败不影响其它 |
| 定时 | 只提供开关与 `--scheduled` 入口，不登记 cron | 方案 §9：默认不声明 schedule，需要时由你打开并登记 |
| 导出的范围 | 仅 published 文章；概念仅 ok 状态 | 方案 Q5 与 P6 的“生成文章不成为唯一证据” |
| 导出的格式 | 纯静态 HTML，内置小型 Markdown 转换 | 方案 Q13；不引入新依赖，也不需要 JavaScript |

## 4. 验证

- `pytest`：**190 项全部通过**（P6 的 173 项 + P7 新增 17 项）。全套连续运行稳定；闭环测试反复运行 8 次全部通过。
- **变异测试**（移除保护后对应测试必须失败，已逐一确认）：
  - 去掉每日预算的上限检查 → 2 项预算测试失败；
  - 去掉 research 的挡位闸门 → G1 测试失败；
  - 去掉 write 的挡位闸门 → G1 测试失败（测试已加强为同时断言“调研”“成文”“审阅与发布”三步均跳过）。
- 过程中发现并修正的问题：
  1. **停止在最后一次调用之后发生时，报告仍显示“已完成”**——已修正为在闭环结束前检查停止信号，并如实记录（`test_stop_during_last_call_is_still_reported`）。
  2. 停止测试依赖模型调用次数，偶发失败——改为在第一次调用时请求停止，结果确定。
  3. G0 时入口先报“模型未就绪”而非“G0 为全手动”——已调整检查顺序。
- `pyflakes` 无告警。
- 真实进程验证：`cycle.py` 在 G0 / G1 下的报错、`--stop` 写入标志、`export_site.py` 导出空站、`--zip`；`project.yaml` 经 daemon 的 `load_manifest` 解析通过（12 个入口，web_app 端口 8508）；`streamlit run app.py` 健康检查返回 200。

## 5. 与方案的偏差 / 未做

- **未在真实模型或真实网络上运行过整轮闭环**；所有验证都使用假模型、假搜索与假网络。第一次真实运行请从 G1、小库开始，并观察「运行日志」与 `runs/last_cycle.json`。
- **定时运行未登记计划任务**：按方案 §9 保守默认，需要你自行配置。
- **预算的并发精度**：日计数在本进程内加锁；多进程同时运行时可能略有偏差（方案 §20.3 的串行策略是对策，未在本期实现串行队列）。
- **“全局总闸”只统计本机的数据根目录**：不同机器或不同数据根目录各自计数。
- **超时与停止不能中断已发出的模型请求**：请求会在后台线程结束，结果被丢弃。
- **导出没有目录级的搜索与样式主题**：只做了可读的静态页面。
- **看板没有在真实浏览器中点过**，仅做了 AppTest 与 Streamlit 进程级的健康检查。
- **成本估算未校准**：方案 §15 的调用数估算尚未用你的库实测。
- **一轮闭环没有“批量放行”与“选题批量处理”**：沿用 P4 的限制。

## 6. 需要你确认的事项

1. 请在 G1 下对你的 20 余篇文章先跑一轮，检查选题质量与成本。
2. 确认是否需要定时运行；如需要，请告诉我你偏好的时间与计划任务方式（系统 cron、Windows 任务计划程序或 mini_agent 的 daemon）。
3. 确认 P5 代码包恢复的方式是否可接受（见 `article_garden_p6_implementation_record.md` 第 6 节）。
