# 步骤失败自动重跑

worker 对所有步骤（skill 与 python 步骤）在失败后自动重跑，默认最多 3 次，退避 5/15/30 秒；收到暂停/停止或预算用尽时不再重跑。

## 本次增强
1. 可配置：`_studio/settings.json` 的 `step_retry`
   - `max_retries`：全局最大次数（null = 内置 3）
   - `backoff_sec`：各次重跑前等待秒数列表（null = 5/15/30）
   - `overrides`：`{步骤id或类别: 次数}`，步骤 id 优先于类别；`0` 表示该步骤失败后不自动重跑
2. 重跑带上次失败原因：worker 把上次错误写入步骤状态 `retry_hint`，skill 步骤第一轮 prompt 会收到「上次失败原因」，结束后清除。
3. 默认行为不变（仍是 3 次），均为向后兼容的 opt-in 调整。

## 说明
修改循环等 python 步骤内部嵌套的子 skill 步骤失败时，整个 handler 作为一个步骤被重跑，不单独对子步骤重试，避免重试次数相乘。
