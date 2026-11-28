# 每日融合日报：通知与桌面查看

## 行为概览

每天 22:00 `sys:daily_digest` 生成**当天**融合日报（行为时间分布、目标进展、代码提交、异常统计、会话活动），落盘到 `.agent/daily_reports/<YYYY-MM-DD>.{json,md}`，然后推送通知：

| 渠道 | 行为 |
|---|---|
| 看板「关注与通知」 | 作为一条汇报出现，分类为「关注汇报」（`source=daily_digest`），正文为完整 Markdown |
| 其它已启用渠道（email / webhook 等） | 走 `notification/config.yaml` 的 `default_channels`，与其它通知一致 |
| 桌面端弹窗 | 生成时在线：SSE `attention` 事件实时弹「今日日报已生成」（非阻塞，遵守免打扰） |
| 桌面端补弹 | 启动或重连时，若最近 2 天内生成的日报尚未弹过，补弹一次 |

## 实现要点

- `sys:daily_digest` 现在是**本地 handler**（`evolution/daily_digest_job.py`），不再以 LLM 对话方式执行 `/digest daily`，零 token 成本。
- 生成与通知分离：`generate_daily_digest()` 只负责落盘；`notify_digest_ready()` 负责派发通知并发布 `attention_hub.KIND_DAILY_DIGEST`（`ref_id` 为日期）。通知失败只记日志，不影响日报文件。
- 手动 `/digest daily` 只生成与展示，不触发通知，避免手动操作刷屏。

## 桌面端

- 默认 `toast_enabled = true`。已有 `desktop.json` 里显式写了 `false` 的仍然保持关闭。
- 托盘菜单「今日日报…」打开日报窗口，支持前一天 / 后一天 / 最新切换；无日报时给出提示。
- 补弹状态记录在 `desktop.json` 同目录的 `digest_toast_state.json`（字段 `last_popped_day`）。只有真正弹出 Toast 才会记录；被免打扰或弹窗关闭时不记录，下次重连仍会尝试补弹。删除该文件即可重新补弹最近一份日报。

## 接口

`GET /v1/digest/daily?date=YYYY-MM-DD` 返回：

```json
{"digest": {...结构化数据...} | null, "markdown": "...日报正文..." | null}
```

`date` 缺省时返回最新一份。

## 注意

- 实时弹窗依赖 `http.desktop_attention_events_enabled`（`agent_config.json` 中已为 `true`）。该项关闭时，只有启动/重连补弹生效。
- 补弹只针对最近 2 天内生成的日报，避免首次安装时弹出很久以前的日报。

## 测试

```
PYTHONPATH=src:apps/mini_agent_desktop python3 -m pytest tests/test_daily_digest_notification.py -q
```
