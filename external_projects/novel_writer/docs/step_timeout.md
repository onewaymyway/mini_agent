# 单步执行时限（step_timeout）

每个步骤最多执行 **40 分钟**（默认，可配置）。超过时限后，直接终止该步骤正在运行的 agent，然后重新跑这个步骤。

## 行为
1. `run_step()` 开始执行时登记截止时刻。时限是**整个步骤的总时限**：修改循环、乱码守卫重生成等 python 步骤里嵌套的子 skill 调用，以及补写回执/产出的多轮尝试，都共用同一个截止时刻。
2. skill 调用一律在**子进程**里执行（有无时限都一样，见下方「日志可见性」；以前无时限时在 worker 进程内运行，agent 输出只进 worker.stdout.log）；带时限时（`novel_writer/invoke_child.py`），到点由父进程整树终止（`procs.terminate_tree`，连同 agent 拉起的工具子进程）。Python 线程无法被安全强杀，所以不在进程内中止。子进程的标准输出经管道由 `outlog.LinePump` 写入该次调用的 `agent.log`，并逐行镜像进 run.log，运行中的实时输出照常可见；worker 若被杀，子进程会自行退出，不留孤儿。
3. 被终止的步骤记为 `failed`，并带 `timed_out: true`；随后走现有的**自动重跑**（`step_retry`，默认最多 3 次，退避 5/15/30 秒），重跑的 prompt 会带上「已超时被强制终止、产出可能只写了一半」的提示。超时重跑与普通失败共用同一个重跑次数；用尽后与普通失败一样停下（G5 不停机挡会继续重置并重跑）。
4. 日志、`last_run.json` 的 executed 记录里有 `timed_out` 标记，run.log 里有「⏱」与「超时被终止，自动重跑」行。

## 配置（`_studio/settings.json`，也可在「⚙ 设置」页修改全局分钟数）
```json
"step_timeout": {"minutes": 40, "overrides": {"plan_blueprint": 90, "writing": 60, "ch05.review": 0}}
```
- `minutes`：全局时限（分钟，可小数）；`null` 或 `0` = 全局不限时。旧项目没有该字段时自动按 40 分钟。
- `overrides`：`{步骤id或类别: 分钟}`，步骤 id 优先于类别，类别优先于全局；`0` = 该步骤不限时。

## 与其它时限/预算的关系
- 不限时（`minutes` 为 null/0）时保持旧行为：单次 skill 调用沿用 900 秒的框架超时，在本进程内执行。
- 限时开启时，单次调用的框架超时 = 步骤剩余时间，原来的 900 秒不再单独卡住长步骤。
- `budget.max_wall_minutes_per_run`（整次运行的分钟预算）不变；预算用尽时超时后的自动重跑也会停止。

## 已知限制
- 纯 python 步骤（不调用 skill 的确定性处理）不会被强杀，只有它们内部调用 skill 时才受时限约束。
- 被终止的 agent 可能已经写了一半文件；重跑会覆盖/修正，步骤边界守卫（`step_guard`）照常工作。
- 通过 `FrameworkInvoker(cfg=...)` 传入自定义配置时无法传给子进程，退回本进程执行（无强杀）；项目内所有入口都不传 cfg。
- 真实 LLM 下的端到端效果未验证（本项目测试均使用桩执行器）；子进程路径用「卡住的桩进程 + 孙进程」验证了整树终止。

## 日志可见性（无时限也走子进程）

`FrameworkInvoker.__call__` 不再按「有没有时限」分路：除调用方自带 cfg（无法传给子进程）外，一律走 `_invoke_subprocess`。
无时限（`step_timeout` 为 0/null，或该步骤/类别 override 为 0）时 `remaining=None`：不设截止时刻、不整树强杀，单次调用仍受 `timeout`（默认 900 秒，框架自身超时）约束。
这样「📜 运行输出」面板里总能看到 agent 的完整过程。此前构思修订等被设成不限时的步骤只能看到「调用模型中」，具体过程在 `_studio/worker.stdout.log`。
测试：`tests/test_log_visibility.py`。

没有显式登记接收器的直接调用（数据修复 `repair.repair_attempt`、旧版迁移转换等）：`FrameworkInvoker` 会根据 `export_dir`（位于 `<工作目录>/_studio/` 之下）找到工作目录，
同样把 agent 输出镜像进该工作目录的 run.log（`outlog.sink_for_export_dir`）；找不到 `_studio` 时只写 agent.log。
