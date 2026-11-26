# 桌面关注事件与汇总端点指南（Desktop Attention Events）

> 设计与分阶段计划见 [`next_doc/windows_desktop_resident_plan.md`](../next_doc/windows_desktop_resident_plan.md)。
> 本文面向使用者/客户端开发者：是什么、怎么开、怎么消费。

## 1. 解决什么问题

Windows 常驻桌面客户端（托盘/Toast，后续阶段实现）需要在**新增待处理事项**时被即时唤醒，而不是不停轮询。本功能提供两样东西：

1. SSE `attention` 事件：新增一条待处理汇报或 cron 异步提问时推送一条摘要；
2. `GET /v1/desktop/summary`：一次请求拿到所有"待用户处理"事项的计数快照，供托盘角标使用。

两者都是**只读/只通知**，不改变任何现有存储与行为。

## 2. 怎么开启（保守 opt-in，默认关闭）

`agent_config.json`：

```json
{
  "http": {
    "desktop_attention_events_enabled": true
  }
}
```

- 默认 `false`：不注册任何监听，`attention` 事件不会产生，现有行为完全不变。
- 修改后需重启 daemon 生效。
- `/v1/desktop/summary` **不受该开关影响**，始终可用；开关只决定 SSE 是否推送 `attention`。

> 注意：本开关只控制 daemon 是否推送事件。桌面端是否真的弹出气泡，还取决于托盘里的“桌面弹窗提醒”（默认关闭）。两者的判定规则与日志原因见 [桌面端指南 §4.2](desktop-client-guide.md)。

## 3. SSE `attention` 事件

订阅 `GET /v1/stream`（可带 `since_id`/`Last-Event-ID` 重连），收到：

```text
id: 1234
event: attention
data: {"turn_id": "", "session_id": "", "ts": 1764125000.1, "kind": "cron_question",
       "title": "选A还是B？", "ref_id": "cq_job1_...", "level": "blocking",
       "extra": {"job_id": "job1"}}
```

| 字段 | 含义 |
|---|---|
| `kind` | `report`（汇报）、`cron_question`（cron 异步提问）、`permission`（工具审批等待你）、`interaction`（ask_user 系列提问）、`turn_done` / `turn_failed`（你自己发起的任务完成/失败）。桌面端只对其中一部分弹 Toast，见 [桌面客户端指南 §4.2](desktop-client-guide.md#42-toast-与待处理事项s3) |
| `title` | 一行摘要，最长 200 字；**不含正文** |
| `ref_id` | 对应记录 id（`report_id` / `question_id`），用于回查 |
| `level` | `normal` 或 `blocking`（`ask_user_async` 的 `urgency=blocking`：没有答案无法继续） |
| `extra` | 附加信息：`cron_question` 带 `job_id`；`permission` 带 `tool_name`/`turn_id`；`interaction` 带 `interaction_kind`/`turn_id`；`turn_done`/`turn_failed` 带 `session_id`（桌面端据此忽略快速面板自己的任务） |

> S3 起，`permission` / `interaction` 在登记待回答时即发布，`turn_done` / `turn_failed` 只对**用户自己发起**的 turn 发布（cron、autonomous 发起的静默）。无监听者时发布是空操作，对未开启本功能的部署没有任何影响。

回查详情：

- `report` → `GET /v1/notifications/pending`，确认已读 `POST /v1/notifications/pending/{id}/ack`
- `cron_question` → `GET /v1/cron_questions/pending`，回答 `POST /v1/cron_questions/{id}/answer`

事件**不带 `session_id`**（跨 session 的系统级事件），所以按 `session_id` 过滤订阅的客户端也能收到。

## 4. `GET /v1/desktop/summary`

```json
{
  "counts": {
    "pending_permissions": 1,
    "pending_interactions": 0,
    "failed_objectives": 0,
    "pending_reports": 2,
    "cron_questions_pending": 1,
    "crash_alerts_unacked": 0
  },
  "attention_total": 4,
  "attention_events_enabled": true,
  "server_time": 1764125000.1
}
```

- `attention_total`：以上计数之和，托盘角标直接显示；
- `attention_events_enabled`：为 `false` 时客户端应退回低频轮询本端点；
- 权限/交互/失败 Objective 复用 `/v1/inbox` 的跨 session 聚合逻辑；
- 需要鉴权（Bearer token），多用户模式下仅 owner 可访问。

## 5. 客户端消费约定

1. 启动/重连后**先请求 summary 取快照**，再订阅 `/v1/stream` 的新事件。环形缓冲在内存中（默认 2000 条），daemon 重启后 `attention` 事件会丢失，不能依赖它做状态恢复。
2. **崩溃告警不会以 `attention` 事件到达**：它由 supervisor 进程写入，崩溃的 daemon 进程里已无监听者。SSE 断线后应查询 summary 的 `crash_alerts_unacked`，详情见 `GET /v1/daemon/crash_alerts`（参见 [daemon-crash-recovery-guide.md](daemon-crash-recovery-guide.md)）。
3. 权限审批/交互请求本来就有 `permission_req`/`interaction_req` SSE 事件，不属于 `attention`，按原有方式处理。

## 6. 代码位置

- `src/mini_agent/notification/attention_hub.py`：进程内发布点（无监听者为空操作，监听者异常不外溢）
- `src/mini_agent/notification/reports_store.py::append_report`、`questions_store.py::append_question`：落盘成功后发布
- `src/mini_agent/api/bridge.py::AgentBridge.emit_attention`、`api/models.py::EventType.ATTENTION`
- `src/mini_agent/api/server.py::HttpServer.start/stop`：按开关注册/注销监听
- `src/mini_agent/api/routes.py::get_desktop_summary`
- 测试：`tests/test_desktop_attention_s0.py`
